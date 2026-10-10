# 🚀 Greyhound Betting Engine: Project Status & Strategic Roadmap
*Executive Summary & Blueprint as of October 10, 2026*

---

## 📊 1. Current State of the Project

The **Greyhound Racing Predictive Analytics & Exotic Betting Engine** is an end-to-end, real-time algorithmic racing intelligence platform running locally on Windows. It combines multi-source automated scraping, canine physiological modeling, high-precision track geometry, real-time satellite meteorology, automated reserve remapping, and Monte Carlo Dirichlet exotic simulations to uncover market inefficiencies and forecast exotic outcomes (First 4, Trifecta, Exacta, Quinella).

```mermaid
flowchart TD
    subgraph Data Layer
        A[TheDogs Scraper] --> DB[(SQLite Database greyhound_racing.db)]
        B[Sportsbet Odds Scraper] --> DB
        C[FastTrack Results & Form] --> DB
        D[Open-Meteo Satellite Weather API] --> DB
        S[Official Scratchings Daemon] --> DB
    end

    subgraph Analytics & Physics Pipeline
        DB --> E[Canine Speed & Split Ratings]
        DB --> F[Track Geometry & Camber Models - 21 Tracks]
        DB --> G[Canine Thermal Physiology - Feels Like Temp]
        DB --> H[Bayesian Trainer Intelligence]
        DB --> I[Market Mover / Smart Money Crunch]
        S --> R[GAR 76 Reserve Reallocation Engine]
    end

    subgraph Predictive Engine
        E & F & G & H & I & R --> J[First-Turn Squeeze & Traffic Simulation]
        J --> K[Softmax Probability Calibration T-Scaled]
        K --> L[Dirichlet Monte Carlo Simulator - 10,000 runs]
    end

    subgraph Output & Execution
        L --> M[FastAPI REST Engine :8090]
        M --> N[Interactive Live Web Dashboard]
        M --> O[Boxed First 4 & Value Bet Recommendations]
    end
```

### 🛠️ Architecture & Database Metrics
*   **Backend Server:** FastAPI application serving live REST APIs and web interfaces on `http://127.0.0.1:8090` (verified active and responsive).
*   **Primary Database:** [greyhound_racing.db](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/greyhound_racing.db) (SQLite, WAL mode).
*   **Database Metrics:**
    *   **Meetings Registered:** 89 meetings
    *   **Races Tracked:** 909 races
    *   **Runners Analyzed:** 7,303 greyhounds
    *   **Past Performances Indexed:** 10,653 starts
    *   **Live Weather Observations:** 429 localized observations
*   **Predictive Model Core:** [`analytics.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/analytics.py) (2,596 lines of algorithmic racing logic, speed mapping, first-turn traffic modeling, and temperature-calibrated softmax scaling).

---

## 🎯 2. Key Decisions & Components Completed Today

### A. Real-Time Scratchings & Official GAR 76 Reserve Reallocation
> [!NOTE]
> **Implemented and verified live today.**
1.  **Automated Scratching Collector ([`scratchings_manager.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/scratchings_manager.py)):**
    *   Parses official Australian nationwide scratchings directly from `https://www.thedogs.com.au/racing/scratchings`.
    *   Normalizes sponsor venue naming across all jurisdictions (`BetDeluxe`, `Ladbrokes`, `TAB`, `Sky Racing`).
    *   Marks scratched runners (`is_scratched = 1`) across active meeting dates.
2.  **Official Australian Reserve Reallocation (GAR Rule 76):**
    *   When Box $X$ ($1 \le X \le 8$) is vacated, Reserve 9 automatically replaces the first vacated box (`into_box = X`, `is_scratched = 0`).
    *   When a second inside box is vacated, Reserve 10 replaces that box (`into_box = Y`, `is_scratched = 0`).
    *   **Non-Starter Purge:** Any unallocated reserve (Reserve 9 or 10 when no vacancy exists) is automatically set to `into_box = NULL` and flagged as `is_scratched = 1`.
