from __future__ import annotations

import argparse
import os
import re
from datetime import date as dt_date, datetime, timedelta

import pandas as pd

import gsp_bidding_sim as sim


_AD_NUM_RE = re.compile(r"AD(\d+)", re.IGNORECASE)


def _parse_yyyy_mm_dd(s: str) -> dt_date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_date_range(s: str) -> tuple[dt_date, dt_date]:
    # format: YYYY-MM-DD:YYYY-MM-DD
    a, b = s.split(":")
    start = _parse_yyyy_mm_dd(a.strip())
    end = _parse_yyyy_mm_dd(b.strip())
    if end < start:
        start, end = end, start
    return start, end


def _expand_dates(start: dt_date, end: dt_date) -> list[dt_date]:
    out: list[dt_date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def _fmt_mmddyyyy(d: dt_date) -> str:
    return d.strftime("%m%d%Y")


def _next_ad_base(existing: list[str]) -> str:
    max_n = 0
    for s in existing:
        m = _AD_NUM_RE.search(str(s))
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                pass
    return f"AD{max_n + 1:04d}"


def generate_ads_long(
    *,
    base_ads_df: pd.DataFrame | None,
    merchant_id: str,
    zipcode: str,
    dates: list[dt_date],
    slots: list[int],
    total_budget_usd: float,
    ad_base: str | None = None,
) -> pd.DataFrame:
    """
    Generate one ad_id per (date, slot), with bid_usd evenly split across all (date×slot) units.
    Long-format output columns:
      merchant_id, ad_id, time_slot, bid_usd
    """
    if not dates or not slots:
        return pd.DataFrame(columns=["merchant_id", "ad_id", "time_slot", "bid_usd"])

    existing_codes: list[str] = []
    if base_ads_df is not None and not base_ads_df.empty:
        if "ad_id" in base_ads_df.columns:
            existing_codes = base_ads_df["ad_id"].astype(str).tolist()
        elif "ad_code" in base_ads_df.columns:
            existing_codes = base_ads_df["ad_code"].astype(str).tolist()

    ad_base = (ad_base or _next_ad_base(existing_codes)).strip().upper()
    z = str(zipcode).strip()
    slots_u = sorted(set(int(s) for s in slots))

    n_units = len(dates) * len(slots_u)
    bid = round(float(total_budget_usd) / max(n_units, 1), 2)

    rows = []
    for d in dates:
        ds = _fmt_mmddyyyy(d)
        for s in slots_u:
            # Keep your naming: ADxxxx + z(zip) + d(date); add sXX so each slot has its own ad_id
            ad_id = f"{ad_base}z{z}d{ds}s{s:02d}"
            rows.append(
                {
                    "merchant_id": merchant_id,
                    "ad_id": ad_id,
                    "time_slot": int(s),
                    "bid_usd": bid,
                }
            )
    return pd.DataFrame(rows)


def long_to_wide_1000_format(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert long rows (merchant_id, ad_id, time_slot, bid_usd) to the wide format used by 1000 table:
      merchant_id, ad_id, num_selected_slots,
      preferred_slot_1,bid_usd_1,...,preferred_slot_5,bid_usd_5
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


def recommend_budget_from_base(
    base_ads_df: pd.DataFrame,
    *,
    zipcodes: list[str],
    dates: list[dt_date],
    slots: list[int],
    default_floor: float = 10.0,
) -> tuple[float, float, int]:
    """
    Budget recommendation (ported from the older bidding agent idea):
    - For each (zipcode, date, slot), take the 3rd-highest historical bid as a threshold
      (fallback to last if <3 bids; fallback to default_floor if no bids).
    - Use median threshold across all selected units.
    - Recommended total budget = median_threshold * n_units

    Returns: (recommended_total_budget, median_threshold, n_units)
    """
    if base_ads_df is None or base_ads_df.empty or not zipcodes or not dates or not slots:
        return 0.0, 0.0, 0

    bids = sim.ads_to_bids_long(base_ads_df).copy()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ads CSV compatible with the ranking system input.")
    parser.add_argument("--base", default=None, help="Optional base ads CSV to infer next AD id and date span.")
    parser.add_argument("--out", required=True, help="Output path (should be under data/input and start with ads_).")
    parser.add_argument("--merchant", required=True, help="merchant_id")
    parser.add_argument("--zipcode", required=True, help="zipcode digits (e.g. 98101)")
    parser.add_argument("--slots", required=True, help="Comma-separated time slots, e.g. 1,3,5")
    parser.add_argument("--budget", type=float, required=True, help="Total budget USD to split evenly across date×slot.")
    parser.add_argument("--date", default=None, help="Single date in MMDDYYYY (e.g. 07122025)")
    parser.add_argument("--date-range", default=None, help="Date range in YYYY-MM-DD:YYYY-MM-DD (inclusive)")
    parser.add_argument("--ad-base", default=None, help="Optional AD base, e.g. AD0123")
    parser.add_argument("--format", choices=["wide", "long"], default="wide", help="Output format: wide (1000 table) or long.")
    args = parser.parse_args()

    base_df = sim.load_ads_csv(args.base) if args.base else None

    slots = [int(s.strip()) for s in args.slots.split(",") if s.strip()]
    if not (1 <= len(slots) <= 5):
        raise SystemExit("slots must be 1..5 values")

    if args.date and args.date_range:
        raise SystemExit("Use either --date or --date-range, not both.")
    if not args.date and not args.date_range:
        raise SystemExit("Provide --date or --date-range.")

    if args.date:
        d = datetime.strptime(str(args.date).zfill(8), "%m%d%Y").date()
        dates = [d]
    else:
        start, end = _parse_date_range(args.date_range)
        dates = _expand_dates(start, end)

    long_df = generate_ads_long(
        base_ads_df=base_df,
        merchant_id=args.merchant,
        zipcode=args.zipcode,
        dates=dates,
        slots=slots,
        total_budget_usd=args.budget,
        ad_base=args.ad_base,
    )

    out_df = long_to_wide_1000_format(long_df) if args.format == "wide" else long_df

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out_df.to_csv(args.out, index=False)
    print(f"Wrote: {args.out} ({len(out_df)} rows)")


if __name__ == "__main__":
    main()


