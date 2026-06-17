"""
tests/test_agent.py — planning loop + stretch features.

Most tests run network-free by using queries that resolve before any LLM call
(the no-results / error branches) or by exercising pure helpers. The one test
that reaches the happy path is guarded by GROQ_API_KEY.
"""

import os

import pytest

from agent import run_agent, _relaxation_ladder, _parse_query
from style_memory import (
    load_profile, save_profile, update_profile, profile_summary, _default_profile,
)
from utils.data_loader import get_example_wardrobe

needs_llm = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set — skipping LLM-backed tests",
)


# ── ① retry / relaxation ladder ───────────────────────────────────────────────

def test_relaxation_ladder_order():
    steps = list(_relaxation_ladder("M", 30.0))
    assert steps[0] == ("M", 30.0, None)          # original attempt first
    assert steps[1][0] is None and steps[1][1] == 30.0   # dropped size
    assert steps[2][0] is None and steps[2][1] is None   # dropped price too


def test_relaxation_ladder_no_filters_is_single_attempt():
    assert list(_relaxation_ladder(None, None)) == [(None, None, None)]


def test_relaxation_ladder_price_only():
    steps = list(_relaxation_ladder(None, 10.0))
    assert steps[0] == (None, 10.0, None)
    assert steps[1] == (None, None, "nothing came in under that price, so I removed the price limit")


def test_no_results_branch_network_free():
    # Impossible even after loosening → error set, downstream tools never run.
    s = run_agent("designer ballgown size XXS under $5", get_example_wardrobe(),
                  use_memory=False)
    assert s["error"] is not None
    assert s["selected_item"] is None
    assert s["outfit_suggestion"] is None
    assert s["fit_card"] is None


@needs_llm
def test_retry_loosens_size_and_finds():
    # XXS doesn't exist, but the description matches → loop drops size and finds.
    s = run_agent("vintage graphic tee under $30 size XXS", get_example_wardrobe(),
                  use_memory=False)
    assert s["error"] is None
    assert s["selected_item"] is not None
    assert s["adjusted"] and "size" in s["adjusted"]


# ── ② price_check inside the loop ─────────────────────────────────────────────

@needs_llm
def test_loop_runs_price_check():
    s = run_agent("vintage graphic tee under $30", get_example_wardrobe(),
                  use_memory=False)
    assert s["price_assessment"] is not None
    assert s["price_assessment"]["verdict"] in {"great deal", "fair", "priced high", "unknown"}


# ── ③ cross-session style memory ──────────────────────────────────────────────

def test_profile_roundtrip(tmp_path):
    path = str(tmp_path / "profile.json")
    prof = _default_profile()
    item = {"style_tags": ["vintage", "denim"], "platform": "depop", "title": "X tee"}
    prof = update_profile(prof, "M", item)
    save_profile(prof, path)
    loaded = load_profile(path)
    assert loaded["usual_size"] == "M"
    assert loaded["favorite_styles"]["vintage"] == 1
    assert loaded["platforms"]["depop"] == 1
    assert loaded["visits"] == 1


def test_update_profile_tracks_usual_size():
    prof = _default_profile()
    item = {"style_tags": [], "platform": "depop", "title": "x"}
    prof = update_profile(prof, "M", item)
    prof = update_profile(prof, "M", item)
    prof = update_profile(prof, "L", item)
    assert prof["usual_size"] == "M"   # M requested twice, L once
    assert prof["visits"] == 3


def test_load_profile_missing_file_returns_default(tmp_path):
    loaded = load_profile(str(tmp_path / "nope.json"))
    assert loaded == _default_profile()


def test_memory_autofills_size_network_free():
    # Explicit profile + use_memory=False (no disk). Impossible description so the
    # run errors before any LLM call — but AFTER the size auto-fill we want to test.
    prof = _default_profile()
    prof["usual_size"] = "M"
    s = run_agent("zzzznonexistentgarment", get_example_wardrobe(),
                  profile=prof, use_memory=False)
    assert s["parsed"]["size"] == "M"
    assert s["memory_note"] and "M" in s["memory_note"]


def test_profile_summary_handles_empty():
    assert "No saved preferences" in profile_summary(_default_profile())
