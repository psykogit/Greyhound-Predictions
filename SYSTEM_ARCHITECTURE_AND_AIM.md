# Greyhound Multi-Betting & Analytics Engine: Complete System Architecture & Operational Guide

## 1. Executive Summary & Core Aim

The **Greyhound Multi-Betting & Analytics Engine** is an institutional-grade, physics-informed quantitative modeling platform and automated betting assistant developed specifically for Australian greyhound racing. 

### Core Aim
The primary objective of the engine is to **consistently identify Positive Expected Value (+EV) opportunities** across Australian greyhound markets and construct mathematically sound singles, Same-Race Multis (SRMs), compound Cross-Meeting Multis, and exotic betting combinations (Exacta, Trifecta, First 4).

Rather than relying purely on historical starting prices or unadjusted finish times, the engine models the **fundamental physics and biomechanics of canine track racing**:
- Kinetic trajectory from the boxes (split-1 sectional acceleration).
- Track-specific geometry (corner radius, run-up length, chute alignment, transition banking, track surface loam/sand).
- Spatial congestion and collision dynamics (inside pinch, middle box squeeze, outside swooper clean-air momentum).
- Real-time weather observations (ambient temperature, wind vector, track surface moisture & kickback resistance).
- Bayesian trainer form intelligence.
- Correlational joint probabilities using modified Harville discount formulations.

---

## 2. System Architecture & Components

The platform is organized into a modular pipeline spanning ingestion, feature engineering, physical simulation, market synthesis, and web visualization:

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                   DATA INGESTION LAYER                                  │
├────────────────────────────┬─────────────────────────────┬──────────────────────────────┤
│    fasttrack_scraper.py    │     thedogs_scraper.py      │    sportsbet_scraper.py      │
│  - Official split times    │  - Race card schedules      │  - Live fixed win/place odds │
│  - Career past starts      │  - Running styles (RR, ER)  │  - Real-time scratchings     │
│  - Historical PBs & grades │  - Historical SP odds       │  - Live price fluctuations   │
└─────────────┬──────────────┴──────────────┬──────────────┴──────────────┬───────────────┘
              │                             │                             │
              ▼                             ▼                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                            DATABASE & STORAGE (db.py / SQLite)                          │
│   `tracks`, `meetings`, `races`, `greyhounds`, `race_runners`, `past_performances`      │
│   WAL Mode Enabled • Thread-safe pooled connections • Foreign Key Integrity             │
└─────────────┬───────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                          REAL-TIME WEATHER (weather_collector.py)                       │
│   Live Open-Meteo API • GPS Track Coordinates • Surface Wetness & Wind Vectors          │
└─────────────┬───────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                          QUANTITATIVE ENGINE (analytics.py)                             │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. Early Speed Sectionals: Delta to track/race benchmarks & split rank                  │
│ 2. Track & Distance Geometry: 30+ Dedicated Track Models (Sandown, Wenty, Mandurah, etc)│
│ 3. Congestion & Collision Physics: Box squeeze, carted-wide checks, clearance runway    │
│ 4. Track Debutante Synthesis: Global class-adjusted synthetic PBs & market blending     │
│ 5. Bayesian Trainer Intelligence: Track & distance-specific strike rates                │
│ 6. Harville Joint Probability Modeling: Win, Top 2, Top 3, Top 4, and Fair Odds         │
└─────────────┬───────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                    OPTIMIZATION & BET BUILDERS (multi / srm_optimizer.py)               │
├──────────────────────────────────────────────────────────┬──────────────────────────────┤
│                   multi_optimizer.py                     │       srm_optimizer.py       │
│  - Compound +EV Cross-Track Multi-Bet Combinations       │  - Intra-Race Correlation    │
│  - Kelly-Criterion fractional stake sizing               │  - Same Race Multi Pricing   │
│  - Minimum edge thresholding (> 3% - 5%)                 │  - Win + Top 2/3/4 Legs      │
└─────────────┬────────────────────────────────────────────┴──────────────┬───────────────┘
              │                                                           │
              ▼                                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           APPLICATION & INTERFACE LAYER                                 │
├──────────────────────────────────────────────────────────┬──────────────────────────────┤
│                   app.py (FastAPI Server)                │   poller_daemon.py (Daemon)  │
│  - RESTful APIs for predictions, runners, & multis       │  - Auto-polls imminent races │
│  - Real-time WebSocket / auto-refresh updates            │  - Fetches weather & odds    │
│  - Database export & performance reconciliation          │  - Purges late scratchings   │
├──────────────────────────────────────────────────────────┴──────────────────────────────┤
│                        templates/index.html & static/app.js                             │
│  - Interactive Web Dashboard with Sportsbet official rug colors (checkers, stripes, etc)│
│  - Dynamic countdown timers, edge indicators, and historical backtest laboratory        │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. How the Mathematical Engine Works Step-by-Step

Every race evaluated by the engine passes through an 8-stage quantitative calculation pipeline:

### Stage 1: Feature Extraction & Par Normalization
- The engine calculates the race-level baseline: `avg_race_split`, `avg_best_time`, and `fastest_split`.
- **Early Speed Score:** Runners are evaluated based on their first split time relative to the field:
  $$\text{Speed Delta} = \text{avg\_race\_split} - \text{runner\_split}$$
  Converted into an early speed index ($1.0$ to $10.0$).
- **Benchmark Motor (PB) Analysis:** Each runner's best winning time at the specific track and trip is compared against the field minimum (`delta_to_min`).

