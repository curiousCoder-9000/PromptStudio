"""A1 — querying the outputs gallery.

Mirrors `query_photos`: same `(rows, total)` contract, same `limit`/`offset`
vocabulary, so the frontend paging that already exists works unchanged.

The gate from design_generation_loop.md §4 is "1,000 generations paginate
without a full-table scan", which is a claim about the query plan, not about
wall-clock on a laptop — so it is asserted as one.
"""

from __future__ import annotations

import pytest

from promptstudio.storage.db import ArchiveIndex


@pytest.fixture
def seeded():
    """Nine generations across two creators, two workflows, two checkpoints."""
    index = ArchiveIndex.get()
    made = []
    for i in range(9):
        creator = "nina" if i < 5 else "ada"
        made.append(
            index.record_generation(
                rel_path=f"_generations/{creator}/g{i}.png",
                source_rel=f"{creator}/photo{i % 3}.jpg",
                creator=creator,
                workflow="pro" if i % 2 == 0 else "txt2img",
                checkpoint="ckpt_a" if i < 4 else "ckpt_b",
                seed=1000 + i,
                positive_prompt=f"prompt {i}",
                created_at=f"2026-08-{i + 1:02d}T10:00:00+00:00",
                batch_id="batch-1" if i < 3 else None,
            )
        )
    return index, made


def test_unfiltered_returns_everything_newest_first(seeded):
    index, _ = seeded

    rows, total = index.list_generations()

    assert total == 9
    assert len(rows) == 9
    dates = [r["created_at"] for r in rows]
    assert dates == sorted(dates, reverse=True)


def test_total_is_the_unpaged_count_not_the_page_size(seeded):
    """The frontend's `has_more` is computed from it — a page-sized total makes
    infinite scroll stop after one page."""
    index, _ = seeded

    rows, total = index.list_generations(limit=4)

    assert len(rows) == 4
    assert total == 9


def test_offset_walks_without_repeating_or_skipping(seeded):
    index, _ = seeded

    first, _ = index.list_generations(limit=4, offset=0)
    second, _ = index.list_generations(limit=4, offset=4)
    third, _ = index.list_generations(limit=4, offset=8)

    seen = [r["gen_id"] for r in first + second + third]
    assert len(seen) == 9
    assert len(set(seen)) == 9


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"creator": "nina"}, 5),
        ({"creator": "ada"}, 4),
        ({"workflow": "pro"}, 5),
        ({"checkpoint": "ckpt_b"}, 5),
        ({"batch_id": "batch-1"}, 3),
        ({"source_rel": "nina/photo0.jpg"}, 2),
    ],
)
def test_each_filter_narrows_to_the_right_rows(seeded, kwargs, expected):
    index, _ = seeded

    _rows, total = index.list_generations(**kwargs)

    assert total == expected


def test_filters_combine_rather_than_replace(seeded):
    index, _ = seeded

    _rows, total = index.list_generations(creator="nina", workflow="pro")

    assert total == 3


def test_rating_filter_finds_only_what_was_rated(seeded):
    index, made = seeded
    index.rate_generation(made[0], 2)
    index.rate_generation(made[1], -1)

    _starred, starred_total = index.list_generations(rating=2)
    _discarded, discarded_total = index.list_generations(rating=-1)
    _unrated, unrated_total = index.list_generations(rating=0)

    assert starred_total == 1
    assert discarded_total == 1
    assert unrated_total == 7


def test_rated_only_is_distinct_from_rating_zero(seeded):
    """`rating=0` means "show me the unrated"; `rated_only` means "show me
    everything I have judged". Collapsing them loses one of the two."""
    index, made = seeded
    index.rate_generation(made[0], 2)
    index.rate_generation(made[1], -1)

    _rows, total = index.list_generations(rated_only=True)

    assert total == 2


def test_sort_by_rating_puts_the_best_first(seeded):
    index, made = seeded
    index.rate_generation(made[3], 2)
    index.rate_generation(made[4], 1)
    index.rate_generation(made[5], -1)

    rows, _ = index.list_generations(sort="rating")

    assert rows[0]["rating"] == 2
    assert rows[-1]["rating"] == -1


def test_sort_by_source_groups_a_photos_outputs_together(seeded):
    index, _ = seeded

    rows, _ = index.list_generations(sort="source")

    sources = [r["source_rel"] for r in rows]
    assert sources == sorted(sources)


def test_since_excludes_older_rows(seeded):
    index, _ = seeded

    _rows, total = index.list_generations(since="2026-08-07T00:00:00+00:00")

    assert total == 3


def test_until_is_inclusive_of_a_date_only_day(seeded):
    """A date-only until must not drop same-day timestamps just because they
    sort after the bare date string."""
    index, _ = seeded

    _rows, total = index.list_generations(until="2026-08-03")

    assert total == 3


def test_date_range_clips_both_ends(seeded):
    index, _ = seeded

    _rows, total = index.list_generations(since="2026-08-03", until="2026-08-05")

    assert total == 3


def test_has_source_hides_pure_txt2img_rows(seeded):
    index, _ = seeded
    index.record_generation(
        rel_path="_generations/nina/txt.png",
        source_rel="",
        creator="nina",
        workflow="txt2img",
        seed=1,
        positive_prompt="no reference",
        created_at="2026-08-20T10:00:00+00:00",
    )

    _with, with_total = index.list_generations(has_source=True)
    _without, without_total = index.list_generations(has_source=False)
    _any, any_total = index.list_generations()

    assert with_total == 9
    assert without_total == 1
    assert any_total == 10


def test_an_unknown_sort_falls_back_rather_than_erroring(seeded):
    """Sort arrives from a query string. A bad value must not 500, and must not
    be interpolated into SQL."""
    index, _ = seeded

    rows, total = index.list_generations(sort="'; DROP TABLE generations; --")

    assert total == 9
    assert len(rows) == 9
    assert index.list_generations()[1] == 9


def test_paging_a_thousand_rows_uses_an_index_not_a_scan():
    """The §4 gate, asserted as a query plan rather than a stopwatch."""
    index = ArchiveIndex.get()
    for i in range(1000):
        index.record_generation(
            rel_path=f"_generations/bulk/g{i}.png",
            source_rel="bulk/photo.jpg",
            creator="bulk",
            workflow="pro",
            seed=i,
            positive_prompt="p",
            created_at=f"2026-08-10T10:{i // 60:02d}:{i % 60:02d}+00:00",
        )

    rows, total = index.list_generations(limit=50, offset=900)
    assert total == 1000
    assert len(rows) == 50

    plan = index.explain_generations_query()
    assert "idx_gen_created_id" in plan, plan
    # The composite covers both ORDER BY terms. A temp b-tree here means the
    # index went back to created_at alone, which measured 17x slower deep.
    assert "TEMP B-TREE" not in plan.upper(), plan
