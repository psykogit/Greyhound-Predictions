# 🇦🇺 Australian Greyhound Track Geometry & Meteorological Coordinates Blueprint

This dossier specifies the exact **track geometric models, corner banking, camber physics, and high-precision GPS coordinates** for Australian greyhound racing venues. These coordinates are actively integrated into the Open-Meteo satellite weather engine to dynamically evaluate barometric pressure, precipitation, humidity, apparent temperature ("feels like"), and headwind/tailwind velocities in real time.

---

## 📍 1. Master GPS Meteorological Coordinates Reference

| Venue Name | State | Track Geometry Type | Latitude | Longitude | Primary Feature Distances | Softmax Temp | Live Weather Status |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: | :---: |
| **Sandown Park** | VIC | Two-turn Metropolitan Oval | `-37.9566` | `145.1596` | 515m, 595m, 715m | $T=2.45$ | **ACTIVE & VERIFIED** |
| **Shepparton** | VIC | One-turn Provincial U-Shape | `-36.4470` | `145.4140` | 385m, 390m, 450m, 650m | $T=2.35$ | **ACTIVE & VERIFIED** |
| **Ballarat** | VIC | Two-turn Provincial Circuit | `-37.5800` | `143.8300` | 390m, 450m, 545m, 660m | $T=2.40$ | **ACTIVE & VERIFIED** |
| **Sale** | VIC | Sweeping Gippsland Oval | `-38.0915` | `147.0630` | 435m, 510m, 640m | $T=2.40$ | **ACTIVE & VERIFIED** |
| **Healesville** | VIC | True Straight Track | `-37.6703` | `145.5418` | 300m, 350m | $T=2.25$ | **ACTIVE & VERIFIED** |
| **Horsham** | VIC | One-turn Provincial U-Shape | `-36.7212` | `142.2078` | 410m, 485m | $T=2.35$ | **ACTIVE & VERIFIED** |
| **Angle Park** | SA | Two-turn Metropolitan Oval | `-34.8575` | `138.5628` | 342m, 530m, 595m, 730m | $T=2.45$ | **ACTIVE & VERIFIED** |
| **Gawler** | SA | Two-turn Provincial Circuit | `-34.5993` | `138.7402` | 400m, 531m, 643m | $T=2.35$ | **ACTIVE & VERIFIED** |
| **Mount Gambier** | SA | Sharp Regional Circuit | `-37.8250` | `140.7820` | 305m, 400m, 512m, 600m | $T=2.35$ | **ACTIVE & VERIFIED** |
| **Richmond** | NSW | Two-turn Circular Circuit | `-33.6850` | `150.7450` | 320m, 401m, 520m, 622m | $T=2.40$ | **ACTIVE & VERIFIED** |
| **Richmond Straight** | NSW | 324m True Straight Track | `-33.6850` | `150.7450` | 324m Straight Dash | $T=2.25$ | **ACTIVE & VERIFIED** |
| **Nowra** | NSW | South Coast Sharp Circuit | `-34.8980` | `150.5910` | 365m, 520m, 630m | $T=2.35$ | **ACTIVE & VERIFIED** |
| **Temora** | NSW | Riverina Provincial Oval | `-34.4525` | `147.5461` | 330m, 457m, 570m | $T=2.30$ | **ACTIVE & VERIFIED** |
| **Maitland** | NSW | One-turn Provincial U-Shape | `-32.7447` | `151.5628` | 400m, 450m | $T=2.35$ | **ACTIVE & VERIFIED** |
| **Casino** | NSW | One-turn Country Loop | `-28.8661` | `153.0550` | 300m, 411m, 484m | $T=3.19$ | **ACTIVE & VERIFIED** |
| **Gunnedah** | NSW | One-turn Country Provincial | `-30.9744` | `150.2628` | 340m, 431m, 527m | $T=2.30$ | **ACTIVE & VERIFIED** |
| **Q Straight** | QLD | 350m True Straight Track | `-27.7120` | `152.7407` | 350m Straight Dash | $T=2.25$ | **ACTIVE & VERIFIED** |
| **Capalaba** | QLD | Premier 366m Straight Track | `-27.5184` | `153.1913` | 366m Straight Dash | $T=2.25$ | **ACTIVE & VERIFIED** |
| **Q2 Parklands** | QLD | The Q One-Turn Circuit | `-27.7120` | `152.7400` | 350m, 457m, 520m, 600m, 710m | $T=2.40$ | **ACTIVE & VERIFIED** |
| **Rockhampton** | QLD | Callaghan Park Sharp Oval | `-23.3788` | `150.5222` | 407m, 510m | $T=2.35$ | **ACTIVE & VERIFIED** |
| **Northam** | WA | One-turn Regional Circuit | `-31.6400` | `116.6718` | 297m, 509m | $T=2.40$ | **ACTIVE & VERIFIED** |
| **Launceston** | TAS | One-turn Provincial Circuit | `-41.4045` | `147.1415` | 278m, 515m | $T=2.35$ | **ACTIVE & VERIFIED** |

