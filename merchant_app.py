from __future__ import annotations

import io
import os
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st

import gsp_bidding_sim as sim
from ui_utils import format_slot_label, slot_to_time_window


PREFERRED_SLOTS = sim.PREFERRED_SLOTS


def _find_local_ads_csvs(base_dir: str) -> list[str]:
    try:
        files = os.listdir(base_dir)
    except OSError:
        return []
    return sorted([f for f in files if f.lower().endswith(".csv") and f.startswith("ads_")])


def _load_ads_df(base_dir: str) -> Optional[pd.DataFrame]:
    st.sidebar.header("Input")
    source = st.sidebar.radio("Ads source", ["Pick local file", "Upload CSV"], index=0)

    if source == "Pick local file":
        local_files = _find_local_ads_csvs(base_dir)
        if not local_files:
            st.sidebar.warning("No local ads_*.csv found in this folder. Use Upload CSV instead.")
            return None
        picked = st.sidebar.selectbox("ads CSV", local_files)
        try:
            return sim.load_ads_csv(os.path.join(base_dir, picked))
        except Exception as e:
            st.sidebar.error(f"Failed to load {picked}: {e}")
            return None

    uploaded = st.sidebar.file_uploader("Upload ads CSV", type=["csv"])
    if uploaded is None:
        return None
    try:
        raw = uploaded.getvalue()
        return sim.load_ads_csv(io.BytesIO(raw))
    except Exception as e:
        st.sidebar.error(f"Failed to load upload: {e}")
        return None


def _run(ads_df: pd.DataFrame, reserve_cpm: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    bids_df = sim.ads_to_bids_long(ads_df)
    auction_df = sim.run_gsp_auctions(bids_df, reserve_price=reserve_cpm)
    merchant_df, ad_df = sim.summarize(ads_df, auction_df)
    return auction_df, merchant_df, ad_df


def _merchant_kpis(auction_df: pd.DataFrame, merchant_id: str) -> dict[str, float]:
    m = auction_df[auction_df["merchant_id"] == merchant_id] if not auction_df.empty else auction_df
    spend = float(m["cost_usd"].sum()) if not m.empty else 0.0
    imps = int(m["impressions"].sum()) if not m.empty else 0
    wins = int(len(m)) if not m.empty else 0
    pay_col = "pay_usd" if "pay_usd" in m.columns else "pay_cpm" if "pay_cpm" in m.columns else None
    avg_pay = float(m[pay_col].mean()) if (not m.empty and pay_col) else 0.0
    return {"spend": spend, "imps": imps, "wins": wins, "avg_pay": avg_pay, "pay_col": pay_col or "pay"}


def _kpi_row(kpis: dict[str, float], title: str) -> None:
    st.markdown(f"**{title}**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spend (USD)", f"{kpis['spend']:,.2f}")
    c2.metric("Impressions", f"{int(kpis['imps']):,d}")
    c3.metric("Wins (positions)", f"{int(kpis['wins']):,d}")
    c4.metric("Avg pay", f"{kpis['avg_pay']:,.2f}")


def _merchant_editor(ads_df: pd.DataFrame, merchant_id: str) -> pd.DataFrame:
    merchant_ads_df = ads_df[ads_df["merchant_id"] == merchant_id].copy()
    bid_prefix = "bid_usd" if any(str(c).startswith("bid_usd_") for c in merchant_ads_df.columns) else "bid_cpm"
    rows = []
    for _, r in merchant_ads_df.iterrows():
        for i in range(1, PREFERRED_SLOTS + 1):
            slot_val = r.get(f"preferred_slot_{i}")
            bid_val = r.get(f"{bid_prefix}_{i}")
            rows.append(
                {
                    "ad_code": r["ad_code"],
                    "pref_idx": i,
                    "time_slot": (int(slot_val) if pd.notna(slot_val) else pd.NA),
                    "bid": (float(bid_val) if pd.notna(bid_val) else pd.NA),
                }
            )
    long_df = pd.DataFrame(rows).sort_values(["ad_code", "pref_idx"]).reset_index(drop=True)

    st.markdown("**What-if editor (only affects your merchant)**")
    # Infer slot range from this merchant's filled preferences (fallback: sim.NUM_SLOTS)
    max_slot = sim.NUM_SLOTS
    if not long_df.empty:
        try:
            max_slot = int(max(pd.to_numeric(long_df["time_slot"], errors="coerce").max(), 1))
        except Exception:
            max_slot = sim.NUM_SLOTS

    edited = st.data_editor(
        long_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ad_code": st.column_config.TextColumn("ad_code", disabled=True),
            "pref_idx": st.column_config.NumberColumn("pref_idx", disabled=True),
            "time_slot": st.column_config.NumberColumn("time_slot", min_value=1, max_value=max_slot, step=1),
            "bid": st.column_config.NumberColumn(bid_prefix, min_value=0.0, step=0.1),
        },
    )
    edited["time_slot"] = pd.to_numeric(edited["time_slot"], errors="coerce").astype("Int64")
    edited["bid"] = pd.to_numeric(edited["bid"], errors="coerce")
    edited.attrs["bid_prefix"] = bid_prefix
    return edited


