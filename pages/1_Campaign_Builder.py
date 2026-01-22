from __future__ import annotations

import os
import re
from datetime import date as dt_date, datetime, timedelta

import pandas as pd
import streamlit as st

import dashboard as admin_dash
import gsp_bidding_sim as sim
from ui_utils import format_slot_label, slot_to_time_window


_AD_NUM_RE = re.compile(r"AD(\d+)", re.IGNORECASE)


def _infer_max_slot(ads_df: pd.DataFrame) -> int:
    if ads_df is None or ads_df.empty:
        return sim.NUM_SLOTS
    if "time_slot" in ads_df.columns:
        return int(max(pd.to_numeric(ads_df["time_slot"], errors="coerce").max(), 1))
    slot_cols = [c for c in ads_df.columns if str(c).startswith("preferred_slot_")]
    if slot_cols:
        vals = pd.to_numeric(ads_df[slot_cols].stack(), errors="coerce")
        m = vals.max()
        if pd.notna(m):
            return int(max(m, 1))
    return sim.NUM_SLOTS


def _next_ad_base(existing_ad_codes: list[str]) -> str:
    max_n = 0
    for s in existing_ad_codes:
        m = _AD_NUM_RE.search(str(s))
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                continue
    return f"AD{max_n + 1:04d}"


def _fmt_mmddyyyy(d: dt_date) -> str:
    return d.strftime("%m%d%Y")


def _parse_mmddyyyy(s: str) -> dt_date | None:
    try:
        return datetime.strptime(str(s).zfill(8), "%m%d%Y").date()
    except Exception:
        return None


def _available_date_span_from_base(base_ads_df: pd.DataFrame) -> tuple[dt_date | None, dt_date | None]:
    """
    Infer date span from base input by parsing ad_code/ad_id (z<zip>d<MMDDYYYY>).
    Returns (min_date, max_date) or (None, None) if not available.
    """
    if base_ads_df is None or base_ads_df.empty:
        return None, None

    col = None
    if "ad_code" in base_ads_df.columns:
        col = "ad_code"
    elif "ad_id" in base_ads_df.columns:
        col = "ad_id"
    if not col:
        return None, None

    parsed = base_ads_df[col].astype(str).map(lambda x: sim._parse_ad_code_meta(x)[2])  # type: ignore[attr-defined]
    dates = [d for d in parsed.dropna().unique().tolist() if d]
    dt_list = [x for x in (_parse_mmddyyyy(d) for d in dates) if x is not None]
    if not dt_list:
        return None, None
    return min(dt_list), max(dt_list)


