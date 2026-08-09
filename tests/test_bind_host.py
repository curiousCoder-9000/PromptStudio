"""The server must not bind every interface by default.

There is no auth and CORS is `*`, so a non-loopback bind exposes `/media/...`
(the whole archive) and `DELETE /api/photo` to anything that can reach the
port. The regression this guards is specific: `PROMPTSTUDIO_HOST=` in
`.env.example` is a *set but empty* variable, so `os.environ.get(name,
"127.0.0.1")` returns `""` — not the default — and `("", port)` binds
INADDR_ANY. A default alone does not fix that; the empty case has to be
handled explicitly.
"""

import os

import pytest

from promptstudio.config import HOST, LOOPBACK_HOSTS, resolve_host

# Anything here means "listen on every interface".
ALL_INTERFACES = ("", "0.0.0.0", "::", "*")


@pytest.mark.parametrize("raw", [None, "", "   ", "\t", "\n"])
def test_blank_host_resolves_to_loopback(raw):
    assert resolve_host(raw) == "127.0.0.1"


def test_explicit_host_is_respected():
    """Exposing the server stays possible — it just has to be deliberate."""
    assert resolve_host("0.0.0.0") == "0.0.0.0"
    assert resolve_host("192.168.1.10") == "192.168.1.10"
    assert resolve_host("  10.0.0.5  ") == "10.0.0.5"


def test_module_default_is_loopback():
    if os.environ.get("PROMPTSTUDIO_HOST", "").strip():
        pytest.skip("PROMPTSTUDIO_HOST is set in this environment")
    assert HOST not in ALL_INTERFACES
    assert HOST in LOOPBACK_HOSTS


def test_env_example_does_not_ship_an_exposed_bind():
    """The shipped template is what most users copy verbatim."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".env.example"), encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip().startswith("PROMPTSTUDIO_HOST=")]

    assert lines, "PROMPTSTUDIO_HOST should stay documented in .env.example"
    for line in lines:
        value = line.split("=", 1)[1].strip()
        assert resolve_host(value) in LOOPBACK_HOSTS, (
            f".env.example ships {line!r}, which binds a non-loopback address"
        )
