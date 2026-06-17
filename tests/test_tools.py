"""
tests/test_tools.py

Run from the project root with:  pytest tests/

Network-free tests (search filtering + the create_fit_card empty-input guard)
always run. The tests that actually call the LLM are skipped automatically if
GROQ_API_KEY is not set, so the suite still passes offline.
"""

import os
import re

import pytest

from tools import search_listings, suggest_outfit, create_fit_card, price_check
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

needs_llm = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set — skipping LLM-backed tests",
)


# ── search_listings ───────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Impossible query → empty list, no exception (the no-results failure mode).
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_filter():
    # Every result must actually carry the requested size token.
    results = search_listings("tee", size="M", max_price=None)
    assert len(results) > 0
    for item in results:
        tokens = {t for t in re.split(r"[^a-z0-9]+", item["size"].lower()) if t}
        assert "m" in tokens


def test_search_relevance_ranking():
    # Top result for "denim jacket" should be outerwear/denim, not a random top.
    results = search_listings("denim jacket", size=None, max_price=None)
    assert len(results) > 0
    top = results[0]
    assert "denim" in " ".join(top["style_tags"]).lower() or "jacket" in top["title"].lower()


# ── create_fit_card guard (network-free) ──────────────────────────────────────

def test_fitcard_empty_outfit_returns_message():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    msg = create_fit_card("", item)
    assert isinstance(msg, str) and len(msg) > 0
    # It's an informative message, not a caption or a crash.
    assert "outfit" in msg.lower()


def test_fitcard_whitespace_outfit_returns_message():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    msg = create_fit_card("   \n  ", item)
    assert isinstance(msg, str) and "outfit" in msg.lower()


# ── LLM-backed tools ──────────────────────────────────────────────────────────

@needs_llm
def test_suggest_outfit_empty_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str) and len(out) > 20  # general advice, not empty


@needs_llm
def test_suggest_outfit_with_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str) and len(out) > 20


def test_suggest_outfit_bad_item_is_graceful():
    # Missing item should not crash, with or without the LLM.
    out = suggest_outfit(None, get_example_wardrobe())
    assert isinstance(out, str) and len(out) > 0


@needs_llm
def test_fitcard_varies_by_input():
    results = search_listings("jacket", size=None, max_price=200)
    assert len(results) >= 2
    c1 = create_fit_card("pair it with baggy jeans and chunky white sneakers", results[0])
    c2 = create_fit_card("style it over a midi skirt with tall boots", results[1])
    assert c1 != c2  # different inputs → different captions


# ── price_check (Stretch ②, network-free) ─────────────────────────────────────

def test_price_check_keys_and_verdict():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    pa = price_check(item)
    assert {"verdict", "item_price", "median", "n_comparables", "message"} <= set(pa)
    assert pa["verdict"] in {"great deal", "fair", "priced high", "unknown"}


def test_price_check_unknown_when_no_comparables():
    weird = {"id": "x", "price": 20.0, "category": "spacesuit", "style_tags": []}
    assert price_check(weird)["verdict"] == "unknown"


def test_price_check_missing_price_is_unknown():
    assert price_check({"id": "x", "category": "tops"})["verdict"] == "unknown"


def test_price_check_verdicts_against_synthetic_market():
    market = [
        {"id": "a", "price": 100.0, "category": "tops", "style_tags": ["x"]},
        {"id": "b", "price": 100.0, "category": "tops", "style_tags": ["x"]},
        {"id": "c", "price": 100.0, "category": "tops", "style_tags": ["x"]},
    ]
    cheap = {"id": "z", "price": 50.0, "category": "tops", "style_tags": ["x"]}
    pricey = {"id": "z", "price": 150.0, "category": "tops", "style_tags": ["x"]}
    fair = {"id": "z", "price": 100.0, "category": "tops", "style_tags": ["x"]}
    assert price_check(cheap, market)["verdict"] == "great deal"
    assert price_check(pricey, market)["verdict"] == "priced high"
    assert price_check(fair, market)["verdict"] == "fair"
