"""A3 — `PUT /api/generation/rate` over real HTTP.

The route's whole job is mapping: a bad ordinal is a 400, an unknown id is a
404, and neither may look like success. That mapping is the part a db-level
test cannot see.
"""

from __future__ import annotations

import pytest

from promptstudio.storage.db import ArchiveIndex

RATE = "/api/generation/rate"


@pytest.fixture
def gen_id():
    return ArchiveIndex.get().record_generation(
        rel_path="_generations/nina/a.png",
        source_rel="nina/photo.jpg",
        creator="nina",
        workflow="pro",
        seed=4242,
        positive_prompt="a portrait",
    )


def test_rating_a_generation_returns_ok_and_persists(api, gen_id):
    status, payload = api("PUT", RATE, {"gen_id": gen_id, "rating": 2})

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["rating"] == 2
    row = ArchiveIndex.get().list_generations_for("nina/photo.jpg")[0]
    assert row["rating"] == 2


@pytest.mark.parametrize("rating", [3, -2, 99])
def test_an_ordinal_off_the_scale_is_a_400(api, gen_id, rating):
    status, _ = api("PUT", RATE, {"gen_id": gen_id, "rating": rating})

    assert status == 400
    assert ArchiveIndex.get().list_generations_for("nina/photo.jpg")[0]["rating"] == 0


def test_a_stringy_rating_is_a_400_not_a_silent_coercion(api, gen_id):
    """`int("2")` would have accepted this and stored a rating the client never
    meant to send as an int."""
    status, _ = api("PUT", RATE, {"gen_id": gen_id, "rating": "2"})

    assert status == 400


def test_an_unknown_generation_is_a_404(api):
    status, _ = api("PUT", RATE, {"gen_id": "no-such-id", "rating": 1})

    assert status == 404


def test_a_missing_gen_id_is_a_400(api):
    status, _ = api("PUT", RATE, {"rating": 1})

    assert status == 400


def test_the_keep_rate_moves_after_rating_through_the_api(api, gen_id):
    """End to end: the route feeds the B1 metric it exists to enable."""
    from promptstudio.insights import compute_insights

    assert compute_insights()["generations"]["keep_rate"] is None

    api("PUT", RATE, {"gen_id": gen_id, "rating": 1})

    assert compute_insights()["generations"]["keep_rate"] == 1.0


def test_the_lightbox_sees_an_existing_rating_when_it_reopens(api, gen_id):
    """`/api/generations` reads the legacy JSON index, which has no rating
    column. Without enrichment you rate an output, reopen the photo, and the
    control shows nothing — which reads as "the rating was lost"."""
    api("PUT", RATE, {"gen_id": gen_id, "rating": 2})

    status, payload = api("GET", "/api/generations?path=nina%2Fphoto.jpg")

    assert status == 200
    files = payload["generations"][0]["files"]
    assert files[0]["gen_id"] == gen_id
    assert files[0]["rating"] == 2