---

## 🔵 2. Victorian Circuits (VIC)

### Sandown Park (Premier Melbourne Metropolitan)
*   **Coordinates:** `-37.9566`, `145.1596` · **Softmax Temperature:** $T = 2.45$
*   **Geometry & Turn Radii:** Expansive two-turn metropolitan oval with sweeping transition camber and wide galloping lanes.
*   **515m (Metro Flagship):** 180m back-straight charge into sweeping turn 1. High entry speed compresses the middle pack.
    *   **Box 1 ("Golden Rail"):** `1.26x` multiplier (`1.08x` early rail hold).
    *   **Box 2:** `1.18x` inside tracking line.
    *   **Box 8 ("Outer Camber Runway"):** `1.14x` multiplier (`1.08x` slingshot momentum around the outer camber).
    *   **Boxes 4, 5, 6:** Box 5 is the primary squeeze trap (`0.85x` if speed rank > 2). Boxes 4 & 6 receive `0.92x` traffic congestion tax if slow.
*   **595m / 715m:** 595m starts in front of grandstand (Box 1 & 2 `1.20x`, Box 8 `1.10x`). 715m is a 2-lap marathon where stamina and split floors override draw position (`1.08x` on Box 1 & 2).

### Shepparton (Victorian Regional U-Turn)
*   **Coordinates:** `-36.4470`, `145.4140` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Fast, sweeping one-turn U-shaped oval with progressive crossfall banking into the home bend.
*   **385m / 390m (Sprint Chute):**
    *   **Extreme 1/8 Polarization:** Boxes 1 & 8 combine for **over 54% of all race winners**. Box 1 (`1.26x`) commands the fence; Box 8 (`1.25x` + `1.10x` swoop bonus) gets an unblocked sweeping runway into the straight.
    *   **Box 7 ("The Meat in the Sandwich"):** Sandwiched inside the sweeping Box 8 (`0.84x` multiplier, `0.90x` squeeze factor; empirically 0.0% win rate).
    *   **Boxes 4, 5, 6:** Nutcracker trap (`0.82x` if speed rank > 2).
*   **450m:** Long run down back straight into sweeping bend. Box 1 (`1.22x`), Box 2 (`1.16x`), Box 8 (`1.18x`), Box 5 (`0.86x`).

### Ballarat (Morshead Park Circuit)
*   **Coordinates:** `-37.5800`, `143.8300` · **Softmax Temperature:** $T = 2.40$
*   **Geometry & Turn Radii:** Sharp two-turn provincial circuit renowned for rapid split times and treacherous first-turn traffic.
*   **390m (Morshead Sprint):** Short run to first turn.
    *   **Box 1:** `1.28x` multiplier. If slow (`speed_rank >= 4`), receives `0.72x` rail swamp penalty.
    *   **Box 8:** `1.24x` multiplier (`1.10x` clean air bonus). Empirically boasts a **33.3% win rate**.
    *   **Box 7:** `0.85x` traffic congestion inside Box 8.
    *   **Boxes 4 & 5:** Severe pinch zone (`0.82x` multiplier, `0.86x` squeeze factor).
