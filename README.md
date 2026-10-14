# Greyhound Multi-Betting & Analytics Engine

A quantitative greyhound racing data collector, analytics engine, and **Positive Expected Value (+EV)** multi-bet optimizer designed for Australian greyhound racing.

---

## Architecture Overview

```
greyhound-betting-engine/
├── db.py                   # SQLite schema: tracks, meetings, races, runners, past_performances, weather
├── fasttrack_scraper.py    # Official FastTrack (GRV) scraper: meetings, split sectionals, PIR & past starts
├── thedogs_scraper.py      # TheDogs.com.au scraper: active/today's race cards, running styles & live odds
├── sportsbet_scraper.py    # Sportsbet official API scraper: live fixed win & place prices, flucs & scratchings
├── poller_daemon.py        # Autonomous background polling daemon (re-checks imminent races & scratchings)
├── backtest.py             # Historical simulation harness: ROI, Strike Rate, Yield, Drawdown & Brier score
├── weather_collector.py    # Real-time meteorological observations per track via Open-Meteo
├── scratchings_manager.py  # Late scratchings synchronizer & runner purge module
├── analytics.py            # Feature engineering: early speed, box draw bias, running style & fair odds
├── multi_optimizer.py      # Value detection & compound multi-bet builder (+EV combinations)
├── srm_optimizer.py        # Same Race Multi (SRM) correlational engine (Win, Top 2, Top 3, Top 4)
├── app.py                  # FastAPI REST server & Web dashboard
├── templates/index.html    # Real-time UI with live countdowns, Sportsbet pricing & Backtest Lab
└── cli.py                  # Unified CLI orchestrator
```

---

## 1. Prerequisites & Dependencies

The project runs on Python 3.10+ (tested on Python 3.14). The required libraries are:

```bash
pip install httpx beautifulsoup4 lxml fastapi uvicorn
```

---

## 2. CLI Commands

From the directory `C:\Users\apple\.gemini\antigravity\scratch\greyhound-betting-engine`:

### Initialize Database
```bash
python cli.py init
```

### Scrape Live Sportsbet Fixed Win & Place Odds
Pulls live domestic greyhound fixed odds and scratchings directly from Sportsbet:
```bash
python cli.py collect-sportsbet
```

### Start Background Polling Daemon
Runs the automated scheduler that periodically polls imminent races for odds and scratchings:
```bash
python cli.py daemon --interval 60
```

### Run Historical Strategy Backtest
Simulates quantitative betting rules across completed historical meetings:
```bash
python cli.py backtest --min-edge 0.03 --staking flat --stake 10 --odds-source sportsbet
```

### Scrape Official Past Starts & Sectionals (FastTrack)
Pulls race fields, box statistics, split-1 times, and detailed sectional history into the database:
```bash
python cli.py collect-fasttrack
```

### Scrape Today's Race Cards & Market Odds (TheDogs)
Pulls scheduled races and starting prices across Australia:
```bash
python cli.py collect-thedogs
```

### Fetch Real-Time Weather Observations
Retrieves current track temperature, humidity, rainfall, and wind speed:
```bash
python cli.py collect-weather
```

### Scan & Construct High-Odds +EV Multis
Finds individual positive-edge selections (from Sportsbet or SP) and compiles them into multi combinations:
```bash
python cli.py scan-multis --odds-source sportsbet
```

---

## 3. Web Dashboard

Launch the FastAPI interactive dashboard:

```bash
python app.py
# or double-click start_server.bat
```

Navigate to `http://127.0.0.1:8090` to access:
* **Live "Next to Jump" Countdown & Race Feed** with AWST/AEDT timezone toggle.
* **Sportsbet Live Odds & Value Edge Table** with direct dog dossier deep-dives.
* **Autonomous Poller Daemon Widget** with real-time cycle status, pause/resume, and immediate sync.
* **Compound Multi & Same Race Multi (SRM) Optimizer** configured for Sportsbet prices.
* **Historical Backtest & Strategy Lab** allowing interactive simulations and parameter tuning.