def _apply_merchant_edits(ads_df: pd.DataFrame, merchant_id: str, edited_long_df: pd.DataFrame) -> pd.DataFrame:
    out = ads_df.copy()
    m_mask = out["merchant_id"] == merchant_id
    bid_prefix = edited_long_df.attrs.get("bid_prefix") if hasattr(edited_long_df, "attrs") else None
    if not bid_prefix:
        bid_prefix = "bid_usd" if any(str(c).startswith("bid_usd_") for c in out.columns) else "bid_cpm"
    for ad_code, ad_group in edited_long_df.groupby("ad_code"):
        ad_group = ad_group.sort_values("pref_idx")
        for _, row in ad_group.iterrows():
            i = int(row["pref_idx"])
            slot_val = row.get("time_slot")
            bid_val = row.get("bid")
            if pd.isna(slot_val) and pd.isna(bid_val):
                out.loc[m_mask & (out["ad_code"] == ad_code), f"preferred_slot_{i}"] = pd.NA
                out.loc[m_mask & (out["ad_code"] == ad_code), f"{bid_prefix}_{i}"] = pd.NA
                continue
            if pd.isna(slot_val) or pd.isna(bid_val):
                continue
            out.loc[m_mask & (out["ad_code"] == ad_code), f"preferred_slot_{i}"] = int(slot_val)
            out.loc[m_mask & (out["ad_code"] == ad_code), f"{bid_prefix}_{i}"] = float(bid_val)
    return out


def _chart_spend_by_slot(auction_df: pd.DataFrame, merchant_id: str, title: str) -> None:
    m = auction_df[auction_df["merchant_id"] == merchant_id].copy()
    if m.empty:
        st.info("No wins for this merchant under the current simulation.")
        return
    slot_spend = m.groupby("time_slot", as_index=False).agg(spend_usd=("cost_usd", "sum"))
    slot_spend["time_window"] = slot_spend["time_slot"].astype(int).map(slot_to_time_window)
    fig = px.bar(
        slot_spend,
        x="time_window" if slot_spend["time_window"].notna().any() else "time_slot",
        y="spend_usd",
        title=title,
        labels={"time_window": "Time window", "time_slot": "Time slot", "spend_usd": "Spend (USD)"},
    )
    st.plotly_chart(fig, use_container_width=True)


