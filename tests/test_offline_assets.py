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
