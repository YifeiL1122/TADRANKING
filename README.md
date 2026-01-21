## GSP bidding simulation dashboard

### What you get
- Run the GSP simulation from an `ads_*.csv`
- Visualize results (per time slot, per merchant, per ad)

### Setup

```bash
cd /Users/suzyliu/Desktop/bidding
python3 -m pip install -r requirements.txt
```

### Run the dashboard

```bash
cd /Users/suzyliu/Desktop/bidding
streamlit run dashboard.py
```

### Merchant portal (商家侧前端)
This repo uses Streamlit multipage. After you start the app, open the left sidebar and switch to **Merchant Portal**.

### Merchant-only app (单独给商家看的)
Run a separate, merchant-only UI (no admin dashboard page):

```bash
cd /Users/suzyliu/Desktop/bidding
streamlit run merchant_app.py --server.port 8502
```

Tip: you can preselect a merchant with query params, e.g. `?merchant_id=M007`.

### CLI (optional)

```bash
cd /Users/suzyliu/Desktop/bidding
python3 gsp_bidding_sim.py --ads data/input/ads_experiment.csv --reserve-cpm 1.0 --scenario experiment
```


