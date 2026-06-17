"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

# Model used for the two creative tools (suggest_outfit, create_fit_card).
MODEL = "llama-3.3-70b-versatile"


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(system: str, user: str, temperature: float = 0.7, max_tokens: int = 450) -> str:
    """Send a system+user prompt to Groq and return the assistant text.

    Raised exceptions (no key, network error, rate limit) are intentionally
    left to the calling tool to catch, so each tool can apply its own fallback.
    """
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


# ── search helpers ────────────────────────────────────────────────────────────

# Common words that carry no matching signal — dropped from the keyword set.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "in", "of", "to", "my", "i",
    "im", "looking", "want", "need", "under", "below", "size", "please", "find",
    "me", "some", "that", "this", "is", "are", "be", "on", "at", "it", "thats",
    "something", "really", "very", "any", "kind", "sort", "like",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase a string into alphanumeric tokens, dropping stop-words,
    pure numbers, and 1-character tokens."""
    raw = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [t for t in raw if t not in _STOPWORDS and not t.isdigit() and len(t) > 1]


def _size_matches(query_size: str, listing_size: str) -> bool:
    """Case-insensitive, token-wise size match.

    Splits both sides on non-alphanumeric characters and returns True when every
    token of the query appears among the listing's tokens. So "M" matches "S/M"
    and "M/L" but not "One Size", and "8" matches "US 8".
    """
    def toks(s: str) -> set[str]:
        return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}

    q, l = toks(query_size), toks(listing_size)
    return bool(q) and q.issubset(l)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform
    """
    try:
        listings = load_listings()
    except Exception:
        # Data file missing/corrupt — fail soft so the agent can report it.
        return []

    keywords = _tokenize(description)

    scored: list[tuple[int, float, dict]] = []
    for item in listings:
        # 1) hard filters ----------------------------------------------------
        if max_price is not None and item.get("price", 0) > max_price:
            continue
        if size is not None and not _size_matches(size, item.get("size", "")):
            continue

        # 2) relevance score by weighted keyword overlap ---------------------
        title = (item.get("title") or "").lower()
        desc = (item.get("description") or "").lower()
        category = (item.get("category") or "").lower()
        brand = (item.get("brand") or "").lower()
        tags = " ".join(item.get("style_tags") or []).lower()
        colors = " ".join(item.get("colors") or []).lower()

        score = 0
        for kw in keywords:
            if kw in tags:
                score += 3
            if kw in title:
                score += 3
            if kw in category:
                score += 2
            if kw in desc:
                score += 1
            if kw in colors:
                score += 1
            if kw in brand:
                score += 1

        # If the user gave no usable keywords (e.g. "size M under $20"), the
        # filters alone define the match set, so keep every filtered item.
        if not keywords:
            score = 1

        if score > 0:
            scored.append((score, float(item.get("price", 0.0)), item))

    # 3) sort: highest relevance first, cheaper first on ties ----------------
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict, profile: dict | None = None) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.
        profile:  (Stretch ③, optional) a saved style profile. When the wardrobe
                  is empty, remembered favorite styles personalize the advice.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.
    """
    if not new_item or not isinstance(new_item, dict):
        return "I don't have an item to style yet — search for a piece first."

    title = new_item.get("title", "this piece")
    category = new_item.get("category", "item")
    tags = ", ".join(new_item.get("style_tags") or []) or "versatile"
    colors = ", ".join(new_item.get("colors") or []) or "neutral"
    item_line = f'"{title}" — a {category} with a {tags} vibe in {colors}.'

    items = (wardrobe or {}).get("items", []) or []

    if not items:
        # Empty wardrobe → general styling advice, not an error.
        fav = ""
        top = _top_styles(profile, 3)
        if top:
            fav = f" The user has previously gravitated toward {', '.join(top)} styles — lean that way when it fits."
        system = (
            "You are FitFindr, a warm, knowledgeable secondhand-fashion stylist. "
            "The user has not entered a wardrobe yet, so give general styling advice."
        )
        user = (
            f"New thrifted find:\n{item_line}\n\n"
            "Suggest how to style this piece for someone just starting their closet."
            f"{fav} "
            "Describe the kinds of pieces that pair well with it and one or two example "
            "outfits using generic staples (e.g. straight jeans, white sneakers). "
            "Keep it to 3-5 sentences, friendly and concrete."
        )
        temperature = 0.7
    else:
        # Populated wardrobe → combine with specific named pieces.
        closet = "\n".join(
            f"- {w.get('name','(unnamed)')} "
            f"[{w.get('category','?')}; {', '.join(w.get('style_tags') or []) or 'no tags'}]"
            for w in items
        )
        system = (
            "You are FitFindr, a warm, knowledgeable secondhand-fashion stylist. "
            "Build outfits ONLY from the new item plus pieces in the user's wardrobe, "
            "referring to wardrobe pieces by their names."
        )
        user = (
            f"New thrifted find:\n{item_line}\n\n"
            f"The user's wardrobe:\n{closet}\n\n"
            "Suggest 1-2 complete outfits that pair the new item with specific named "
            "wardrobe pieces. For each, name the pieces and add one concrete styling "
            "tip (how to wear/tuck/layer it). Keep it to 4-6 sentences total."
        )
        temperature = 0.7

    try:
        out = _chat(system, user, temperature=temperature, max_tokens=450)
        if out:
            return out
        raise ValueError("empty completion")
    except Exception:
        # LLM/network/key failure → still return something useful.
        base = "general staples in matching tones" if not items else "pieces you already own"
        return (
            f"(Styling assistant is offline, but here's a quick idea.) The {title} has a "
            f"{tags} feel in {colors} — make it the statement piece and build around it with "
            f"{base}. Keep the rest simple so the {category} stands out."
        )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.
    """
    # Guard: no outfit to caption → descriptive error string, never a crash.
    if not outfit or not outfit.strip():
        return (
            "⚠️ I can't write a fit card without an outfit to describe. "
            "Run suggest_outfit first so there's an actual look to caption."
        )

    item = new_item or {}
    title = item.get("title", "this piece")
    price = item.get("price")
    platform = item.get("platform", "a thrift app")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "a steal"

    system = (
        "You write short, authentic outfit captions for thrifted finds — the kind a "
        "real person posts with an OOTD photo. Casual and specific, never a product "
        "description. Light emoji are welcome. Do not use hashtags-only filler."
    )
    user = (
        f"Item: {title}\n"
        f"Price: {price_str}\n"
        f"Platform: {platform}\n"
        f"The outfit:\n{outfit}\n\n"
        "Write a 2-4 sentence caption for this look. Mention the item name, the price, "
        "and the platform once each, naturally. Capture the vibe of the outfit in "
        "specific terms. Sound like a person, not a catalog."
    )

    try:
        # High temperature so captions vary for different inputs.
        card = _chat(system, user, temperature=0.95, max_tokens=200)
        if card:
            return card
        raise ValueError("empty completion")
    except Exception:
        # LLM/network/key failure → templated caption so the user still gets one.
        return (
            f"thrifted this {title} off {platform} for {price_str} ✨ styling it exactly "
            f"how i pictured — obsessed already. full look soon 🤍"
        )


