from __future__ import annotations

import os
from datetime import date as dt_date

import pandas as pd
import streamlit as st

import dashboard as admin_dash
import gsp_bidding_sim as sim
from ui_utils import inject_brand_css, slot_to_time_window, metric_card, progress_bar


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

    st.markdown(
        """
        <div style="text-align:center;padding:2rem 0 1rem 0;">
            <div class="accent" style="font-size:0.9rem;font-weight:800;letter-spacing:0.15em;margin-bottom:0.5rem;">CAMPAIGN BUILDER</div>
            <h1 style="font-size:2.5rem;margin:0;font-weight:900;">Create Your Campaign</h1>
            <p style="color:#A6A6C7;margin-top:0.5rem;">Choose where and when you want to show ads. We'll recommend a budget and generate the input file.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(repo_dir, "data", "input")

    with st.expander("Base dataset (optional)", expanded=False):
        base_ads_df, status = admin_dash._load_inputs_ui(input_dir)
        st.caption(status)

    min_d, max_d = _infer_date_span(base_ads_df) if "base_ads_df" in locals() else (None, None)

    st.markdown("### 🎯 Step 1: Targeting")
    st.caption("Define who you are and where you want to reach customers.")
    c1, c2, c3 = st.columns([1.2, 1.2, 1.2])
    with c1:
        merchant_id = st.text_input("Merchant ID", value="M001", help="Your unique merchant identifier")
    with c2:
        zipcode = st.text_input("Zipcode", value="98101", help="Target location by zipcode")
    with c3:
        mode = st.radio("Date Mode", ["Single day", "Date range"], horizontal=True)

    if mode == "Single day":
        d = st.date_input("Date", value=min_d or dt_date.today(), min_value=min_d, max_value=max_d)
        dates = [d]
    else:
        start_default = min_d or dt_date.today()
        end_default = max_d or start_default
        rng = st.date_input("Date Range (inclusive)", value=(start_default, end_default), min_value=min_d, max_value=max_d)
        if isinstance(rng, tuple) and len(rng) == 2:
            dates = _expand_dates(rng[0], rng[1])
        else:
            dates = [rng] if isinstance(rng, dt_date) else [start_default]

    st.divider()
    st.markdown("### ⏰ Step 2: Time Slots")
    st.caption("Select up to 5 time slots when your ads should appear. Click to toggle on/off.")
    slots = _timeline_picker(max_slot=8, max_select=5, key="cust_slots")
    
    # Visual summary
    st.markdown(f"**Selected:** {len(slots)} slot(s) × {len(dates)} day(s) = **{len(slots) * len(dates)} ad units**")

    st.divider()
    st.markdown("### 💰 Step 3: Budget")
    st.caption("We'll recommend a competitive budget based on historical bidding data.")
    rec_total = 0.0
    median_thr = 0.0
    n_units = len(slots) * len(dates)
    
    if base_ads_df is not None and not base_ads_df.empty:
        import bidding_agent as ba

        rec_total, median_thr, n_units = ba.recommend_budget_from_base(
            base_ads_df,
            zipcodes=[zipcode],
            dates=dates,
            slots=slots,
            default_floor=10.0,
        )
        
        # Visual recommendation card
        with st.expander("📊 Budget Recommendation", expanded=True):
            col_a, col_b = st.columns(2)
            with col_a:
                metric_card("Recommended Budget", f"${rec_total:,.0f}", icon="💵")
            with col_b:
                metric_card("Median CPM", f"${median_thr:,.2f}", icon="📈")
            st.caption(f"Based on {n_units} ad units (historical median threshold)")
            if st.button("✨ Use Recommended Budget", type="primary", use_container_width=True):
                st.session_state.cust_budget = float(rec_total)
                st.rerun()

    default_budget = float(st.session_state.get("cust_budget", 200.0))
    budget = st.number_input("Your Budget (USD)", min_value=0.0, value=default_budget, step=10.0, help="Total budget across all ad units")
    
    # Budget progress vs recommendation
    if rec_total > 0:
        progress_bar(budget, rec_total, label="Your budget vs recommended", color="#10B981" if budget >= rec_total else "#F59E0B")

    st.divider()
    st.markdown("### 🚀 Step 4: Generate & Export")
    st.caption("Generate your campaign CSV file for auction submission.")
    if st.button("🎨 Generate Campaign CSV", type="primary", use_container_width=True):
        if not merchant_id.strip():
            st.error("❌ Merchant ID is required.")
            st.stop()
        if not zipcode.strip().isdigit():
            st.error("❌ Zipcode must be digits only.")
            st.stop()
        if not (1 <= len(slots) <= 5):
            st.error("❌ Please select 1–5 time slots.")
            st.stop()

        # Use the repo bidding agent generator
        import bidding_agent as ba

        with st.spinner("✨ Generating your campaign..."):
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
        
        st.success("✅ Campaign generated successfully!")
        
        # Summary metrics
        st.markdown("#### Campaign Summary")
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            metric_card("Total Ads", f"{len(wide_df):,d}", icon="📝")
        with sc2:
            metric_card("Budget", f"${budget:,.0f}", icon="💰")
        with sc3:
            metric_card("Avg Bid", f"${budget/n_units:,.2f}" if n_units > 0 else "$0", icon="💵")
        with sc4:
            metric_card("Ad Units", f"{n_units:,d}", icon="📊")
        
        st.markdown("#### Preview")
        st.dataframe(wide_df, use_container_width=True)
        
        st.download_button(
            "📥 Download ads_input.csv",
            data=wide_df.to_csv(index=False).encode("utf-8"),
            file_name="ads_input_customer.csv",
            mime="text/csv",
            use_container_width=True,
            type="primary",
        )


if __name__ == "__main__":
    main()