### Stage 2: Track Debutante Proxy Synthesis
- If an unproven greyhound has never raced at the venue, its time cannot simply be treated as $0.0$.
- The engine dynamically synthesizes a proxy personal best using the dog's global class grade differential:
  $$\text{PB}_{\text{synth}} = \text{avg\_best\_time} - (\Delta\text{Grade} \times 0.12\text{s}) + 0.08\text{s (greenness tax)}$$

### Stage 3: Physics-Informed Track & Box Geometry Correction
- Australian tracks vary widely: tight two-turn bullrings (e.g., Casino), premier two-turn metropolitan circuits (Sandown, Wentworth Park), straight dragstrips (Healesville, Capalaba), and straight chute starts (Mandurah 300m/400m, Gunnedah 431m).
- The engine applies dedicated distance-specific multipliers (`box_factor`):
  - **Golden Rail Guidance:** Box 1 and 2 advantages when possessing early speed to hold the fence.
  - **Mid-Field Trap Penalties:** Adjusting for squeeze when middle boxes are flanked by aggressive cutters.
  - **Wide Sweeper Camber:** Elevating Box 8 when wide sweeping turns allow uninterrupted centrifugal momentum without collision.

### Stage 4: Congestion, Squeeze & Collision Simulation
- Evaluates runner interactions based on running style (`RR` hard railer, `ER` early railer, `WIDE` swooper):
  - **Vacant Box Runway:** $+6\%$ power bonus if immediate neighboring boxes are vacant or scratched.
  - **Pinch Traps:** Penalties if an inside runner is significantly slower than outer dogs that cross inwards.
  - **Chute Start Logic:** Straight chutes (e.g. Mandurah) are exempted from circular bend squeeze penalties because dogs accelerate in parallel straight lanes.

### Stage 5: Bayesian Trainer Intelligence
- Historical trainer performance is evaluated across track and distance bands with empirical Bayesian shrinkage:
  $$\text{Factor} = \frac{N \cdot \text{ActualWinRate} + K \cdot \text{PriorWinRate}}{N + K}$$
  Shrunk towards track average when sample size is low ($N < 10$), and capped within $[0.88, 1.15]$.

### Stage 6: Environmental & Meteorological Impact
- **Track Wetness & Rain:** On wet tracks with heavy kickback, early speed weights increase ($\approx 80\%$), and backmarker swooper bonuses are nullified due to vision and flying sand resistance.
- **Wind Vector:** Headwinds penalize stamina-deficient front-runners, while tailwinds assist pure speed.

### Stage 7: Market Probability Blending & Sanity Dampening
- The raw physical power score is converted to a baseline model probability:
  $$P_{\text{model}}(i) = \frac{\text{PowerScore}_i}{\sum_j \text{PowerScore}_j}$$
- In races with comprehensive data, model probabilities are blended with normalized market implied probabilities ($78\%$ model / $22\%$ market).
- **Adaptive Debutante & Longshot Protections:**
  - Strongly backed debutantes (SP $\le \$4.50$) receive elevated market prior weighting ($48\%$).
  - Cold-form extreme longshots (SP $\ge \$33.00$ with form $< 3.5$) have market sanity weight increased to $60\%$ to prevent stale, outdated PBs from producing false +EV value.

### Stage 8: Harville Place & Exotic Matrix Calculations
- Harville's formulation is applied to compute conditional probability distributions for Top 2 (Quinella/Exacta), Top 3 (Place/Trifecta), and Top 4 (First 4):
  $$P(\text{Place}_i) \approx \min\left(0.94, (P_{\text{win}, i} \times 1.85) + 0.12\right)$$
- **Fair Odds Calculation:**
  $$\text{Fair Win Odds}_i = \frac{1}{P_{\text{win}, i}}, \quad \text{Fair Place Odds}_i = \frac{1}{P_{\text{place}, i}}$$
- **Edge Detection:**
  $$\text{Edge} = P_{\text{model}} - \frac{1}{\text{Bookmaker Odds}}$$
  If $\text{Edge} > 0.03$, the runner is flagged as an active positive value bet (+EV).

---

## 4. Key Track Modules Implemented

The platform currently includes customized, ground-truth calibrated physics modules for:
1. **Sandown Park (VIC):** Two-turn 515m/595m metropolitan geometry, capped rail deficits for railers, outside clean-air camber.
2. **The Meadows (VIC):** 525m dual-pole dominance (Box 1 rail command + Box 8 outer sweep runway).
3. **Wentworth Park (NSW):** Premier 520m circuit, Box 1 "Golden Box", Box 2 premier slipstream rail line, Box 5 graveyard squeeze, and multi-distance support (280m, 520m, 720m).
4. **Mandurah (WA):** 2023 reconstructed 593m circuit with 140m straight chute at 300m, Box 8 outer sweeper bonus, and chute start collision exemption.
5. **Hobart (TAS):** Tattersall's Park wide loam circuit, 340m outside sweeper dominance (Box 8 winning 100% of short dashes), and 461m Hobart Thousand championship balance.
6. **Gunnedah (NSW):** Multi-distance regional sand dynamics for 340m short sprint, 431m chute start, and 527m staying test; Maiden Racecraft experience modifiers.
7. **Shepparton, Ballarat, Bendigo, Geelong, Warragul, Warrnambool (VIC):** Sweeping U-turn regional oval profiles with universal running style fallbacks.
8. **Straight Tracks (Healesville, Capalaba, Richmond Straight, Murray Bridge Straight):** Outside fence guide rails (Boxes 1 & 8) and middle dog weaving penalties.
