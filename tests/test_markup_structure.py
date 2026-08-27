"""Structural guards on `index.html`, and on the browser suites that read it.

Everything here shares one shape: the file reads correctly and means something
else, so the only way to see it is to parse rather than to look.

Written after a one-character mistake shipped a broken layout: changing
`<div class="creator-list">` to `<nav class="creator-list">` without changing
its `</div>` left the `<nav>` unclosed, so the parser used the next four
`</div>`s to unwind `aside`, `.workspace` and `.app-container` in turn. The
`<main>` that should have been `.workspace`'s second grid child became a
sibling of `.workspace` and landed in grid row 2, below a 650px sidebar,
pushing the first photo to y=1049 of a 900px viewport.

Nothing failed. The file "looked" balanced -- every tag had a partner on its
own line, and the browser recovered silently, as browsers must. Only a parse
sees it, so this parses.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "index.html"

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "source", "track", "wbr",
}


class _Structure(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str, int]] = []
        self.mismatched: list[str] = []
        self.mains: list[list[str]] = []
        self.h1s: list[str] = []
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in VOID:
            return
        got = dict(attrs)
        ident = got.get("id") or got.get("class") or ""
        self.stack.append((tag, ident, self.getpos()[0]))
        if tag == "main":
            self.mains.append([f"{t}#{i}" for t, i, _ in self.stack[:-1]])
        if tag == "h1":
            self._in_h1 = True
            self.h1s.append("")

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self.h1s[-1] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if tag == "h1":
            self._in_h1 = False
        while self.stack and self.stack[-1][0] != tag:
            open_tag, ident, line = self.stack.pop()
            self.mismatched.append(
                f"<{open_tag} {ident!r}> opened on line {line} was closed by "
                f"</{tag}> on line {self.getpos()[0]}"
            )
        if self.stack:
            self.stack.pop()


def _parsed() -> _Structure:
    parser = _Structure()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def test_every_element_is_closed_by_its_own_tag():
    parser = _parsed()
    assert not parser.mismatched, "\n".join(parser.mismatched)
    leftover = [f"<{t} {i!r}> on line {ln}" for t, i, ln in parser.stack]
    assert not leftover, "unclosed at EOF: " + ", ".join(leftover)


def test_one_main_and_it_is_the_workspace_grid_child():
    parser = _parsed()
    assert len(parser.mains) == 1, f"{len(parser.mains)} <main> elements"
    # The layout depends on this: `.workspace` is a `320px 1fr` grid, so <main>
    # has to be its child to land beside the sidebar rather than under it.
    assert parser.mains[0][-1] == "div#workspace", parser.mains[0]


def test_one_h1_naming_the_product():
    parser = _parsed()
    assert len(parser.h1s) == 1, f"{len(parser.h1s)} <h1> elements"
    assert "PromptStudio" in parser.h1s[0].replace(" ", ""), parser.h1s


def test_ids_are_unique():
    ids = re.findall(r'\sid="([^"]+)"', INDEX.read_text(encoding="utf-8"))
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate ids: {sorted(dupes)}"


def test_every_aria_labelledby_resolves():
    """A dangling `aria-labelledby` leaves the element with *no* name at all --
    it does not fall back to the text or the title."""
    src = INDEX.read_text(encoding="utf-8")
    ids = set(re.findall(r'\sid="([^"]+)"', src))
    refs = re.findall(r'aria-labelledby="([^"]+)"', src)
    dangling = [ref for ref in refs for token in ref.split() if token not in ids]
    assert not dangling, f"aria-labelledby points at missing ids: {dangling}"


# ── copy that describes the machine rather than the thing (U32) ──────────
#
# `seed`, `CFG`, `denoise` and `steps` are deliberately absent from this list:
# they are ComfyUI's own vocabulary, and a user driving a ComfyUI workflow
# knows them by those names. What does not belong on screen is this repo's
# internal shorthand.
SYSTEM_VOICE = [
    "(C1)", "(A1)", "(B4)", "(F1)", "(E1)",   # backlog ids from the review docs
    "pHash",                                   # how duplicate detection works
    "embeddings",                              # how semantic search works
    "instrumentation",
    "backoff",
    "rel_path",
    "sidecar",
]


def test_no_internal_shorthand_in_user_visible_copy():
    src = INDEX.read_text(encoding="utf-8")
    # Comments are for whoever edits this file and may say anything.
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    found = {token for token in SYSTEM_VOICE if token in src}
    assert not found, f"user-visible copy leaks internal shorthand: {sorted(found)}"


# ── the browser suites' own source (a mistake made twice) ────────────────
#
# `Session.eval` interpolates its argument into a template literal, so a regex
# written as `/\s+/g` inside one of those backtick blocks ships a bare `\s`.
# `\s` is not a JS escape, so it collapses to `s`: the regex becomes `/s+/g`
# and every letter s in the measured string turns into a space. It read back as
# "Still not an wering ... `ollama  erve` ... localho t:11434", and the first
# occurrence had already shipped -- harmlessly, because that string only fed an
# em-dash test, which is exactly why nothing caught it.
#
# Only inside backticks: at Node level a single backslash is correct, and this
# check flagged fourteen innocent ones before it was narrowed.
CLASS_ESCAPES = "sdwbSDWB"
BACKSLASH = chr(92)


def _template_spans(text: str) -> list[tuple[int, int]]:
    """Byte ranges of every backtick-delimited literal, escapes respected."""
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        start = text.find("`", i)
        if start < 0:
            return spans
        j = start + 1
        while j < len(text):
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == "`":
                break
            j += 1
        if j >= len(text):
            return spans
        spans.append((start + 1, j))
        i = j + 1


def test_ui_suites_do_not_double_regex_backslashes_outside_template_literals():
    """The mirror image, and the second half of the same mistake.

    Outside a template literal one backslash is correct, so a doubled one is a
    regex matching a literal backslash followed by any character. It never
    matches and it never throws: the check using it fails for a reason with
    nothing to do with the code under test. Both directions of this were made
    in the same afternoon, in adjacent lines of the same suite.
    """
    doubled = re.compile(re.escape(BACKSLASH * 2) + "([" + CLASS_ESCAPES + r".])")
    offenders = []
    for suite in sorted((Path(__file__).resolve().parent / "ui").glob("test_*.js")):
        text = suite.read_text(encoding="utf-8")
        spans = _template_spans(text)
        for match in doubled.finditer(text):
            at = match.start()
            if any(start <= at < end for start, end in spans):
                continue  # inside a literal, where doubling is correct
            line = text[:at].count("\n") + 1
            offenders.append(
                f"{suite.name}:{line} a doubled backslash before "
                f"{match.group(1)!r} outside a template literal matches a "
                "literal backslash -- use one"
            )
    assert not offenders, "\n".join(offenders)


def test_ui_suites_double_regex_backslashes_inside_template_literals():
    offenders = []
    for suite in sorted((Path(__file__).resolve().parent / "ui").glob("test_*.js")):
        text = suite.read_text(encoding="utf-8")
        for start, end in _template_spans(text):
            for match in re.finditer(r"(?<!\\)\\([" + CLASS_ESCAPES + r"])", text[start:end]):
                at = start + match.start()
                line = text[:at].count("\n") + 1
                offenders.append(
                    f"{suite.name}:{line} `\\{match.group(1)}` inside a template "
                    "literal collapses to a bare letter -- double the backslash"
                )
    assert not offenders, "\n".join(offenders)


# ── CSS a browser test cannot reach ─────────────────────────────────────
#
# `getComputedStyle(el, '::-webkit-calendar-picker-indicator')` answers with the
# host element's own style, not the pseudo-element's, so the browser suite
# cannot tell whether the picker glyph is styled. Without the invert it is a
# near-black glyph on a near-black field. Asserted here, at source level, with
# the limitation stated rather than a browser check that always passes.
STYLE = Path(__file__).resolve().parent.parent / "style.css"

REQUIRED_PSEUDO_RULES = [
    "::-webkit-calendar-picker-indicator",
    "::file-selector-button",
]


def test_native_control_pseudo_elements_are_styled():
    css = STYLE.read_text(encoding="utf-8")
    missing = [rule for rule in REQUIRED_PSEUDO_RULES if rule not in css]
    assert not missing, f"native controls left to the platform default: {missing}"
