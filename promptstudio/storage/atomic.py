"""Durable writes for derived-state files.

Every JSON file in the archive — prompt cache, favorites, queues, sidecars — is
rewritten whole on each save. A bare ``open(path, "w")`` truncates the target
*before* the new bytes land, so a crash, power cut, or full disk mid-write
leaves a truncated file. Every loader in this codebase then swallows the parse
error and returns an empty default, which turns a partial write into **silent
total loss** — for ``prompts_cache.json`` that is thousands of LLM-generated
prompts and hours of GPU time.

Write to a temp file in the *same directory*, fsync, then ``os.replace``. The
replace is atomic on POSIX and Windows; same-directory matters because it is
only atomic within one filesystem.

Extracted from ``CreatorScrapeQueue._save``, which was the only writer in the
codebase already doing this correctly.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

__all__ = ["atomic_write_text", "atomic_write_json"]


def atomic_write_text(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = True,
) -> None:
    """Replace `path` with `text`, atomically. Never leaves a partial file.

    fsync=False skips the durability barrier (faster, still atomic against a
    process crash — but not against power loss). Only worth it for chatty,
    reconstructible state like live job status.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp = tempfile.mkstemp(prefix=".ps_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt here would otherwise
        # strand the temp file next to the real one.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: str,
    data: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    fsync: bool = True,
) -> None:
    """Serialize `data` to JSON and write it atomically.

    Serializes fully before touching the filesystem, so an unserializable value
    raises without having created a temp file.
    """
    text = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(path, text, fsync=fsync)
