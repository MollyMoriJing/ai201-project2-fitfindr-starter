# FitFindr — planning.md

> I filled this in before writing any of the implementation. It also doubled as my
> prompt material: for each tool I pasted its block below into Claude, and for the
> loop I pasted the diagram plus the Planning Loop and State sections.

---

## Tools

Three required tools, plus one stretch tool I added later (price_check). The
signatures here match what's actually in `tools.py`.

### Tool 1: search_listings

**What it does:**
This is the search step. I give it keywords (and optionally a size and a price cap)
and it goes through the 40 mock listings, keeps the ones that fit, and ranks them so
the most relevant one comes back first.

**Input parameters:**
- `description` (str): the keywords, e.g. `"vintage graphic tee"`. I split this into
  words and drop stop-words and bare numbers before matching.
- `size` (str | None): a size to filter on, e.g. `"M"` or `"8"`. Matched token-wise
  and case-insensitively, so `"M"` catches `"S/M"` and `"M/L"` but not `"One Size"`.
  `None` skips the size filter.
- `max_price` (float | None): the most I'm willing to pay, inclusive. `None` skips it.

**What it returns:**
A `list[dict]`. Each dict is a full listing with `id, title, description, category,
style_tags (list), size, condition, price (float), colors (list), brand, platform`.
I sort by a relevance score (keyword hits, weighted so style_tags/title count more
than category, which counts more than description/colors/brand), highest first, and
break ties by the cheaper item. Anything that scores 0 gets dropped.

**What happens if it fails or returns nothing:**
It returns `[]` and never throws. The loop sees the empty list, writes a helpful
message into `session["error"]`, and stops there before any LLM tool runs.

---

### Tool 2: suggest_outfit

**What it does:**
Takes the item I picked plus the user's wardrobe and asks the LLM
(Groq `llama-3.3-70b-versatile`) for one or two full outfits that pair the new piece
with things the user already owns.

**Input parameters:**
- `new_item` (dict): the listing the loop chose (the top search result).
- `wardrobe` (dict): shaped `{"items": [ {name, category, colors, style_tags, notes}, ... ]}`.
  Can be empty.
- `profile` (dict | None): optional, added for stretch ③. When the wardrobe is empty,
  remembered favorite styles get folded into the advice. Defaults to `None`.