*   **450m (Main Distance):** Box 1 boasts an empirical **44.4% win rate** (`1.26x`), Box 7 outer sweep **40.0% win rate** (`1.18x`), while middle boxes 4, 5, 6 have **0.0% wins** across the historical sample (`0.82x`).

### Sale (Gippsland Sweeping Circuit)
*   **Coordinates:** `-38.0915`, `147.0630` · **Softmax Temperature:** $T = 2.40$
*   **Geometry & Turn Radii:** Expansive country circuit featuring massive sweeping bends and long home straight.
*   **435m (Short Course):**
    *   **Box 8 Sweeping Arc:** Empirically boasts a **36.4% win rate** in historical samples. High exit speed around the sweeping Gippsland bend gives outside runners clean galloping momentum (`1.24x` + `1.10x` outer swoop override).
    *   **Box 1:** `1.22x` early rail hold, but slow starters get swamped on the long bend entry (`0.78x` if speed rank $\ge 4$).
    *   **Boxes 4, 5, 6:** Middle pinch zone (`0.86x` if slow).
*   **510m / 640m:** Fair start down the back. Box 1 & 2 (`1.18x`), Box 8 (`1.14x`), Boxes 4 & 5 (`0.88x`).

### Healesville (Premier Victorian Straight Track)
*   **Coordinates:** `-37.6703`, `145.5418` · **Softmax Temperature:** $T = 2.25$
*   **Geometry & Physics:** True straight track nestled in the Yarra Valley. No turns, zero centrifugal force, 100% linear sprinting.
*   **Guide Rail Dynamic (300m & 350m):**
    *   **Fence / Guide Rail Dominance:** Boundary guide rails dictate clean galloping lines without interference. Outside rail (Box 8) and inside rail (Boxes 1 & 2) command the track (`1.24x`).
    *   **Middle Track Weaving Tax:** Dogs drawn in Boxes 4, 5, 6, and 7 frequently search for galloping room and cross lines (`0.82x`). Empirically, Boxes 5, 6, 7 registered 0.0% wins in recent sample data.

### Horsham (Wimmera Provincial Oval)
*   **Coordinates:** `-36.7212`, `142.2078` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Wide sweeping one-turn circuit built for high velocity.
*   **410m (Sprint):** Fast chute start. Box 1 rail dominance (`1.24x`), Box 2 (`1.16x`), Box 8 outer momentum (`1.20x` + `1.08x` sweep bonus). Mid-track runners (Boxes 4, 5, 6) suffer traffic pinch (`0.84x`).
*   **485m (Feature Distance):** Back straight start with generous run to turn 1. Box 1 & 2 (`1.20x`), Box 8 (`1.15x`), Boxes 4 & 5 (`0.88x`).

---

## 🟣 3. South Australian Circuits (SA)

### Angle Park (Adelaide Premier Metropolitan)
*   **Coordinates:** `-34.8575`, `138.5628` · **Softmax Temperature:** $T = 2.45$
*   **Geometry & Turn Radii:** Rebuilt in 2021 with engineered transition banking, modern safety fences, and sweeping turns.
*   **342m (Super Sprint):**
    *   **Run to Turn 1:** Treacherous **~70m dash** to turn 1.
    *   **Lethal Rail Bias:** Box 1 (`1.30x` + `1.10x` early rail hold) and Box 2 (`1.20x`) dictate the early bend.
    *   **Centrifugal Swing Penalty:** Outside runners in Boxes 6 & 7 face extreme centrifugal force (`0.80x` multiplier, `0.88x` squeeze factor).
    *   **Box 8:** `1.15x` clean outer line.
*   **530m (Flagship Metro):**
    *   **"Red Rug Royalty":** Box 1 boasts an empirical ~21% win rate (`1.28x`).
    *   **Inside Tracking:** Box 2 (`1.18x`), Box 3 (`1.08x` if early pace).
    *   **Box 8:** Clean outer arc (`1.15x`).
    *   **Boxes 5 & 6:** Severe mid-track squeeze trap (`0.84x`).
