"""Shared pytest fixtures.

`promptstudio.config` reads the environment at import time and derives every
archive path from it, so PROMPTSTUDIO_ARCHIVE must be set *before* the package
is imported anywhere. pytest imports conftest first, so that happens here.
"""

import os
import shutil
import sys
import tempfile

# ── must run before any promptstudio import ──────────────────────────
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

TEST_ARCHIVE = tempfile.mkdtemp(prefix="promptstudio-tests-")
# The E5a distribution guard is the one test that must read the *real*
# archive: saturation is a property of the data, not of a fixture. Stash the
# developer's path under a name the override below cannot eat. `setdefault`,
# so pointing the guard elsewhere by hand still wins.
os.environ.setdefault(
    "PROMPTSTUDIO_GUARD_ARCHIVE", os.environ.get("PROMPTSTUDIO_ARCHIVE", "")
)
os.environ["PROMPTSTUDIO_ARCHIVE"] = TEST_ARCHIVE
# Deterministic settings regardless of the developer's local .env
os.environ["PROMPTSTUDIO_TRASH"] = "1"
os.environ["PROMPTSTUDIO_TRASH_DAYS"] = "30"
os.environ["IG_AUTO_DRAIN_ON_START"] = "0"
os.environ["INSTAGRAM_SESSION_USER"] = ""
# No log file: clean_archive() wipes the archive between tests, and a live
# RotatingFileHandler would keep writing to the deleted inode.
os.environ["PROMPTSTUDIO_LOG_FILE"] = ""
os.environ["PROMPTSTUDIO_LOG_CONSOLE"] = "0"

import pytest
from PIL import Image

from promptstudio.config import SAVED_DIR
from promptstudio.prompts.cache import PromptCache
from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.favorites import FavoritesStore


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_ARCHIVE, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_archive():
    """Empty the archive root and reset all in-memory caches between tests.

    config's paths are module-level, so the archive directory is shared for the
    whole session; isolation comes from wiping it rather than re-pointing config.

    `archive.db` is deliberately *not* unlinked — `ArchiveIndex` is a singleton
    holding an open sqlite connection, and deleting the file underneath it makes
    every later write fail with "attempt to write a readonly database". Truncate
    the tables instead.
    """
    assert SAVED_DIR == TEST_ARCHIVE, (
        f"tests must run against the temp archive, got {SAVED_DIR!r}"
    )
    for name in os.listdir(SAVED_DIR):
        if name.startswith("archive.db"):  # .db, -wal, -shm
            continue
        target = os.path.join(SAVED_DIR, name)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        else:
            os.remove(target)

    PromptCache().invalidate_memory()
    FavoritesStore().invalidate_memory()
    index = ArchiveIndex.get()
    # Tombstones survive rebuild() by design, so clear them explicitly —
    # a leftover row would let a "tombstone cleared" assertion pass falsely.
    with index._lock:
        index._conn.execute("DELETE FROM deleted_posts")
        # Prompts live in the DB now, so wiping the archive directory no longer
        # clears them — without this they leak between tests.
        index._conn.execute("DELETE FROM prompts")
        index._conn.execute("DELETE FROM phashes")
        # Verdicts deliberately survive a soft delete, so wiping the archive
        # directory does not clear them either — without this a stale keep/reject
        # leaks into the next test's counters.
        index._conn.execute("DELETE FROM media_verdicts")
        # Generations live under _generations/, which the archive wipe above
        # does remove — but the rows describing them do not go with it.
        index._conn.execute("DELETE FROM generations")
        if index.fts_enabled:
            index._conn.execute("DELETE FROM prompts_fts")
        index._conn.execute(
            "DELETE FROM meta WHERE key IN "
            "('prompts_imported_from_json', 'generations_imported_from_json')"
        )
        index._conn.commit()
    index.rebuild()
    yield
    PromptCache().invalidate_memory()
    FavoritesStore().invalidate_memory()


@pytest.fixture
def store():
    return ArchiveStore()


@pytest.fixture(scope="session")
def _api_server():
    """The real handler on a real socket, bound to port 0.

    Routes were previously reachable only from `tests/ui/` (a browser over CDP),
    so status-code mapping — 400 vs 404 vs 200 — had no Python coverage at all.
    Session-scoped: one bind for the run, and the server holds no state of its
    own, so `clean_archive` between tests is still the isolation boundary.
    """
    import threading

    from promptstudio.server.handler import (
        GalleryRequestHandler,
        ThreadingHTTPServer,
    )

    srv = ThreadingHTTPServer(("127.0.0.1", 0), GalleryRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def api(_api_server):
    """Call the HTTP API. Returns (status, payload) — never raises on 4xx/5xx,
    because the status code is usually the thing under test."""
    import json as _json
    import urllib.error
    import urllib.request

    def _req(method, path, body=None):
        data = None if body is None else _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{_api_server}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            return status, _json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return status, raw

    return _req


@pytest.fixture
def fake_comfy(monkeypatch):
    """Run a Comfy job without ComfyUI, capturing the graph that would be queued.

    Returns the capture dict — `captured["graph"]` after a job completes.
    """
    from promptstudio.comfy import client as comfy
    from promptstudio.comfy.runner import ComfyRunner

    captured = {}

    def fake_upload(local_path, *, filename=None, overwrite=True):
        return "uploaded_ref.jpg"

    def fake_queue(self, workflow, client_id):
        captured["graph"] = workflow
        return "prompt-1"

    def fake_wait(self, prompt_id, timeout_sec=600):
        return [{"filename": "out.png", "subfolder": "", "type": "output"}]

    def fake_download(self, meta):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 32

    # Patched on ComfyRunner, not ComfyJobManager: the one-shot job and A2's
    # batch both drive the runner, so one seam fakes ComfyUI for both.
    monkeypatch.setattr(comfy, "upload_image_to_comfy", fake_upload)
    monkeypatch.setattr(ComfyRunner, "_queue_prompt", fake_queue)
    monkeypatch.setattr(ComfyRunner, "_wait_for_images", fake_wait)
    monkeypatch.setattr(ComfyRunner, "_download_image", fake_download)
    return captured


@pytest.fixture
def run_comfy_job():
    """Start a job on an isolated manager and block until it finishes.

    A bare `ComfyJobManager()` rather than the singleton: `_status` would
    otherwise carry across tests.
    """
    import time

    from promptstudio.comfy.client import ComfyJobManager

    def _run(**kwargs):
        manager = ComfyJobManager()
        assert manager.start(**kwargs) is True
        for _ in range(400):
            if not manager.is_running():
                break
            time.sleep(0.01)
        assert not manager.is_running(), "job did not finish"
        return manager.get_status()

    return _run


@pytest.fixture
def make_photo():
    """Create a real JPEG under <archive>/<creator>/<name> and index it.

    Returns a callable → (rel_path, full_path).
    """

    def _make(creator="test_creator", name="photo_1.jpg", *, meta=None, size=(64, 80)):
        folder = os.path.join(SAVED_DIR, creator)
        os.makedirs(folder, exist_ok=True)
        full = os.path.join(folder, name)
        Image.new("RGB", size, (120, 40, 160)).save(full, "JPEG")
        if meta is not None:
            from promptstudio.storage.metadata import save_post_metadata

            save_post_metadata(full, dict(meta))
        rel = f"{creator}/{name}"
        ArchiveIndex.get().upsert_photo(rel)
        return rel, full

    return _make
