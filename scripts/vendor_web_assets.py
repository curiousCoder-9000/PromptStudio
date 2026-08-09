"""Download the web fonts and icon font into `assets/` so the UI works offline.

The app is a local-first tool — local archive, local Ollama, local ComfyUI —
and it was loading its entire icon set from cdnjs and its typography from
Google Fonts. With no network every icon renders as an empty box, and several
buttons in `index.html` have no text at all, so the lightbox close/next/prev
and grid-size toggles simply vanish. It also meant two third-party requests on
every page load from an app whose whole premise is that nothing leaves the
machine.

Run this to (re)create `assets/`. Committing the output is intentional: the
point is that a clone works with the network unplugged. This script exists so
the blobs are auditable and refreshable rather than mystery binaries.

    py scripts/vendor_web_assets.py [--check]

`--check` verifies every file `index.html` needs is present and non-empty,
without touching the network. That is what CI should run.
"""

from __future__ import annotations

import argparse
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(REPO_ROOT, "assets")

FA_VERSION = "6.4.0"
FA_BASE = f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FA_VERSION}"
# The families index.html uses (fa-solid / fa-regular / fa-brands), plus the
# v4 shim: all.min.css declares an @font-face for it unconditionally, so
# leaving it out means a guaranteed 404 on every page load — exactly the
# offline breakage this script exists to remove. Keep this list in sync with
# `grep -o 'url(\.\./webfonts/[^)]*)' assets/fontawesome/css/all.min.css`.
FA_WEBFONTS = (
    "fa-solid-900.woff2",
    "fa-regular-400.woff2",
    "fa-brands-400.woff2",
    "fa-v4compatibility.woff2",
)

GOOGLE_CSS = (
    "https://fonts.googleapis.com/css2"
    "?family=Outfit:wght@300;400;500;600;700"
    "&family=Inter:wght@400;500;600"
    "&family=Fira+Code:wght@400;500"
    "&display=swap"
)
# Google serves per-script subsets. The UI is Latin-only; pulling cyrillic and
# friends would triple the payload for glyphs nothing renders.
KEEP_SUBSETS = ("latin", "latin-ext")

# A modern UA is required or Google returns TTF instead of WOFF2.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _ssl_context():
    """Verified TLS, with a certifi fallback.

    A framework Python on macOS that never ran `Install Certificates.command`
    has no trust store, and the honest options there are "use certifi" or
    "disable verification". Never the second one.
    """
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cafile=__import__("certifi").where())
    except Exception:  # certifi absent — rely on the system store
        pass
    return ctx


def _fetch_curl(url: str) -> bytes:
    """Fallback for trust stores Python cannot see (corporate MITM roots).

    `curl` validates against the OS store — this is a different trust source,
    not a weaker one. `--fail` so an HTML error page never lands in a .woff2.
    """
    out = subprocess.run(
        ["curl", "-fsSL", "--max-time", "30", "-A", UA, url],
        capture_output=True,
        check=True,
    )
    return out.stdout


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(
            req, timeout=30, context=_ssl_context()
        ) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        if not isinstance(e.reason, ssl.SSLError):
            raise
        try:
            return _fetch_curl(url)
        except (OSError, subprocess.CalledProcessError):
            raise e from None


def _write(rel_path: str, data: bytes) -> str:
    dest = os.path.join(ASSETS, rel_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


def vendor_fontawesome() -> list[str]:
    written = []
    css = _fetch(f"{FA_BASE}/css/all.min.css").decode("utf-8")
    # Upstream lists .ttf after .woff2 in every src. Every browser that can run
    # this UI takes the woff2, so the ttf is dead weight — but a stale URL that
    # 404s offline is noise in the console, so drop those fallbacks.
    css = re.sub(r',\s*url\("?\.\./webfonts/[^)]*?\.ttf"?\)\s*format\("truetype"\)', "", css)
    written.append(_write("fontawesome/css/all.min.css", css.encode("utf-8")))
    for name in FA_WEBFONTS:
        written.append(_write(f"fontawesome/webfonts/{name}", _fetch(f"{FA_BASE}/webfonts/{name}")))
    return written


def vendor_google_fonts() -> list[str]:
    css = _fetch(GOOGLE_CSS).decode("utf-8")

    # css2 emits `/* subset */` immediately before each @font-face block.
    blocks = re.split(r"(?=/\*\s*[a-z-]+\s*\*/)", css)
    kept: list[str] = []
    written: list[str] = []
    seen: set[str] = set()

    for block in blocks:
        m = re.match(r"/\*\s*([a-z-]+)\s*\*/", block.strip())
        if not m or m.group(1) not in KEEP_SUBSETS:
            continue

        def _localise(match: re.Match) -> str:
            url = match.group(1)
            filename = url.rsplit("/", 1)[-1].split("?")[0]
            if not filename.endswith(".woff2"):
                filename += ".woff2"
            if filename not in seen:
                seen.add(filename)
                written.append(_write(f"fonts/{filename}", _fetch(url)))
            return f"url(../fonts/{filename})"

        kept.append(re.sub(r"url\((https://[^)]+)\)", _localise, block).strip())

    if not kept:
        raise RuntimeError("Google Fonts returned no latin subsets — check the UA")

    header = (
        "/* Vendored by scripts/vendor_web_assets.py — do not hand-edit.\n"
        f"   Source: {GOOGLE_CSS}\n"
        f"   Subsets kept: {', '.join(KEEP_SUBSETS)} */\n"
    )
    written.append(_write("fonts/fonts.css", (header + "\n\n".join(kept) + "\n").encode("utf-8")))
    return written


def _urls_in(css_rel: str, pattern: str) -> list[str]:
    path = os.path.join(ASSETS, css_rel)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return sorted(set(re.findall(pattern, fh.read())))


def required_files() -> list[str]:
    """Every asset the two vendored stylesheets reference, relative to `assets/`.

    Derived from the CSS rather than a hand-kept list: the FA stylesheet
    declares a v4-compat @font-face nobody asked for, and a hardcoded list
    silently misses additions like that on the next version bump.
    """
    files = ["fontawesome/css/all.min.css", "fonts/fonts.css"]
    files += [
        f"fontawesome/webfonts/{n}"
        for n in _urls_in("fontawesome/css/all.min.css", r"url\(\.\./webfonts/([^)?#]+)")
    ]
    files += [f"fonts/{n}" for n in _urls_in("fonts/fonts.css", r"url\(\.\./fonts/([^)?#]+)")]
    return files


def check() -> int:
    missing = []
    for rel in required_files():
        path = os.path.join(ASSETS, rel)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            missing.append(rel)
    if missing:
        print("Missing or empty vendored assets:", file=sys.stderr)
        for rel in missing:
            print(f"  assets/{rel}", file=sys.stderr)
        print("Run: py scripts/vendor_web_assets.py", file=sys.stderr)
        return 1
    print(f"assets/ OK — {len(required_files())} files")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the vendored files exist; no network access.",
    )
    args = parser.parse_args()
    if args.check:
        return check()

    written = vendor_fontawesome() + vendor_google_fonts()
    total = sum(os.path.getsize(p) for p in written)
    for path in written:
        print(f"  {os.path.relpath(path, REPO_ROOT)}  {os.path.getsize(path) / 1024:.0f} KB")
    print(f"{len(written)} files, {total / 1024:.0f} KB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
