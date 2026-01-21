from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

import gsp_bidding_sim as sim


@dataclass(frozen=True)
class RunOutputs:
    auction_df: pd.DataFrame
    merchant_df: pd.DataFrame
    ad_df: pd.DataFrame


def _find_local_ads_csvs(base_dir: str) -> list[str]:
    try:
        files = os.listdir(base_dir)
    except OSError:
        return []
    return sorted([f for f in files if f.lower().endswith(".csv") and f.startswith("ads_")])


def _read_ads_df_from_upload(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.getvalue()
    return sim.load_ads_csv(io.BytesIO(raw))  # pandas can read file-like objects


def _read_ads_df_from_path(path: str) -> pd.DataFrame:
    return sim.load_ads_csv(path)


def _run_simulation_in_memory(ads_df: pd.DataFrame, reserve_cpm: float) -> RunOutputs:
    bids_df = sim.ads_to_bids_long(ads_df)
    auction_df = sim.run_gsp_auctions(bids_df, reserve_price=reserve_cpm)
    merchant_df, ad_df = sim.summarize(ads_df, auction_df)
    return RunOutputs(auction_df=auction_df, merchant_df=merchant_df, ad_df=ad_df)

def _normalize_geo_time_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep zipcode/date as strings (avoid losing leading zeros when users later read CSVs).
    """
    out = df.copy()
    if "zipcode" in out.columns:
        out["zipcode"] = out["zipcode"].astype(str)
    if "date" in out.columns:
        out["date"] = out["date"].astype(str).str.zfill(8)
    return out


def _apply_admin_filters(auction_df: pd.DataFrame) -> pd.DataFrame:
    import streamlit as st

    df = _normalize_geo_time_cols(auction_df)
    if not {"zipcode", "date"}.issubset(df.columns):
        return df

    with st.expander("Filters (zipcode / date)", expanded=False):
        zips = sorted([z for z in df["zipcode"].dropna().unique().tolist()])
        dates = sorted([d for d in df["date"].dropna().unique().tolist()])

        b1, b2 = st.columns([1, 1])
        with b1:
            all_z = st.checkbox("All zipcodes", value=True, key="flt_all_zip")
        with b2:
            all_d = st.checkbox("All dates", value=True, key="flt_all_date")

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
        return df.iloc[0:0].copy()
    return df[df["zipcode"].isin(sel_zips) & df["date"].isin(sel_dates)].copy()


def _recompute_summaries_from_auction(auction_df: pd.DataFrame, budgets_df: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Recompute merchant/ad summaries from a filtered auction_df so charts/tables reflect filters.
    """
    merchant_summary = auction_df.groupby("merchant_id").agg(
        total_impressions=("impressions", "sum"),
        total_spend_usd=("cost_usd", "sum"),
        wins=("ad_code", "count"),
    ).reset_index()
    merchant_summary["total_spend_usd"] = merchant_summary["total_spend_usd"].round(2)

    if budgets_df is not None and not budgets_df.empty:
        merchant_summary = budgets_df.merge(merchant_summary, on="merchant_id", how="left").fillna(
            {"total_impressions": 0, "total_spend_usd": 0, "wins": 0}
        )
        if "merchant_budget_usd" in merchant_summary.columns:
            merchant_summary["budget_remaining_usd"] = (
                merchant_summary["merchant_budget_usd"] - merchant_summary["total_spend_usd"]
            ).round(2)

    if {"zipcode", "date", "time_slot"}.issubset(auction_df.columns):
        tmp = auction_df.copy()
        tmp = _normalize_geo_time_cols(tmp)
        tmp["_auction_key"] = tmp[["zipcode", "date", "time_slot"]].astype(str).agg("|".join, axis=1)
        ad_outcome = tmp.groupby(["ad_code", "merchant_id"]).agg(
            won_auctions=("_auction_key", "nunique"),
            won_positions=("position", lambda s: ",".join(map(str, sorted(s.unique())))),
            impressions=("impressions", "sum"),
            spend_usd=("cost_usd", "sum"),
        ).reset_index()
    else:
        ad_outcome = auction_df.groupby(["ad_code", "merchant_id"]).agg(
            won_slots=("time_slot", "nunique"),
            won_positions=("position", lambda s: ",".join(map(str, sorted(s.unique())))),
            impressions=("impressions", "sum"),
            spend_usd=("cost_usd", "sum"),
        ).reset_index()
    ad_outcome["spend_usd"] = ad_outcome["spend_usd"].round(2)
    return merchant_summary, ad_outcome


def _kpi_row(auction_df: pd.DataFrame) -> None:
    import streamlit as st

    total_spend = float(auction_df["cost_usd"].sum()) if not auction_df.empty else 0.0
    total_imps = int(auction_df["impressions"].sum()) if not auction_df.empty else 0
    unique_ads = int(auction_df["ad_code"].nunique()) if not auction_df.empty else 0
    covered_slots = int(auction_df["time_slot"].nunique()) if not auction_df.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total spend (USD)", f"{total_spend:,.2f}")
    c2.metric("Total impressions", f"{total_imps:,d}")
    c3.metric("Unique winning ads", f"{unique_ads:,d}")
    c4.metric("Slots with winners", f"{covered_slots:,d}")


def _chart_spend_by_slot(auction_df: pd.DataFrame) -> None:
    import streamlit as st
    import plotly.express as px

    if auction_df.empty:
        st.info("No auction rows to chart (did any slot receive bids?).")
        return

    slot_spend = (
        auction_df.groupby("time_slot", as_index=False)
        .agg(total_spend_usd=("cost_usd", "sum"), total_impressions=("impressions", "sum"))
    )
    fig = px.bar(
        slot_spend,
        x="time_slot",
        y="total_spend_usd",
        title="Spend by time slot",
        labels={"time_slot": "Time slot", "total_spend_usd": "Spend (USD)"},
    )
    st.plotly_chart(fig, use_container_width=True)


def _chart_top_merchants(merchant_df: pd.DataFrame) -> None:
    import streamlit as st
    import plotly.express as px

    if merchant_df.empty:
        return
    top_n = st.slider("Top N merchants", min_value=5, max_value=50, value=15, step=5)
    df = merchant_df.sort_values("total_spend_usd", ascending=False).head(top_n)
    fig = px.bar(
        df,
        x="merchant_id",
        y="total_spend_usd",
        title=f"Top {top_n} merchants by spend",
        labels={"merchant_id": "Merchant", "total_spend_usd": "Spend (USD)"},
    )
    st.plotly_chart(fig, use_container_width=True)


def _chart_position_mix(auction_df: pd.DataFrame) -> None:
    import streamlit as st
    import plotly.express as px

    if auction_df.empty:
        return
    mix = auction_df.groupby("position", as_index=False).agg(wins=("ad_code", "count"))
    fig = px.pie(mix, names="position", values="wins", title="Win share by position")
    st.plotly_chart(fig, use_container_width=True)


def _table_section(outputs: RunOutputs) -> None:
    import streamlit as st

    with st.expander("Raw tables", expanded=False):
        st.subheader("auction_df (per slot winners)")
        st.dataframe(outputs.auction_df, use_container_width=True)
        st.subheader("merchant_df (merchant summary)")
        st.dataframe(outputs.merchant_df, use_container_width=True)
        st.subheader("ad_df (ad outcome)")
        st.dataframe(outputs.ad_df, use_container_width=True)


def _download_buttons(outputs: RunOutputs) -> None:
    import streamlit as st

    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "Download auction_results.csv",
        data=outputs.auction_df.to_csv(index=False).encode("utf-8"),
        file_name="auction_results.csv",
        mime="text/csv",
        use_container_width=True,
    )
    c2.download_button(
        "Download merchant_summary.csv",
        data=outputs.merchant_df.to_csv(index=False).encode("utf-8"),
        file_name="merchant_summary.csv",
        mime="text/csv",
        use_container_width=True,
    )
    c3.download_button(
        "Download ad_outcome.csv",
        data=outputs.ad_df.to_csv(index=False).encode("utf-8"),
        file_name="ad_outcome.csv",
        mime="text/csv",
        use_container_width=True,
    )


