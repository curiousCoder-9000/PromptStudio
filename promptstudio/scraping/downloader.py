"""Instagram download orchestration: saved posts, creator feeds, following bulk."""

import glob
import json
import os
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional, Sequence

import instaloader

from promptstudio.config import (
    ABORT_RATE_LIMIT_STREAK,
    ACCOUNT_PAUSE_MAX_SEC,
    ACCOUNT_PAUSE_MIN_SEC,
    BATCH_PAUSE_EVERY,
    BATCH_PAUSE_MAX_SEC,
    BATCH_PAUSE_MIN_SEC,
    CATCH_UP_STREAK,
    DEFAULT_ACCOUNTS_PER_DAY,
    DEFAULT_BIO_KEYWORDS,
    DEFAULT_MAX_POSTS_PER_CREATOR,
    DEFAULT_MIN_MEDIA_COUNT,
    FOLLOWING_LIST_FILE,
    FULL_SCRAPE_MAX_POSTS,
    INCLUDE_VIDEOS_DEFAULT,
    MEDIA_EXTENSIONS,
    POST_DELAY_MAX_SEC,
    POST_DELAY_MIN_SEC,
    POST_RANK_ENABLED,
    POST_SCAN_FACTOR,
    RATE_LIMIT_BACKOFF_MAX_SEC,
    RATE_LIMIT_BACKOFF_SEC,
    SAVED_DIR,
    SESSION_USER,
)
from promptstudio.scraping.checkpoints import SyncCheckpoints
from promptstudio.scraping.filters import (
    filter_following_entries,
    normalize_keywords,
    score_instagram_post,
)
from promptstudio.scraping.organizer import deduplicate_archive, organize_root_images
from promptstudio.scraping.queue import FollowingQueue
from promptstudio.scraping.session import authenticated_profile, create_instaloader, load_session
from promptstudio.storage.metadata import build_metadata_from_post, save_post_metadata


LogFn = Optional[Callable[[str], None]]

ABUSE_SIGNAL_PHRASES = (
    "feedback_required",
    "challenge_required",
    "pleasewaitfewminutes",
    "please wait a few minutes",
    "checkpoint_required",
)


