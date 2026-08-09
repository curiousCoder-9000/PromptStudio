"""Perceptual hashing and near-duplicate grouping.

`deduplicate.py` matches bytes, so it misses the common case entirely: the same
picture re-encoded, resized, lightly cropped, or reposted by another creator.
A DCT hash survives all of those.

The property that matters is *separation* — transformed copies must land far
closer to each other than unrelated photos do, with enough of a gap that the
threshold is not balanced on a knife edge.
"""

import os

import cv2
import numpy as np
import pytest

from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.dedupe import (
    DEFAULT_MAX_DISTANCE,
    HASH_BITS,
    compute_phash,
    find_near_duplicate_groups,
    find_similar,
    hamming,
    iter_media_paths,
    phash_from_hex,
    phash_hex,
    pick_keeper,
)


def _photo(seed: int, size=(400, 300)):
    """A synthetic photo with real low-frequency structure to hash."""
    h, w = size
    r = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = ((x * int(r.integers(1, 4)) + y * int(r.integers(1, 4))) % 255).astype(np.uint8)
    img = np.dstack([base, np.roll(base, 40), np.roll(base, 90)])
    for _ in range(5):
        centre = (int(r.integers(40, w - 40)), int(r.integers(40, h - 40)))
        colour = tuple(int(v) for v in r.integers(0, 255, 3))
        cv2.circle(img, centre, int(r.integers(20, 60)), colour, -1)
    return img


@pytest.fixture
def photos(tmp_path):
    """Original plus a set of transformations of it, and two unrelated photos."""
    orig = _photo(1)
    paths = {}

    def write(name, img, quality=95):
        p = str(tmp_path / name)
        cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        paths[name] = p
        return p

    write("orig.jpg", orig)
    write("reencoded.jpg", orig, quality=40)
    write("resized.jpg", cv2.resize(orig, (150, 200)))
    write("cropped.jpg", orig[12:388, 9:291])
    write("brighter.jpg", np.clip(orig.astype(np.int16) + 45, 0, 255).astype(np.uint8))
    write("other.jpg", _photo(2))
    write("other2.jpg", _photo(3))
    return paths


# ── hashing ──────────────────────────────────────────────────────────


def test_hash_is_stable_for_the_same_file(photos):
    assert compute_phash(photos["orig.jpg"]) == compute_phash(photos["orig.jpg"])


def test_hash_fits_in_64_bits(photos):
    assert 0 <= compute_phash(photos["orig.jpg"]) < (1 << HASH_BITS)


def test_unreadable_paths_return_none(tmp_path):
    assert compute_phash(str(tmp_path / "missing.jpg")) is None
    assert compute_phash("") is None
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"not an image")
    assert compute_phash(str(junk)) is None


@pytest.mark.parametrize(
    "variant,ceiling",
    [("reencoded.jpg", 4), ("resized.jpg", 4), ("brighter.jpg", 6), ("cropped.jpg", 8)],
)
def test_transformations_stay_close_to_the_original(photos, variant, ceiling):
    base = compute_phash(photos["orig.jpg"])
    assert hamming(base, compute_phash(photos[variant])) <= ceiling


def test_unrelated_photos_are_far_apart(photos):
    base = compute_phash(photos["orig.jpg"])
    for name in ("other.jpg", "other2.jpg"):
        assert hamming(base, compute_phash(photos[name])) > 16


def test_there_is_a_wide_margin_around_the_threshold(photos):
    """A threshold sitting between 8 and 16 bits is not balanced on an edge."""
    base = compute_phash(photos["orig.jpg"])
    same = max(
        hamming(base, compute_phash(photos[n]))
        for n in ("reencoded.jpg", "resized.jpg", "cropped.jpg", "brighter.jpg")
    )
    different = min(
        hamming(base, compute_phash(photos[n])) for n in ("other.jpg", "other2.jpg")
    )
    assert same <= DEFAULT_MAX_DISTANCE < different
    assert different - same >= 8, f"margin too thin: {same} vs {different}"


# ── hex round trip ───────────────────────────────────────────────────


def test_hex_round_trip():
    for value in (0, 1, 2**63, (1 << HASH_BITS) - 1):
        assert phash_from_hex(phash_hex(value)) == value


def test_hex_is_fixed_width():
    """Stored as TEXT because a 64-bit hash overflows SQLite's signed INTEGER."""
    assert len(phash_hex(1)) == 16
    assert len(phash_hex((1 << HASH_BITS) - 1)) == 16


def test_bad_hex_is_none():
    assert phash_from_hex("nonsense") is None
    assert phash_from_hex(None) is None


# ── grouping ─────────────────────────────────────────────────────────