def _expand_date_range(start: dt_date, end: dt_date) -> list[dt_date]:
    if end < start:
        start, end = end, start
    out: list[dt_date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def _recommend_budget_from_base(
    base_ads_df: pd.DataFrame,
    *,
    zipcodes: list[str],
    dates: list[dt_date],
    slots: list[int],
    default_floor: float = 10.0,
) -> tuple[float, float, int]:
    """
    From bidding_agent.py: use historical 3rd-place threshold per (zipcode,date,slot) and take median.
    Returns (recommended_total_budget, median_threshold, n_units).
    """
    if base_ads_df is None or base_ads_df.empty or not zipcodes or not dates or not slots:
        return 0.0, 0.0, 0

    bids = sim.ads_to_bids_long(base_ads_df)
    bids = bids.copy()
    if "zipcode" in bids.columns:
        bids["zipcode"] = bids["zipcode"].astype(str)
    if "date" in bids.columns:
        bids["date"] = bids["date"].astype(str).str.zfill(8)
    bids["time_slot"] = pd.to_numeric(bids["time_slot"], errors="coerce").astype("Int64")

    bid_col = "bid_usd" if "bid_usd" in bids.columns else "bid_cpm"

    target_dates = {d.strftime("%m%d%Y") for d in dates}
    zset = set(map(str, zipcodes))
    sset = set(map(int, slots))

    filt = bids[
        bids.get("zipcode", pd.Series(dtype=str)).isin(zset)
        & bids.get("date", pd.Series(dtype=str)).isin(target_dates)
        & bids["time_slot"].isin(sset)
        & pd.to_numeric(bids[bid_col], errors="coerce").notna()
    ].copy()

    n_units = len(zset) * len(target_dates) * len(sset)
    if filt.empty:
        return float(default_floor * n_units), float(default_floor), int(n_units)

    def _third_or_last(s: pd.Series) -> float:
        vals = pd.to_numeric(s, errors="coerce").dropna().sort_values(ascending=False)
        if len(vals) >= 3:
            return float(vals.iloc[2])
        if len(vals) > 0:
            return float(vals.iloc[-1])
        return float(default_floor)

    thresholds = (
        filt.groupby(["zipcode", "date", "time_slot"], as_index=False)[bid_col]
        .apply(_third_or_last)
        .rename(columns={bid_col: "threshold"})
    )

    median_thr = float(pd.to_numeric(thresholds["threshold"], errors="coerce").median())
    rec_total = round(median_thr * n_units, 2)
    return rec_total, round(median_thr, 2), int(n_units)


def _timeline_slot_selector(*, max_slot: int, max_select: int, key: str) -> list[int]:
    """
    A left-to-right "light" timeline slot selector.
    Stores selected slots in st.session_state[key] as a sorted list[int].
    """
    import streamlit as st

    if key not in st.session_state:
        st.session_state[key] = [1]

    selected: list[int] = list(sorted(set(int(x) for x in st.session_state[key] if x is not None)))

    # For large slot counts, fall back to multiselect (timeline would be too wide).
    if max_slot > 24:
        selected = st.multiselect("time slots (select 1–5)", options=list(range(1, max_slot + 1)), default=selected[:max_select], max_selections=max_select)
        st.session_state[key] = selected
        return selected

    st.markdown("**Time slots (timeline)**")
    st.caption("Click the lights from left to right to select time slots (max 5).")

    cols = st.columns(max_slot)

    def toggle(slot: int) -> None:
        cur = set(st.session_state[key])
        if slot in cur:
            cur.remove(slot)
        else:
            if len(cur) >= max_select:
                # refuse silently; UI shows warning below
                return
            cur.add(slot)
        st.session_state[key] = sorted(cur)

    for i in range(1, max_slot + 1):
        is_on = i in selected
        label = "●" if is_on else "○"
        with cols[i - 1]:
            st.button(label, key=f"{key}_btn_{i}", on_click=toggle, args=(i,))
            w = slot_to_time_window(i)
            st.caption(w if w else str(i))

    selected = list(sorted(set(int(x) for x in st.session_state[key])))
    if len(selected) > max_select:
        selected = selected[:max_select]
        st.session_state[key] = selected
    if len(selected) == 0:
        st.warning("Select at least 1 time slot.")
    elif len(selected) >= max_select:
        st.info("You have reached the maximum of 5 selected time slots.")
    return selected


def _export_long_to_wide_1000_format(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert Campaign Builder's long rows (merchant_id, ad_id, time_slot, bid_usd)
    to the wide format used by the 1000 input table.
    """
    df = long_df.copy()
    if df.empty:
        cols = ["merchant_id", "ad_id", "num_selected_slots"]
        for i in range(1, 6):
            cols += [f"preferred_slot_{i}", f"bid_usd_{i}"]
        return pd.DataFrame(columns=cols)

    df["time_slot"] = pd.to_numeric(df["time_slot"], errors="coerce").astype("Int64")
    df["bid_usd"] = pd.to_numeric(df["bid_usd"], errors="coerce")
    df = df.dropna(subset=["merchant_id", "ad_id", "time_slot", "bid_usd"]).copy()

    out_rows = []
    for (merchant_id, ad_id), g in df.groupby(["merchant_id", "ad_id"]):
        g = g.sort_values("time_slot")
        slots = g["time_slot"].astype(int).tolist()[:5]
        bids = g["bid_usd"].astype(float).tolist()[:5]
        row = {"merchant_id": merchant_id, "ad_id": ad_id, "num_selected_slots": len(slots)}
        for i in range(1, 6):
            row[f"preferred_slot_{i}"] = slots[i - 1] if i <= len(slots) else pd.NA
            row[f"bid_usd_{i}"] = bids[i - 1] if i <= len(bids) else pd.NA
        out_rows.append(row)
    return pd.DataFrame(out_rows)

def _generate_rows(
    *,
    merchant_id: str,
    ad_base: str,
    zipcode: str,
    dates: list[dt_date],
    slots: list[int],
    total_budget_usd: float,
) -> pd.DataFrame:
    if not slots or not dates:
        return pd.DataFrame(columns=["merchant_id", "ad_id", "time_slot", "bid_usd"])
    total_items = len(set(slots)) * len(dates)
    per_item_bid = round(float(total_budget_usd) / max(total_items, 1), 2)

    rows = []
    for d in dates:
        date_str = _fmt_mmddyyyy(d)
        for s in sorted(set(slots)):
            # One ad_id per (date, slot)
            ad_id = f"{ad_base}z{zipcode}d{date_str}s{s:02d}"
            rows.append(
                {
                    "merchant_id": merchant_id,
                    "ad_id": ad_id,
                    "time_slot": int(s),
                    "bid_usd": per_item_bid,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    st.set_page_config(page_title="Campaign Builder", layout="wide")
    st.title("Campaign Builder")
    st.caption("Enter merchant, total budget, zipcode, date(s), and 1–5 time slots to generate an editable ads list.")

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_dir = os.path.join(repo_dir, "data", "input")

    st.subheader("Base input (optional)")
    base_ads_df, status = admin_dash._load_inputs_ui(input_dir)
    st.caption(status)

    if "builder_rows" not in st.session_state:
        st.session_state.builder_rows = pd.DataFrame(columns=["merchant_id", "ad_id", "time_slot", "bid_usd"])
    if "builder_total_budget" not in st.session_state:
        st.session_state.builder_total_budget = 100.0

    # Compute defaults from base file (if provided)
    existing_ad_codes: list[str] = []
    merchant_options: list[str] = []
    max_slot = sim.NUM_SLOTS
    if base_ads_df is not None:
        existing_ad_codes = base_ads_df.get("ad_code", pd.Series(dtype=str)).astype(str).tolist()
        if "merchant_id" in base_ads_df.columns:
            merchant_options = sorted(map(str, base_ads_df["merchant_id"].dropna().unique().tolist()))
        max_slot = _infer_max_slot(base_ads_df)

    st.divider()
    st.subheader("Create ads from inputs")

    c1, c2, c3 = st.columns([1.2, 1.2, 2.0])
    with c1:
        merchant_id = st.selectbox("merchant_id", merchant_options, index=0) if merchant_options else st.text_input("merchant_id", value="M001")
    with c2:
        zipcode = st.text_input("zipcode", value="98101")
    with c3:
        min_d, max_d = _available_date_span_from_base(base_ads_df)
        mode = st.radio("date mode", ["Single day", "Date range"], horizontal=True)
        if mode == "Single day":
            d_single = st.date_input(
                "date (dMMDDYYYY)",
                value=min_d or dt_date.today(),
                min_value=min_d,
                max_value=max_d,
            )
            selected_dates = [d_single]
        else:
            # Constrain to base date span if available; otherwise allow any range.
            default_start = min_d or dt_date.today()
            default_end = max_d or default_start
            d_range = st.date_input(
                "date range (inclusive)",
                value=(default_start, default_end),
                min_value=min_d,
                max_value=max_d,
            )
            if isinstance(d_range, tuple) and len(d_range) == 2:
                selected_dates = _expand_date_range(d_range[0], d_range[1])
            else:
                # Fallback (Streamlit returns a single date)
                selected_dates = [d_range] if isinstance(d_range, dt_date) else [default_start]

    # Timeline selection shows real time windows, but the generated CSV keeps time_slot as 1..8 for analysis.
    slots = _timeline_slot_selector(max_slot=max_slot, max_select=5, key="builder_slots")

    # Budget recommendation must run BEFORE we instantiate the total budget widget key,
    # otherwise Streamlit raises an exception when we try to update session_state for that key.
    if base_ads_df is not None and selected_dates and slots:
        st.markdown("**Budget recommendation (from history)**")
        if st.button("Compute recommended budget", key="rec_budget_btn"):
            rec_total, median_thr, n_units = _recommend_budget_from_base(
                base_ads_df,
                zipcodes=[str(zipcode).strip()],
                dates=selected_dates,
                slots=[int(x) for x in slots],
                default_floor=10.0,
            )
            if n_units > 0:
                st.session_state.builder_total_budget = float(rec_total)
                st.info(f"Median 3rd-place threshold ≈ ${median_thr:.2f} / unit × {n_units} units ⇒ recommended total ≈ ${rec_total:.2f}")

    total_budget = st.number_input(
        "total budget (USD)",
        min_value=0.0,
        value=float(st.session_state.builder_total_budget),
        step=10.0,
        key="builder_total_budget",
    )

    ad_base_default = _next_ad_base(existing_ad_codes + st.session_state.builder_rows.get("ad_id", pd.Series(dtype=str)).astype(str).tolist())
    ad_base = st.text_input("AD base (ADxxxx)", value=ad_base_default)

    add_clicked = st.button("Generate and add to list", type="primary")
    if add_clicked:
        if not merchant_id.strip():
            st.error("merchant_id is required.")
            st.stop()
        if not zipcode.strip().isdigit():
            st.error("zipcode must be digits only (e.g. 98101).")
            st.stop()
        if not ad_base.strip().upper().startswith("AD"):
            st.error("AD base must start with 'AD' (e.g. AD0123).")
            st.stop()
        if not (1 <= len(slots) <= 5):
            st.error("Please select 1–5 time slots.")
            st.stop()
        if not selected_dates:
            st.error("Please select at least 1 date.")
            st.stop()

        new_rows = _generate_rows(
            merchant_id=str(merchant_id).strip(),
            ad_base=str(ad_base).strip().upper(),
            zipcode=str(zipcode).strip(),
            dates=selected_dates,
            slots=[int(x) for x in slots],
            total_budget_usd=float(total_budget),
        )
        st.session_state.builder_rows = pd.concat([st.session_state.builder_rows, new_rows], ignore_index=True)

    st.divider()
    st.subheader("Dynamic editable list (will be exported)")
    st.caption("You can edit bid_usd/time_slot directly, and add/remove rows.")

    with st.expander("Time slot legend", expanded=False):
        lines = []
        for i in range(1, 9):
            lines.append(f"- {format_slot_label(i)}")
        st.markdown("\n".join(lines))

    edited = st.data_editor(
        st.session_state.builder_rows,
        use_container_width=True,
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "merchant_id": st.column_config.TextColumn("merchant_id"),
            "ad_id": st.column_config.TextColumn("ad_id"),
            "time_slot": st.column_config.NumberColumn("time_slot", min_value=1, max_value=max_slot, step=1),
            "bid_usd": st.column_config.NumberColumn("bid_usd", min_value=0.0, step=0.1),
        },
    )
    st.session_state.builder_rows = edited

    # Basic validation
    if not edited.empty:
        if edited["ad_id"].isna().any() or (edited["ad_id"].astype(str).str.len() == 0).any():
            st.warning("Some rows have empty ad_id.")
        if pd.to_numeric(edited["bid_usd"], errors="coerce").isna().any():
            st.warning("Some rows have non-numeric bid_usd.")

    st.divider()
    st.subheader("Export / run")

    export_format = st.radio("export format", ["wide (1000 table)", "long (compact)"], horizontal=True)
    out_name = st.text_input("output filename", value="ads_input_dynamic.csv")
    out_path = os.path.join(input_dir, out_name)

    b1, b2, b3 = st.columns([1, 1, 2])
    with b1:
        if st.button("Save generated CSV to data/input", disabled=edited.empty):
            os.makedirs(input_dir, exist_ok=True)
            edited_to_save = edited.copy()
            edited_to_save["time_slot"] = pd.to_numeric(edited_to_save["time_slot"], errors="coerce").astype("Int64")
            edited_to_save["bid_usd"] = pd.to_numeric(edited_to_save["bid_usd"], errors="coerce")

            if export_format.startswith("wide"):
                out_df = _export_long_to_wide_1000_format(edited_to_save)
            else:
                out_df = edited_to_save

            out_df.to_csv(out_path, index=False)
            st.success(f"Saved: {out_path}")
    with b2:
        if st.button("Clear list"):
            st.session_state.builder_rows = pd.DataFrame(columns=["merchant_id", "ad_id", "time_slot", "bid_usd"])
            st.rerun()
    with b3:
        edited_to_dl = edited.copy()
        edited_to_dl["time_slot"] = pd.to_numeric(edited_to_dl["time_slot"], errors="coerce").astype("Int64")
        edited_to_dl["bid_usd"] = pd.to_numeric(edited_to_dl["bid_usd"], errors="coerce")
        dl_df = _export_long_to_wide_1000_format(edited_to_dl) if export_format.startswith("wide") else edited_to_dl
        st.download_button(
            "Download generated CSV",
            data=dl_df.to_csv(index=False).encode("utf-8") if not edited.empty else b"",
            file_name=out_name,
            mime="text/csv",
            disabled=edited.empty,
            use_container_width=True,
        )

    if base_ads_df is not None and not edited.empty:
        st.divider()
        st.subheader("Quick simulation (base + generated)")
        reserve_default = 5.0  # USD bids for generated rows
        reserve = st.number_input("Reserve (USD)", min_value=0.0, value=reserve_default, step=1.0, key="builder_reserve")
        if st.button("Run simulation now"):
            with st.spinner("Running simulation..."):
                base_bids = sim.ads_to_bids_long(base_ads_df)
                gen_df = edited.copy().rename(columns={"ad_id": "ad_code"})
                gen_df = gen_df[["merchant_id", "ad_code", "time_slot", "bid_usd"]].copy()
                combined_long = pd.concat([base_bids[["merchant_id", "ad_code", "time_slot", "bid_usd"]] if "bid_usd" in base_bids.columns else base_bids[["merchant_id", "ad_code", "time_slot", "bid_cpm"]], gen_df], ignore_index=True)
                # Re-add missing columns for sim (it parses zipcode/date from ad_code)
                combined_long = combined_long.dropna(subset=["merchant_id", "ad_code", "time_slot"])
                auction_df = sim.run_gsp_auctions(sim.ads_to_bids_long(combined_long), reserve_price=float(reserve))
                st.dataframe(auction_df.head(50), use_container_width=True)


if __name__ == "__main__":
    main()


