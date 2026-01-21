
"""
Minimal GSP bidding simulation (pure auction system)
- Day split into 12 time slots
- Each ad chooses 5 preferred slots, with a CPM bid per chosen slot
- For each time slot, run a GSP-like auction:
  * Rank all bids for that slot
  * Assign positions 1..3 to top 3
  * Impressions: 3600 / 2400 / 1200 for positions 1/2/3
  * Second-price payments per position:
      pos1 pays bid(pos2)
      pos2 pays bid(pos3)
      pos3 pays reserve_cpm
- Outputs:
  * auction_results.csv (per slot winners)
  * merchant_summary.csv (total spend + impressions)
  * ad_outcome.csv (whether each ad won / spend / impressions)
"""

from __future__ import annotations
import argparse
import os
import re
import pandas as pd

NUM_SLOTS = 12  # fallback only (legacy); auctions now iterate over observed slots
PREFERRED_SLOTS = 5  # max columns supported in wide input (preferred_slot_1..5)
IMPRESSIONS_BY_RANK = {1: 3600, 2: 2400, 3: 1200}

_AD_BASE_RE = re.compile(r"(AD\d+)", re.IGNORECASE)
_ZIP_RE = re.compile(r"z(\d+)", re.IGNORECASE)
# IMPORTANT: date must be parsed from the "z<zipcode>d<date>" suffix, not from the "AD" prefix.
_ZIP_DATE_RE = re.compile(r"z(?P<zip>\d+)d(?P<date>\d+)", re.IGNORECASE)  # e.g. z98101d07142025
_SLOT_COL_RE = re.compile(r"^preferred_slot_(\d+)$")
_BID_COL_RE = re.compile(r"^bid_(?:cpm|usd)_(\d+)$")


def _parse_ad_code_meta(ad_code: str) -> tuple[str, str | None, str | None]:
    """
    New naming: ADxxx unchanged, 'z' -> zipcode, 'd' -> date.
    Example: AD0001z98101d07142025 -> (AD0001, 98101, 07142025)
    """
    s = str(ad_code)
    base_m = _AD_BASE_RE.search(s)
    base = base_m.group(1).upper() if base_m else s
    zd = _ZIP_DATE_RE.search(s)
    if zd:
        zipcode = zd.group("zip")
        date = zd.group("date")
    else:
        zip_m = _ZIP_RE.search(s)
        zipcode = zip_m.group(1) if zip_m else None
        date = None
    return base, zipcode, date


def load_ads_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    # Standardize column names across versions
    if "ad_code" not in df.columns and "ad_id" in df.columns:
        df = df.rename(columns={"ad_id": "ad_code"})

    required_base = {"merchant_id", "ad_code"}
    missing_base = required_base - set(df.columns)
    if missing_base:
        raise ValueError(f"Missing columns in ads CSV: {sorted(missing_base)}")

    # Long format support:
    #  - (merchant_id, ad_code, time_slot, bid_cpm) OR (merchant_id, ad_code, time_slot, bid_usd)
    if {"time_slot", "bid_cpm"}.issubset(df.columns) or {"time_slot", "bid_usd"}.issubset(df.columns):
        return df

    # Wide format support: preferred_slot_i + bid_cpm_i OR bid_usd_i (any count, blanks allowed)
    slot_cols = {}
    bid_cpm_cols = {}
    bid_usd_cols = {}
    for c in df.columns:
        sm = _SLOT_COL_RE.match(str(c))
        if sm:
            slot_cols[int(sm.group(1))] = c
        bm = re.match(r"^bid_cpm_(\d+)$", str(c))
        if bm:
            bid_cpm_cols[int(bm.group(1))] = c
        um = re.match(r"^bid_usd_(\d+)$", str(c))
        if um:
            bid_usd_cols[int(um.group(1))] = c

    if bid_cpm_cols and bid_usd_cols:
        raise ValueError("Ads CSV contains both bid_cpm_* and bid_usd_* columns. Please keep only one bid unit.")

    bid_cols = bid_usd_cols or bid_cpm_cols

    indices = sorted(set(slot_cols).intersection(set(bid_cols)))
    if not indices:
        raise ValueError(
            "Ads CSV must be either long format with columns (time_slot, bid_cpm/bid_usd) "
            "or wide format with (preferred_slot_i, bid_cpm_i/bid_usd_i)."
        )
    return df

