"""B2/C1/C3 — hashed embeddings, P(keep), semantic rank, embedding dups."""

from __future__ import annotations

import numpy as np

from promptstudio.prompts.cache import PromptCache
from promptstudio.storage.db import ArchiveIndex
from promptstudio.taste import (
    HASHED_MODEL,
    cosine,
    embed_text,
    embedding_near_dup_groups,
    fit_logistic,
    hashed_embed,
    predict_proba,
    rank_by_query,
    text_blob_for_photo,
    train,
)

PROMPT_KEEP = {
    "positive_prompt": "studio portrait bikini golden hour sharp",
    "visual_tags": ["studio", "bikini"],
    "structured_vision": {
        "clothing": "red bikini",
        "pose": "standing",
        "lighting": "golden hour",
        "background": "white studio seamless",
        "face": "smiling",
        "hair": "long",
        "body": "",
        "expression": "smile",
    },
    "parameters": {"pipeline_version": "v2-structured"},
}

PROMPT_DISC = {
    "positive_prompt": "blurry street snapshot night grain",
    "visual_tags": ["street", "night"],
    "structured_vision": {
        "clothing": "coat",
        "pose": "walking",
        "lighting": "neon night",
        "background": "busy street",
        "face": "",
        "hair": "",
        "body": "",
        "expression": "",
    },
    "parameters": {"pipeline_version": "v2-structured"},
}


def test_hashed_embed_is_deterministic_and_normalised():
    a = hashed_embed("red bikini studio portrait")
    b = hashed_embed("red bikini studio portrait")
    assert a.shape == b.shape
    assert np.allclose(a, b)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5


def test_similar_text_outranks_unrelated():
    q = hashed_embed("red bikini studio")
    keep = hashed_embed("studio portrait red bikini golden hour")
    drop = hashed_embed("blurry street night grain neon")
    assert cosine(q, keep) > cosine(q, drop)


def test_logistic_separates_two_clouds():
    rng = np.random.default_rng(0)
    keep = rng.normal(1.0, 0.1, size=(20, 8)).astype(np.float32)
    disc = rng.normal(-1.0, 0.1, size=(20, 8)).astype(np.float32)
    X = np.vstack([keep, disc])
    y = np.concatenate([np.ones(20), np.zeros(20)]).astype(np.float32)
    w, b = fit_logistic(X, y, steps=80)
    p = predict_proba(X, w, b)
    assert p[:20].mean() > 0.7
    assert p[20:].mean() < 0.3


def test_train_writes_p_keep_and_facets(make_photo):
    index = ArchiveIndex.get()
    keep_paths = []
    disc_paths = []
    for i in range(3):
        rel, _ = make_photo(name=f"keep_{i}.jpg")
        PromptCache().set(rel, dict(PROMPT_KEEP), push_history=False)
        index.set_label(rel, 1)
        keep_paths.append(rel)
    for i in range(3):
        rel, _ = make_photo(name=f"disc_{i}.jpg")
        PromptCache().set(rel, dict(PROMPT_DISC), push_history=False)
        index.set_label(rel, -1)
        disc_paths.append(rel)
    unlabeled, _ = make_photo(name="other.jpg")
    PromptCache().set(unlabeled, dict(PROMPT_KEEP), push_history=False)

    summary = train(index)
    assert summary["embedded"] >= 7
    assert summary["weights"] is True
    assert summary["scored"] >= 7
    assert summary["model"] == HASHED_MODEL

    keep_score = index.query_photos(path=keep_paths[0])[0][0]["p_keep"]
    disc_score = index.query_photos(path=disc_paths[0])[0][0]["p_keep"]
    assert keep_score > disc_score

    photo = index.query_photos(path=keep_paths[0])[0][0]
    assert photo.get("setting") == "studio"
    assert photo.get("outfit") == "red bikini"


def test_semantic_search_ranks_keep_first(make_photo):
    index = ArchiveIndex.get()
    keep, _ = make_photo(name="studio.jpg")
    disc, _ = make_photo(name="street.jpg")
    PromptCache().set(keep, dict(PROMPT_KEEP), push_history=False)
    PromptCache().set(disc, dict(PROMPT_DISC), push_history=False)
    train(index)

    photos, total = index.query_photos(search="studio bikini", search_mode="semantic")
    assert total == 2
    assert photos[0]["rel_path"] == keep


def test_embedding_near_dups_cluster_identical_text(make_photo):
    index = ArchiveIndex.get()
    a, _ = make_photo(name="a.jpg")
    b, _ = make_photo(name="b.jpg")
    c, _ = make_photo(name="c.jpg")
    PromptCache().set(a, dict(PROMPT_KEEP), push_history=False)
    PromptCache().set(b, dict(PROMPT_KEEP), push_history=False)
    PromptCache().set(c, dict(PROMPT_DISC), push_history=False)
    train(index)
    groups = embedding_near_dup_groups(index.all_embeddings())
    members = {frozenset(g) for g in groups}
    assert frozenset([a, b]) in members or any(a in g and b in g for g in groups)
    assert not any(c in g and a in g for g in groups)


def test_foryou_sort_puts_high_p_keep_first(make_photo):
    index = ArchiveIndex.get()
    low, _ = make_photo(name="low.jpg")
    high, _ = make_photo(name="high.jpg")
    index.set_p_keeps([(low, 0.1), (high, 0.9)])
    photos, _ = index.query_photos(sort="foryou")
    assert photos[0]["rel_path"] == high


def test_text_blob_uses_structured_vision():
    blob = text_blob_for_photo(PROMPT_KEEP)
    assert "bikini" in blob
    assert "studio" in blob.lower()


def test_embed_text_default_is_hashed():
    vec, model = embed_text("hello studio")
    assert model == HASHED_MODEL
    assert vec.dtype == np.float32
    ranked = rank_by_query("hello studio", {"a": vec})
    assert ranked[0][0] == "a"