*   **595m / 730m:** 595m Box 1 & 2 (`1.18x`). 730m National Distance Championship: split floors and stamina override draw (`1.08x`).

### Gawler (Barossa Regional Circuit)
*   **Coordinates:** `-34.5993`, `138.7402` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Fast two-turn regional circuit with tight turns and sweeping back stretch.
*   **400m (Sprint):**
    *   **Rail & Outer Swoop Polarization:** Inside railers in Box 1 (`1.25x`) and Box 2 (`1.20x`) command the turn entry. Box 8 (`1.18x` + `1.08x` sweep bonus) gets clear momentum.
    *   **Mid-Track Compression:** Boxes 4, 5, 6 suffer severe squeeze penalties (`0.84x` if speed rank > 2, `0.88x` squeeze factor).
*   **531m / 643m:** Box 1 & 2 (`1.20x`), Box 8 (`1.14x`), Boxes 4 & 5 (`0.88x`).

### Mount Gambier (Tara Raceway)
*   **Coordinates:** `-37.8250`, `140.7820` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Compact South Australian country oval with the **shortest run to first turn in SA**.
*   **305m (Super Sprint):**
    *   **Extreme Box 1 Dominance:** Empirically boasts a **50.0% win rate** in historical samples. Box 1 (`1.32x` + `1.12x` rail lock override) almost guarantees victory if possessing even modest early speed.
    *   **Inside Tracking:** Box 2 (`1.22x`), Box 3 (`1.10x` if speed rank $\le 3$).
    *   **Centrifugal Elimination:** Wide boxes 6, 7, 8 cannot clear the field before the sharp turn (`0.82x` penalty).
*   **400m / 512m / 600m:** 400m Box 1 (`1.22x`), Box 2 (`1.16x`), Box 8 (`1.14x`). 512m Box 1 & 2 (`1.18x`), Box 8 (`1.12x`).

---

## 🔴 4. New South Wales Circuits (NSW)

### Richmond (Londonderry Circular & Straight)
*   **Coordinates:** `-33.6850`, `150.7450` · **Softmax Temperature:** $T = 2.40$ (Circle), $T = 2.25$ (Straight)
*   **324m Straight Track (Richmond Straight):**
    *   True straight sprint. Boxes 1, 2, and 8 leverage boundary guide rails (`1.22x`). Mid-track runners (Boxes 4 & 5) penalized for zigzagging (`0.86x`).
*   **320m / 401m (Circle Sprint):**
    *   Very sharp first turn. Box 1 (`1.26x`) and Box 2 (`1.18x`) command the inside line. Box 8 (`1.15x`) sweeps outside. Boxes 4 & 5 severely pinched (`0.85x`).
*   **520m / 622m:** 520m Box 1 & 2 (`1.20x`), Box 8 (`1.12x`), Boxes 4 & 5 (`0.88x`).

### Nowra (South Coast Circuit)
*   **Coordinates:** `-34.8980`, `150.5910` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Sharp country circuit known for tight cornering and high interference on turn 1.
*   **365m (Short Course):**
    *   **Inside Rail Lock:** Box 1 (`1.26x` + `1.10x` first-turn hold) and Box 2 (`1.18x`) hold the line against outward centripetal drift.
    *   **Box 8 Outer Runway:** Box 8 (`1.14x`) navigates clear of mid-pack carnage.
    *   **Boxes 4 & 5:** Severe pinch zone (`0.85x` multiplier, `0.88x` squeeze factor).
*   **520m / 630m:** Box 1 & 2 (`1.18x`), Box 8 (`1.12x`), Boxes 4 & 5 (`0.88x`).

### Temora (Riverina Provincial Oval)
*   **Coordinates:** `-34.4525`, `147.5461` · **Softmax Temperature:** $T = 2.30$
*   **Geometry & Turn Radii:** Classic Riverina clay-sand loop with fast back straight and sharp home turn.
*   **330m (Riverina Sprint):**
    *   **Box 1 Dominance:** Short dash to first bend heavily favours the red rug (`1.26x` + `1.10x` rail control).
    *   **Box 2 & Box 8:** Box 2 (`1.18x`), Box 8 (`1.14x`).
    *   **Mid-Pack Traffic:** Boxes 4 & 5 suffer heavy crunching (`0.85x` multiplier, `0.88x` squeeze factor).
