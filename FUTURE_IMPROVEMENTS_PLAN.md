# Greyhound Betting & Analytics Engine: Future Improvements & Development Plan

This strategic development plan outlines the phased roadmap for future enhancements, algorithmic refinements, exotic bet optimization, and system automation.

---

## Phase 1: Advanced Modeling & Biomechanical Physics [IMPLEMENTED]

### 1.1 Micro-Sectional Velocity Profiling (Complete Engine vs Stoppers vs Swoopers)
* **Status:** Implemented in `analytics.py`.
* **Implementation:**
  - **Complete Engine (+6%):** Top 3 early speed (`speed_rank <= 3`) AND top 3 run-home stamina (`run_home_rank <= 3`).
  - **Leader Fader / Stopper Deceleration Penalty (-5% to -8%):** Identifies early leaders (`speed_rank <= 2`) with poor run-home stamina (`run_home_rank >= 6`) stepping up to middle and staying distances ($\ge 440$m: -5%, $\ge 500$m: -8%).
  - **Sustained Run-Home Swooper (+6% to +12%):** Verifies late-closing stamina under contested pace conditions (2–3+ contesting frontrunners).

### 1.2 Canine Weight Corridors & Kinetic Inertia
* **Status:** Implemented in `analytics.py` and `get_dog_profile()`.
* **Implementation:**
  - Computes each canine's historical median winning weight corridor (falling back to median race weight).
  - **Overweight Inertia Penalty:** $\Delta W \ge +1.0$kg penalised for increased turn inertia and centrifugal drift ($\max(-8\%)$).
  - **Condition Loss Penalty:** $\Delta W \le -1.2$kg penalised for potential dehydration, illness, or muscle loss ($\max(-6\%)$).
  - **Peak Sweet Spot Bonus:** $|\Delta W| \le 0.4$kg rewarded with a $+2\%$ physical prime modifier.
  - **Run-to-Run Fluctuation:** Sharp jumps $|\text{Weight}_{\text{today}} - \text{Weight}_{\text{last}}| \ge 1.2$kg penalised by $-8\%$.

### 1.3 Box-Trainer Synergy & Rolling Kennel Form Cycles
* **Status:** Implemented in `TrainerIntelligence` (`analytics.py`).
* **Implementation:**
  - **Box-Trainer Synergy:** Evaluates inside (Boxes 1–2), middle (3–6), and outside (7–8) strike rates with Bayesian shrinkage. Trainers schooling elite inside railers or wide sweepers receive up to $+8\%$ synergy bonus; negative synergy penalised up to $-6\%$.
  - **Rolling Kennel Momentum:** Evaluates last 10 starts per trainer chronologically. Hot stables ($\ge 30\%$ strike rate over 8+ runs) boosted by $+5\%$; cold stables (0 wins over 10+ runs) damped by $-5\%$. Clamped strictly within $[0.88, 1.15]$.

### 1.4 Expert Consensus Tie-Breaker Engine [IMPLEMENTED]
* **Status:** Complete & Integrated in `thedogs_scraper.py`, `analytics.py`, `app.py`, `cli.py`, and `templates/index.html`.
* **Implementation:**
  - **TheDogs Scraping:** Extracts ordered expert tips from race cards (`rug_{box}`) and stores in `races.expert_tips` as CSV strings (e.g. `'1,2,4,8'`).
  - **Deadlock Gating:** Triggers strictly when adjacent top-3 contenders have $|\Delta P| \le 0.025$ ($2.5\%$ win probability).
  - **Consensus Nudge:** Applies a subtle $+2.5\%$ multiplier to the subordinate contender favored by expert tips, breaking the tie while preserving field softmax normalization.
  - **+EV Edge Immunity:** Large algorithmic divergences ($|\Delta P| > 2.5\%$, e.g. Geelong R5 Fairway Flyer 75.9% vs Coolabah Station 4.7%) are strictly protected from expert bias.
  - **Auditable Telemetry:** Telemetry fields `expert_tips`, `expert_tip_rank`, `tie_breaker_applied`, and `tie_breaker_note` are surfaced in the API, CLI (`analyze-race`), and UI badges.
  - **CLI Backfill Utility:** `python cli.py backfill-tips [--limit N]` enables on-demand ingestion of historical tips.

