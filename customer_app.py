from __future__ import annotations

import os
from datetime import date as dt_date

import pandas as pd
import streamlit as st

import dashboard as admin_dash
import gsp_bidding_sim as sim
from ui_utils import inject_brand_css, slot_to_time_window


def _timeline_picker(max_slot: int, max_select: int, key: str) -> list[int]:
    if key not in st.session_state:
        st.session_state[key] = [1]
    selected = set(st.session_state[key])

    st.markdown("**Pick time slots**")
    cols = st.columns(max_slot)

    def toggle(i: int) -> None:
        cur = set(st.session_state[key])
        if i in cur:
            cur.remove(i)
        else:
            if len(cur) >= max_select:
                return
            cur.add(i)
        st.session_state[key] = sorted(cur)

    for i in range(1, max_slot + 1):
        is_on = i in selected
        label = "●" if is_on else "○"
        with cols[i - 1]:
            st.button(label, key=f"{key}_{i}", on_click=toggle, args=(i,))
            st.caption(slot_to_time_window(i))

    return list(st.session_state[key])


def _infer_date_span(base_ads_df: pd.DataFrame | None) -> tuple[dt_date | None, dt_date | None]:
    if base_ads_df is None or base_ads_df.empty:
        return None, None
    col = "ad_code" if "ad_code" in base_ads_df.columns else "ad_id" if "ad_id" in base_ads_df.columns else None
    if not col:
        return None, None
    parsed = base_ads_df[col].astype(str).map(lambda x: sim._parse_ad_code_meta(x)[2])  # type: ignore[attr-defined]
    dates = pd.to_datetime(parsed, format="%m%d%Y", errors="coerce").dropna()
    if dates.empty:
        return None, None
    return dates.min().date(), dates.max().date()


def _expand_dates(start: dt_date, end: dt_date) -> list[dt_date]:
    if end < start:
        start, end = end, start
    return [d.date() for d in pd.date_range(start=start, end=end, freq="D")]


def main() -> None:
    st.set_page_config(page_title="Customer Campaign", layout="wide")
    inject_brand_css(mode="customer")

    st.markdown('<div class="accent" style="font-weight:800; letter-spacing:0.02em;">CAMPAIGN</div>', unsafe_allow_html=True)
    st.title("Create your campaign")
    st.caption("Choose where and when you want to show ads. We’ll recommend a budget and generate the input file for ranking.")

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(repo_dir, "data", "input")

    with st.expander("Base dataset (optional)", expanded=False):
        base_ads_df, status = admin_dash._load_inputs_ui(input_dir)
        st.caption(status)

    min_d, max_d = _infer_date_span(base_ads_df) if "base_ads_df" in locals() else (None, None)

    st.markdown("### 1) Targeting")
    c1, c2, c3 = st.columns([1.2, 1.2, 1.2])
    with c1:
        merchant_id = st.text_input("merchant_id", value="M001")
    with c2:
        zipcode = st.text_input("zipcode", value="98101")
    with c3:
        mode = st.radio("date mode", ["Single day", "Date range"], horizontal=True)

    if mode == "Single day":
        d = st.date_input("date", value=min_d or dt_date.today(), min_value=min_d, max_value=max_d)
        dates = [d]
    else:
        start_default = min_d or dt_date.today()
        end_default = max_d or start_default
        rng = st.date_input("date range (inclusive)", value=(start_default, end_default), min_value=min_d, max_value=max_d)
        if isinstance(rng, tuple) and len(rng) == 2:
            dates = _expand_dates(rng[0], rng[1])
        else:
            dates = [rng] if isinstance(rng, dt_date) else [start_default]

    st.markdown("### 2) Time")
    slots = _timeline_picker(max_slot=8, max_select=5, key="cust_slots")

    st.markdown("### 3) Budget")
    rec_hint = ""
    if base_ads_df is not None and not base_ads_df.empty:
        import bidding_agent as ba

        rec_total, median_thr, n_units = ba.recommend_budget_from_base(
            base_ads_df,
            zipcodes=[zipcode],
            dates=dates,
            slots=slots,
            default_floor=10.0,
        )
        rec_hint = f"Recommended ≈ ${rec_total:,.2f} (median threshold ${median_thr:.2f} × {n_units} units)"
        if st.button("Use recommended budget", type="primary"):
            st.session_state.cust_budget = float(rec_total)

    default_budget = float(st.session_state.get("cust_budget", 200.0))
    budget = st.number_input("total budget (USD)", min_value=0.0, value=default_budget, step=10.0, help=rec_hint or None)

    st.markdown("### 4) Generate + export")
    if st.button("Generate ads_input CSV", type="primary"):
        if not merchant_id.strip():
            st.error("merchant_id is required.")
            st.stop()
        if not zipcode.strip().isdigit():
            st.error("zipcode must be digits only.")
            st.stop()
        if not (1 <= len(slots) <= 5):
            st.error("Select 1–5 time slots.")
            st.stop()

        # Use the repo bidding agent generator
        import bidding_agent as ba

        long_df = ba.generate_ads_long(
            base_ads_df=base_ads_df,
            merchant_id=merchant_id.strip(),
            zipcode=zipcode.strip(),
            dates=dates,
            slots=slots,
            total_budget_usd=float(budget),
            ad_base=None,
        )
        wide_df = ba.long_to_wide_1000_format(long_df)
        st.success("Generated input rows.")
        st.dataframe(wide_df, use_container_width=True)
        st.download_button(
            "Download ads_input.csv",
            data=wide_df.to_csv(index=False).encode("utf-8"),
            file_name="ads_input_customer.csv",
            mime="text/csv",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()