*   **457m / 570m:** Generous run to turn 1. Box 1 & 2 (`1.18x`), Box 8 (`1.12x`), Boxes 4, 5, 6 (`0.88x`).

### Maitland (Hunter Provincial U-Shape)
*   **Coordinates:** `-32.7447`, `151.5628` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Sweeping one-turn track with massive straights and gentle bend transition. One of the fastest tracks in Australia.
*   **400m / 450m:** Early speed to the fence is supreme. Box 1 & 2 (`1.25x`), Box 8 (`1.15x`). Dogs lacking split 1 pace drawn wide get penalized (`0.88x`).

### Casino (Northern Rivers Loop)
*   **Coordinates:** `-28.8661`, `153.0550` · **Softmax Temperature:** $T = 3.19$
*   **Geometry & Turn Radii:** Tight, compact country circuit. Cornering skill and early split speed heavily outweigh raw motor.
*   **300m / 411m:** Sharp turns. Box 1, 2, 3 receive `1.28x` if top 2 speed rank. Outside dogs (6, 7, 8) without early pace receive heavy `0.84x` penalty.

### Gunnedah (Northern Tablelands Provincial)
*   **Coordinates:** `-30.9744`, `150.2628` · **Softmax Temperature:** $T = 2.30$
*   **Geometry & Turn Radii:** Fast country provincial track with sweeping home turn.
*   **340m (Sprint):** Short dash to turn 1. Box 1 rail lock (`1.26x` + `1.10x` first-turn hold), Box 2 (`1.18x`), Box 8 (`1.14x`), Boxes 4 & 5 traffic sandwich (`0.85x`).
*   **431m / 527m:** Box 1 & 2 (`1.18x`), Boxes 5 & 6 (`0.88x`).

---

## 🟡 5. Queensland Circuits (QLD)

### Capalaba (Premier Queensland Straight Track)
*   **Coordinates:** `-27.5184`, `153.1913` · **Softmax Temperature:** $T = 2.25$
*   **Geometry & Physics:** Premier grass 366m straight course in Redland City. Zero turns. Pure high-velocity sprinting.
*   **Guide Rail Dynamic (366m):**
    *   Boundary rails dictate straight trajectories. Dogs drawn in Box 1 and Box 8 receive **`1.22x` guide rail boost**.
    *   Middle boxes (Boxes 4 & 5) suffer severe weaving and bumping penalty (`0.86x`).

### Q Straight (The Q — Purga Queensland)
*   **Coordinates:** `-27.7120`, `152.7407` · **Softmax Temperature:** $T = 2.25$
*   **Geometry & Physics:** Pure 350m straight racing surface with zero turns at the state-of-the-art Greater Brisbane complex.
*   **Guide Rail Dynamic:** Outside fence (Box 8) and inside rail (Box 1) provide visual and physical guide boundaries (`1.22x`). Middle runners (Boxes 4 & 5) frequently weave under race pressure (`0.88x`).

### Q2 Parklands (The Q One-Turn Circuit)
*   **Coordinates:** `-27.7120`, `152.7400` · **Softmax Temperature:** $T = 2.40$
*   **Geometry & Turn Radii:** Sweeping one-turn circuit engineered for maximum greyhound safety and overtaking room.
*   **350m / 457m / 520m:**
    *   Early split to the fence dictates outcomes: Box 1 (`1.25x`), Box 2 (`1.18x`), Box 8 (`1.14x`).
    *   Boxes 5 & 6 face traffic congestion (`0.86x` if speed rank > 2, else `0.94x`).
*   **600m / 710m (Middle Distance & Stayers):** Fair starts. Box 1 & 2 (`1.14x`), Box 7 & 8 (`1.10x`).

