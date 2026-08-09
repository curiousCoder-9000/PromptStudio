"""gallery-dl backed sources: X / Twitter and Reddit.

Driven as a **subprocess**, not an import. gallery-dl exposes no supported Python
API (library use is a long-standing open request), so importing `gallery_dl.job`
would couple us to private internals that change without notice. The CLI
contract and its `--write-metadata` JSON are documented and stable. A subprocess
also makes cancellation trivial: terminate the child.

Flow per run:

1. Build argv (pacing, cookies, output layout, ceiling).
2. Snapshot the destination folder.
3. Run gallery-dl, streaming its output into the job log and watching for cancel.
4. Diff the folder, convert each new file's gallery-dl sidecar into PromptStudio's
   own `.meta.json`, and index it.

Download counts come from the filesystem diff rather than from parsing stdout —
gallery-dl's human-readable output format is not a documented contract.

Deliberately NOT using `--download-archive`: `ArchiveIndex` plus the
`deleted_posts` tombstones are already the authority on what exists and what the
user intentionally removed. A second, independent ledger would either
re-download tombstoned posts (gallery-dl can't see tombstones) or refuse to
repair a partially-downloaded post (gallery-dl thinks it's done).
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from promptstudio.config import (
    CATCH_UP_STREAK,
    GALLERY_DL_BIN,
    GALLERY_DL_COOKIES_FROM_BROWSER,
    GALLERY_DL_COOKIES_REDDIT,
    GALLERY_DL_COOKIES_X,
    GALLERY_DL_EXTRA_ARGS,
    GALLERY_DL_TIMEOUT_SEC,
    MEDIA_EXTENSIONS,
    SAVED_DIR,
    SCRAPE_ABORT_AFTER_KNOWN,
    SCRAPE_RETRIES,
    SCRAPE_SLEEP_429_SEC,
    SCRAPE_SLEEP_REQUEST_SEC,
    SCRAPE_SLEEP_SEC,
    X_INCLUDE_RETWEETS,
    X_MEDIA_TIMELINE_ONLY,
)
from promptstudio.scraping.results import SyncResult
from promptstudio.scraping.sources.base import (
    NormalizedPost,
    ScrapeOptions,
    SourceContext,
    SourceTarget,
    resolve_folder_name,
    sanitize_folder,
)
from promptstudio.storage.metadata import (
    build_metadata_from_normalized,
    save_post_metadata,
)

# gallery-dl's own metadata sidecar suffix (ours is `.meta.json`, so no clash).
_GDL_META_SUFFIX = ".json"

# Signals scraped from gallery-dl output. Ordered: first match wins.
_AUTH_PHRASES = (
    "login required",
    "authorization required",
    "authentication required",
    "no login",
    "401 unauthorized",
    "403 forbidden",
    "requires authentication",
)
_RATE_PHRASES = (
    "too many requests",
    "rate limit",
    "rate-limit",
)
_NOT_FOUND_PHRASES = (
    "404 not found",
    "not found",
    "does not exist",
    "no such user",
)
_PRIVATE_PHRASES = (
    "private",
    "protected",
    "suspended",
)

# gallery-dl exit status is a BIT MASK — `job.py` does `self.status |= exc.code`,
# so several of these can be set at once and equality tests are wrong.
# Values from gallery_dl/exception.py.
_EXIT_GENERIC = 1     # GalleryDLException
_EXIT_EXTRACTION = 4  # ExtractionError / HttpError / NotFoundError
_EXIT_CHALLENGE = 8   # ChallengeError — captcha / bot check
_EXIT_AUTH = 16       # Authentication / Authorization / AuthRequired
_EXIT_INPUT = 32      # InputError — bad config, filter or format string (our bug)
# 64 is not an exception `code`: __init__.py does `retval |= 64` on
# NoExtractorError, i.e. we built a URL gallery-dl has no extractor for.
_EXIT_NO_EXTRACTOR = 64
_EXIT_INTERRUPT = 128

# NOTE: `--abort N` raises StopExtraction, a ControlException with code 0, so a
# clean catch-up stop exits 0 and is indistinguishable from a normal finish by
# status alone. Catch-up is therefore inferred from mode + download count.


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse the datetime shapes gallery-dl emits into JSON."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text or text.lower() in ("none", "null"):
        return None
    # Epoch as string
    try:
        if text.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _first(raw: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    """First present, non-empty value among `keys` (supports "a.b" paths)."""
    for key in keys:
        cur: Any = raw
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur not in (None, "", []):
            return cur
    return default


class GalleryDlSource:
    """Shared gallery-dl driver. Subclasses supply target parsing and mapping."""

    name: str = ""
    label: str = ""
    extractor: str = ""  # gallery-dl extractor name, for -o overrides

    # ---------------------------------------------------------------- hooks

    def parse_target(self, target: str, **kwargs: Any) -> SourceTarget:
        raise NotImplementedError

    def _cookies_file(self) -> str:
        return ""

    def _extractor_options(self, options: ScrapeOptions) -> List[str]:
        return []

    def _map_raw(self, raw: Dict[str, Any], target: SourceTarget) -> NormalizedPost:
        raise NotImplementedError

    # ------------------------------------------------------------- argv

    def _build_argv(
        self,
        target: SourceTarget,
        options: ScrapeOptions,
        dest: str,
    ) -> List[str]:
        argv = [
            GALLERY_DL_BIN,
            "--directory", dest,          # -D: exact dir, no extractor subdirs
            "--filename", self._filename_format(target),
            "--write-metadata",
            "--retries", str(max(0, int(SCRAPE_RETRIES))),
            "--sleep", str(SCRAPE_SLEEP_SEC),
            "--sleep-request", str(SCRAPE_SLEEP_REQUEST_SEC),
            "--sleep-429", str(SCRAPE_SLEEP_429_SEC),
            "--no-part",  # no .part files left behind if we terminate mid-download
        ]

        # Ceiling. `--range` counts files considered, including skipped ones, so
        # it is a scan bound rather than an exact download count.
        ceiling = options.max_posts
        if ceiling is not None and int(ceiling) > 0 and options.mode != "full":
            argv += ["--range", f"1-{int(ceiling)}"]

        # Catch-up: gallery-dl's own "stop after N consecutive skips".
        if options.mode != "full" or not options.deep:
            streak = SCRAPE_ABORT_AFTER_KNOWN or CATCH_UP_STREAK
            if streak > 0:
                argv += ["--abort", str(int(streak))]

        if not options.include_videos:
            argv += ["--filter", "extension not in ('mp4','webm','m4v','mov')"]

        cookies = self._cookies_file()
        if cookies and os.path.isfile(cookies):
            argv += ["--cookies", cookies]
        elif GALLERY_DL_COOKIES_FROM_BROWSER:
            argv += ["--cookies-from-browser", GALLERY_DL_COOKIES_FROM_BROWSER]

        argv += self._extractor_options(options)

        if GALLERY_DL_EXTRA_ARGS.strip():
            argv += shlex.split(GALLERY_DL_EXTRA_ARGS)

        argv.append(target.url)
        return argv

    def _filename_format(self, target: SourceTarget) -> str:
        """Match the archive's `<creator>_<date>_UTC` convention.

        `taken_at_for_image` falls back to parsing the timestamp out of the
        filename when a sidecar is missing, so keeping the shape pays off. The
        folder name is pre-sanitized to [A-Za-z0-9_.], so inlining it is safe.
        """
        return (
            f"{target.folder}"
            "_{date:%Y-%m-%d_%H-%M-%S}_UTC"
            "_{num:>02}.{extension}"
        )

    # -------------------------------------------------------------- run

    def run(
        self,
        target: SourceTarget,
        options: ScrapeOptions,
        ctx: SourceContext,
    ) -> SyncResult:
        result = SyncResult(job_type="creator", source=self.name)
        save_dir = os.path.expanduser(ctx.save_dir or SAVED_DIR)
        dest = os.path.join(save_dir, target.folder)
        os.makedirs(dest, exist_ok=True)

        argv = self._build_argv(target, options, dest)
        ctx.log(
            f"=== {self.label} scrape {target.label} mode={options.mode} "
            f"videos={'on' if options.include_videos else 'off'} ==="
        )
        ctx.log("gallery-dl " + " ".join(shlex.quote(a) for a in argv[1:]))

        before = self._snapshot(dest)

        try:
            code, lines = self._spawn(argv, ctx, result)
        except FileNotFoundError:
            msg = (
                f"gallery-dl not found (looked for '{GALLERY_DL_BIN}'). "
                "Install it with: pip install gallery-dl"
            )
            result.errors += 1
            result.messages.append(msg)
            result.stop_reason = "error"
            ctx.log(msg)
            return result

        # Convert whatever landed, even on a non-zero exit — a partial run still
        # produced real files that must be indexed.
        added = sorted(self._snapshot(dest) - before)
        converted, convert_errors, newest = self._ingest(added, target, save_dir, ctx)
        result.downloaded = converted
        result.errors += convert_errors
        self._record_checkpoint(target, converted, newest)

        self._classify_outcome(code, lines, result, ctx, options=options)
        ctx.log(
            f"{self.label} {target.label}: {result.downloaded} new, "
            f"{result.errors} errors"
            + (f" [{result.stop_reason}]" if result.stop_reason else "")
            + (" [ABORTED]" if result.aborted else "")
        )
        return result

    @staticmethod
    def _record_checkpoint(
        target: SourceTarget,
        downloaded: int,
        newest: Dict[str, str],
    ) -> None:
        """Stamp `sync_state.json` so the sidebar can show a synced badge.

        Keyed on `target.folder`, not the raw handle. `db.list_creators` looks
        this up by folder name, and for Instagram folder == handle, so folder
        keying is backward compatible — while being the only thing that stops
        `nina` on Instagram and `nina` on X from overwriting each other.

        One call per run, not per file: `update()` rewrites the whole dict, so
        per-file would be quadratic writes for no extra information.
        """
        if downloaded <= 0:
            return
        from promptstudio.scraping.checkpoints import SyncCheckpoints

        try:
            SyncCheckpoints().update(
                target.folder,
                shortcode=newest.get("shortcode", ""),
                post_id=newest.get("post_id", ""),
                downloaded_delta=downloaded,
            )
        except OSError:
            # A missing badge is cosmetic; the media is already on disk.
            pass

    @staticmethod
    def _snapshot(folder: str) -> set:
        """Media files currently in `folder` (non-recursive; layout is flat)."""
        try:
            return {
                name
                for name in os.listdir(folder)
                if name.lower().endswith(MEDIA_EXTENSIONS)
            }
        except OSError:
            return set()

    def _spawn(
        self,
        argv: List[str],
        ctx: SourceContext,
        result: SyncResult,
    ) -> Tuple[int, List[str]]:
        """Run gallery-dl, streaming output and honouring cancel. Returns (code, lines)."""
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        lines: List[str] = []
        out_q: "queue.Queue[Optional[str]]" = queue.Queue()

        def reader() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    out_q.put(line.rstrip("\n"))
            except Exception:
                pass
            finally:
                out_q.put(None)

        threading.Thread(target=reader, daemon=True).start()

        deadline = time.monotonic() + max(60, int(GALLERY_DL_TIMEOUT_SEC))
        cancelled = False
        timed_out = False
        done = False
        while not done:
            # A queue poll (rather than a blocking read) is what makes cancel
            # responsive while gallery-dl is inside a long sleep.
            try:
                line = out_q.get(timeout=1.0)
            except queue.Empty:
                line = ""
            else:
                if line is None:
                    done = True
                    line = ""

            if line:
                lines.append(line)
                self._note_signal(line, result, ctx)
                ctx.log(line[:500])

            if not done and ctx.cancelled():
                cancelled = True
                break
            if not done and time.monotonic() > deadline:
                timed_out = True
                break

        if cancelled or timed_out:
            self._terminate(proc)
            reason = (
                "Cancelled by user"
                if cancelled
                else f"gallery-dl exceeded {GALLERY_DL_TIMEOUT_SEC}s timeout"
            )
            result.aborted = True
            result.abort_reason = reason
            result.stop_reason = "cancel" if cancelled else "error"
            result.messages.append(reason)
            ctx.log(reason)
            return proc.returncode if proc.returncode is not None else -1, lines

        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._terminate(proc)
        return int(proc.returncode or 0), lines

    @staticmethod
    def _terminate(proc: "subprocess.Popen") -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            pass

    def _note_signal(self, line: str, result: SyncResult, ctx: SourceContext) -> None:
        """Track rate-limit hits as they stream past."""
        low = line.lower()
        if any(p in low for p in _RATE_PHRASES):
            result.rate_limit_hits += 1
            if ctx.on_rate_limit:
                try:
                    ctx.on_rate_limit(result.rate_limit_hits, int(SCRAPE_SLEEP_429_SEC))
                except Exception:
                    pass

    def _classify_outcome(
        self,
        code: int,
        lines: List[str],
        result: SyncResult,
        ctx: SourceContext,
        *,
        options: ScrapeOptions,
    ) -> None:
        """Map exit status + output onto the stop_reason vocabulary the queue knows.

        The numeric status is the primary signal (it is a bit mask); output text
        is only used to refine a generic extraction failure into not_found /
        private, since gallery-dl has no distinct status for those.
        """
        if result.aborted:
            return

        blob = "\n".join(lines[-80:]).lower()
        catch_up_mode = options.mode != "full" or not options.deep

        def hard_abort(reason: str) -> None:
            """Abort in a way that makes CreatorScrapeQueue pause the whole queue."""
            result.aborted = True
            result.abort_reason = reason
            result.stop_reason = "abort"
            result.errors += 1
            result.messages.append(reason)

        # Auth and challenge failures must stop the queue, not just this job:
        # every following job would fail identically, and hammering a
        # cookie-authenticated endpoint is how accounts get suspended.
        if code & _EXIT_AUTH:
            hard_abort(
                f"{self.label} authentication failed (status {code}) — "
                "cookies missing, expired, or insufficient"
            )
            return
        if code & _EXIT_CHALLENGE:
            hard_abort(
                f"{self.label} returned a challenge/captcha (status {code}) — "
                "backing off"
            )
            return
        if result.rate_limit_hits and result.downloaded == 0:
            hard_abort(f"{self.label} rate limited with nothing downloaded")
            return

        if code == 0:
            # Includes a clean `--abort N` catch-up stop (StopExtraction => 0).
            if result.downloaded == 0:
                result.stop_reason = "nothing_new"
            elif catch_up_mode:
                result.stop_reason = "catch_up"
            else:
                result.stop_reason = "end_of_feed"
            return

        # A config/format/filter mistake is our bug, not the platform's — surface
        # it loudly but don't pause the queue over it.
        if code & _EXIT_INPUT:
            result.errors += 1
            result.stop_reason = "error"
            result.messages.append(
                f"gallery-dl rejected our invocation (status {code}) — "
                "check filename format / filter / -o options"
            )
            return
        if code & _EXIT_NO_EXTRACTOR:
            result.errors += 1
            result.stop_reason = "error"
            result.messages.append(
                f"gallery-dl has no extractor for the URL we built (status {code}) — "
                f"{self.name} target resolution is wrong, or gallery-dl dropped support"
            )
            return

        if code & _EXIT_INTERRUPT:
            result.errors += 1
            result.stop_reason = "error"
            result.messages.append(
                f"gallery-dl was interrupted (status {code})"
            )
            return

        if code & _EXIT_EXTRACTION:
            if any(p in blob for p in _NOT_FOUND_PHRASES):
                result.errors += 1
                result.stop_reason = "not_found"
                result.messages.append(f"{self.label}: target not found")
                return
            if any(p in blob for p in _PRIVATE_PHRASES):
                result.errors += 1
                result.stop_reason = "private"
                result.messages.append(f"{self.label}: target is private/protected")
                return
            if any(p in blob for p in _AUTH_PHRASES):
                hard_abort(f"{self.label} needs authentication — check cookies")
                return

        if result.downloaded > 0:
            # Files landed despite the error — partial success, not a failure.
            result.stop_reason = "end_of_feed"
            result.messages.append(
                f"gallery-dl exited {code} after downloading {result.downloaded}"
            )
            return

        result.errors += 1
        result.stop_reason = "error"
        tail = next(
            (ln for ln in reversed(lines) if ln.strip()),
            f"gallery-dl exited with status {code}",
        )
        result.messages.append(tail[:300])

    # ---------------------------------------------------------- ingestion

    def _ingest(
        self,
        filenames: List[str],
        target: SourceTarget,
        save_dir: str,
        ctx: SourceContext,
    ) -> Tuple[int, int, Dict[str, str]]:
        """Convert gallery-dl sidecars to PromptStudio sidecars + index rows.

        Third return is the newest post seen this run (by `taken_at`), which the
        caller records as the resume checkpoint.
        """
        from promptstudio.storage.db import ArchiveIndex, normalize_rel_path

        converted = 0
        errors = 0
        newest: Dict[str, str] = {}
        index = None
        try:
            index = ArchiveIndex.get()
        except Exception as exc:
            ctx.log(f"Index unavailable, sidecars only: {exc}")

        for name in filenames:
            full = os.path.join(save_dir, target.folder, name)
            raw = self._read_gdl_meta(full)
            try:
                post = self._map_raw(raw, target)
                carousel_index = self._carousel_index(raw)
                meta = build_metadata_from_normalized(post, carousel_index=carousel_index)
                if not meta.get("taken_at"):
                    # Keep sort order sane even when the extractor gave no date.
                    meta["taken_at"] = datetime.fromtimestamp(
                        os.path.getmtime(full), tz=timezone.utc
                    ).isoformat()
                save_post_metadata(full, meta)
                converted += 1
                taken = str(meta.get("taken_at") or "")
                if taken >= newest.get("taken_at", ""):
                    newest = {
                        "taken_at": taken,
                        "post_id": str(meta.get("post_id") or ""),
                        "shortcode": str(meta.get("shortcode") or ""),
                    }
            except Exception as exc:
                errors += 1
                ctx.log(f"Metadata mapping failed for {name}: {exc}")
                continue

            if index is not None:
                try:
                    rel = normalize_rel_path(
                        os.path.relpath(full, save_dir)
                    )
                    index.upsert_photo(
                        rel,
                        taken_at=str(meta.get("taken_at") or ""),
                        post_id=str(meta.get("post_id") or "") or None,
                        shortcode=str(meta.get("shortcode") or "") or None,
                        source=self.name,
                        caption=str(meta.get("caption") or ""),
                    )
                except Exception as exc:
                    ctx.log(f"Index upsert warning for {name}: {exc}")

            self._discard_gdl_meta(full)

        if converted:
            ctx.log(f"Converted metadata for {converted} new file(s)")
        return converted, errors, newest

    @staticmethod
    def _read_gdl_meta(media_path: str) -> Dict[str, Any]:
        path = media_path + _GDL_META_SUFFIX
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _discard_gdl_meta(media_path: str) -> None:
        """Drop gallery-dl's raw sidecar once converted.

        Our `.meta.json` is the single source of truth downstream, and the raw
        fields we might still want are preserved under `source_extra`.
        """
        path = media_path + _GDL_META_SUFFIX
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _carousel_index(raw: Dict[str, Any]) -> int:
        """gallery-dl's `num` is 1-based; the archive's carousel_index is 0-based."""
        try:
            num = int(_first(raw, "num", default=0) or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, num - 1)


class XSource(GalleryDlSource):
    """X / Twitter. Requires cookies."""

    name = "x"
    label = "X"
    extractor = "twitter"

    def parse_target(self, target: str, **kwargs: Any) -> SourceTarget:
        raw = (target or "").strip()
        if not raw:
            raise ValueError("X handle required")
        handle = raw
        if "://" in raw:
            # Accept a pasted profile URL: keep the first path segment.
            tail = raw.split("://", 1)[1]
            parts = [p for p in tail.split("/")[1:] if p]
            if not parts:
                raise ValueError(f"Could not read an X handle from {raw!r}")
            handle = parts[0]
        handle = sanitize_folder(handle)
        if not handle:
            raise ValueError(f"Invalid X handle: {target!r}")
        url = f"https://x.com/{handle}"
        if X_MEDIA_TIMELINE_ONLY:
            url += "/media"
        return SourceTarget(
            source=self.name,
            raw=raw,
            url=url,
            folder=resolve_folder_name(self.name, handle),
            handle=handle,
            label=f"@{handle} (X)",
        )

    def _cookies_file(self) -> str:
        return GALLERY_DL_COOKIES_X

    def _extractor_options(self, options: ScrapeOptions) -> List[str]:
        return [
            "-o",
            f"extractor.twitter.retweets={'true' if X_INCLUDE_RETWEETS else 'false'}",
            "-o",
            "extractor.twitter.videos=" + ("true" if options.include_videos else "false"),
        ]

    def _map_raw(self, raw: Dict[str, Any], target: SourceTarget) -> NormalizedPost:
        tweet_id = str(_first(raw, "tweet_id", "id", default="") or "")
        author = str(
            _first(raw, "author.name", "user.name", "author", default=target.handle)
        )
        taken_at = _parse_dt(_first(raw, "date", "date_original", default=None))
        try:
            media_count = int(_first(raw, "count", default=1) or 1)
        except (TypeError, ValueError):
            media_count = 1
        extension = str(_first(raw, "extension", default="")).lower()
        return NormalizedPost(
            source=self.name,
            creator=target.folder,
            post_id=tweet_id,
            shortcode="",  # X has no separate shortcode; the tweet id is the ref
            taken_at=taken_at,
            caption=str(_first(raw, "content", "description", default="")),
            is_video=extension in ("mp4", "webm", "m4v", "mov"),
            media_count=max(1, media_count),
            post_url=(
                f"https://x.com/{author}/status/{tweet_id}" if tweet_id else ""
            ),
            author=author,
            extra={
                k: raw[k]
                for k in ("favorite_count", "retweet_count", "reply_count", "lang")
                if k in raw
            },
        )


class RedditSource(GalleryDlSource):
    """Reddit. Works unauthenticated; supports OAuth for higher limits.

    Reddit is topic-scoped while the archive is creator-scoped, so a subreddit
    becomes the folder ("creator") and the real submitter is kept in `author`.
    """

    name = "reddit"
    label = "Reddit"
    extractor = "reddit"

    def parse_target(self, target: str, **kwargs: Any) -> SourceTarget:
        raw = (target or "").strip()
        if not raw:
            raise ValueError("Reddit subreddit or user required")

        text = raw
        if "://" in text:
            tail = text.split("://", 1)[1]
            text = "/".join(tail.split("/")[1:])
        text = text.strip("/")

        kind = "r"
        handle = text
        lowered = text.lower()
        for prefix, k in (
            ("r/", "r"),
            ("/r/", "r"),
            ("u/", "u"),
            ("/u/", "u"),
            ("user/", "u"),
        ):
            if lowered.startswith(prefix):
                kind = k
                handle = text[len(prefix):]
                break
        handle = sanitize_folder(handle.split("/")[0])
        if not handle:
            raise ValueError(f"Invalid Reddit target: {target!r}")

        if kind == "u":
            url = f"https://www.reddit.com/user/{handle}/submitted/"
            label = f"u/{handle}"
        else:
            url = f"https://www.reddit.com/r/{handle}/"
            label = f"r/{handle}"

        return SourceTarget(
            source=self.name,
            raw=raw,
            url=url,
            # r/foo and u/foo are unrelated namespaces — keep them apart.
            folder=resolve_folder_name(self.name, handle, kind=kind),
            handle=handle,
            kind=kind,
            label=label,
        )

    def _cookies_file(self) -> str:
        return GALLERY_DL_COOKIES_REDDIT

    def _extractor_options(self, options: ScrapeOptions) -> List[str]:
        # Reddit posts frequently link out to imgur/gfycat/etc. Following those
        # is what makes a subreddit scrape actually yield media.
        return ["-o", "extractor.reddit.parent-directory=false"]

    def _map_raw(self, raw: Dict[str, Any], target: SourceTarget) -> NormalizedPost:
        submission_id = str(_first(raw, "id", "post_id", default="") or "")
        author = str(_first(raw, "author", default=""))
        taken_at = _parse_dt(_first(raw, "date", "created_utc", "created", default=None))
        permalink = str(_first(raw, "permalink", default=""))
        if permalink and permalink.startswith("/"):
            permalink = f"https://www.reddit.com{permalink}"
        extension = str(_first(raw, "extension", default="")).lower()
        subreddit = str(_first(raw, "subreddit", default=target.handle))
        return NormalizedPost(
            source=self.name,
            creator=target.folder,
            post_id=submission_id,
            shortcode="",
            taken_at=taken_at,
            caption=str(_first(raw, "title", "selftext", default="")),
            is_video=extension in ("mp4", "webm", "m4v", "mov"),
            media_count=1,
            post_url=permalink or str(_first(raw, "url", default="")),
            author=author,
            extra={
                "subreddit": subreddit,
                **{
                    k: raw[k]
                    for k in ("score", "num_comments", "over_18", "flair")
                    if k in raw
                },
            },
        )
