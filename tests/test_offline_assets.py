"""The UI must render with no network.

This is a local-first tool — local archive, local Ollama, local ComfyUI — and
`index.html` was loading its whole icon set from cdnjs and its typography from
Google Fonts. Offline, every icon becomes an empty box, and several buttons
(`#lightboxClose`, `#lightboxPrev`, `#lightboxNext`, `#gridNormal`,
`#gridLarge`, `#photoViewerClose`, `#newCreatorBtn`) have no text at all, so
they disappear entirely. It also meant two third-party requests per page load.

Assets are vendored under `assets/` by `scripts/vendor_web_assets.py`.
"""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO_ROOT, "index.html")
ASSETS = os.path.join(REPO_ROOT, "assets")


def _index_html() -> str:
    with open(INDEX, encoding="utf-8") as fh:
        return fh.read()


def _external_refs(html: str) -> list[str]:
    """Remote href/src on link/script/img tags — comments don't count."""
    return re.findall(
        r"""<(?:link|script|img)\b[^>]*?\b(?:href|src)=["'](https?://[^"']+)["']""",
        html,
        re.IGNORECASE,
    )


def test_index_loads_no_remote_stylesheets_or_scripts():
    remote = _external_refs(_index_html())
    assert not remote, (
        "index.html must not fetch from the network — the app is local-first "
        f"and breaks offline. Found: {remote}. "
        "Vendor with: py scripts/vendor_web_assets.py"
    )


def test_index_references_the_vendored_stylesheets():
    html = _index_html()
    assert "assets/fonts/fonts.css" in html
    assert "assets/fontawesome/css/all.min.css" in html


def test_every_vendored_asset_exists_and_is_non_empty():
    """Catches a stylesheet whose font files were never downloaded."""
    from scripts.vendor_web_assets import required_files

    missing = [
        rel
        for rel in required_files()
        if not os.path.exists(os.path.join(ASSETS, rel))
        or os.path.getsize(os.path.join(ASSETS, rel)) == 0
    ]
    assert not missing, (
        f"Missing vendored assets: {missing}. Run: py scripts/vendor_web_assets.py"
    )


@pytest.mark.parametrize("css_rel", ["fonts/fonts.css", "fontawesome/css/all.min.css"])
def test_vendored_css_has_no_remote_font_urls(css_rel):
    """A leftover https:// in a src() is a silent fallback to the CDN."""
    path = os.path.join(ASSETS, css_rel)
    with open(path, encoding="utf-8") as fh:
        css = fh.read()
    # Strip /* comments */ — upstream FA keeps its licence URL in one.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    remote = re.findall(r"url\(\s*['\"]?(https?://[^)'\"]+)", css)
    assert not remote, f"{css_rel} still points at {remote}"


# ── Icon names must exist in the set we actually ship ──────────────────
#
# The checks above prove the *files* are vendored. They cannot see a glyph that
# is not in them: `fa-image-slash` (every empty state) and `fa-sparkles` (the
# first-run screen) are Font Awesome **Pro** names, the vendored set is Free,
# and a missing glyph gets no `content` at all — so the element renders at width
# zero. Both class names read as correct in the source, both shipped, and the
# result was a blank hole above the heading on the first screen a new archive
# shows. Only the computed style, or this test, tells you.

APP_JS = os.path.join(REPO_ROOT, "app.js")
FA_CSS = os.path.join(ASSETS, "fontawesome", "css", "all.min.css")

# `fa-` prefixed classes that select a style, size or animation rather than a
# glyph. Everything else must resolve to a `.fa-<name>:before` rule.
FA_NON_GLYPH = {
    "solid", "regular", "brands", "light", "thin", "duotone", "sharp", "classic",
    "fw", "border", "li", "ul", "stack", "stack-1x", "stack-2x", "inverse",
    "pull-left", "pull-right", "spin", "spin-pulse", "spin-reverse", "pulse",
    "beat", "fade", "beat-fade", "bounce", "flip", "shake",
    "rotate-90", "rotate-180", "rotate-270", "rotate-by",
    "flip-horizontal", "flip-vertical", "flip-both",
    "1x", "2x", "3x", "4x", "5x", "6x", "7x", "8x", "9x", "10x",
    "2xs", "xs", "sm", "lg", "xl", "2xl",
    "sr-only", "sr-only-focusable",
}


def _shipped_glyphs() -> set[str]:
    with open(FA_CSS, encoding="utf-8") as fh:
        return set(re.findall(r"\.fa-([a-z0-9-]+):before", fh.read()))


def _strip_comments(text: str) -> str:
    """Comments are prose, not usage — this very file names the two Pro icons."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Only whole-line `//`, so a `https://` inside a string keeps its line.
    return "\n".join(
        "" if line.lstrip().startswith("//") else line for line in text.splitlines()
    )


def _referenced_glyphs() -> dict[str, set[str]]:
    """glyph name -> the files that ask for it."""
    used: dict[str, set[str]] = {}
    for path in (INDEX, APP_JS):
        with open(path, encoding="utf-8") as fh:
            for name in re.findall(r"fa-([a-z0-9-]+)", _strip_comments(fh.read())):
                if name in FA_NON_GLYPH:
                    continue
                used.setdefault(name, set()).add(os.path.basename(path))
    return used


def test_every_icon_name_exists_in_the_vendored_set():
    shipped = _shipped_glyphs()
    assert shipped, f"no glyphs parsed out of {FA_CSS} — did the format change?"
    missing = {
        name: sorted(where)
        for name, where in _referenced_glyphs().items()
        if name not in shipped
    }
    assert not missing, (
        "These icon names are not in the vendored Font Awesome set, so they "
        "render at width 0 — an invisible icon, not a fallback box: "
        f"{missing}. They are usually Pro-only names; pick a Free equivalent."
    )
