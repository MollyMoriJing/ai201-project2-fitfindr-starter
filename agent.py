"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card, price_check
from style_memory import load_profile, save_profile, update_profile


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
        # ── stretch fields ──
        "adjusted": None,            # ① note on which filters the retry loosened
        "price_assessment": None,    # ② dict from price_check
        "memory_note": None,         # ③ note when size was auto-filled from memory
        "profile": None,             # ③ the style profile used / updated this run
    }


# ── query parsing ─────────────────────────────────────────────────────────────

# "under/below/max $30" style price ceilings.
_PRICE_RE = re.compile(
    r"(?:under|below|less than|max(?:imum)?|up to|cheaper than|no more than|<)\s*\$?\s*(\d+(?:\.\d+)?)",
    re.I,
)
_DOLLAR_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
# "size M", "in size US 8" → capture the size token.
_SIZE_RE = re.compile(r"\bsize\s+(?:us\s+)?([a-z0-9]+(?:\.\d+)?(?:/[a-z0-9]+)?)", re.I)
# Phrases that introduce wardrobe context, not the item being searched for.
_WARDROBE_CUT_RE = re.compile(
    r"\bi (?:mostly |usually |normally |often |already )?(?:wear|have|own|wore|like|got)\b"
    r"|\bmy (?:wardrobe|closet|style)\b|\bto (?:go|pair) with\b|\bthat (?:goes|pairs) with\b",
    re.I,
)
# Lead-in filler at the start of a query.
_FILLER_RE = re.compile(
    r"^\s*(?:i(?:'m| am)?\s+looking for|looking for|i want|i need|i'd like|find me|"
    r"find|show me|searching for|search for|got any|do you have)\s+",
    re.I,
)


def _parse_query(query: str) -> dict:
    """Pull a search description, optional size, and optional max_price out of a
    natural-language query using regex + light heuristics (no LLM, so the loop
    stays deterministic and testable).

    Returns:
        {"description": str, "size": str | None, "max_price": float | None}
    """
    q = (query or "").strip()
    low = q.lower()

    # max_price — prefer an explicit "under/below N", fall back to any "$N".
    max_price = None
    m = _PRICE_RE.search(low) or _DOLLAR_RE.search(low)
    if m:
        max_price = float(m.group(1))

    # size — only from an explicit "size X" phrase, to avoid false positives.
    size = None
    sm = _SIZE_RE.search(low)
    if sm:
        size = sm.group(1).upper()

    # description — strip price/size phrases, cut wardrobe context, drop filler.
    desc = _PRICE_RE.sub("", q)
    desc = _DOLLAR_RE.sub("", desc)
    desc = re.sub(r"\bin\s+size\s+(?:us\s+)?\S+", "", desc, flags=re.I)
    desc = re.sub(r"\bsize\s+(?:us\s+)?\S+", "", desc, flags=re.I)
    desc = _WARDROBE_CUT_RE.split(desc)[0]
    desc = _FILLER_RE.sub("", desc)
    desc = re.sub(r"^\s*(?:a|an|some|the)\s+", "", desc, flags=re.I)
    desc = re.sub(r"\s+", " ", desc).strip(" .,!?")

    return {"description": desc, "size": size, "max_price": max_price}


# ── retry ladder (Stretch ①) ──────────────────────────────────────────────────

def _relaxation_ladder(size: str | None, max_price: float | None):
    """Yield (size, max_price, note) tuples from strictest to loosest.

    The note is None for the original attempt, then a human-readable string for
    each loosened attempt. Filters are dropped in priority order: size, then price.
    """
    yield (size, max_price, None)

    cur_size, cur_price, dropped = size, max_price, []
    if cur_size is not None:
        cur_size = None
        dropped.append("size")
        yield (cur_size, cur_price, _relax_note(dropped))
    if cur_price is not None:
        cur_price = None
        dropped.append("price")
        yield (cur_size, cur_price, _relax_note(dropped))


