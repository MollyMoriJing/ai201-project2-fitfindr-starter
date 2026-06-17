"""
app.py

Gradio interface for FitFindr. The layout and wiring are already set up —
your job is to fill in handle_query() so it calls run_agent() and maps
the session results to the three output panels.

Run with:
    python app.py

Then open the localhost URL shown in your terminal (usually http://localhost:7860,
but check your terminal — the port may differ).
"""

import gradio as gr

from agent import run_agent
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── formatting ────────────────────────────────────────────────────────────────

def _format_listing(item: dict, total_found: int, price_assessment: dict | None = None) -> str:
    """Render the selected listing dict (and its price check) into a UI block."""
    price = item.get("price")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "—"
    tags = ", ".join(item.get("style_tags") or []) or "—"
    lead = f"Found {total_found} match{'es' if total_found != 1 else ''} — showing the top one:\n\n"
    block = (
        f"{lead}"
        f"🛍️ {item.get('title', 'Untitled')}\n"
        f"{price_str} · {item.get('platform', '—')} · {item.get('condition', '—')} condition\n"
        f"Size: {item.get('size', '—')}\n"
        f"Brand: {item.get('brand') or '—'}\n"
        f"Style: {tags}\n\n"
        f"{item.get('description', '')}"
    )
    if price_assessment:
        if price_assessment.get("verdict") and price_assessment["verdict"] != "unknown":
            block += f"\n\n💰 Price check [{price_assessment['verdict']}]: {price_assessment['message']}"
        else:
            block += f"\n\n💰 Price check: {price_assessment['message']}"
    return block


# ── query handler ─────────────────────────────────────────────────────────────

def handle_query(user_query: str, wardrobe_choice: str) -> tuple[str, str, str]:
    """
    Called by Gradio when the user submits a query.

    Args:
        user_query:     The text the user typed into the search box.
        wardrobe_choice: Either "Example wardrobe" or "Empty wardrobe (new user)".

    Returns:
        A tuple of three strings:
            (listing_text, outfit_suggestion, fit_card)
        Each string maps to one of the three output panels in the UI.

    TODO:
        1. Guard against an empty query (return early with an error message).
        2. Select the wardrobe based on wardrobe_choice.
        3. Call run_agent() with the query and selected wardrobe.
        4. If session["error"] is set, return the error in the first panel
           and empty strings for the other two.
        5. Otherwise, format session["selected_item"] into a readable listing_text
           string and return it along with session["outfit_suggestion"] and
           session["fit_card"].
    """
    # 1. Guard against an empty query.
    if not user_query or not user_query.strip():
        return (
            "⚠️ Tell me what you're looking for — e.g. 'vintage graphic tee under $30, size M'.",
            "",
            "",
        )

    # 2. Pick the wardrobe based on the radio choice.
    wardrobe = (
        get_empty_wardrobe()
        if wardrobe_choice.startswith("Empty")
        else get_example_wardrobe()
    )

    # 3. Run the planning loop.
    session = run_agent(user_query, wardrobe)

    # 4. Error / no-results path → message in panel 1, others empty.
    if session["error"]:
        mem = f"🧠 {session['memory_note']}\n\n" if session.get("memory_note") else ""
        return f"{mem}⚠️ {session['error']}", "", ""

    # 5. Happy path → map the session into the three panels.
    notes = []
    if session.get("memory_note"):
        notes.append(f"🧠 {session['memory_note']}")
    if session.get("adjusted"):
        notes.append(f"🔁 Heads up: I {session['adjusted']}.")
    prefix = ("\n".join(notes) + "\n\n") if notes else ""

    listing_text = prefix + _format_listing(
        session["selected_item"],
        len(session["search_results"]),
        session.get("price_assessment"),
    )
    return listing_text, session["outfit_suggestion"], session["fit_card"]


# ── interface ─────────────────────────────────────────────────────────────────

EXAMPLE_QUERIES = [
    "vintage graphic tee under $30",
    "90s track jacket in size M",
    "flowy midi skirt under $40",
    "black combat boots size 8",
    "designer ballgown size XXS under $5",   # deliberate no-results test
]

def build_interface():
    with gr.Blocks(title="FitFindr") as demo:
        gr.Markdown("""
# FitFindr 🛍️
Find secondhand pieces and get outfit ideas based on your wardrobe.
Describe what you're looking for — include size and price if you want to filter.
        """)

        with gr.Row():
            query_input = gr.Textbox(
                label="What are you looking for?",
                placeholder="e.g. vintage graphic tee under $30, size M",
                lines=2,
                scale=3,
            )
            wardrobe_choice = gr.Radio(
                choices=["Example wardrobe", "Empty wardrobe (new user)"],
                value="Example wardrobe",
                label="Wardrobe",
                scale=1,
            )

        submit_btn = gr.Button("Find it", variant="primary")

        with gr.Row():
            listing_output = gr.Textbox(
                label="🛍️ Top listing found",
                lines=8,
                interactive=False,
            )
            outfit_output = gr.Textbox(
                label="👗 Outfit idea",
                lines=8,
                interactive=False,
            )
            fitcard_output = gr.Textbox(
                label="✨ Your fit card",
                lines=8,
                interactive=False,
            )

        gr.Examples(
            examples=[[q, "Example wardrobe"] for q in EXAMPLE_QUERIES],
            inputs=[query_input, wardrobe_choice],
            label="Try these queries",
        )

        submit_btn.click(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice],
            outputs=[listing_output, outfit_output, fitcard_output],
        )
        query_input.submit(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice],
            outputs=[listing_output, outfit_output, fitcard_output],
        )

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch()
