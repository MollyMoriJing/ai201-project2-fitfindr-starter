# FitFindr 🛍️

FitFindr is a multi-tool agent for thrifting. You describe what you want in plain
language, and it searches the listings, checks whether the price is fair, styles the
best find against your wardrobe, and writes a caption you could actually post. It
decides what to run based on what each step gives back, and it doesn't fall over when
something comes back empty.

---

## Demo



https://github.com/user-attachments/assets/52f8bf9a-3a61-4cdc-b88f-8605437f0da3


---

## Setup & Run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: source .venv/Scripts/activate
pip install -r requirements.txt
```

Put your Groq key in a `.env` at the project root (same free key as Project 1, from
[console.groq.com](https://console.groq.com)). It's git-ignored, so don't commit it.

```
GROQ_API_KEY=your_key_here
```

Then:

```bash
pytest tests/          # 26 tests (tools + agent + stretch)
python agent.py        # CLI: happy path + retry + no-results + memory demo
python app.py          # Gradio UI on http://localhost:7860 (check the terminal for the port)
```

---

## Tool Inventory

These signatures match `tools.py` exactly.

### 1. `search_listings(description, size, max_price) -> list[dict]`
| | |
|---|---|
| **Inputs** | `description` (str): keywords, e.g. `"vintage graphic tee"`. `size` (str \| None): e.g. `"M"`, `"8"`, or `None` to skip. `max_price` (float \| None): inclusive cap, or `None` to skip. |
| **Output** | `list[dict]`: matching listings (`id, title, description, category, style_tags, size, condition, price, colors, brand, platform`), ranked best-first. `[]` when nothing matches. |
| **Purpose** | The search step. Filters the 40 listings by size/price, scores the rest by weighted keyword overlap, drops the zero-score ones, and ranks what's left. |

### 2. `suggest_outfit(new_item, wardrobe, profile=None) -> str`
| | |
|---|---|
| **Inputs** | `new_item` (dict): the chosen listing. `wardrobe` (dict): `{"items": [...]}`, possibly empty. `profile` (dict \| None): optional saved prefs (stretch ③), used for empty-wardrobe advice. |
| **Output** | `str`: one or two outfit ideas that name real wardrobe pieces, or general advice if the wardrobe is empty. |
| **Purpose** | The styling step. Asks Groq `llama-3.3-70b-versatile` to build looks out of the new item plus things the user already owns. |

### 3. `create_fit_card(outfit, new_item) -> str`
| | |
|---|---|
| **Inputs** | `outfit` (str): the suggestion from `suggest_outfit`. `new_item` (dict): the chosen listing. |
| **Output** | `str`: a 2–4 sentence OOTD caption that names the item, price, and platform once each. |
| **Purpose** | The sharing step. Runs at temperature 0.95 so different items produce different captions. |

### 4. `price_check(item, listings=None) -> dict`  *(stretch ②)*
| | |
|---|---|
| **Inputs** | `item` (dict): the listing to judge. `listings` (list[dict] \| None): set to compare against, defaults to `load_listings()`. |
| **Output** | `dict`: `{verdict, item_price, median, n_comparables, message}`, where `verdict` is one of `great deal`, `fair`, `priced high`, `unknown`. |
| **Purpose** | Works out whether a price is fair against comparable same-category listings (median based). No LLM, just the data. |

---

## How the Planning Loop Works

`run_agent(query, wardrobe, profile=None, use_memory=True)` in [`agent.py`](agent.py)
isn't a "run every tool in order" pipeline. The real branch is what happens after the
search, and the search retries before it gives up.

1. **Parse** the query into `{description, size, max_price}` (regex/heuristics, no
   LLM). If there's no size but memory has a `usual_size`, fill it in (③). If I can't
   pull a description out at all, set `error` and return.
2. **Search, loosening if needed (①).** Run `search_listings(...)`. If it's empty,
   drop the size filter and retry; if it's still empty, drop the price filter too.
   Record whatever got loosened in `adjusted`.
3. **The branch: did anything come back?**
   - **No** (even loosened): write a specific `error` (it says what I searched for and
     that nothing matched even after loosening) and return right away. `price_check`,
     `suggest_outfit`, and `create_fit_card` never run, and `fit_card` stays `None`.
   - **Yes:** set `selected_item = search_results[0]` and continue.
4. **`price_check(selected_item)`** (②) → `price_assessment`.
5. **`suggest_outfit(selected_item, wardrobe, profile)`** → `outfit_suggestion`.
6. **`create_fit_card(outfit_suggestion, selected_item)`** → `fit_card`.
7. Update and save the profile (③), then return the session.

So the path really does change with the input. An exact match runs all four tools. A
match that only shows up after loosening runs all four and also reports what it
adjusted. A query nothing can satisfy runs only `search_listings` (a few times) and
then stops.

---

## State Management

Everything for one interaction lives in a single `session` dict that `_new_session`
builds. The user never re-types anything between steps. Each tool reads what it needs
out of the session and writes its result back in:

| Key | Written by | Read by |
|-----|-----------|---------|
| `query` | entry point | parser |
| `parsed` (`{description, size, max_price}`) | `_parse_query` | `search_listings` |
| `search_results` (list[dict]) | `search_listings` | the empty-check branch |
| `selected_item` (dict) | loop (`= search_results[0]`) | `suggest_outfit`, `create_fit_card` |
| `wardrobe` (dict) | entry point | `suggest_outfit` |
| `outfit_suggestion` (str) | `suggest_outfit` | `create_fit_card` |
| `fit_card` (str) | `create_fit_card` | UI |
| `error` (str \| None) | any failing step | UI / early-return guard |
| `adjusted` (str \| None) | retry ladder (①) | UI ("I loosened …") |
| `price_assessment` (dict) | `price_check` (②) | UI |
| `memory_note` (str \| None) | profile load (③) | UI ("used your usual size …") |
| `profile` (dict) | `style_memory` (③) | `suggest_outfit`, profile update |

The item the search finds flows into styling and captioning as the *same object*, no
re-entry. I checked it directly:
`session["selected_item"] is session["search_results"][0]` returns `True`.

**Cross-session state (stretch ③).** On top of the per-run dict, `style_memory.py`
keeps a `style_profile.json` with `usual_size`, `favorite_styles` counts, `platforms`,
and `visits`. `run_agent` loads it at the start (filling in the size when it's left
out) and saves the updated copy at the end, so preferences carry between separate
runs. That file is git-ignored, and the tests pass `use_memory=False` or temp paths
so they stay deterministic.

---

## Error Handling

Each tool handles its own failure. Nothing fails silently and nothing crashes the
agent.

| Tool | Failure mode | What the agent does |
|------|-------------|---------------------|
| `search_listings` | No match | Returns `[]`. The loop retries with loosened filters (①) and only sets a specific `error` if every attempt is empty, stopping before the other tools. |
| `price_check` (②) | Too few comparables, or no price | Returns `verdict="unknown"` with a message explaining there isn't enough to compare against. No exception, flow continues. |
| `suggest_outfit` | Empty wardrobe | Switches to a general styling-advice prompt and still returns a useful string. |
| `suggest_outfit` | LLM/network/key error | `try/except` falls back to a templated tip from the item's title/tags/colors. |
| `create_fit_card` | Outfit missing or blank | A guard returns a plain error string. No LLM call, no exception. |
| `create_fit_card` | LLM/network/key error | `try/except` falls back to a templated caption from the item's title/price/platform. |

**A real example from testing.** The impossible query, run through the whole agent:

```
$ python agent.py
=== No-results path ===
⚠️  No listings matched "designer ballgown", size XXS, under $5, even after
    dropping the size and price filters. Try broader keywords (e.g. 'denim jacket'
    instead of 'vintage cropped denim jacket').
   (selected_item=None, fit_card=None)