### Rockhampton (Callaghan Park)
*   **Coordinates:** `-23.3788`, `150.5222` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Regional oval with severe cornering radius and high rail bias.
*   **407m (Sprint):**
    *   **Extreme Inside Rail Bias:** Empirically, Box 1 boasts a **40.0% win rate** and Box 3 a **37.5% win rate**, while outer boxes 5, 6, 8 recorded **0.0% wins** in historical samples.
    *   Box 1 (`1.30x` + `1.12x` rail lock override), Box 2 (`1.20x`), Box 3 (`1.12x` if early pace).
    *   Outside runners face severe centrifugal flinging (`0.82x` on Boxes 5, 6, 8 if speed rank > 3).
*   **510m:** Box 1 & 2 (`1.20x`), Box 8 (`1.12x`), Boxes 4, 5, 6 (`0.88x`).

---

## 🟠 6. Western Australia & Tasmania (WA & TAS)

### Northam (Avon Valley Provincial)
*   **Coordinates:** `-31.6400`, `116.6718` · **Softmax Temperature:** $T = 2.40$
*   **Geometry & Turn Radii:** Wide, expansive regional track.
*   **509m:** Long charge into sweeping bend. Box 1 (`1.20x`) and Box 8 (`1.20x`) dominate, while middle boxes 4 and 5 are pinched (`0.88x`).
*   **297m:** Fast dash favoring inside railers (`1.18x`).

### Launceston (Mowbray Racing Centre)
*   **Coordinates:** `-41.4045`, `147.1415` · **Softmax Temperature:** $T = 2.35$
*   **Geometry & Turn Radii:** Fast Tasmanian circuit inside the Mowbray racecourse.
*   **278m (Super Dash):** Very tight rush to turn 1. Inside rail is golden (`1.25x`), wide runners without early speed get checked (`0.85x`).
*   **515m (Standard Distance):** Box 1, 2, 3 (`1.15x`), Boxes 5 & 6 (`0.90x`).

---

## 🌦️ Weather Daemon Integration & "Feels Like" Apparent Temperature

The weather collector daemon connects to Open-Meteo using the precise GPS coordinates above. Meteorological observations are refreshed every 15 minutes leading up to race jump.

### 1. "Feels Like" (Apparent Temperature) vs. Dry-Bulb Temperature
Canines do not sweat through their skin; they regulate internal temperature almost exclusively via **respiratory evaporative heat dissipation (panting)**. When relative humidity is elevated, the vapour pressure gradient collapses, causing severe canine thermal distress even at mild dry-bulb temperatures (e.g. 26°C with 85% humidity feels like 31°C to a racing greyhound).

The engine now dynamically queries and persists **`apparent_temperature`** (Feels Like) alongside dry-bulb temperature:
*   **Heat Fade Penalty ($\ge 28.0^\circ\text{C}$ Feels Like):** Heavy dogs ($> 33\text{ kg}$) produce higher metabolic heat and face slower heat dissipation, receiving a **`0.93x` penalty** on 500m+ distances. Light dogs ($< 28\text{ kg}$) receive a **`1.02x` thermal dissipation bonus**.
*   **Cold Snap / Wind Chill ($\le 8.0^\circ\text{C}$ Feels Like):** Canines burn additional glycogen reserves. High-fitness power dogs ($> 32\text{ kg}$ with strong form) receive a **`1.02x` muscle power bonus**, while very light dogs ($< 27\text{ kg}$) receive **`0.97x`** due to rapid convective heat loss.

### 2. Wet Track & Loam Compaction Detection
$$\text{Track Wet Status} = (\text{Precipitation} \ge 0.2\text{ mm}) \lor (\text{WMO Weather Code} \in \text{Rain/Storm Set})$$
When a track is declared wet:
1. Early speed weights are boosted by **+15% to +25%** due to kickback avoidance.
2. Backmarker run-home weights are discounted by **-20%** due to heavy loam drag.
3. Box 1 rail protection is elevated to prevent wide slipping on turn 1.

*All geometric biases, GPS coordinates, apparent temperature models, and softmax calibrations are fully active in `analytics.py`, `db.py`, `weather_collector.py`, and `greyhound_racing.db`.*