@dataclass
class SyncResult:
    job_type: str
    downloaded: int = 0
    skipped: int = 0
    skipped_deleted: int = 0
    errors: int = 0
    rate_limit_hits: int = 0
    aborted: bool = False
    abort_reason: str = ""
    accounts_processed: int = 0
    queue_summary: Optional[dict] = None
    messages: List[str] = field(default_factory=list)
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class InstagramDownloader:
    """Download Instagram media into the local archive."""

    def __init__(
        self,
        save_dir: str = SAVED_DIR,
        session_user: str = SESSION_USER,
        log: LogFn = None,
        on_rate_limit: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.save_dir = os.path.expanduser(save_dir)
        self.session_user = session_user
        self.log = log or print
        self.on_rate_limit = on_rate_limit
        self.should_cancel = should_cancel or (lambda: False)
        self.checkpoints = SyncCheckpoints()
        self.queue = FollowingQueue()
        self._consecutive_rate_limits = 0
        self._aborted = False
        self._abort_reason = ""
        os.makedirs(self.save_dir, exist_ok=True)
        self._L = create_instaloader(os.path.join(self.save_dir, "{owner_username}"))

    def _cancel_requested(self) -> bool:
        try:
            return bool(self.should_cancel())
        except Exception:
            return False

    def _interruptible_sleep(self, seconds: float, *, what: str = "sleep") -> bool:
        """Return True if cancelled/aborted early."""
        end = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < end:
            if self._aborted or self._cancel_requested():
                return True
            remaining = end - time.monotonic()
            time.sleep(min(1.0, max(0.0, remaining)))
        return False

    def _attach_metadata(self, post, creator: str) -> None:
        """Write sidecar metadata for files downloaded from this post."""
        target_dir = os.path.join(self.save_dir, creator)
        if not os.path.isdir(target_dir):
            return
        date_str = post.date_utc.strftime("%Y-%m-%d_%H-%M-%S")
        patterns = [
            os.path.join(target_dir, f"{creator}_{date_str}_UTC*.jpg"),
            os.path.join(target_dir, f"{creator}_{date_str}_UTC*.webp"),
            os.path.join(target_dir, f"{creator}_{date_str}_UTC*.png"),
            os.path.join(target_dir, f"{creator}_{date_str}_UTC*.mp4"),
        ]
        files = []
        for pattern in patterns:
            files.extend(glob.glob(pattern))
        files.sort()
        for idx, fpath in enumerate(files):
            meta = build_metadata_from_post(post, carousel_index=idx)
            save_post_metadata(fpath, meta)
            try:
                from promptstudio.storage.db import ArchiveIndex, normalize_rel_path

                rel = normalize_rel_path(os.path.relpath(fpath, self.save_dir))
                ArchiveIndex.get().upsert_photo(
                    rel,
                    taken_at=str(meta.get("taken_at") or ""),
                    post_id=str(meta.get("post_id") or "") or None,
                    shortcode=str(meta.get("shortcode") or "") or None,
                )
            except Exception as e:
                self.log(f"Index upsert warning: {e}")

    def _filename_fallback_exists(self, post) -> bool:
        """Legacy skip when no sidecar/index identity is available."""
        creator = post.owner_username
        target_dir = os.path.join(self.save_dir, creator)
        date_str = post.date_utc.strftime("%Y-%m-%d_%H-%M-%S")
        pattern = os.path.join(target_dir, f"{creator}_{date_str}_UTC*")
        return bool(
            glob.glob(pattern + ".jpg")
            or glob.glob(pattern + ".webp")
            or glob.glob(pattern + ".png")
            or glob.glob(pattern + ".mp4")
        )

    def _expected_slide_count(self, post) -> int:
        try:
            count = int(getattr(post, "mediacount", 0) or 0)
            if count > 0:
                return count
        except Exception:
            pass
        return 1

    def _sidecar_scan_paths(self, creator: str, shortcode: str, post_id: str) -> list:
        """Find on-disk media whose sidecars match shortcode/post_id (index lag / legacy)."""
        from promptstudio.storage.metadata import load_post_metadata

        folder = os.path.join(self.save_dir, creator)
        if not os.path.isdir(folder):
            return []
        found = []
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        for name in names:
            if not name.lower().endswith(MEDIA_EXTENSIONS):
                continue
            full = os.path.join(folder, name)
            meta = load_post_metadata(full) or {}
            sc = str(meta.get("shortcode") or "")
            pid = str(meta.get("post_id") or "")
            if shortcode and sc == shortcode:
                found.append(full)
            elif post_id and pid == post_id:
                found.append(full)
        return found

    def _post_archive_state(self, post, username: str = "") -> str:
        """Return 'complete', 'incomplete', 'missing', or 'deleted' for local archive state."""
        shortcode = getattr(post, "shortcode", "") or ""
        post_id = str(getattr(post, "mediaid", "") or "")
        creator = getattr(post, "owner_username", "") or ""
        handle = (username or creator or "").lstrip("@").strip()

        # Intentional user deletes — never re-download
        if shortcode or post_id:
            try:
                from promptstudio.storage.db import ArchiveIndex

                index = ArchiveIndex.get()
                for cand in {handle.lower(), creator.lower()} if handle or creator else set():
                    if cand and index.is_deleted_post(
                        cand, shortcode=shortcode or None, post_id=post_id or None
                    ):
                        return "deleted"
            except Exception as e:
                self.log(f"Tombstone lookup warning: {e}")

        paths: list = []
        try:
            from promptstudio.storage.db import ArchiveIndex

            index = ArchiveIndex.get()
            paths = index.carousel_paths(shortcode=shortcode or None, post_id=post_id or None)
        except Exception as e:
            self.log(f"Index lookup warning: {e}")

        if not paths and (shortcode or post_id):
            # Prefer sidecar scan over filename date (avoids same-second collisions)
            disk = self._sidecar_scan_paths(creator, shortcode, post_id)
            if disk:
                paths = disk

        if not paths:
            # Filename fallback only when Instagram identity is unavailable
            if not shortcode and not post_id and self._filename_fallback_exists(post):
                return "complete"
            return "missing"

        expected = self._expected_slide_count(post)
        if len(paths) >= expected:
            return "complete"
        return "incomplete"

    def _post_already_archived(self, post, username: str = "") -> bool:
        return self._post_archive_state(post, username=username) in ("complete", "deleted")

    def _backoff_seconds(self) -> int:
        n = max(1, self._consecutive_rate_limits)
        delay = RATE_LIMIT_BACKOFF_SEC * (2 ** (n - 1))
        return min(delay, RATE_LIMIT_BACKOFF_MAX_SEC)

    @staticmethod
    def _clamp_range(lo: float, hi: float) -> tuple[float, float]:
        lo = max(0.0, float(lo))
        hi = max(lo, float(hi))
        return lo, hi

    def _sleep_post_delay(self) -> None:
        lo, hi = self._clamp_range(POST_DELAY_MIN_SEC, POST_DELAY_MAX_SEC)
        delay = random.uniform(lo, hi)
        self.log(f"Post delay {delay:.1f}s")
        if self._interruptible_sleep(delay, what="post delay"):
            self._aborted = True
            self._abort_reason = self._abort_reason or "Cancelled by user"

    def _sleep_account_pause(self) -> None:
        lo, hi = self._clamp_range(ACCOUNT_PAUSE_MIN_SEC, ACCOUNT_PAUSE_MAX_SEC)
        delay = random.uniform(lo, hi)
        self.log(f"Account pause {delay:.0f}s")
        if self._interruptible_sleep(delay, what="account pause"):
            self._aborted = True
            self._abort_reason = self._abort_reason or "Cancelled by user"

    def _maybe_batch_pause(self, processed: int) -> None:
        if BATCH_PAUSE_EVERY <= 0 or processed <= 0:
            return
        if processed % BATCH_PAUSE_EVERY != 0:
            return
        lo, hi = self._clamp_range(BATCH_PAUSE_MIN_SEC, BATCH_PAUSE_MAX_SEC)
        delay = random.uniform(lo, hi)
        self.log(
            f"Batch pause after {processed} accounts — waiting {delay / 60:.1f} min"
        )
        if self._interruptible_sleep(delay, what="batch pause"):
            self._aborted = True
            self._abort_reason = self._abort_reason or "Cancelled by user"

    @staticmethod
    def _is_abuse_signal(exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(phrase in text for phrase in ABUSE_SIGNAL_PHRASES)

    def _trigger_abort(self, reason: str, result: SyncResult) -> None:
        self._aborted = True
        self._abort_reason = reason
        result.aborted = True
        result.abort_reason = reason
        result.messages.append(reason)
        self.log(f"ABORT: {reason}")

    def _should_abort_rate_limit(self) -> bool:
        return self._consecutive_rate_limits >= ABORT_RATE_LIMIT_STREAK

    def _download_post(self, post, result: SyncResult, username: str = "") -> bool:
        if self._aborted or self._cancel_requested():
            if self._cancel_requested() and not self._aborted:
                self._trigger_abort("Cancelled by user", result)
            return False
        creator = post.owner_username
        state = self._post_archive_state(post, username=username)
        if state == "deleted":
            result.skipped += 1
            result.skipped_deleted += 1
            sc = getattr(post, "shortcode", "") or ""
            self.log(f"Skip (deleted): @{username or creator} {sc}")
            return False
        if state == "complete":
            result.skipped += 1
            sc = getattr(post, "shortcode", "") or ""
            self.log(f"Skip (archived): @{creator} {sc or post.date_utc.date()}")
            return False
        if state == "incomplete":
            self.log(
                f"Incomplete carousel @{creator} "
                f"{getattr(post, 'shortcode', '')} — fetching missing slides"
            )
        try:
            self.log(f"Downloading @{creator} ({post.date_utc.date()})...")
            self._L.download_post(post, target=creator)
            self._attach_metadata(post, creator)
            result.downloaded += 1
            self._consecutive_rate_limits = 0
            handle = username or creator
            self.checkpoints.update(
                handle,
                shortcode=getattr(post, "shortcode", "") or "",
                post_id=str(getattr(post, "mediaid", "") or ""),
                downloaded_delta=1,
            )
            self._sleep_post_delay()
            if self._aborted and self._cancel_requested() and not result.aborted:
                self._trigger_abort("Cancelled by user", result)
            return True
        except instaloader.exceptions.ConnectionException as exc:
            result.errors += 1
            result.rate_limit_hits += 1
            self._consecutive_rate_limits += 1
            if self._is_abuse_signal(exc):
                self._trigger_abort(f"Abuse signal during download: {exc}", result)
                return False
            wait = self._backoff_seconds()
            msg = f"Rate limit / connection: {exc}"
            result.messages.append(msg)
            self.log(msg + f" — waiting {wait}s (hit #{self._consecutive_rate_limits})")
            if self.on_rate_limit:
                self.on_rate_limit(self._consecutive_rate_limits, wait)
            if self._interruptible_sleep(wait, what="rate-limit backoff"):
                self._trigger_abort("Cancelled by user", result)
                return False
            if self._should_abort_rate_limit():
                self._trigger_abort(
                    f"Rate-limit streak reached {self._consecutive_rate_limits} "
                    f"(threshold {ABORT_RATE_LIMIT_STREAK})",
                    result,
                )
            return False
        except Exception as exc:
            result.errors += 1
            if self._is_abuse_signal(exc):
                self._trigger_abort(f"Abuse signal during download: {exc}", result)
                return False
            msg = f"Error @{creator}: {exc}"
            result.messages.append(msg)
            self.log(msg)
            return False

    def sync_saved_posts(self) -> SyncResult:
        """Sync all Instagram :saved posts."""
        result = SyncResult(job_type="saved")
        self.log("=== Syncing Instagram Saved Posts ===")
        load_session(self._L, self.session_user)
        profile = authenticated_profile(self._L, self.session_user)
        self.log(f"Authenticated as @{profile.username}")

        for post in profile.get_saved_posts():
            if self._aborted or self._cancel_requested():
                if self._cancel_requested() and not result.aborted:
                    self._trigger_abort("Cancelled by user", result)
                break
            self._download_post(post, result)

        if not self._aborted:
            organize_root_images(self.save_dir, log=self.log)
        if result.aborted and not result.stop_reason:
            result.stop_reason = (
                "cancel" if "cancelled" in (result.abort_reason or "").lower() else "abort"
            )
        self.log(
            f"Saved sync done: {result.downloaded} new, "
            f"{result.skipped} skipped, {result.errors} errors"
            + (f" [ABORTED: {result.abort_reason}]" if result.aborted else "")
        )
        return result

    def sync_creator_feed(
        self,
        username: str,
        max_posts: int = DEFAULT_MAX_POSTS_PER_CREATOR,
        include_videos: bool = INCLUDE_VIDEOS_DEFAULT,
        *,
        mode: str = "bounded",
        deep: bool = True,
    ) -> SyncResult:
        """Download posts from a single creator.

        mode:
          - bounded: existing rank/scan/top-N path (default; used by following bulk)
          - full: stream entire feed; deep=True disables catch-up (true archive)
          - latest: stream newest-first with catch-up on (existing folders; no glam rank)
        """
        result = SyncResult(job_type="creator")
        username = username.lstrip("@").strip()
        if not username:
            result.messages.append("Empty username")
            result.stop_reason = "error"
            return result

        mode = (mode or "bounded").strip().lower()
        if mode == "latest":
            # Alias: streaming + catch-up on + download ceiling = max_posts
            mode = "full"
            deep = False
            result.messages.append("mode=latest (catch-up stream)")
        if mode not in ("bounded", "full"):
            mode = "bounded"

        self.log(
            f"=== Syncing feed @{username} mode={mode}"
            + (
                f" deep={deep}"
                if mode == "full"
                else f" max={max_posts}"
            )
            + f" videos={'on' if include_videos else 'off'} ==="
        )
        try:
            from promptstudio.storage.db import ArchiveIndex

            filled = ArchiveIndex.get().backfill_creator_identity(username)
            if filled:
                self.log(f"Backfilled identity metadata for {filled} photo(s)")
        except Exception as e:
            self.log(f"Identity backfill warning: {e}")

        load_session(self._L, self.session_user)
        try:
            creator_profile = instaloader.Profile.from_username(self._L.context, username)
        except instaloader.exceptions.ProfileNotExistsException:
            result.messages.append(f"Profile @{username} not found")
            result.errors += 1
            result.stop_reason = "not_found"
            return result
        except Exception as exc:
            result.errors += 1
            if self._is_abuse_signal(exc):
                self._trigger_abort(f"Abuse signal loading @{username}: {exc}", result)
                result.stop_reason = "abort"
            else:
                msg = f"Error loading @{username}: {exc}"
                result.messages.append(msg)
                self.log(msg)
                result.stop_reason = "error"
            return result

        # Private fail-fast when not followed by session user
        try:
            if bool(getattr(creator_profile, "is_private", False)) and not bool(
                getattr(creator_profile, "followed_by_viewer", False)
            ):
                result.errors += 1
                result.stop_reason = "private"
                msg = f"@{username} is private and not followed by session user"
                result.messages.append(msg)
                self.log(msg)
                return result
        except Exception:
            pass

        if mode == "full":
            return self._sync_creator_feed_full(
                creator_profile,
                username,
                result,
                max_posts=max_posts,
                include_videos=include_videos,
                deep=deep,
            )
        return self._sync_creator_feed_bounded(
            creator_profile,
            username,
            result,
            max_posts=max_posts,
            include_videos=include_videos,
        )

    def _sync_creator_feed_bounded(
        self,
        creator_profile,
        username: str,
        result: SyncResult,
        *,
        max_posts: int,
        include_videos: bool,
    ) -> SyncResult:
        """Existing two-phase collect → rank → download top N (following-safe)."""
        max_posts = max(1, int(max_posts))
        scan_factor = max(1.0, float(POST_SCAN_FACTOR))
        scan_limit = max_posts if not POST_RANK_ENABLED else max(
            max_posts, int(max_posts * scan_factor)
        )
        candidates: list = []  # (score, feed_index, post)
        consecutive_known = 0
        scanned = 0

        try:
            post_iter = creator_profile.get_posts()
            for feed_index, post in enumerate(post_iter):
                if self._aborted or self._cancel_requested():
                    if self._cancel_requested() and not result.aborted:
                        self._trigger_abort("Cancelled by user", result)
                    break
                if scanned >= scan_limit:
                    break
                if post.is_video and not include_videos:
                    result.skipped += 1
                    continue
                scanned += 1
                state = self._post_archive_state(post, username=username)
                if state in ("complete", "deleted"):
                    result.skipped += 1
                    sc = getattr(post, "shortcode", "") or ""
                    if state == "deleted":
                        result.skipped_deleted += 1
                        self.log(f"Skip (deleted): @{username} {sc}")
                    else:
                        self.log(f"Skip (archived): @{username} {sc}")
                    consecutive_known += 1
                    if consecutive_known >= CATCH_UP_STREAK:
                        self.log(
                            f"Catch-up streak {consecutive_known} — stop scan @{username}"
                        )
                        result.stop_reason = "catch_up"
                        break
                    continue
                consecutive_known = 0
                score = (
                    score_instagram_post(post, feed_index=feed_index)
                    if POST_RANK_ENABLED
                    else float(-feed_index)
                )
                candidates.append((score, feed_index, post))
        except Exception as exc:
            result.errors += 1
            if self._is_abuse_signal(exc):
                self._trigger_abort(f"Abuse signal on @{username} feed: {exc}", result)
            elif isinstance(exc, instaloader.exceptions.ConnectionException):
                result.rate_limit_hits += 1
                self._consecutive_rate_limits += 1
                wait = self._backoff_seconds()
                msg = f"Rate limit / connection on @{username}: {exc}"
                result.messages.append(msg)
                self.log(msg + f" — waiting {wait}s (hit #{self._consecutive_rate_limits})")
                if self.on_rate_limit:
                    self.on_rate_limit(self._consecutive_rate_limits, wait)
                if self._interruptible_sleep(wait, what="rate-limit backoff"):
                    self._trigger_abort("Cancelled by user", result)
                elif self._should_abort_rate_limit():
                    self._trigger_abort(
                        f"Rate-limit streak reached {self._consecutive_rate_limits} "
                        f"(threshold {ABORT_RATE_LIMIT_STREAK})",
                        result,
                    )
            else:
                msg = f"Error iterating @{username}: {exc}"
                result.messages.append(msg)
                self.log(msg)

        candidates.sort(key=lambda t: (-t[0], t[1]))
        to_fetch = candidates[:max_posts]
        if POST_RANK_ENABLED and candidates:
            top = to_fetch[:5]
            preview = ", ".join(
                f"{getattr(p, 'shortcode', '?')}={s:.1f}" for s, _i, p in top
            )
            self.log(
                f"Ranked {len(candidates)} new post(s) from scan {scanned}; "
                f"downloading top {len(to_fetch)} (max {max_posts}). Top: {preview}"
            )

        for _score, _idx, post in to_fetch:
            if self._aborted or self._cancel_requested():
                if self._cancel_requested() and not result.aborted:
                    self._trigger_abort("Cancelled by user", result)
                break
            self._download_post(post, result, username=username)
            if self._aborted:
                break

        if not self._aborted:
            organize_root_images(self.save_dir, log=self.log)
        if result.aborted and not result.stop_reason:
            result.stop_reason = (
                "cancel" if "cancelled" in (result.abort_reason or "").lower() else "abort"
            )
        elif not result.stop_reason:
            if result.downloaded == 0 and result.skipped > 0:
                result.stop_reason = "nothing_new"
            elif result.downloaded == 0 and scanned == 0:
                result.stop_reason = "nothing_new"
            else:
                result.stop_reason = "end_of_feed"
        self.log(
            f"Feed sync @{username}: {result.downloaded} new, "
            f"{result.skipped} skipped"
            + (f" ({result.skipped_deleted} deleted)" if result.skipped_deleted else "")
            + f", {result.errors} errors"
            + (f" [{result.stop_reason}]" if result.stop_reason else "")
            + (f" [ABORTED]" if result.aborted else "")
        )
        return result

    def _sync_creator_feed_full(
        self,
        creator_profile,
        username: str,
        result: SyncResult,
        *,
        max_posts: int,
        include_videos: bool,
        deep: bool = True,
    ) -> SyncResult:
        """Stream entire feed; download every missing/incomplete post (no rank)."""
        ceiling: Optional[int]
        try:
            mp = int(max_posts) if max_posts is not None else FULL_SCRAPE_MAX_POSTS
        except (TypeError, ValueError):
            mp = FULL_SCRAPE_MAX_POSTS
        if mp <= 0:
            ceiling = None
        else:
            ceiling = mp

        use_catch_up = not bool(deep)
        consecutive_known = 0
        scanned = 0

        try:
            for post in creator_profile.get_posts():
                if self._aborted or self._cancel_requested():
                    if self._cancel_requested() and not result.aborted:
                        self._trigger_abort("Cancelled by user", result)
                    result.stop_reason = (
                        "cancel"
                        if "cancelled" in (result.abort_reason or "").lower()
                        or self._cancel_requested()
                        else "abort"
                    )
                    break

                if post.is_video and not include_videos:
                    result.skipped += 1
                    continue

                scanned += 1
                state = self._post_archive_state(post, username=username)

                if state in ("complete", "deleted"):
                    result.skipped += 1
                    sc = getattr(post, "shortcode", "") or ""
                    if state == "deleted":
                        result.skipped_deleted += 1
                        self.log(f"Skip (deleted): @{username} {sc}")
                    else:
                        self.log(f"Skip (archived): @{username} {sc}")
                    if use_catch_up:
                        consecutive_known += 1
                        if consecutive_known >= CATCH_UP_STREAK:
                            result.stop_reason = "catch_up"
                            self.log(f"Catch-up streak — stop full @{username}")
                            break
                    continue

                consecutive_known = 0
                self._download_post(post, result, username=username)

                if self._aborted:
                    result.stop_reason = (
                        "cancel"
                        if "cancelled" in (result.abort_reason or "").lower()
                        else "abort"
                    )
                    break

                if ceiling is not None and result.downloaded >= ceiling:
                    result.stop_reason = "ceiling"
                    self.log(f"Full scrape ceiling {ceiling} reached @{username}")
                    break
            else:
                if not result.stop_reason:
                    result.stop_reason = "end_of_feed"
        except Exception as exc:
            result.errors += 1
            if self._is_abuse_signal(exc):
                self._trigger_abort(f"Abuse signal on @{username} feed: {exc}", result)
                result.stop_reason = "abort"
            elif isinstance(exc, instaloader.exceptions.ConnectionException):
                result.rate_limit_hits += 1
                self._consecutive_rate_limits += 1
                wait = self._backoff_seconds()
                msg = f"Rate limit / connection on @{username}: {exc}"
                result.messages.append(msg)
                self.log(msg + f" — waiting {wait}s (hit #{self._consecutive_rate_limits})")
                if self.on_rate_limit:
                    self.on_rate_limit(self._consecutive_rate_limits, wait)
                if self._interruptible_sleep(wait, what="rate-limit backoff"):
                    self._trigger_abort("Cancelled by user", result)
                    result.stop_reason = "cancel"
                elif self._should_abort_rate_limit():
                    self._trigger_abort(
                        f"Rate-limit streak reached {self._consecutive_rate_limits} "
                        f"(threshold {ABORT_RATE_LIMIT_STREAK})",
                        result,
                    )
                    result.stop_reason = "abort"
                else:
                    result.stop_reason = result.stop_reason or "error"
            else:
                msg = f"Error iterating @{username}: {exc}"
                result.messages.append(msg)
                self.log(msg)
                result.stop_reason = "error"

        if not result.stop_reason and not result.aborted:
            if scanned == 0 and result.downloaded == 0 and result.errors == 0:
                result.stop_reason = "nothing_new"
            elif result.downloaded == 0 and result.skipped > 0:
                result.stop_reason = "nothing_new"
            else:
                result.stop_reason = "end_of_feed"

        if not self._aborted:
            organize_root_images(self.save_dir, log=self.log)
        self.log(
            f"Full feed @{username}: {result.downloaded} new, "
            f"{result.skipped} skipped"
            + (f" ({result.skipped_deleted} deleted)" if result.skipped_deleted else "")
            + f", {result.errors} errors"
            + (f" [{result.stop_reason}]" if result.stop_reason else "")
            + (f" [ABORTED]" if result.aborted else "")
        )
        return result

    def sync_following(
        self,
        max_accounts: Optional[int] = None,
        max_posts_per_account: int = 20,
        public_only: bool = True,
        following_list_path: str = FOLLOWING_LIST_FILE,
        keywords: Optional[Sequence[str]] = None,
        min_media_count: int = DEFAULT_MIN_MEDIA_COUNT,
        include_videos: bool = INCLUDE_VIDEOS_DEFAULT,
    ) -> SyncResult:
        """Bulk download from accounts in following_list.json with anti-ban pacing."""
        result = SyncResult(job_type="following")
        daily_cap = DEFAULT_ACCOUNTS_PER_DAY if max_accounts is None else int(max_accounts)
        if daily_cap <= 0:
            result.messages.append("max_accounts / accounts-per-day must be > 0")
            result.errors += 1
            return result

        if not os.path.isfile(following_list_path):
            result.messages.append(
                f"Missing {following_list_path} — run export_following_list.py first"
            )
            result.errors += 1
            return result

        with open(following_list_path, "r", encoding="utf-8") as f:
            following = json.load(f)

        kw = normalize_keywords(keywords if keywords is not None else DEFAULT_BIO_KEYWORDS)
        filtered = filter_following_entries(
            following,
            keywords=kw,
            min_media_count=min_media_count,
            public_only=public_only,
        )
        usernames = [e.get("username", "") for e in filtered if e.get("username")]
        added = self.queue.ensure_accounts(usernames)
        summary = self.queue.summary(daily_cap)
        self.log(
            f"=== Bulk sync from following ({len(filtered)}/{len(following)} "
            f"after filters, keywords={kw}) ==="
        )
        self.log(
            f"Queue: {summary['pending']} pending, {summary['done']} done, "
            f"{summary['error']} error · today {summary['accounts_today']}/"
            f"{summary['daily_cap']} (added {added} new)"
        )

        batch = self.queue.next_pending(daily_cap, daily_cap=daily_cap, only=usernames)
        if not batch:
            result.queue_summary = self.queue.summary(daily_cap)
            result.messages.append(
                "No pending accounts within today's budget "
                f"({summary['accounts_today']}/{summary['daily_cap']})"
            )
            self.log(result.messages[-1])
            return result

        self.log(f"Processing {len(batch)} account(s) this run")
        for username in batch:
            if self._aborted:
                break
            self.log(f"--- Account {result.accounts_processed + 1}: @{username} ---")
            sub = self.sync_creator_feed(username, max_posts=max_posts_per_account, include_videos=include_videos)
            result.downloaded += sub.downloaded
            result.skipped += sub.skipped
            result.errors += sub.errors
            result.rate_limit_hits += sub.rate_limit_hits
            result.messages.extend(sub.messages)

            if sub.aborted or self._aborted:
                result.aborted = True
                result.abort_reason = sub.abort_reason or self._abort_reason
                self.queue.mark(
                    username,
                    "error",
                    downloaded=sub.downloaded,
                    last_error=result.abort_reason,
                )
                self.queue.record_processed_today(1)
                result.accounts_processed += 1
                break

            status = "error" if sub.errors and sub.downloaded == 0 else "done"
            last_error = ""
            if status == "error" and sub.messages:
                last_error = sub.messages[-1]
            self.queue.mark(
                username,
                status,
                downloaded=sub.downloaded,
                last_error=last_error,
            )
            self.queue.record_processed_today(1)
            result.accounts_processed += 1

            if result.accounts_processed < len(batch) and not self._aborted:
                self._sleep_account_pause()
                self._maybe_batch_pause(result.accounts_processed)

        if not result.aborted:
            deduplicate_archive(self.save_dir, log=self.log)

        result.queue_summary = self.queue.summary(daily_cap)
        self.log(
            f"Following bulk done: {result.downloaded} new across "
            f"{result.accounts_processed} accounts"
            + (f" [ABORTED: {result.abort_reason}]" if result.aborted else "")
        )
        self.log(f"Queue summary: {result.queue_summary}")
        return result