```

`search_listings` returned `[]` on every attempt, the loop set `error`, and
`selected_item`, `outfit_suggestion`, and `fit_card` all stayed `None`, so
`suggest_outfit` never ran. And the caption guard, hit directly:

```
>>> create_fit_card("", item)
"⚠️ I can't write a fit card without an outfit to describe. Run suggest_outfit
 first so there's an actual look to caption."
```

A string, not a traceback.

---

## Stretch Features

**① Retry with loosened constraints.** Rather than failing the second
`search_listings` returns `[]`, `run_agent` walks a small ladder (`_relaxation_ladder`):
original filters, then drop `size`, then drop `max_price`. The first attempt that
finds something wins, and `session["adjusted"]` saves a note so the UI can explain
it. From testing, with `"vintage graphic tee under $30 size XXS"` (there's no XXS in
the data):

```
🔁 Adjusted: couldn't match that size, so I searched without the size filter
Found: Graphic Tee — 2003 Tour Bootleg Style — $24
```

**② price_check tool.** The fourth tool (see the inventory). The loop runs it right
after picking the item, and the verdict shows up in the listing panel:

```
💰 Price check [priced high]: $24 is priced on the high side for tops
   (median $19.5 across 12 comparable listings).
```

**③ Cross-session style memory.** `style_memory.py` keeps a `style_profile.json`.
Visit 1 with `"…in size M"` teaches the size; visit 2 with no size fills it in
automatically:

```
Visit 2 → Used your usual size M from past visits (name a size to override).
🧠 I remember: usual size M · you like vintage, 90s, athletic · mostly on poshmark (2 visits).
```

For this I added `profile=None` and `use_memory=True` to `run_agent` (so tests can
turn memory off) and an optional `profile` to `suggest_outfit`. The three required
tool interfaces didn't change.

---

## Interaction Walkthrough

**Query:** `"vintage graphic tee under $30"`, with the Example wardrobe.

**Step 1, `_parse_query`** → `{"description": "vintage graphic tee", "size": None, "max_price": 30.0}`.

**Step 2, `search_listings("vintage graphic tee", None, 30.0)`** → 20 matches under
$30, ranked. Top one: **Graphic Tee — 2003 Tour Bootleg Style, $24, depop, good
condition** (it hits all three keywords in its title and tags). The list isn't empty,
so `selected_item = results[0]`. (`price_check` then flags this one as priced high vs
other tops.)

**Step 3, `suggest_outfit(selected_item, example_wardrobe)`** →
> "…pair it with the Baggy straight-leg jeans and Black combat boots for a
> grunge-inspired look. Tuck the graphic tee into the jeans… layer the Oversized
> grey crewneck over the top…"

**Step 4, `create_fit_card(outfit, selected_item)`** →
> "I'm obsessed with this Graphic Tee — 2003 Tour Bootleg Style I picked up on Depop
> for $24. Paired it with my fave baggy jeans and combat boots for a grunge vibe
> that's giving early-2000s nostalgia 🤘…"

**What the user sees:** three panels, the listing, the outfit idea, and the fit card.
If step 2 had returned `[]`, they'd get one message about what didn't match and what
to loosen instead, and steps 3 and 4 would never run.

---

## Spec Reflection

**Where planning.md helped.** Writing out each tool's *failure mode* before any code
meant the error handling was part of the design instead of something I bolted on
after. "create_fit_card empty outfit → plain error string, never raise" was literally
the first line I wrote in that function, and the matching test
(`test_fitcard_empty_outfit_returns_message`) basically wrote itself. That same
table turned into the Error Handling section here almost word for word.

**Where I diverged.** In planning.md I'd guessed the top result for the graphic-tee
query would be the Y2K butterfly baby tee ($18). In practice the agent picks the 2003
bootleg graphic tee ($24), because my relevance score leans hard on exact
`style_tags` and `title` hits, and the bootleg tee matches "graphic", "tee", and
"vintage" in both, which outscores the butterfly tee (its title is just "Y2K Baby
Tee"). I left the scoring alone, since ranking the most clearly-relevant item above
the cheapest is the behavior I actually want. I updated my expectation instead of the
code.

---

## AI Usage

**1, the three tools (Milestone 3).** I gave Claude each tool's block from
planning.md (inputs, return shape, failure mode) plus the listings/wardrobe field
lists, and had it implement them in `tools.py` reusing `load_listings()` and a shared
Groq helper. What I changed: the first draft matched sizes with a plain
case-insensitive substring, which made `size="S"` match `"One Size"`. I swapped it for
token-subset matching, so `"M"` still catches `"S/M"` but `"S"` no longer matches
`"One Size"`. I also added the `try/except` LLM fallbacks (a templated tip and
caption) that weren't in the draft, and bumped `create_fit_card`'s temperature to
0.95 after the first version kept producing near-identical captions.

**2, the planning loop (Milestone 4).** I handed Claude the architecture diagram plus
the Planning Loop and State Management sections and asked for `run_agent()` and the
parser. What I changed: the draft parser dumped the whole query into the search
description, so the verbose example ("…I mostly wear baggy jeans and chunky
sneakers") leaked into the keywords. I added a cut at wardrobe-context phrases so the
description comes out as just `"vintage graphic tee"`, and I confirmed the loop stores
`search_results[0]` itself rather than a copy, so `selected_item` is the same object
that flows on to the next two tools.

**3, the stretch features (①②③).** I gave Claude the Stretch section of planning.md
and asked for the relaxation ladder, `price_check`, and `style_memory.py`. What I
changed: (a) profile updates were reinforcing the agent's *own* auto-filled sizes, so
I made `run_agent` record only the size the user actually typed (`explicit_size`); (b)
I had `price_check` narrow its comparables to listings that share a style tag, but
only when there are at least 3, otherwise fall back to the whole category; (c) I added
the `use_memory` flag and temp-path tests so the saved profile never makes the suite
flaky.

---

## Project Layout

```
tools.py            # the 4 tools (search / suggest / fit_card + price_check) + Groq helper
agent.py            # run_agent() planning loop + _parse_query() + _relaxation_ladder()
style_memory.py     # stretch ③: load/save/update the persistent style profile
app.py              # Gradio UI + handle_query()
tests/test_tools.py # tool tests (search filters, failure guards, price_check, LLM)
tests/test_agent.py # planning loop + stretch (retry ladder, memory round-trip)
data/               # listings.json (40 items) + wardrobe_schema.json
utils/data_loader.py
planning.md         # the spec, written before the code (stretch specs included)
```