**What it returns:**
A non-empty `str`. If the wardrobe has items, it names the new piece and real
wardrobe pieces ("pair with your baggy straight-leg jeans and chunky white
sneakers…") and adds a concrete styling tip. If the wardrobe is empty, it gives
general styling advice for the item instead.

**What happens if it fails or returns nothing:**
- Empty wardrobe: switch to the general-advice prompt (this is normal, not an error).
- `new_item` missing or not a dict: return a short "search for an item first" string.
- LLM/network/key error: caught, and I return a templated suggestion built from the
  item's own title, style_tags, and colors so the agent still says something useful.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion into a short, casual caption, the kind of thing you'd
actually post under an OOTD photo. I run it at a high temperature so it reads
differently for different items.

**Input parameters:**
- `outfit` (str): the suggestion string from `suggest_outfit`.
- `new_item` (dict): the chosen listing (for the item name, price, platform).

**What it returns:**
A `str`, 2 to 4 sentences. It mentions the item name, price, and platform once each,
captures the vibe of the outfit, and is supposed to sound like a person wrote it.

**What happens if it fails or returns nothing:**
- `outfit` empty or just whitespace: return a plain error string ("can't write a fit
  card without an outfit to describe…"). No exception.
- LLM/network/key error: caught, return a templated caption from the item fields.

---

### Additional Tools (Stretch ② — implemented)

### Tool 4: price_check

**What it does:**
Guesses whether a listing's price is fair by comparing it to similar listings in the
same category (and narrowing to ones that share a style tag when there are enough).

**Input parameters:**
- `item` (dict): the listing to judge (needs at least `price`, `category`, `id`).
- `listings` (list[dict] | None): the set to compare against. `None` loads all listings.

**What it returns:**
A `dict`: `{"verdict": str, "item_price": float, "median": float | None,
"n_comparables": int, "message": str}`. `verdict` is one of
`"great deal" | "fair" | "priced high" | "unknown"`, and `message` is a line a person
can read, like *"$24 is about average for vintage tops (median $26 across 8
comparable listings)."*

**What happens if it fails or returns nothing:**
If there are fewer than 2 comparables, or the item has no price, it returns
`verdict="unknown"` with a message saying there isn't enough to compare against. It
never throws, and the rest of the flow keeps going.

---

## Planning Loop

**How does the agent decide which tool to call next?**

It's not a fixed "run all the tools" sequence. The real decision is what happens
after the search, and the search itself retries before it gives up.

1. **Parse** the query into `{description, size, max_price}` with regex/heuristics
   (no LLM, so this stays predictable and testable). Stretch ③: if the user didn't
   give a size but I have a saved `usual_size`, I fill it in and note that in
   `session["memory_note"]`.
2. **Search, loosening as needed (stretch ①).** Run
   `search_listings(description, size, max_price)`. If it comes back empty, I drop the
   size filter and try again; if it's still empty, I drop the price filter too. I
   keep track of what I loosened in `session["adjusted"]`, and store whatever I end
   up with in `session["search_results"]`.
3. **The decision: did anything come back at all?**
   - **No** (even after loosening): write a specific message into `session["error"]`
     saying what I looked for and that nothing matched, then return right away.
     `price_check`, `suggest_outfit`, and `create_fit_card` never get called.
   - **Yes:** set `session["selected_item"] = search_results[0]` and keep going.
4. **price_check(selected_item)** (stretch ②), stored in `session["price_assessment"]`.
5. **suggest_outfit(selected_item, wardrobe, profile)**, stored in `session["outfit_suggestion"]`.
6. **create_fit_card(outfit_suggestion, selected_item)**, stored in `session["fit_card"]`.
7. Update the saved profile (size, the item's style tags, the platform) and write it
   to disk (stretch ③), then return the session.

The point is that the path changes with the input. An exact match runs all four
tools. A query that only works after loosening runs all four and also tells the user
what got dropped. A query nothing can satisfy runs only `search_listings` (a few
times) and then stops.

---

## State Management

**How does one tool's output reach the next tool?**

Everything lives in one `session` dict that `_new_session` builds at the start. The
user never re-types anything between steps. Each tool reads what it needs out of the
session and writes its result back in:

| Key | Written by | Read by |
|-----|-----------|---------|
| `query` | entry point | parser |
| `parsed` (`{description, size, max_price}`) | parser (regex/heuristic) | search_listings |
| `search_results` (list[dict]) | search_listings | the empty-check branch |
| `selected_item` (dict) | loop (`= search_results[0]`) | suggest_outfit, create_fit_card |
| `wardrobe` (dict) | entry point | suggest_outfit |
| `outfit_suggestion` (str) | suggest_outfit | create_fit_card |
| `fit_card` (str) | create_fit_card | UI |
| `error` (str \| None) | any failing step | UI / early-return check |
| `adjusted` (str \| None) | retry ladder (①) | UI ("I loosened …") |
| `price_assessment` (dict) | price_check (②) | UI |
| `memory_note` (str \| None) | profile load (③) | UI ("used your usual size …") |
| `profile` (dict) | `style_memory` (③) | suggest_outfit, UI summary |

The item from the search flows straight into styling and captioning as the *same
object*, no re-entry. I checked this directly:
`session["selected_item"] is session["search_results"][0]` is `True`.

**Cross-session state (stretch ③).** On top of the per-run dict, there's a
`style_profile.json` on disk holding `usual_size`, `favorite_styles` (tag counts),
`platforms`, and a `visits` count. `run_agent` loads it at the start and saves the
updated version at the end, so preferences survive between separate runs.

**Why I parse with regex instead of the LLM.** Size and `max_price` come out with
regex (`under/below/max/$N` for price, `size X` for size). For the description I take
the query, strip the price/size phrases and the lead-in filler ("I'm looking for
a…"), and cut it off at wardrobe-context phrases ("I mostly wear…"). Keeping this
deterministic means the loop is easy to unit-test, and I save the LLM for the two
creative tools where it actually earns its keep.

---

## Error Handling

| Tool | Failure mode | What the agent does |
|------|-------------|---------------------|
| search_listings | Nothing matches the query | Returns `[]`. The loop first retries with loosened filters (stretch ①); only if every attempt is still empty does it set `session["error"]` and stop before the other tools. |
| price_check (②) | Too few comparables, or no price | Returns `verdict="unknown"` with a message that there's not enough to compare against. No exception, and the flow keeps going. |
| suggest_outfit | Wardrobe is empty | Notices `items == []` and switches to a general styling-advice prompt instead. Still returns a real, non-empty string. |
| suggest_outfit | LLM/network/key error | Wrapped in `try/except`; falls back to a templated tip built from the item's title/style_tags/colors. |
| create_fit_card | Outfit missing or blank | A guard returns a plain error string instead of calling the LLM or crashing. |
| create_fit_card | LLM/network/key error | `try/except`; falls back to a templated caption from the item's title/price/platform. |

---

## Architecture

```
   user query  +  wardrobe choice
          |
          v
   _parse_query  ->  {description, size, max_price}
          |   (no size given? fill usual_size from the saved profile - ③)
          v
   search_listings(description, size, max_price)  <----------+
          |                                                  |
          |                                       retry: drop size,
     any results?                                 then drop price (①)
          |   no, but a filter is still droppable -----------+
          |
          |  no results and nothing left to loosen     yes, found some
          v                                                  v
   session["error"] = "nothing matched,           selected_item = results[0]
   even loosened" -> return now.                          |
   price_check / suggest_outfit /                         v
   create_fit_card never run.                      price_check(selected_item)   - ②
          |                                                |
          |                                                v
          |                                  suggest_outfit(selected_item, wardrobe, profile)
          |                                                |
          |                                                v
          |                                  create_fit_card(outfit, selected_item)
          |                                                |
          |                                                v
          |                                  update + save style_profile.json   - ③
          +------------------------+-----------------------+
                                   v
                            return session
                                   |
                                   v
        app.py handle_query() maps session -> [🛍️ listing] [👗 outfit] [✨ fit card]
                                              (or the error message in panel 1)

  (the session dict is read/written by every box above - it's the shared state)
```

---

## AI Tool Plan

**Milestone 3 — the individual tools.** I used Claude (Claude Code). For each tool I
pasted its block from the Tools section above (what it does, the exact params and
types, the return shape, the failure mode) plus the listings/wardrobe field lists,
and asked for one function per tool in `tools.py`, reusing `load_listings()` for
search and a shared Groq helper for the two LLM tools. Before trusting any of them I
checked: search filters by all three params and returns `[]` (not an exception) on an
impossible query; suggest_outfit branches on the empty wardrobe; create_fit_card
guards the empty-outfit case and actually varies between different items. The pytest
suite plus a few manual runs confirmed it.

**Milestone 4 — the loop and state.** Again Claude. I gave it the Architecture
diagram plus the Planning Loop and State Management sections and asked for
`run_agent()` and the query parser. My checks: the exact `selected_item` dict that
gets stored is the one passed into `suggest_outfit` (object identity, not a copy),
and the no-results query sets `error` while leaving `fit_card` as `None` (the second
case in `agent.py`'s `__main__`).

---

## Stretch Features (planned before I started each one)

**① Retry with loosened constraints.** Instead of failing the moment
`search_listings` returns `[]`, the loop walks a small ladder: original filters, then
drop `size`, then drop `max_price` too. The first attempt that returns something
wins, and `session["adjusted"]` records a note ("couldn't match the size, so I
dropped the size filter") that the UI shows. Only an all-empty ladder produces an
error. It's a generator, `_relaxation_ladder()`, in `agent.py`.

**② price_check tool.** The fourth tool (spec under Tool 4). The loop runs it on the
selected item right after picking it and stores the dict in
`session["price_assessment"]`; the UI prints the verdict and message. No LLM, just
the dataset.

**③ Cross-session style memory.** `style_memory.py` keeps a `style_profile.json`
(`usual_size`, `favorite_styles` counts, `platforms`, `visits`). `run_agent` loads it
at the start (filling in the size when the query leaves it out) and saves the updated
copy at the end. `suggest_outfit` got an optional `profile` param so empty-wardrobe
advice can lean on remembered styles, and `run_agent` got `profile=None` and
`use_memory=True` so the tests can turn memory off.

**AI plan for the stretch work.** Claude again. I gave it this Stretch section plus
the updated loop/state tables. To verify, I added pytest cases for the ladder
(loosens, then succeeds), price_check (each verdict plus the unknown fallback), and
the profile round-trip (save, load, usual_size auto-fill), all with
`use_memory=False` or temp paths so they don't depend on the real file.

---

## A Complete Interaction (Step by Step)

**Example query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — parse.** `_parse_query` pulls out `description="vintage graphic tee"`,
`size=None`, `max_price=30.0`. The "$30" becomes the price cap, and the "I mostly
wear…" sentence gets cut off so it doesn't pollute the keywords (the wardrobe itself
comes from the wardrobe choice, not the text). Saved to `session["parsed"]`.

**Step 2 — search.** `search_listings("vintage graphic tee", None, 30.0)` scores the
listings on keyword overlap, throws out anything over $30, and returns the matches.
When I wrote this I expected the Y2K butterfly baby tee ($18) and other graphic/vintage
tops to rank near the top. The list isn't empty, so `session["selected_item"] = results[0]`.

**Step 3 — suggest.** `suggest_outfit(selected_item, example_wardrobe)`. The wardrobe
has items, so the LLM combines the tee with named pieces, something like "tuck it
into your baggy straight-leg jeans, throw on the vintage black denim jacket, finish
with the chunky white sneakers." Saved to `session["outfit_suggestion"]`.

**Step 4 — fit card.** `create_fit_card(outfit_suggestion, selected_item)` gives back
a caption like "thrifted this y2k baby tee off depop for $18 and it's already my
favorite 🦋 wearing it with my baggies + chunky sneaks all summer." Saved to
`session["fit_card"]`.

**What the user sees:** three panels, the chosen listing (title, price, platform,
condition, size, style tags, description), the outfit suggestion, and the fit card.
If step 2 had come back empty, they'd instead see one message about what didn't match
and what to loosen, and steps 3 and 4 would never run.