# ── Tool 4: price_check (Stretch ②) ───────────────────────────────────────────

def _top_styles(profile: dict | None, n: int) -> list[str]:
    """Top-n style tags from a saved profile's favorite_styles counter."""
    if not profile:
        return []
    counts = profile.get("favorite_styles") or {}
    return [tag for tag, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:n]]


def _median(values: list[float]) -> float:
    """Median of a non-empty list of numbers."""
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def price_check(item: dict, listings: list[dict] | None = None) -> dict:
    """
    Estimate whether a listing's price is fair versus comparable listings.

    Args:
        item:     The listing dict to evaluate (needs price, category, id).
        listings: The market to compare against; None → load_listings().

    Returns:
        dict with keys:
            verdict (str)        — "great deal" | "fair" | "priced high" | "unknown"
            item_price (float)
            median (float | None)
            n_comparables (int)
            message (str)        — a human-readable one-liner.
        Returns verdict="unknown" (never raises) when there isn't enough data.
    """
    price = item.get("price") if isinstance(item, dict) else None
    if not isinstance(price, (int, float)):
        return {
            "verdict": "unknown", "item_price": None, "median": None,
            "n_comparables": 0,
            "message": "Couldn't read this item's price, so I can't compare it.",
        }

    try:
        market = listings if listings is not None else load_listings()
    except Exception:
        market = []

    category = item.get("category")
    item_id = item.get("id")
    item_tags = set(item.get("style_tags") or [])

    # Comparables: same category, excluding the item itself.
    same_cat = [
        l for l in market
        if l.get("category") == category
        and l.get("id") != item_id
        and isinstance(l.get("price"), (int, float))
    ]
    # Tighten to listings that share a style tag, if enough of them exist.
    tagged = [l for l in same_cat if item_tags & set(l.get("style_tags") or [])]
    comparables = tagged if len(tagged) >= 3 else same_cat

    if len(comparables) < 2:
        return {
            "verdict": "unknown", "item_price": float(price), "median": None,
            "n_comparables": len(comparables),
            "message": (
                f"Only {len(comparables)} comparable listing(s) in {category or 'this category'} — "
                "not enough to judge the price confidently."
            ),
        }

    prices = [float(l["price"]) for l in comparables]
    med = _median(prices)
    cat_label = category or "similar items"

    if price <= med * 0.85:
        verdict = "great deal"
        verdict_phrase = "a great deal — below the typical price"
    elif price >= med * 1.15:
        verdict = "priced high"
        verdict_phrase = "priced on the high side"
    else:
        verdict = "fair"
        verdict_phrase = "about average"

    message = (
        f"${price:g} is {verdict_phrase} for {cat_label} "
        f"(median ${med:g} across {len(comparables)} comparable listings)."
    )
    return {
        "verdict": verdict, "item_price": float(price), "median": med,
        "n_comparables": len(comparables), "message": message,
    }
