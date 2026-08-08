"""Instaloader session bootstrap."""

import os

import instaloader

from promptstudio.config import (
    INSTALOADER_SESSION_DIR,
    SAVED_DIR,
    SESSION_USER,
)


def create_instaloader(dirname_pattern: str | None = None) -> instaloader.Instaloader:
    pattern = dirname_pattern or SAVED_DIR + "/{owner_username}"
    return instaloader.Instaloader(
        dirname_pattern=pattern,
        filename_pattern="{owner_username}_{date_utc:%Y-%m-%d_%H-%M-%S}_UTC",
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        max_connection_attempts=3,
        request_timeout=30,
        sleep=True,
    )


def load_session(L: instaloader.Instaloader, session_user: str = SESSION_USER) -> None:
    """Load Instaloader session file for session_user."""
    user = (session_user or SESSION_USER or "").strip()
    if not user:
        raise RuntimeError(
            "INSTAGRAM_SESSION_USER is not set. Copy .env.example to .env and set "
            "your Instagram username, then run: instaloader --login YOUR_USERNAME"
        )
    session_file = os.path.join(INSTALOADER_SESSION_DIR, f"session-{user}")
    if os.path.isfile(session_file):
        L.load_session_from_file(user, filename=session_file)
        return
    # Fall back to Instaloader's default search path
    L.load_session_from_file(user)


def authenticated_profile(L: instaloader.Instaloader, session_user: str = SESSION_USER):
    user = (session_user or SESSION_USER or "").strip()
    load_session(L, user)
    return instaloader.Profile.from_username(L.context, user)