def ads_to_bids_long(ads_df: pd.DataFrame) -> pd.DataFrame:
    # If already long, just ensure required columns exist and add zipcode/date if possible.
    if {"time_slot", "bid_cpm"}.issubset(ads_df.columns) or {"time_slot", "bid_usd"}.issubset(ads_df.columns):
        if "bid_usd" in ads_df.columns:
            out = ads_df[["time_slot", "ad_code", "merchant_id", "bid_usd"]].copy()
        else:
            out = ads_df[["time_slot", "ad_code", "merchant_id", "bid_cpm"]].copy()
        meta = out["ad_code"].map(_parse_ad_code_meta)
        out["ad_base"] = meta.map(lambda t: t[0])
        out["zipcode"] = meta.map(lambda t: t[1])
        out["date"] = meta.map(lambda t: t[2])
        out["time_slot"] = out["time_slot"].astype(int)
        if "bid_usd" in out.columns:
            out["bid_usd"] = out["bid_usd"].astype(float)
        else:
            out["bid_cpm"] = out["bid_cpm"].astype(float)
        return out

    # Wide format: allow variable number of filled slots; skip blanks
    slot_cols = {}
    bid_cpm_cols = {}
    bid_usd_cols = {}
    for c in ads_df.columns:
        sm = _SLOT_COL_RE.match(str(c))
        if sm:
            slot_cols[int(sm.group(1))] = c
        bm = re.match(r"^bid_cpm_(\d+)$", str(c))
        if bm:
            bid_cpm_cols[int(bm.group(1))] = c
        um = re.match(r"^bid_usd_(\d+)$", str(c))
        if um:
            bid_usd_cols[int(um.group(1))] = c

    if bid_cpm_cols and bid_usd_cols:
        raise ValueError("Ads CSV contains both bid_cpm_* and bid_usd_* columns. Please keep only one bid unit.")

    bid_cols = bid_usd_cols or bid_cpm_cols
    bid_field = "bid_usd" if bid_usd_cols else "bid_cpm"

    indices = sorted(set(slot_cols).intersection(set(bid_cols)))
    rows = []
    for _, r in ads_df.iterrows():
        ad_code = r["ad_code"]
        ad_base, zipcode, date = _parse_ad_code_meta(ad_code)
        for i in indices:
            slot_val = r.get(slot_cols[i])
            bid_val = r.get(bid_cols[i])
            if pd.isna(slot_val) or pd.isna(bid_val):
                continue
            rows.append(
                {
                    "time_slot": int(slot_val),
                    "ad_code": ad_code,
                    "ad_base": ad_base,
                    "zipcode": zipcode,
                    "date": date,
                    "merchant_id": r["merchant_id"],
                    bid_field: float(bid_val),
                }
            )
    return pd.DataFrame(rows)

def run_gsp_auctions(bids_df: pd.DataFrame, reserve_price: float = 1.0) -> pd.DataFrame:
    auction_rows = []
    group_cols = [c for c in ["zipcode", "date"] if c in bids_df.columns] + ["time_slot"]
    grouped = bids_df.groupby(group_cols, dropna=False)

    for group_key, slot_bids in grouped:
        if slot_bids.empty:
            continue

        # Normalize group key -> context dict
        if not isinstance(group_key, tuple):
            group_key = (group_key,)
        ctx = dict(zip(group_cols, group_key))

        slot_bids = slot_bids.copy()
        if "bid_usd" in slot_bids.columns:
            bid_col = "bid_usd"
            pay_col = "pay_usd"
        else:
            bid_col = "bid_cpm"
            pay_col = "pay_cpm"

        slot_bids.sort_values([bid_col, "ad_code"], ascending=[False, True], inplace=True)
        top = slot_bids.head(3).reset_index(drop=True)

        for pos in [1, 2, 3]:
            if pos > len(top):
                break
            bid = float(top.loc[pos - 1, bid_col])
            if pos == 1:
                pay = float(top.loc[1, bid_col]) if len(top) >= 2 else reserve_price
            elif pos == 2:
                pay = float(top.loc[2, bid_col]) if len(top) >= 3 else reserve_price
            else:
                pay = reserve_price

            impressions = IMPRESSIONS_BY_RANK[pos]
            # If bids are CPM, convert to USD using impressions. If bids are USD, cost is direct.
            if bid_col == "bid_cpm":
                cost_usd = round(pay * impressions / 1000.0, 2)
            else:
                cost_usd = round(pay, 2)

            row = {
                **ctx,
                "position": pos,
                "ad_code": top.loc[pos - 1, "ad_code"],
                "merchant_id": top.loc[pos - 1, "merchant_id"],
                bid_col: round(bid, 2),
                pay_col: round(pay, 2),
                "impressions": impressions,
                "cost_usd": cost_usd,
            }
            if "ad_base" in top.columns:
                row["ad_base"] = top.loc[pos - 1, "ad_base"]
            auction_rows.append(row)
    return pd.DataFrame(auction_rows)