def _chart_top_ads(auction_df: pd.DataFrame, merchant_id: str, title: str) -> None:
    m = auction_df[auction_df["merchant_id"] == merchant_id].copy()
    if m.empty:
        return
    top = (
        m.groupby("ad_code", as_index=False)
        .agg(spend_usd=("cost_usd", "sum"), impressions=("impressions", "sum"))
        .sort_values("spend_usd", ascending=False)
        .head(15)
    )
    fig = px.bar(
        top,
        x="ad_code",
        y="spend_usd",
        title=title,
        labels={"ad_code": "Ad", "spend_usd": "Spend (USD)"},
    )
    st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Merchant Dashboard", layout="wide")
    st.title("Merchant dashboard")
    st.caption("View a single merchant's results and run what-if adjustments.")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(base_dir, "data", "input")
    ads_df = _load_ads_df(input_dir)

    if ads_df is None:
        st.info("Select or upload an `ads_*.csv` to begin.")
        return

    st.sidebar.header("Simulation params")
    bid_unit = "usd" if any(str(c).startswith("bid_usd_") for c in ads_df.columns) else "cpm"
    default_reserve = 5.0 if bid_unit == "usd" else 1.0
    reserve_price = st.sidebar.number_input(
        f"Reserve ({bid_unit})",
        min_value=0.0,
        value=default_reserve,
        step=0.1,
        key=f"reserve_{bid_unit}",
    )

    merchants = sorted(map(str, ads_df["merchant_id"].dropna().unique().tolist()))
    if not merchants:
        st.error("merchant_id not found in the ads CSV.")
        return

    qp = st.query_params
    qp_mid = qp.get("merchant_id")
    default_idx = merchants.index(qp_mid) if qp_mid in merchants else 0
    merchant_id = st.sidebar.selectbox("Your merchant_id", merchants, index=default_idx)

    with st.spinner("Running baseline simulation..."):
        base_auction, base_merchant, base_ad = _run(ads_df, reserve_cpm=float(reserve_price))

    # Optional filters (zipcode/date) for the new format (render in main area)
    filt_auction = base_auction.copy()
    sel_zips, sel_dates = None, None
    if {"zipcode", "date"}.issubset(filt_auction.columns):
        filt_auction["zipcode"] = filt_auction["zipcode"].astype(str)
        filt_auction["date"] = filt_auction["date"].astype(str).str.zfill(8)

        with st.expander("Filters (zipcode / date)", expanded=False):
            zips = sorted([z for z in filt_auction["zipcode"].dropna().unique().tolist()])
            dates = sorted([d for d in filt_auction["date"].dropna().unique().tolist()])

            b1, b2 = st.columns([1, 1])
            with b1:
                all_z = st.checkbox("All zipcodes", value=True, key="mflt_all_zip")
            with b2:
                all_d = st.checkbox("All dates", value=True, key="mflt_all_date")

            c1, c2 = st.columns(2)
            with c1:
                sel_zips = st.multiselect(
                    "zipcode",
                    options=zips,
                    default=zips if all_z else [],
                    placeholder="Select zipcodes…",
                )
            with c2:
                sel_dates = st.multiselect(
                    "date",
                    options=dates,
                    default=dates if all_d else [],
                    placeholder="Select dates…",
                )

        if not sel_zips or not sel_dates:
            filt_auction = filt_auction.iloc[0:0].copy()
        else:
            filt_auction = filt_auction[filt_auction["zipcode"].isin(sel_zips) & filt_auction["date"].isin(sel_dates)].copy()

    base_kpis = _merchant_kpis(filt_auction, merchant_id)
    _kpi_row(base_kpis, "Baseline results")

    st.divider()
    left, right = st.columns([3, 2])
    with left:
        _chart_spend_by_slot(filt_auction, merchant_id, "Baseline: spend by time slot")
    with right:
        _chart_top_ads(filt_auction, merchant_id, "Baseline: top ads by spend")

    if {"zipcode", "date", "time_slot"}.issubset(filt_auction.columns):
        st.subheader("Breakdown (zipcode/date/slot)")
        m = filt_auction[filt_auction["merchant_id"] == merchant_id].copy()
        if not m.empty:
            breakdown = (
                m.groupby(["zipcode", "date", "time_slot"], as_index=False)
                .agg(wins=("position", "count"), impressions=("impressions", "sum"), spend_usd=("cost_usd", "sum"))
                .sort_values(["date", "zipcode", "time_slot"])
            )
            breakdown["time_window"] = breakdown["time_slot"].astype(int).map(slot_to_time_window)
            cols = ["zipcode", "date", "time_slot", "time_window", "wins", "impressions", "spend_usd"]
            st.dataframe(breakdown[cols], use_container_width=True)

    st.divider()
    edited_long = _merchant_editor(ads_df, merchant_id)

    if st.button("Run what-if simulation", type="primary"):
        # Validate slot range using observed max (fallback: sim.NUM_SLOTS)
        ts = pd.to_numeric(edited_long["time_slot"], errors="coerce")
        max_slot = int(max(ts.max(), 1)) if not edited_long.empty else sim.NUM_SLOTS
        bad_slots = edited_long[ts.notna() & ((ts < 1) | (ts > max_slot))]
        bad_bids = edited_long[pd.to_numeric(edited_long["bid"], errors="coerce").notna() & (edited_long["bid"] < 0)]
        incomplete = edited_long[ts.notna() & pd.to_numeric(edited_long["bid"], errors="coerce").isna()]
        if not bad_slots.empty:
            st.error(f"Some time_slot values are out of range (must be 1..{max_slot}).")
            st.stop()
        if not bad_bids.empty:
            st.error("Some bids are negative (must be >= 0).")
            st.stop()
        if not incomplete.empty:
            st.error("Some rows have time_slot filled but bid is blank. Please fill both or clear both.")
            st.stop()

        new_ads_df = _apply_merchant_edits(ads_df, merchant_id, edited_long)
        with st.spinner("Running what-if simulation..."):
            w_auction, w_merchant, w_ad = _run(new_ads_df, reserve_cpm=float(reserve_price))

        # Apply same filters to what-if (so comparisons are apples-to-apples)
        w_filt = w_auction.copy()
        if (
            {"zipcode", "date"}.issubset(w_filt.columns)
            and {"zipcode", "date"}.issubset(filt_auction.columns)
            and sel_zips is not None
            and sel_dates is not None
        ):
            w_filt["zipcode"] = w_filt["zipcode"].astype(str)
            w_filt["date"] = w_filt["date"].astype(str).str.zfill(8)
            if not sel_zips or not sel_dates:
                w_filt = w_filt.iloc[0:0].copy()
            else:
                w_filt = w_filt[w_filt["zipcode"].isin(sel_zips) & w_filt["date"].isin(sel_dates)].copy()

        w_kpis = _merchant_kpis(w_filt, merchant_id)

        st.divider()
        st.markdown("**Baseline vs What-if**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Spend (USD)", f"{w_kpis['spend']:,.2f}", delta=f"{w_kpis['spend']-base_kpis['spend']:,.2f}")
        c2.metric("Impressions", f"{int(w_kpis['imps']):,d}", delta=f"{int(w_kpis['imps']-base_kpis['imps']):,d}")
        c3.metric("Wins", f"{int(w_kpis['wins']):,d}", delta=f"{int(w_kpis['wins']-base_kpis['wins']):,d}")
        c4.metric(
            "Avg pay",
            f"{w_kpis['avg_pay']:,.2f}",
            delta=f"{w_kpis['avg_pay']-base_kpis['avg_pay']:,.2f}",
        )

        left2, right2 = st.columns([3, 2])
        with left2:
            _chart_spend_by_slot(w_filt, merchant_id, "What-if: spend by time slot")
        with right2:
            _chart_top_ads(w_filt, merchant_id, "What-if: top ads by spend")

        st.divider()
        st.markdown("**Downloads (merchant-only)**")
        m_auction = w_filt[w_filt["merchant_id"] == merchant_id]
        m_ad = w_ad[w_ad["merchant_id"] == merchant_id]
        m_merchant = w_merchant[w_merchant["merchant_id"] == merchant_id]

        d1, d2, d3 = st.columns(3)
        d1.download_button(
            "auction_results (merchant)",
            data=m_auction.to_csv(index=False).encode("utf-8"),
            file_name=f"auction_results_{merchant_id}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        d2.download_button(
            "ad_outcome (merchant)",
            data=m_ad.to_csv(index=False).encode("utf-8"),
            file_name=f"ad_outcome_{merchant_id}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        d3.download_button(
            "merchant_summary (merchant)",
            data=m_merchant.to_csv(index=False).encode("utf-8"),
            file_name=f"merchant_summary_{merchant_id}.csv",
            mime="text/csv",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()


