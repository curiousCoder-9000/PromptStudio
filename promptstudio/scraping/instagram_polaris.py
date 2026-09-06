"""List Instagram profile posts via Polaris GraphQL.

`/api/v1/feed/user/<id>/` currently 302s to home, so gallery-dl's REST
profile extractor downloads nothing. The logged-in Polaris query
(`xdt_api__v1__feed__user_timeline_graphql_connection`) still returns
shortcodes. gallery-dl then fetches each `/p/<shortcode>/` through
`/api/v1/media/<id>/info/`, which still works.

Instagram rotates `doc_id` values. This one is what Instaloader's
logged-in `Profile.get_posts()` moved to after the previous timeline id
was revoked.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import MozillaCookieJar
from typing import Any, Callable, Dict, List, Optional

from promptstudio.logging_setup import get_logger

log = get_logger(__name__)

POLARIS_PROFILE_POSTS_DOC_ID = "34579740524958711"
_CONNECTION = "xdt_api__v1__feed__user_timeline_graphql_connection"
_APP_ID = "936619743392459"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_PAGE = 12


def list_profile_shortcodes(
    username: str,
    *,
    cookies_file: str,
    max_posts: int,
    cancelled: Optional[Callable[[], bool]] = None,
    log_line: Optional[Callable[[str], None]] = None,
    page_sleep_sec: float = 1.0,
    _request: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> List[str]:
    """Return up to `max_posts` shortcodes for `username`.

    `_request` is a test seam: given the POST fields (variables JSON, doc_id),
    return the parsed GraphQL body. Production talks to Instagram.
    """
    handle = (username or "").strip().lstrip("@")
    if not handle:
        return []
    ceiling = max(0, int(max_posts))
    if ceiling <= 0:
        return []

    post = _request or (_make_requester(cookies_file, handle))
    codes: List[str] = []
    after: Optional[str] = None
    pages = 0

    while len(codes) < ceiling:
        if cancelled and cancelled():
            break
        if pages and page_sleep_sec > 0:
            time.sleep(page_sleep_sec)
        variables: Dict[str, Any] = {
            "data": {
                "count": _PAGE,
                "include_relationship_info": True,
                "latest_besties_reel_media": True,
                "latest_reel_media": True,
            },
            "username": handle,
            "__relay_internal__pv__PolarisFeedShareMenurelayprovider": False,
        }
        if after:
            variables["after"] = after
            variables["before"] = None
            variables["first"] = _PAGE
            variables["last"] = None
        body = post(
            {
                "variables": json.dumps(variables, separators=(",", ":")),
                "doc_id": POLARIS_PROFILE_POSTS_DOC_ID,
                "server_timestamps": "true",
            }
        )
        pages += 1
        conn = ((body.get("data") or {}).get(_CONNECTION)) or {}
        edges = conn.get("edges") or []
        if not edges:
            if log_line and not codes:
                log_line(f"Polaris returned no posts for @{handle}")
            break
        for edge in edges:
            node = (edge or {}).get("node") or {}
            code = str(node.get("code") or "").strip()
            if code and code not in codes:
                codes.append(code)
                if len(codes) >= ceiling:
                    break
        page = conn.get("page_info") or {}
        if not page.get("has_next_page"):
            break
        nxt = page.get("end_cursor")
        if not nxt or nxt == after:
            break
        after = str(nxt)

    if log_line:
        log_line(f"Polaris listed {len(codes)} posts for @{handle}")
    return codes[:ceiling]


def _make_requester(
    cookies_file: str, handle: str
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    jar = MozillaCookieJar(cookies_file)
    jar.load(ignore_discard=True, ignore_expires=True)
    csrf = next((c.value for c in jar if c.name == "csrftoken" and c.value), "")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def _post(fields: Dict[str, Any]) -> Dict[str, Any]:
        payload = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            "https://www.instagram.com/graphql/query",
            data=payload,
            method="POST",
            headers={
                "User-Agent": _UA,
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf or "missing",
                "X-IG-App-ID": _APP_ID,
                "X-Requested-With": "XMLHttpRequest",
                "X-FB-Friendly-Name": "PolarisProfilePostsQuery",
                "Referer": f"https://www.instagram.com/{handle}/",
            },
        )
        try:
            with opener.open(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            log.warning("Polaris GraphQL HTTP %s for @%s", exc.code, handle)
            raise
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            log.warning("Polaris GraphQL returned non-JSON for @%s", handle)
            raise RuntimeError("Polaris GraphQL returned non-JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("Polaris GraphQL returned a non-object")
        if body.get("errors") and not (body.get("data") or {}).get(_CONNECTION):
            raise RuntimeError(f"Polaris GraphQL errors for @{handle}")
        return body

    return _post