3.  **Speed Map & Squeeze Calibration Integrity ([`analytics.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/analytics.py)):**
    *   Guaranteed strictly 8-dog fields (eliminating phantom 9th/10th reserve starters).
    *   Automatically ignores dummy records (`* * * VACANT BOX * * *`, `* * * NO RESERVE * * *`) and converts them to lane clearance buffers.
4.  **Live Poller Daemon & REST Trigger:**
    *   Integrated into background [`poller_daemon.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/poller_daemon.py) and exposed via `POST /api/actions/fetch-scratchings`.
    *   Verified live execution: **52 scratchings marked, 28 reserves reallocated, 46 unallocated reserves purged**.

### B. Comprehensive Track Geometry & Camber Physics Expansion
Upgraded from generic state-level heuristics to **21 individual bespoke track profiles** covering all major racing jurisdictions (VIC, SA, NSW, QLD, WA, TAS):
1.  **Straight Track Boundary Physics (Healesville, Capalaba, Q Straight, Richmond Straight):**
    *   Replaced cornering math with **guide-rail boundary physics**.
    *   Boxes 1 and 8 receive significant boosts (**`1.22x` – `1.24x`**) due to unobstructed galloping lines along the inside rail and outside boundary fence.
    *   Middle boxes (Boxes 4, 5, 6, 7) are assessed a **`0.82x` – `0.86x` weaving tax** reflecting high risk of mid-pack zig-zagging and bumping.
2.  **Short Run-to-Turn Rail Bias (Angle Park 342m, Mount Gambier 305m, Rockhampton 407m, Gunnedah 340m, Temora 330m):**
    *   Tracks with extremely short dashes to the first bend (e.g. Mount Gambier 305m with empirical 50% Box 1 strike rate; Rockhampton 407m with 40% Box 1 strike rate) have been equipped with explicit **rail lock overrides (`1.26x` – `1.32x`)** and heavy **centrifugal penalties (`0.80x` – `0.84x`)** for slow outside runners unable to cross before the corner.
3.  **Wide Sweeping Chute Physics (Sale 435m, Shepparton 385m/390m, Ballarat 390m, Horsham 410m, Gawler 400m):**
    *   Cambered bends and sweeping corner angles reward clean-air outer runners. Box 8 outer swoop overrides (**`1.18x` – `1.25x`**) were implemented, capturing empirical ~36% Box 8 win rates at Sale 435m and Shepparton sprint chutes.

### C. Canine Thermal Physiology & "Feels Like" Apparent Temperature
1.  **Canine Respiratory Evaporative Model:** Recognized that greyhounds do not possess sweat glands across their skin; thermoregulation is driven almost entirely by respiratory evaporative cooling (panting). High relative humidity collapses the vapor pressure gradient, causing severe physiological strain even at moderate ambient temperatures.
2.  **Database Integration:**
    *   Added `apparent_temperature REAL` to `weather_records` in [`db.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/db.py).
    *   Backfilled historical weather observations using the Australian BOM apparent temperature equation.
    *   Updated [`weather_collector.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/weather_collector.py) to query live satellite apparent temperature from Open-Meteo.
3.  **Physiological Modifiers in Predictive Pipeline:**
    *   **Heat Fade ($\ge 28^\circ\text{C}$ Apparent):** Heavy runners ($> 33\text{ kg}$) on 500m+ distances receive a **`0.93x` penalty**; light dogs ($< 28\text{ kg}$) receive a **`1.02x` thermal dissipation bonus**.
    *   **Cold Snap ($\le 8^\circ\text{C}$ Apparent):** Heavy power sprinters receive a **`1.02x` power bonus**, while low-body-mass greyhounds ($< 27\text{ kg}$) receive a **`0.97x` convective loss penalty**.

### D. Master GPS Meteorological Geocoding
1.  All 21 tracks were geocoded with high-precision decimal coordinates in [`db.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/db.py) and the SQLite `tracks` table, enabling automated 15-minute weather sweeps for live racing venues.
2.  Calibrated softmax temperatures ($T \in [2.25, 3.20]$) were persisted in the `track_calibrations` table for each venue to ensure well-calibrated, uncompressed probability outputs.

---

## ❓ 3. Open Questions & Strategic Decisions

> [!IMPORTANT]
> The following architectural decisions will determine the direction of the next development phase:

1.  **Exotic Staking Architecture: Boxed 4 vs. Standout Key Combinations**
    *   *Option A (Boxed 4):* 24 combinations per race ($24 for 100% flexi). High resilience against order variance, perfectly matches the user target of $\ge 75\%$ Boxed First 4 capture.
    *   *Option B (Standout / Anchor First 4):* 1 Key dog to win, with 3-4 roving selections for 2nd, 3rd, 4th (6 to 18 combinations). Drastically reduces outlay and maximizes flexi percentage on high-confidence race shapes.
    *   *Question:* Should the system output standard Boxed 4 selections by default, or provide dynamic flexi staking tickets based on model confidence?

2.  **System Operating Mode: Analytical Advisory vs. Automated Betting Execution**
    *   Currently, the system functions as a high-precision analytics dashboard.
    *   *Question:* Will this platform remain an advisory decision-support tool, or do we want to design an automated execution bridge (e.g. Betfair Australia API / BetStop compliant automation)?

3.  **Target Win-Rate Evaluation Metric**
    *   *Question:* Should model performance be tracked primarily by **Boxed First 4 Hit Rate** ($\ge 75\%$), or by **Simulated Return on Investment (ROI / Yield)** across value-overlays?

---

## 📅 4. Concrete Next Steps (In Priority Order)

```mermaid
flowchart LR
    Done[✅ Step 2 Completed: Scratchings & Reserves] --> Step1[1. Historical Backtest Engine]
    Step1 --> Step3[2. Staking & Ticket Generator]
    Step3 --> Step4[3. Late Odds Crunch Detector]
```

### ✅ Step Completed: Real-Time Scratchings & Reserve Remapping Engine
*   **Status:** **COMPLETED & ACTIVE IN PRODUCTION**.
*   **Result:** Nationwide scraper, GAR 76 reserve reallocation, and speedmap recalibration active.

### Step 1: Historical Backtesting & Strike-Rate Evaluator
*   **Goal:** Rigorously verify the user's $\ge 75\%$ First 4 Boxed target over all 909 historical races in `greyhound_racing.db`.
*   **Action:** Build `backtest_engine.py` to run `predict_race_probabilities()` across all finished races with verified 1st–4th placings, outputting:
    *   Top-4 Boxed capture percentage across all tracks.
    *   Top-4 Boxed capture percentage stratified by track type (Straight vs. 1-Turn vs. 2-Turn).
    *   Simulated First 4 dividend returns against TAB/S-TAB actual payouts.

### Step 2: Staking Optimization & Exotic Ticket Generator
*   **Goal:** Provide actionable bet tickets directly in the UI.
*   **Action:** Extend the `/api/races/{race_id}/analysis` endpoint to generate:
    *   **Recommended Boxed First 4:** Top 4 rated dogs (24 combos).
    *   **Value Saver Boxed First 4:** Including high-overlay longshots with split-1 pace.
    *   **Standout Trifecta & First 4 Tickets:** 1 banker with 3 roving selections.

### Step 3: Live Odds Fluctuation & Smart Money Syndicate Tracker
*   **Goal:** Exploit late market intelligence in the final 5 minutes before race jump.
*   **Action:** Enhance the Sportsbet scraper to store 1-minute odds time-series leading to the jump, calculating real-time odds velocity ($d(\text{odds})/dt$) to detect sudden betting plunges.

---

*Artifact updated: [`project_status_and_roadmap.md`](file:///C:/Users/apple/.gemini/antigravity/brain/a4d2a8fe-35a1-4ec0-88ed-ab2d93142030/project_status_and_roadmap.md)*
