from __future__ import annotations

from datetime import time

import streamlit as st


def slot_to_time_window(
    slot: int,
    *,
    day_start_hour: int = 6,
    hours_per_slot: int = 2,
    n_slots: int = 8,
) -> str:
    """
    Map time_slot 1..8 to real time windows:
      1 -> 06:00–08:00
      ...
      8 -> 20:00–22:00
    For slots outside 1..n_slots, returns an empty string.
    """
    if slot < 1 or slot > n_slots:
        return ""
    start_hour = day_start_hour + hours_per_slot * (slot - 1)
    end_hour = start_hour + hours_per_slot
    start = time(start_hour, 0).strftime("%H:%M")
    end = time(end_hour, 0).strftime("%H:%M")
    return f"{start}–{end}"


def format_slot_label(slot: int) -> str:
    w = slot_to_time_window(int(slot))
    return f"Slot {int(slot)} ({w})" if w else f"Slot {int(slot)}"


def inject_brand_css(*, mode: str) -> None:
    """
    mode:
      - 'customer': dark + pink
      - 'tmobile': dark + magenta accents (same palette)
      - 'admin': neutral
    """
    if mode in {"customer", "tmobile"}:
        bg = "#0B0B10"
        panel = "#121220"
        panel2 = "#17172A"
        text = "#EDEDF7"
        muted = "#A6A6C7"
        accent = "#FF2D8D"  # signature pink
        border = "rgba(255,255,255,0.08)"
    else:
        bg = "#FFFFFF"
        panel = "#FFFFFF"
        panel2 = "#F7F7FB"
        text = "#111827"
        muted = "#6B7280"
        accent = "#FF2D8D"
        border = "rgba(17,24,39,0.12)"

    st.markdown(
        f"""
<style>
/* App background */
.stApp {{
  background: {bg};
  color: {text};
}}
/* Reduce default padding a bit */
.block-container {{
  padding-top: 2rem;
  padding-bottom: 2.5rem;
}}
/* Headings */
h1,h2,h3,h4,h5,h6 {{ color: {text}; }}
/* Caption / secondary text */
.stCaption, .stMarkdown p, .stMarkdown li {{
  color: {text};
}}
.stMarkdown small, .stCaption {{
  color: {muted} !important;
}}
/* Cards */
.card {{
  background: linear-gradient(180deg, {panel}, {panel2});
  border: 1px solid {border};
  border-radius: 14px;
  padding: 14px 16px;
}}
.card-title {{
  font-weight: 700;
  margin-bottom: 6px;
}}
.card-subtitle {{
  color: {muted};
  font-size: 0.9rem;
}}
.chips {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}}
.chip {{
  border: 1px solid {border};
  background: rgba(255,255,255,0.04);
  color: {text};
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 0.85rem;
}}
/* Accent */
.accent {{
  color: {accent};
}}
/* Buttons */
button[kind="primary"] {{
  border: 1px solid rgba(255,45,141,0.4) !important;
  background: {accent} !important;
  color: #0B0B10 !important;
}}
button {{
  border-radius: 12px !important;
}}
/* Expanders look more like panels */
[data-testid="stExpander"] {{
  border: 1px solid {border};
  border-radius: 14px;
  overflow: hidden;
  background: linear-gradient(180deg, {panel}, {panel2});
}}
</style>
""",
        unsafe_allow_html=True,
    )


def card(title: str, subtitle: str | None = None) -> None:
    sub = f'<div class="card-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="card"><div class="card-title">{title}</div>{sub}</div>',
        unsafe_allow_html=True,
    )


def chips(items: list[str]) -> None:
    if not items:
        st.markdown('<div class="chips"><span class="chip">None</span></div>', unsafe_allow_html=True)
        return
    inner = "".join([f'<span class="chip">{_escape_html(x)}</span>' for x in items])
    st.markdown(f'<div class="chips">{inner}</div>', unsafe_allow_html=True)


def _escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


