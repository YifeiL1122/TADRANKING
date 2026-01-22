## TADRANKING

### What you get
- Generate ads from user intent (budget, zipcode, dates, time slots)
- Run the GSP-style auction simulation (rank within zipcode + date + time_slot)
- Admin dashboard + merchant dashboard to explore results

### Setup

```bash
cd /Users/suzyliu/Desktop/bidding
python3 -m pip install -r requirements.txt
```

### Run the admin dashboard (multipage)

```bash
cd /Users/suzyliu/Desktop/bidding
streamlit run dashboard.py
```

Pages included:
- **Campaign Builder**: create/edit/export `ads_*.csv`
- **Merchant Portal**: merchant view inside the admin app

### Run the merchant-only app

```bash
cd /Users/suzyliu/Desktop/bidding
streamlit run merchant_app.py --server.port 8502
```

Tip: you can preselect a merchant with query params, e.g. `?merchant_id=M007`.

### Time slot mapping (UI display)
UI shows real time windows while CSVs keep `time_slot` as integers for analytics:
- Slot 1: 06:00–08:00
- Slot 2: 08:00–10:00
- Slot 3: 10:00–12:00
- Slot 4: 12:00–14:00
- Slot 5: 14:00–16:00
- Slot 6: 16:00–18:00
- Slot 7: 18:00–20:00
- Slot 8: 20:00–22:00

### CLI (optional)

```bash
cd /Users/suzyliu/Desktop/bidding
python3 gsp_bidding_sim.py --ads data/input/ads_input_1000_local_usd_week_8slots_varslots_clean.csv --scenario experiment --reserve 5
```

### Generate input CSV via bidding agent (optional)

```bash
cd /Users/suzyliu/Desktop/bidding
python3 bidding_agent.py \
  --base data/input/ads_input_1000_local_usd_week_8slots_varslots_clean.csv \
  --out data/input/ads_input_generated.csv \
  --merchant M001 \
  --zipcode 98101 \
  --date-range 2025-07-12:2025-07-18 \
  --slots 1,3,4 \
  --budget 500 \
  --format wide
```


