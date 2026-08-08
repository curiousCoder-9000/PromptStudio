"""HTTP Range parsing — video scrubbing depends on it being exactly right.

Contract (per the docstring on the function under test):
  * missing / non-`bytes=` header  -> None  (serve the whole file)
  * unsatisfiable or malformed      -> ValueError (caller sends 416)
  * otherwise                       -> inclusive (start, end)
"""

import pytest

from promptstudio.server.handler import GalleryRequestHandler

SIZE = 1000
parse = GalleryRequestHandler._parse_byte_range


@pytest.mark.parametrize("header", ["", None])
def test_no_header_means_whole_file(header):
    assert parse(header, SIZE) is None


@pytest.mark.parametrize("header", ["items=0-99", "0-99", "bytes", "seconds=0-1"])
def test_non_bytes_units_ignored(header):
    assert parse(header, SIZE) is None


def test_explicit_closed_range():
    assert parse("bytes=0-99", SIZE) == (0, 99)
    assert parse("bytes=100-199", SIZE) == (100, 199)


def test_open_ended_range_runs_to_last_byte():
    assert parse("bytes=500-", SIZE) == (500, SIZE - 1)


def test_end_is_clamped_to_file_size():
    assert parse("bytes=900-99999", SIZE) == (900, SIZE - 1)


def test_suffix_range_returns_last_n_bytes():
    assert parse("bytes=-100", SIZE) == (900, 999)


def test_suffix_larger_than_file_returns_whole_file():
    assert parse("bytes=-99999", SIZE) == (0, SIZE - 1)


def test_only_first_range_is_honoured():
    # Browsers send single ranges for media; multi-range would need multipart
    assert parse("bytes=0-99,200-299", SIZE) == (0, 99)


def test_outer_whitespace_tolerated():
    assert parse("  bytes=10-20  ", SIZE) == (10, 20)


def test_whitespace_around_bounds_tolerated():
    assert parse("bytes= 10 - 20 ", SIZE) == (10, 20)


def test_space_before_equals_is_not_a_valid_unit():
    """RFC 7233 allows no space in the `bytes=` token, so this is not a range.

    Returning None (serve the whole file) is the safe reading — better than
    guessing at a malformed header. No real client sends this.
    """
    assert parse("bytes = 10-20", SIZE) is None


def test_last_byte_is_reachable():
    assert parse(f"bytes={SIZE - 1}-", SIZE) == (SIZE - 1, SIZE - 1)


@pytest.mark.parametrize(
    "header",
    [
        "bytes=1000-",      # start == size
        "bytes=5000-6000",  # start past EOF
        "bytes=200-100",    # end before start
        "bytes=-0",         # zero-length suffix
        "bytes=-",          # no bounds at all
        "bytes=abc",        # no dash
    ],
)
def test_unsatisfiable_or_malformed_raises(header):
    with pytest.raises(ValueError):
        parse(header, SIZE)


def test_non_numeric_bounds_raise():
    with pytest.raises(ValueError):
        parse("bytes=x-y", SIZE)


def test_single_byte_file():
    assert parse("bytes=0-", 1) == (0, 0)
    assert parse("bytes=-1", 1) == (0, 0)