def _load_inputs_ui(base_dir: str) -> Tuple[Optional[pd.DataFrame], str]:
    import streamlit as st

    st.sidebar.header("Input")
    source = st.sidebar.radio("Ads source", ["Pick local file", "Upload CSV"], index=0)

    if source == "Pick local file":
        local_files = _find_local_ads_csvs(base_dir)
        if not local_files:
            st.sidebar.warning("No local ads_*.csv found in this folder. Use Upload CSV instead.")
            return None, "No input selected"
        picked = st.sidebar.selectbox("ads CSV", local_files)
        ads_path = os.path.join(base_dir, picked)
        try:
            return _read_ads_df_from_path(ads_path), f"Loaded: {picked}"
        except Exception as e:
            return None, f"Failed to load {picked}: {e}"

    uploaded = st.sidebar.file_uploader("Upload ads CSV", type=["csv"])
    if uploaded is None:
        return None, "No input selected"
    try:
        return _read_ads_df_from_upload(uploaded), f"Uploaded: {uploaded.name}"
    except Exception as e:
        return None, f"Failed to load upload: {e}"


def main() -> None:
    try:
        import streamlit as st
    except ModuleNotFoundError as e:
        raise SystemExit(
            "streamlit is not installed. Run: python3 -m pip install -r requirements.txt"
        ) from e

    try:
        import plotly.express as _px  # noqa: F401
    except ModuleNotFoundError as e:
        raise SystemExit(
            "plotly is not installed. Run: python3 -m pip install -r requirements.txt"
        ) from e

    st.set_page_config(page_title="GSP bidding dashboard", layout="wide")
    st.title("GSP bidding simulation dashboard")
    st.caption("Load an ads CSV, run the simulation in-memory, and explore spend/impressions and winners.")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(base_dir, "data", "input")
    ads_df, status = _load_inputs_ui(input_dir)
    st.sidebar.caption(status)

    if ads_df is None:
        st.info("Select or upload an `ads_*.csv` to begin.")
        return

    st.sidebar.header("Simulation params")
    # Detect bid unit from the loaded ads df to choose a sensible default reserve:
    bid_unit = "usd" if any(str(c).startswith("bid_usd_") for c in ads_df.columns) else "cpm"
    default_reserve = 5.0 if bid_unit == "usd" else 1.0
    reserve_price = st.sidebar.number_input(
        f"Reserve ({bid_unit})",
        min_value=0.0,
        value=default_reserve,
        step=0.1,
        key=f"reserve_{bid_unit}",
    )

    st.subheader("Input preview")
    st.dataframe(ads_df.head(20), use_container_width=True)

    if "run_outputs" not in st.session_state:
        st.session_state.run_outputs = None

    run_clicked = st.button("Run simulation", type="primary")
    if run_clicked or st.session_state.run_outputs is None:
        with st.spinner("Running auctions..."):
            st.session_state.run_outputs = _run_simulation_in_memory(ads_df, reserve_cpm=float(reserve_price))

    outputs: RunOutputs = st.session_state.run_outputs
    # Apply zipcode/date filters (new input format)
    filtered_auction = _apply_admin_filters(outputs.auction_df)

    # If budgets exist in input, keep them in filtered view
    budgets_df = None
    if "merchant_budget_usd" in ads_df.columns:
        budgets_df = ads_df[["merchant_id", "merchant_budget_usd"]].drop_duplicates()
    filtered_merchant, filtered_ad = _recompute_summaries_from_auction(filtered_auction, budgets_df=budgets_df)
    st.divider()

    _kpi_row(filtered_auction)
    st.divider()

    left, right = st.columns([2, 1])
    with left:
        _chart_spend_by_slot(filtered_auction)
    with right:
        _chart_position_mix(filtered_auction)

    st.divider()
    _chart_top_merchants(filtered_merchant)
    _download_buttons(RunOutputs(auction_df=filtered_auction, merchant_df=filtered_merchant, ad_df=filtered_ad))
    _table_section(RunOutputs(auction_df=filtered_auction, merchant_df=filtered_merchant, ad_df=filtered_ad))


if __name__ == "__main__":
    main()


