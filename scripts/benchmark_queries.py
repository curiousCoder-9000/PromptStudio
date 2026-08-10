"""Time the gallery hot paths on a synthetic archive.

AGENTS.md rule 13 says measure before optimising and report the number, and this
codebase has twice been right to reverse an "obvious" win on measurement (FTS5
search, incremental rebuild). There was no harness for noticing a regression in
the other direction — this is it.

Deliberately **not** a CI gate. The existing measurements in
`docs/review_backend_architecture.md` S5 were produced by hand on one machine,
and timings on a shared runner are noise. Run it, paste the table into the PR.

    py scripts/benchmark_queries.py                      # 4.4k and 40k rows
    py scripts/benchmark_queries.py --rows 4400
    py scripts/benchmark_queries.py --with-captions      # F1: blob + caption text

Rows are inserted straight into SQLite: `upsert_photo` stats a real file per
row, and 40k JPEGs would measure the filesystem rather than the query.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import statistics
import sys
import tempfile
import time

# Must be set before promptstudio.config is imported — every path is derived
# from it at import time.
_BENCH_ARCHIVE = tempfile.mkdtemp(prefix="promptstudio-bench-")
os.environ["PROMPTSTUDIO_ARCHIVE"] = _BENCH_ARCHIVE
os.environ.setdefault("PROMPTSTUDIO_LOG_FILE", "")
os.environ.setdefault("PROMPTSTUDIO_LOG_CONSOLE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptstudio.storage.db import ArchiveIndex

# S5 used a 3000-word vocabulary; keep it so the numbers stay comparable.
VOCAB_SIZE = 3000
# Words-per-blob roughly matches a real two-stage prompt bundle: positive +
# negative + raw vision description + tags.
PROMPT_WORDS = 60
# A caption is short, hashtag-heavy, and repeats a small set of tokens.
CAPTION_WORDS = 18

RARE_WORD = "w0002"      # seeded into ~1% of rows
COMMON_WORD = "w0000"    # seeded into ~65% of rows

CREATORS = 40
REPEATS = 7
# Round-robin so every creator folder is multi-source — the worst case for the
# creator,source rollup (3x the groups of the creator-only one it replaces).
SOURCES = ("instagram", "x", "reddit")


def _vocab(rng: random.Random) -> list[str]:
    return [f"w{i:04d}" for i in range(VOCAB_SIZE)]


def _prompt_blob(rng: random.Random, words: list[str]) -> str:
    """A prompt-shaped blob: the four model-generated fields, concatenated."""
    parts = [rng.choice(words) for _ in range(PROMPT_WORDS)]
    # Zipf-ish: one token dominates, which is what makes "common word" slow.
    if rng.random() < 0.65:
        parts.append(COMMON_WORD)
    if rng.random() < 0.01:
        parts.append(RARE_WORD)
    return " ".join(parts).lower()


def _caption_blob(rng: random.Random, words: list[str]) -> str:
    """Short and hashtag-heavy, like a real caption."""
    parts = [f"#{rng.choice(words)}" for _ in range(CAPTION_WORDS // 3)]
    parts += [rng.choice(words) for _ in range(CAPTION_WORDS - CAPTION_WORDS // 3)]
    return " ".join(parts).lower()


def seed(index: ArchiveIndex, rows: int, *, with_caption: bool, seed_value: int = 7) -> int:
    """Insert `rows` photos plus a verdict for every third. Returns bytes scanned.

    F1 puts captions in their own `caption_search` column, so `--with-captions`
    has to fill that column rather than lengthening `prompt_search` — otherwise
    this measures a design that was not shipped. The cost being measured is
    "one more OR'd LIKE per row", not "a longer blob".
    """
    rng = random.Random(seed_value)
    words = _vocab(rng)
    now = time.time()
    photos, verdicts = [], []
    total_blob = 0

    for i in range(rows):
        creator = f"creator_{i % CREATORS:02d}"
        rel = f"{creator}/photo_{i:06d}.jpg"
        blob = _prompt_blob(rng, words)
        caption = _caption_blob(rng, words) if with_caption else ""
        total_blob += len(blob) + len(caption)
        photos.append(
            (rel, creator, f"photo_{i:06d}.jpg", None, now - i, now - i,
             1 if i % 20 == 0 else 0, 1, 0, blob, caption, None, None,
             SOURCES[i % len(SOURCES)])
        )
        if i % 3 == 0:
            verdicts.append((rel, creator, i % 5, None, "seeded", "photo",
                             "image", 0.8, "v4-ordinal-frame-v7a", None, None, None, 0))

    with index._lock:
        index._conn.executemany(
            "INSERT OR REPLACE INTO photos(rel_path, creator, filename, taken_at, "
            "mtime, added_at, favorite, has_prompt, prompt_stale, prompt_search, "
            "caption_search, post_id, shortcode, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            photos,
        )
        index._conn.executemany(
            "INSERT OR REPLACE INTO media_verdicts(rel_path, creator, tier, manual, "
            "reason, media_kind, verdict_source, confidence, prompt_version, "
            "sheet_path, error, classified_at, duration_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            verdicts,
        )
        index._conn.commit()
    return total_blob


def assign_post_ids(
    index: ArchiveIndex, rows: int, *, carousel_share: float = 0.35, seed_value: int = 11
) -> int:
    """Give the seeded rows a carousel shape, as an UPDATE after the fact.

    Deliberately not folded into `seed()`: the S5 and S9 tables above were
    produced on rows with a NULL `post_id`, and quietly widening every row would
    make a fresh run of this script incomparable to the numbers already in
    docs/review_backend_architecture.md.

    Slides of one post must share a creator (the group key is creator-scoped),
    and `seed()` round-robins creators, so this walks each creator's own rows.
    Returns the number of posts.
    """
    rng = random.Random(seed_value)
    by_creator: dict[str, list[str]] = {}
    for i in range(rows):
        creator = f"creator_{i % CREATORS:02d}"
        by_creator.setdefault(creator, []).append(f"{creator}/photo_{i:06d}.jpg")

    updates, posts = [], 0
    for rels in by_creator.values():
        pos = 0
        while pos < len(rels):
            slides = rng.randint(3, 10) if rng.random() < carousel_share else 1
            slides = min(slides, len(rels) - pos)
            post_id = f"post_{posts:07d}"
            updates.extend((post_id, rels[pos + n]) for n in range(slides))
            pos += slides
            posts += 1

    with index._lock:
        index._conn.executemany(
            "UPDATE photos SET post_id = ? WHERE rel_path = ?", updates
        )
        index._conn.execute("ANALYZE")
        index._conn.commit()
    return posts


def time_call(fn, repeats: int = REPEATS) -> tuple[float, int]:
    """Median wall time in ms, plus the match count, discarding a warm-up."""
    fn()
    samples, total = [], 0
    for _ in range(repeats):
        start = time.perf_counter()
        _rows, total = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), total


def scenarios(index: ArchiveIndex):
    q = index.query_photos
    return [
        ("first page, no filter", lambda: q(limit=60)),
        ("search, rare word", lambda: q(search=RARE_WORD, limit=60)),
        ("search, common word", lambda: q(search=COMMON_WORD, limit=60)),
        ("search, no match", lambda: q(search="zzzznotfound", limit=60)),
        ("creator filter", lambda: q(creator="creator_07", limit=60)),
        ("source filter", lambda: q(source="x", limit=60)),
        ("creator + source", lambda: q(creator="creator_07", source="x", limit=60)),
        ("verdict=reject", lambda: q(verdict="reject", limit=60)),
        ("sort=tier", lambda: q(sort="tier", limit=60)),
        ("sort=newest", lambda: q(sort="newest", limit=60)),
    ]


def creator_scenarios(index: ArchiveIndex):
    """Sidebar rollup: the GROUP BY creator -> GROUP BY creator, source change.

    `legacy` runs the pre-source-filter SQL so the cost of grouping one level
    finer is a measured delta rather than an assumption (AGENTS.md rule 13).
    """

    def legacy():
        with index._lock:
            rows = index._conn.execute(
                "SELECT creator, COUNT(*) AS photo_count, MIN(filename) AS cover "
                "FROM photos GROUP BY creator ORDER BY photo_count DESC"
            ).fetchall()
        return rows, len(rows)

    def rollup():
        rows = index._creator_source_rollup()
        return rows, len(rows)

    def listed(source=None, verdicts=True):
        rows = index.list_creators(source=source, with_verdicts=verdicts)
        return rows, len(rows)

    return [
        ("legacy GROUP BY creator (raw sql)", legacy),
        ("new GROUP BY creator, source (raw sql)", rollup),
        ("list_creators(), rollup only", lambda: listed(verdicts=False)),
        ("list_creators(), full (rollup + verdicts)", lambda: listed()),
        ("list_creators(source='x'), full", lambda: listed("x")),
    ]


def group_scenarios(index: ArchiveIndex):
    """C2 carousel grouping, flat vs grouped on identical rows.

    The last entry is what `/api/photos?group=post` actually costs: the grouped
    page *plus* the second read that hydrates the slides the grid never draws.
    """
    q = index.query_photos

    def whole_route():
        reps, total = q(sort="newest", limit=60, group_posts=True)
        extra = sorted({
            rel
            for rep in reps
            for rel in rep["group_members"]
            if rel != rep["rel_path"]
        })
        slides = index.photos_for_rel_paths(extra)
        return reps, len(reps) + len(slides)

    return [
        ("sort=name, flat", lambda: q(sort="name", limit=60)),
        ("sort=name, grouped", lambda: q(sort="name", limit=60, group_posts=True)),
        ("sort=newest, flat", lambda: q(sort="newest", limit=60)),
        ("sort=newest, grouped", lambda: q(sort="newest", limit=60, group_posts=True)),
        ("offset=1200, flat", lambda: q(sort="newest", limit=60, offset=1200)),
        ("offset=1200, grouped",
         lambda: q(sort="newest", limit=60, offset=1200, group_posts=True)),
        ("whole route (grouped + hydrate slides)", whole_route),
    ]


def print_group_plan(index: ArchiveIndex) -> None:
    """EXPLAIN QUERY PLAN for both statements a grouped page issues.

    `query_photos` runs a total *and* a page, and once the page can ride the
    index the total is what dominates — which is only visible if both are here.
    """
    from promptstudio.storage.db import _GROUP_BY_SQL, _GROUP_KEY_SQL

    join = " FROM photos p LEFT JOIN media_verdicts v ON v.rel_path = p.rel_path"
    statements = {
        "total (grouped)":
            f"SELECT COUNT(*) AS c FROM (SELECT 1{join} GROUP BY {_GROUP_BY_SQL})",
        "page, sort=name (grouped)":
            f"SELECT p.*, {_GROUP_KEY_SQL} AS group_key, COUNT(*) AS group_count, "
            "MIN(p.rel_path) AS group_rep, "
            f"GROUP_CONCAT(p.rel_path, char(10)) AS group_members{join} "
            f"GROUP BY {_GROUP_BY_SQL} ORDER BY {_GROUP_BY_SQL} LIMIT 60 OFFSET 0",
        "page, sort=newest (grouped)":
            f"SELECT p.*, COUNT(*) AS group_count, MIN(p.rel_path) AS group_rep"
            f"{join} GROUP BY {_GROUP_BY_SQL} "
            "ORDER BY IFNULL(p.added_at, p.mtime) DESC, p.filename ASC "
            "LIMIT 60 OFFSET 0",
    }
    print("\n```")
    for label, sql in statements.items():
        print(f"-- {label}")
        with index._lock:
            for row in index._conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall():
                print("   ", row["detail"])
    print("```")


def run(rows: int, *, with_caption: bool) -> None:
    index = ArchiveIndex.get()
    index.ensure_ready()
    with index._lock:
        index._conn.execute("DELETE FROM photos")
        index._conn.execute("DELETE FROM media_verdicts")
        index._conn.commit()

    blob_bytes = seed(index, rows, with_caption=with_caption)
    label = "prompt + caption" if with_caption else "prompt only"
    print(f"\n### {rows:,} rows — {label} "
          f"(avg blob {blob_bytes // max(rows, 1)} bytes)\n")
    print("| query | matches | median ms |")
    print("|-------|--------:|----------:|")
    for name, fn in scenarios(index):
        ms, matches = time_call(fn)
        print(f"| {name} | {matches:,} | {ms:.1f} |")

    print("\n| creator rollup | creators | median ms |")
    print("|----------------|---------:|----------:|")
    for name, fn in creator_scenarios(index):
        ms, matches = time_call(fn)
        print(f"| {name} | {matches:,} | {ms:.1f} |")

    # Runs last, and mutates post_id — see assign_post_ids().
    posts = assign_post_ids(index, rows)
    print(f"\n| post grouping — {posts:,} posts, "
          f"{rows / posts:.2f} slides each | groups | median ms |")
    print("|---------------|-------:|----------:|")
    for name, fn in group_scenarios(index):
        ms, matches = time_call(fn)
        print(f"| {name} | {matches:,} | {ms:.1f} |")
    print_group_plan(index)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows", type=int, action="append",
        help="Row count to test; repeatable. Default: 4400 and 40000.",
    )
    parser.add_argument(
        "--with-captions", action="store_true",
        help="Append caption-shaped text to every blob (the F1 change).",
    )
    parser.add_argument(
        "--both", action="store_true",
        help="Run with and without captions, so the delta is in one table.",
    )
    args = parser.parse_args()
    row_counts = args.rows or [4400, 40000]

    print(f"archive: {_BENCH_ARCHIVE}")
    try:
        for rows in row_counts:
            if args.both:
                run(rows, with_caption=False)
                run(rows, with_caption=True)
            else:
                run(rows, with_caption=args.with_captions)
    finally:
        ArchiveIndex.get().close()
        shutil.rmtree(_BENCH_ARCHIVE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