def summarize(ads_df: pd.DataFrame, auction_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Merchant budgets (optional column)
    budget_cols = [c for c in ["merchant_budget_usd"] if c in ads_df.columns]
    budgets = ads_df[["merchant_id"] + budget_cols].drop_duplicates()

    merchant_summary = auction_df.groupby("merchant_id").agg(
        total_impressions=("impressions", "sum"),
        total_spend_usd=("cost_usd", "sum"),
        wins=("ad_code", "count")
    ).reset_index()
    merchant_summary["total_spend_usd"] = merchant_summary["total_spend_usd"].round(2)

    merchant_summary = budgets.merge(merchant_summary, on="merchant_id", how="left").fillna(
        {"total_impressions": 0, "total_spend_usd": 0, "wins": 0}
    )
    if "merchant_budget_usd" in merchant_summary.columns:
        merchant_summary["budget_remaining_usd"] = (
            merchant_summary["merchant_budget_usd"] - merchant_summary["total_spend_usd"]
        ).round(2)

    if {"zipcode", "date", "time_slot"}.issubset(auction_df.columns):
        tmp = auction_df.copy()
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

    # Optional: carry ad_base if present
    if "ad_base" in auction_df.columns:
        ad_base_map = (
            auction_df[["ad_code", "ad_base"]]
            .dropna()
            .drop_duplicates(subset=["ad_code"])
            .set_index("ad_code")["ad_base"]
        )
        ad_outcome["ad_base"] = ad_outcome["ad_code"].map(ad_base_map)

    if {"zipcode", "date"}.issubset(auction_df.columns):
        z_map = (
            auction_df[["ad_code", "zipcode", "date"]]
            .dropna(subset=["zipcode", "date"])
            .drop_duplicates(subset=["ad_code"])
            .set_index("ad_code")[["zipcode", "date"]]
        )
        ad_outcome["zipcode"] = ad_outcome["ad_code"].map(z_map["zipcode"])
        ad_outcome["date"] = ad_outcome["ad_code"].map(z_map["date"])

    # Legacy column name compatibility (if we created won_auctions)
    if "won_auctions" in ad_outcome.columns and "won_slots" not in ad_outcome.columns:
        pass
    elif "won_slots" in ad_outcome.columns:
        pass

    # Round spend
    ad_outcome["spend_usd"] = ad_outcome["spend_usd"].round(2)
    return merchant_summary, ad_outcome

def main(ads_csv: str,
         out_auction_csv: str = "auction_results.csv",
         out_merchant_csv: str = "merchant_summary.csv",
         out_ad_csv: str = "ad_outcome.csv",
         reserve_price: float = 1.0) -> None:
    ads_df = load_ads_csv(ads_csv)
    bids_df = ads_to_bids_long(ads_df)
    auction_df = run_gsp_auctions(bids_df, reserve_price=reserve_price)
    merchant_summary, ad_outcome = summarize(ads_df, auction_df)

    os.makedirs(os.path.dirname(out_auction_csv) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(out_merchant_csv) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(out_ad_csv) or ".", exist_ok=True)

    auction_df.to_csv(out_auction_csv, index=False)
    merchant_summary.to_csv(out_merchant_csv, index=False)
    ad_outcome.to_csv(out_ad_csv, index=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a minimal GSP bidding simulation from an ads CSV and output result CSVs."
    )
    parser.add_argument(
        "--ads",
        required=True,
        help="Path to ads CSV (must include merchant_id, ad_code, preferred_slot_1..5, bid_cpm_1..5).",
    )
    parser.add_argument(
        "--scenario",
        choices=["demo", "experiment"],
        default=None,
        help="Optional. If set, default output paths will be written under data/output_<scenario>/.",
    )
    parser.add_argument(
        "--reserve",
        type=float,
        default=None,
        help="Reserve price in the SAME unit as the input bids. If omitted: defaults to 5.0 for USD bids, 1.0 for CPM bids.",
    )
    parser.add_argument(
        "--reserve-cpm",
        type=float,
        default=None,
        help="Deprecated alias for --reserve (kept for backwards compatibility).",
    )
    parser.add_argument(
        "--out-auction",
        default=None,
        help="Output CSV path for per-slot winners (default depends on --scenario, else auction_results.csv).",
    )
    parser.add_argument(
        "--out-merchant",
        default=None,
        help="Output CSV path for merchant summary (default depends on --scenario, else merchant_summary.csv).",
    )
    parser.add_argument(
        "--out-ad",
        default=None,
        help="Output CSV path for per-ad outcome (default depends on --scenario, else ad_outcome.csv).",
    )
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if args.scenario:
        out_dir = os.path.join(base_dir, "data", f"output_{args.scenario}")
        default_out_auction = os.path.join(out_dir, f"auction_results_{args.scenario}.csv")
        default_out_merchant = os.path.join(out_dir, f"merchant_summary_{args.scenario}.csv")
        default_out_ad = os.path.join(out_dir, f"ad_outcome_{args.scenario}.csv")
    else:
        default_out_auction = "auction_results.csv"
        default_out_merchant = "merchant_summary.csv"
        default_out_ad = "ad_outcome.csv"

    # Choose default reserve based on bid unit
    try:
        _ads_tmp = load_ads_csv(args.ads)
        _bids_tmp = ads_to_bids_long(_ads_tmp)
        _is_usd = "bid_usd" in _bids_tmp.columns
    except Exception:
        _is_usd = False

    reserve_price = (
        float(args.reserve_cpm)
        if args.reserve_cpm is not None
        else float(args.reserve) if args.reserve is not None else (5.0 if _is_usd else 1.0)
    )

    main(
        ads_csv=args.ads,
        out_auction_csv=args.out_auction or default_out_auction,
        out_merchant_csv=args.out_merchant or default_out_merchant,
        out_ad_csv=args.out_ad or default_out_ad,
        reserve_price=reserve_price,
    )
