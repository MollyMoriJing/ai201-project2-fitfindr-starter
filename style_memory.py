"""
style_memory.py  (Stretch ③ — cross-session style memory)

Persists a small style profile to disk so the agent remembers a user's
preferences across separate sessions: their usual size, the styles they keep
gravitating toward, the platforms they buy from, and how many times they've
visited.

The profile is a plain JSON file next to this module. `run_agent` loads it at the
start of an interaction and saves an updated copy at the end.
"""

import json
import os

_PROFILE_PATH = os.path.join(os.path.dirname(__file__), "style_profile.json")


def _default_profile() -> dict:
    return {
        "usual_size": None,        # most frequently requested size
        "size_counts": {},         # {size: times requested}
        "favorite_styles": {},     # {style_tag: times seen in a pick}
        "platforms": {},           # {platform: times}
        "visits": 0,               # completed interactions
        "last_finds": [],          # most recent selected item titles (max 5)
    }


def load_profile(path: str = _PROFILE_PATH) -> dict:
    """Load the saved profile, or a fresh default if none exists / is unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge onto defaults so older/partial files still work.
        profile = _default_profile()
        profile.update({k: v for k, v in data.items() if k in profile})
        return profile
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _default_profile()


def save_profile(profile: dict, path: str = _PROFILE_PATH) -> None:
    """Persist the profile to disk. Failures are swallowed (memory is best-effort)."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
    except OSError:
        pass


def update_profile(profile: dict, size: str | None, selected_item: dict) -> dict:
    """Return an updated copy of the profile after a successful interaction.

    Args:
        profile:       the current profile dict.
        size:          the size the USER explicitly asked for (None if they
                       didn't specify, or it was auto-filled from memory — so we
                       don't reinforce our own guesses).
        selected_item: the listing the agent chose.
    """
    p = _default_profile()
    p.update(profile or {})
    # ensure nested dicts are real copies we can mutate
    p["size_counts"] = dict(p.get("size_counts") or {})
    p["favorite_styles"] = dict(p.get("favorite_styles") or {})
    p["platforms"] = dict(p.get("platforms") or {})
    p["last_finds"] = list(p.get("last_finds") or [])

    p["visits"] = int(p.get("visits", 0)) + 1

    if size:
        p["size_counts"][size] = p["size_counts"].get(size, 0) + 1
        # usual_size = most-requested size
        p["usual_size"] = max(p["size_counts"], key=p["size_counts"].get)

    for tag in (selected_item or {}).get("style_tags", []) or []:
        p["favorite_styles"][tag] = p["favorite_styles"].get(tag, 0) + 1

    platform = (selected_item or {}).get("platform")
    if platform:
        p["platforms"][platform] = p["platforms"].get(platform, 0) + 1

    title = (selected_item or {}).get("title")
    if title:
        p["last_finds"] = [title] + [t for t in p["last_finds"] if t != title]
        p["last_finds"] = p["last_finds"][:5]

    return p


def profile_summary(profile: dict) -> str:
    """A short human-readable summary of what the agent remembers."""
    if not profile or not profile.get("visits"):
        return "🧠 No saved preferences yet — I'll start learning your style from this visit."
    parts = []
    if profile.get("usual_size"):
        parts.append(f"usual size {profile['usual_size']}")
    favs = sorted((profile.get("favorite_styles") or {}).items(), key=lambda kv: -kv[1])
    if favs:
        parts.append("you like " + ", ".join(tag for tag, _ in favs[:3]))
    plats = sorted((profile.get("platforms") or {}).items(), key=lambda kv: -kv[1])
    if plats:
        parts.append(f"mostly on {plats[0][0]}")
    visits = profile.get("visits", 0)
    tail = f"{visits} visit{'s' if visits != 1 else ''}"
    return "🧠 I remember: " + " · ".join(parts) + f" ({tail})." if parts else f"🧠 {tail} so far."