def _relax_note(dropped: list[str]) -> str:
    if dropped == ["size"]:
        return "couldn't match that size, so I searched without the size filter"
    if dropped == ["price"]:
        return "nothing came in under that price, so I removed the price limit"
    if dropped == ["size", "price"]:
        return "couldn't match the size or price, so I dropped both filters"
    return "loosened the filters"


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict, profile: dict | None = None,
              use_memory: bool = True) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    session = _new_session(query, wardrobe)

    # ③ Load the saved style profile (cross-session memory).
    if use_memory and profile is None:
        profile = load_profile()
    session["profile"] = profile

    # Step 1 — parse the query.
    parsed = _parse_query(query)

    # ③ Remember the size the USER actually typed (before any auto-fill), so we
    # don't reinforce our own guesses when we later update the profile.
    explicit_size = parsed["size"]

    # ③ Auto-fill size from memory when the query omits one.
    if parsed["size"] is None and profile and profile.get("usual_size"):
        parsed["size"] = profile["usual_size"]
        session["memory_note"] = (
            f"Used your usual size {profile['usual_size']} from past visits "
            "(name a size to override)."
        )
    session["parsed"] = parsed

    if not parsed["description"]:
        session["error"] = (
            "I couldn't tell what you're shopping for. Try naming the item, "
            "e.g. 'vintage graphic tee under $30, size M'."
        )
        return session

    # Step 2 — ① search with a relaxation ladder: loosen filters before giving up.
    results, adjusted = [], None
    for s, p, note in _relaxation_ladder(parsed["size"], parsed["max_price"]):
        try:
            results = search_listings(parsed["description"], s, p)
        except Exception as exc:  # defensive — search_listings is meant to fail soft
            session["error"] = (
                f"Search hit an unexpected problem ({exc}). Try rephrasing your request."
            )
            return session
        if results:
            adjusted = note
            break
    session["search_results"] = results
    session["adjusted"] = adjusted

    # Step 3 — DECISION POINT: even loosened filters found nothing → stop.
    if not results:
        bits = [f'"{parsed["description"]}"']
        if explicit_size:
            bits.append(f"size {explicit_size}")
        if parsed["max_price"] is not None:
            bits.append(f"under ${parsed['max_price']:g}")
        session["error"] = (
            f"No listings matched {', '.join(bits)}, even after dropping the size "
            "and price filters. Try broader keywords (e.g. 'denim jacket' instead "
            "of 'vintage cropped denim jacket')."
        )
        return session  # price_check / suggest_outfit / create_fit_card never run

    # Step 4 — select the top-ranked listing to carry forward.
    session["selected_item"] = results[0]

    # Step 5 — ② check whether the price is fair vs. comparable listings.
    session["price_assessment"] = price_check(session["selected_item"])

    # Step 6 — suggest an outfit (③ profile personalizes empty-wardrobe advice).
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], wardrobe, profile
    )

    # Step 7 — turn that outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # ③ Update + persist the profile with what we learned this visit.
    if use_memory:
        updated = update_profile(profile or {}, explicit_size, session["selected_item"])
        save_profile(updated)
        session["profile"] = updated

    # Step 8 — done; return the fully-populated session.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe

    def show(title: str, session: dict) -> None:
        print(f"\n=== {title} ===")
        if session["memory_note"]:
            print(f"🧠 {session['memory_note']}")
        if session["error"]:
            print(f"⚠️  {session['error']}")
            print(f"   (selected_item={session['selected_item']}, fit_card={session['fit_card']})")
            return
        if session["adjusted"]:
            print(f"🔁 Adjusted: {session['adjusted']}")
        item = session["selected_item"]
        print(f"Found: {item['title']} — ${item['price']:g}")
        pa = session["price_assessment"]
        print(f"💰 Price check [{pa['verdict']}]: {pa['message']}")
        print(f"👗 Outfit: {session['outfit_suggestion'][:150]}…")
        print(f"✨ Fit card: {session['fit_card'][:150]}…")

    # Happy path (memory off for a clean, repeatable run).
    show("Happy path: graphic tee", run_agent(
        "looking for a vintage graphic tee under $30",
        get_example_wardrobe(), use_memory=False))

    # ① Retry: impossible size + matchable description → loosens, still finds.
    show("Stretch ①: retry loosens the size filter", run_agent(
        "vintage graphic tee under $30 size XXS",
        get_example_wardrobe(), use_memory=False))

    # No-results path (impossible even after loosening every filter).
    show("No-results path", run_agent(
        "designer ballgown size XXS under $5",
        get_example_wardrobe(), use_memory=False))

    # ③ Cross-session memory: visit 1 teaches the size, visit 2 auto-fills it.
    print("\n=== Stretch ③: cross-session memory ===")
    from style_memory import save_profile, load_profile, profile_summary, _default_profile
    save_profile(_default_profile())                                  # reset for a clean demo
    run_agent("90s track jacket in size M", get_example_wardrobe())   # visit 1 (size given)
    s = run_agent("denim jacket", get_example_wardrobe())             # visit 2 (no size)
    print(f"Visit 2 → {s['memory_note']}")
    print(profile_summary(load_profile()))