---

## Phase 2: Exotic Bet Optimization & Automated Staking

### 2.1 Automated First 4 & Trifecta Ticket Builders [IMPLEMENTED]
* **Status:** Implemented in `exotic_bet_optimizer.py`, `app.py`, and `cli.py`.
* **Implementation:**
  - **Boxed First 4 & Trifecta Optimizer:** Generates 4-dog and 5-dog Boxed First 4 (24 & 120 combos) and 3-dog & 4-dog Boxed Trifecta (6 & 24 combos) with exact Harville joint probabilities, fair dividends, and multi-tier Flexi percentage calculations.
  - **Standout Banker Tickets:** Automatically detects dominant bankers ($\ge 35\%$ win probability with $\ge 10\%$ margin gap, or $\ge 45\%$ solo). Pinned Banker 1st with 4 Contenders 2nd, 3rd, 4th reduces 120 combos to 24 combos, multiplying Flexi percentage 5x for the same stake.
  - **Roving Banker Tickets:** Protects against early collision by roving the dominant banker across 1st, 2nd, 3rd, or 4th leg (96 combos).
  - **Dutching Exotic Calculator:** Inversely stakes across top 6 Harville finish permutations to guarantee equal target profit and high projected ROI.
  - **Exposed Endpoints & CLI:** `GET /api/races/{race_id}/exotics`, `POST /api/exotics/calculate`, and `python cli.py exotics <race_id>`.

### 2.2 Monte Carlo Correlated SRM Simulator [IMPLEMENTED]
* **Status:** Implemented in `monte_carlo_srm.py`, `srm_optimizer.py`, `app.py`, and `cli.py`.
* **Implementation:**
  - **Vectorized Stochastic Physics Engine:** Runs 10,000 simulated race iterations in $< 25$ms using NumPy.
  - **Micro-Phase Collision Scrimmages:** Models box lid reactions $\mathcal{N}(\mu, \sigma)$, lateral trajectory intersections, first-turn traffic clashes, leader slipstream drafting, and middle-distance lactic fatigue (Leader Faders).
  - **Empirical Joint Multi-Leg Distributions:** Evaluates arbitrary multi-leg Same Race Multis (`win`, `top_2`, `top_3`, `top_4`) without independence assumptions.
  - **Correlation Divergence Diagnostics:** Computes exact correlation ratio $\rho_{\text{corr}} = \hat{P}_{\text{MC}} / P_{\text{Harville}}$, flagging positive drafting synergy ($\ge 1.20\times$) and destructive interference clashes ($\le 0.82\times$).
  - **Exposed Endpoints & CLI:** `GET /api/races/{race_id}/srm/simulate`, `POST /api/srm/evaluate`, and `python cli.py srm <race_id>`.

### 2.3 Fractional Kelly-Criterion Portfolio Allocation
* **Feature:** Provide dynamic stake sizing based on bankroll percentage:
  $$\text{Stake}^* = \text{Bankroll} \times \frac{b \cdot p - q}{b} \times \text{FractionalMultiplier (e.g. 0.25)}$$
  Ensures bankroll protection against variance drawdowns while maximizing long-term geometric compounding.

---

## Phase 3: Autonomous Infrastructure & Market Expansion