def test_groups_the_transformations_and_excludes_the_others(photos):
    hashes = {name: compute_phash(path) for name, path in photos.items()}
    groups = find_near_duplicate_groups(hashes, max_distance=DEFAULT_MAX_DISTANCE)

    assert len(groups) == 1
    assert set(groups[0]) == {
        "orig.jpg", "reencoded.jpg", "resized.jpg", "cropped.jpg", "brighter.jpg",
    }


def test_no_groups_when_everything_is_distinct():
    hashes = {"a": 0, "b": (1 << 64) - 1, "c": 0x0F0F0F0F0F0F0F0F}
    assert find_near_duplicate_groups(hashes, max_distance=4) == []


def test_grouping_is_transitive():
    """A re-crop of a re-encode belongs with the original even if those two
    are further apart than the threshold."""
    hashes = {"a": 0b0000, "b": 0b0011, "c": 0b1111}
    groups = find_near_duplicate_groups(hashes, max_distance=2)
    assert groups == [["a", "b", "c"]]


def test_a_single_hash_has_no_groups():
    assert find_near_duplicate_groups({"only": 1}) == []
    assert find_near_duplicate_groups({}) == []


def test_groups_are_largest_first():
    hashes = {"a": 0, "b": 0, "c": 0, "x": 0xFFFF, "y": 0xFFFF}
    groups = find_near_duplicate_groups(hashes, max_distance=1)
    assert [len(g) for g in groups] == [3, 2]


def test_find_similar_orders_by_distance():
    hashes = {"far": 0b1111, "near": 0b0001, "exact": 0b0000}
    hits = find_similar(hashes, 0b0000, max_distance=4)
    assert [name for name, _ in hits] == ["exact", "near", "far"]
    assert hits[0][1] == 0


def test_find_similar_respects_the_threshold():
    hashes = {"exact": 0b0000, "far": 0b1111}
    assert [n for n, _ in find_similar(hashes, 0, max_distance=2)] == ["exact"]


def test_find_similar_on_an_empty_index():
    assert find_similar({}, 0) == []


# ── keeper suggestion ────────────────────────────────────────────────


def test_keeper_is_the_largest_file(tmp_path):
    (tmp_path / "small.jpg").write_bytes(b"x" * 100)
    (tmp_path / "big.jpg").write_bytes(b"x" * 5000)
    assert pick_keeper(["small.jpg", "big.jpg"], str(tmp_path)) == "big.jpg"


def test_keeper_is_deterministic_when_sizes_match(tmp_path):
    for name in ("b.jpg", "a.jpg"):
        (tmp_path / name).write_bytes(b"x" * 100)
    assert pick_keeper(["b.jpg", "a.jpg"], str(tmp_path)) == pick_keeper(
        ["a.jpg", "b.jpg"], str(tmp_path)
    )


# ── storage ──────────────────────────────────────────────────────────


def test_phash_round_trips_through_the_index(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    index = ArchiveIndex.get()
    value = (1 << 63) | 12345  # deliberately above signed-int64 range

    index.set_phash(rel, value)
    assert index.get_phash(rel) == value
    assert index.all_phashes()[rel] == value


def test_bulk_upsert_and_overwrite(make_photo):
    rel_a, _ = make_photo(creator="nina", name="a.jpg")
    rel_b, _ = make_photo(creator="nina", name="b.jpg")
    index = ArchiveIndex.get()

    assert index.set_phashes([(rel_a, 1), (rel_b, 2)]) == 2
    assert index.set_phashes([(rel_a, 9)]) == 1
    assert index.get_phash(rel_a) == 9
    assert index.get_phash(rel_b) == 2


def test_missing_phash_lists_unhashed_media(make_photo):
    rel_a, _ = make_photo(creator="nina", name="a.jpg")
    rel_b, _ = make_photo(creator="nina", name="b.jpg")
    index = ArchiveIndex.get()
    index.set_phash(rel_a, 1)

    missing = index.paths_missing_phash()
    assert rel_b in missing
    assert rel_a not in missing


def test_delete_removes_the_hash(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    index = ArchiveIndex.get()
    index.set_phash(rel, 1)
    index.delete_phash(rel)
    assert index.get_phash(rel) is None


def test_unknown_path_has_no_hash():
    assert ArchiveIndex.get().get_phash("nobody/nothing.jpg") is None


# ── scanning ─────────────────────────────────────────────────────────


def test_scan_skips_underscore_folders(make_photo, tmp_path):
    from promptstudio.config import EXCLUDED_FOLDERS, SAVED_DIR

    make_photo(creator="nina", name="a.jpg")
    hidden = os.path.join(SAVED_DIR, "_thumbs", "nina")
    os.makedirs(hidden, exist_ok=True)
    cv2.imwrite(os.path.join(hidden, "a.jpg"), np.full((20, 20, 3), 128, np.uint8))

    found = iter_media_paths(SAVED_DIR, EXCLUDED_FOLDERS)
    assert "nina/a.jpg" in found
    assert not any(r.startswith("_thumbs") for r in found)