### 3.1 Live Auto-Reconciliation Results Daemon [IMPLEMENTED]
* **Status:** Complete & Integrated in [`results_daemon.py`](file:///C:/Users/apple/.gemini/antigravity/scratch/greyhound-betting-engine/results_daemon.py).
* **Capabilities Delivered:**
  - **Autonomous Daemon Loop:** Runs a singleton daemon thread (`ResultsDaemon`) scanning pending jumped races ($T > 8\text{m}-10\text{m}$ post-jump).
  - **Live Scraper & Parser:** Scrapes official finish positions, finishing times, official margins, and SP odds from TheDogs.com.au.
  - **Dual Persistence:** Automatically updates `race_runners` and adds completed career stats to `past_performances`.
  - **Prediction Reconciliation Engine:** Freezes model Top 4 predictions prior to jump in `historical_predictions` and reconciles against actual 1st-4th placings in `reconciled_results`.
  - **Telemetry & P&L Tracking:** Automatically computes Win strike rate, Place strike rate, Top 2 strike, SRM 3-of-4 podium coverage, Exact First 4 hits, total flat turnover, net profit, and ROI.
  - **API & CLI:** Exposes `GET /api/results/status`, `POST /api/results/reconcile-now`, `GET /api/results/pnl`, plus CLI commands `python cli.py results-daemon` and `python cli.py reconcile-now`.

### 3.2 Automated Daily Brier Score Self-Calibration [IMPLEMENTED]
* **Status:** Complete & Integrated in `daily_learning_engine.py`, `analytics.py`, `app.py`, and `cli.py`.
* **Capabilities Delivered:**
  - **Multi-Class Brier Score & Cross-Entropy Log-Loss:** Evaluates multi-runner joint probabilities across 650+ completed historical races.
  - **Expected Calibration Error (ECE):** Evaluates probability deciles ($0\%-5\%, 5\%-10\%, \dots, 50\%-100\%$) to diagnose over/under-confidence.
  - **Continuous Softmax Temperature Optimization:** Uses bounded scalar minimization ($T \in [1.4, 3.2]$) to optimize per-track log-loss. Successfully calibrated 42 Australian circuits, saving optimized temperatures into SQLite table `track_calibrations`.
  - **Dynamic In-Engine Retrieval:** `analytics.predict_race_probabilities()` dynamically loads each track's empirically calibrated temperature.
  - **Endpoints & CLI:** `GET /api/calibration/metrics`, `GET /api/calibration/status`, `POST /api/calibration/auto-tune`, and CLI `python cli.py brier-audit`, `python cli.py auto-tune`.

### 3.3 Multi-Bookmaker Odds Aggregator & Arbitrage Scanner
* **Current State:** Live fixed odds are fetched from Sportsbet.
* **Expansion:** Integrate real-time API connectors for **TAB**, **Ladbrokes/Neds**, and **Bet365**:
  - Automatically identify the best market price across all four bookmakers for each +EV selection.
  - Detect bookmaker pricing inefficiencies and cross-bookmaker arbitrage opportunities.

### 3.4 Background Pre-Race Dog Dossier Hydration Pipeline [IMPLEMENTED]
* **Status:** Complete & Integrated in `dog_hydration_daemon.py`, `app.py`, and `cli.py`.
* **Capabilities Delivered:**
  - **Asynchronous Pre-Race Ingestion:** Prioritizes upcoming runners scheduled to jump today/tonight with missing sectional histories or track PBs.
  - **TheDogs Scraping & Master Sync:** Ingests box records, track PBs, career starts, and lifetime sectional split logs into `past_performances` and `greyhounds`.
  - **Daemon & On-Demand Worker:** `DogHydrationDaemon` runs continuously in background (interval 300s, batch size 40), plus manual pass via `POST /api/hydration/run` and `python cli.py hydrate-upcoming`.

---

## Phase 4: UI / UX & Dashboard Enhancements

### 4.1 Mobile-First Progressive Web App (PWA)
* Optimize the web interface for mobile screens with touch-friendly ticket builders, swipeable race cards, and dark-mode Sportsbet rug color schemes.

### 4.2 Real-Time Audio / Push Alerts for High +EV Triggers
* Add configurable browser notifications / audio chimes when a runner's edge exceeds $+10\%$ within 5 minutes of box rise (late market drift opportunities).

### 4.3 Interactive Track Bias Heatmaps
* Surface an interactive visual SVG map for each track showing historical win percentages by box, average winning split times, and track record holders.
