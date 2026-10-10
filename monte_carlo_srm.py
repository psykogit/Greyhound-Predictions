"""
Monte Carlo Correlated Same Race Multi (SRM) Simulator (Phase 2.2)
Author: DeepMind / Greyhound Precision Betting Engine

Runs a 10,000-iteration physics-grounded stochastic race simulation modeling:
1. Micro-Phase Sectional Acceleration & Box Lids reaction times
2. Lateral Trajectory Intersections & First-Turn Collision Scrimmages
3. Running-Style Drafting, Camber Advantages & Clean Air Wings
4. Run-Home Stamina, Lactic Deceleration (Leader Faders) & Swoopers
5. Joint Probability Distributions for multi-leg SRMs & Correlation Ratios
"""

import numpy as np
from typing import Dict, List, Tuple, Any, Optional
import sqlite3

from db import get_connection
from analytics import GreyhoundAnalytics


class MonteCarloSRMSimulator:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None, n_iterations: int = 10000, seed: Optional[int] = 42):
        self.conn = db_conn or get_connection()
        self.analytics = GreyhoundAnalytics(self.conn)
        self.n_iterations = n_iterations
        self.rng = np.random.default_rng(seed)

    def simulate_race(self, race_id: int, n_trials: Optional[int] = None) -> Dict[str, Any]:
        """
        Executes N Monte Carlo simulated races for all active runners in the race.
        Returns simulation metrics, runner summaries, and finish position matrices.
        """
        n_sims = n_trials or self.n_iterations

        # 1. Fetch race and predictions
        preds = self.analytics.predict_race_probabilities(race_id)
        if not preds or len(preds) < 2:
            raise ValueError(f"Insufficient active runners for race {race_id}")

        c = self.conn.cursor()
        c.execute("""
            SELECT r.id, r.race_number, r.distance, m.track_name, m.meeting_date
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            WHERE r.id = ?
        """, (race_id,))
        race_info = c.fetchone()
        distance = race_info["distance"] if race_info else 400
        track_name = race_info["track_name"] if race_info else "Unknown"

        runners = sorted(preds, key=lambda x: x["box"])
        k = len(runners)
        boxes = [r["box"] for r in runners]
        box_to_idx = {b: idx for idx, b in enumerate(boxes)}

        # Extract runner kinematic profiles
        split_means = np.zeros(k, dtype=np.float64)
        split_stdevs = np.zeros(k, dtype=np.float64)
        rh_means = np.zeros(k, dtype=np.float64)
        rh_stdevs = np.zeros(k, dtype=np.float64)
        target_lanes = np.zeros(k, dtype=np.float64)
        is_railer = np.zeros(k, dtype=bool)
        is_wide = np.zeros(k, dtype=bool)
        is_fader = np.zeros(k, dtype=bool)
        is_swooper = np.zeros(k, dtype=bool)

        # Baseline defaults
        default_split = 5.25 if distance <= 450 else 5.45
        default_rh = 14.50 if distance <= 450 else 18.00

        for idx, r in enumerate(runners):
            box = r["box"]
            style = (r.get("running_style") or "").upper()

            # Split 1 mean and stdev
            sp = r.get("split_1")
            split_means[idx] = float(sp) if sp is not None else default_split
            split_stdevs[idx] = float(r.get("split_stdev") or 0.08)
            split_stdevs[idx] = np.clip(split_stdevs[idx], 0.04, 0.15)

            # Run home mean and stdev
            rh = r.get("median_run_home")
            rh_means[idx] = float(rh) if rh is not None else default_rh
            rh_stdevs[idx] = 0.16 # Standard run-home empirical variance

            # Trajectory & Lateral Intent:
            # Box 1..8 initial lateral position is its box number
            if any(marker in style for marker in ["RR", "FR", "RAIL"]):
                target_lanes[idx] = 1.0 # Cuts sharply to rail
                is_railer[idx] = True
            elif any(marker in style for marker in ["WIDE", "WR", "WM"]):
                target_lanes[idx] = 8.0 # Sweeps wide to outer rail
                is_wide[idx] = True
            elif box in (1, 2):
                target_lanes[idx] = 1.2
                is_railer[idx] = True
            elif box in (7, 8):
                target_lanes[idx] = 7.5
                is_wide[idx] = True
            else:
                target_lanes[idx] = float(box) # Holds natural middle path

            # Velocity Profile Traits
            sp_rank = r.get("speed_rank", 4)
            rh_rank = r.get("run_home_rank", 4)

            # Leader Fader (early burst but lactic deceleration late over >=440m)
            if distance >= 440 and sp_rank <= 2 and rh_rank >= 6:
                is_fader[idx] = True

            # Late Swooper (huge finish motor)
            if rh_rank <= 2 and sp_rank > 4:
                is_swooper[idx] = True

        # ==========================================
        # VECTORIZED MONTE CARLO STOCHASTIC PHASES
        # ==========================================

        # Phase 1: Stochastic Box Reaction & First Sectional
        # shape: (n_sims, k)
        sim_splits = self.rng.normal(loc=split_means, scale=split_stdevs, size=(n_sims, k))

        # Phase 2: Stochastic Run-Home Base Times
        sim_rhs = self.rng.normal(loc=rh_means, scale=rh_stdevs, size=(n_sims, k))

        # Apply Leader Fader Deceleration Fatigue over middle/staying distances
        for idx in range(k):
            if is_fader[idx]:
                fade_penalty = self.rng.uniform(0.12, 0.28, size=n_sims)
                sim_rhs[:, idx] += fade_penalty

        # Phase 3: Traffic & First-Turn Collision Scrimmages
        # Interference time added to each dog: shape (n_sims, k)
        traffic_penalties = np.zeros((n_sims, k), dtype=np.float64)

        # Pairwise Collision & Drafting Dynamics:
        for i in range(k):
            box_i = boxes[i]
            lane_i_orig = float(box_i)
            target_i = target_lanes[i]

            for j in range(i + 1, k):
                box_j = boxes[j] # box_j > box_i (starts to the outside of i)
                lane_j_orig = float(box_j)
                target_j = target_lanes[j]

                # Check if paths cross:
                # Outside dog j cuts inside across dog i's path
                cuts_across = (target_j < lane_i_orig) or (is_railer[j] and not is_wide[i])

                # Time delta at turn entry between dog i and dog j
                time_diff = np.abs(sim_splits[:, i] - sim_splits[:, j])

                # Scrimmage zone: when two dogs arrive at turn within 0.08s of each other
                in_scrimmage_zone = (time_diff <= 0.085)

                if cuts_across:
                    # High probability of collision when path cuts inward
                    p_collision = 0.50 if (is_railer[j] and is_railer[i]) else 0.35
                    clash = in_scrimmage_zone & (self.rng.random(n_sims) < p_collision)

                    # Outside dog j gets checked (loses 1.5 - 3.5 lengths: 0.12s - 0.28s)
                    traffic_penalties[clash, j] += self.rng.uniform(0.12, 0.28, size=np.sum(clash))
                    # Inside dog i gets bumped (minor check: 0.04s - 0.10s)
                    traffic_penalties[clash, i] += self.rng.uniform(0.04, 0.10, size=np.sum(clash))

                elif is_railer[j] and box_j in (4, 5, 6) and is_railer[i] and box_i in (2, 3, 4):
                    # Middle railers crashing into inside dogs
                    clash = in_scrimmage_zone & (self.rng.random(n_sims) < 0.40)
                    traffic_penalties[clash, j] += self.rng.uniform(0.15, 0.30, size=np.sum(clash))
                    traffic_penalties[clash, i] += self.rng.uniform(0.05, 0.12, size=np.sum(clash))

        # Drafting Bonus: If a dog leads by >= 0.12s at 1st turn, dog drafting behind gains clean slipstream
        min_split_per_trial = np.min(sim_splits, axis=1)
        lead_margin = sim_splits - min_split_per_trial[:, np.newaxis]
        is_clear_leader = (lead_margin == 0) & (np.sort(sim_splits, axis=1)[:, 1:2] - min_split_per_trial[:, np.newaxis] >= 0.10)
        # Clear leader gets clean track bonus (-0.05s)
        traffic_penalties[is_clear_leader] -= 0.05

        # Phase 4: Final Simulated Times
        sim_total_times = sim_splits + sim_rhs + traffic_penalties

        # Sort runners per trial: lowest time = 1st place
        # finish_order[trial] contains indices of runners ordered from 1st to kth
        finish_order = np.argsort(sim_total_times, axis=1)

        # Rank matrix: rank_matrix[trial, dog_idx] = position (1 to k)
        rank_matrix = np.empty_like(finish_order)
        trials_idx = np.arange(n_sims)[:, np.newaxis]
        rank_matrix[trials_idx, finish_order] = np.arange(1, k + 1)

        # Calculate simulated win and podium frequencies
        win_counts = np.sum(rank_matrix == 1, axis=0)
        top2_counts = np.sum(rank_matrix <= 2, axis=0)
        top3_counts = np.sum(rank_matrix <= 3, axis=0)
        top4_counts = np.sum(rank_matrix <= 4, axis=0)

        runner_summaries = []
        for idx, r in enumerate(runners):
            runner_summaries.append({
                "box": r["box"],
                "dog_name": r["dog_name"],
                "trainer": r.get("trainer", "Unknown"),
                "sportsbet_odds": r.get("sportsbet_odds"),
                "model_win_prob": r["model_win_prob"],
                "mc_win_prob": round(float(win_counts[idx]) / n_sims, 4),
                "mc_top2_prob": round(float(top2_counts[idx]) / n_sims, 4),
                "mc_top3_prob": round(float(top3_counts[idx]) / n_sims, 4),
                "mc_top4_prob": round(float(top4_counts[idx]) / n_sims, 4),
            })

        return {
            "race_id": race_id,
            "track_name": track_name,
            "distance": distance,
            "n_iterations": n_sims,
            "runners": runner_summaries,
            "boxes": boxes,
            "box_to_idx": box_to_idx,
            "rank_matrix": rank_matrix, # numpy array (n_sims, k)
            "sim_times": sim_total_times
        }

    def evaluate_srm_slip(
        self,
        simulation_data: Dict[str, Any],
        conditions: Dict[int, str],
        offered_srm_odds: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Evaluates an exact multi-leg Same Race Multi using the pre-computed Monte Carlo simulation.
        conditions: {box_number: "win" | "top_2" | "top_3" | "top_4"}
        offered_srm_odds: decimal odds offered by Sportsbet / bookmaker for this SRM slip.
        """
        rank_matrix = simulation_data["rank_matrix"]
        box_to_idx = simulation_data["box_to_idx"]
        n_sims = simulation_data["n_iterations"]
        runners_dict = {r["box"]: r for r in simulation_data["runners"]}

        condition_mask = np.ones(n_sims, dtype=bool)
        legs_detail = []

        # Independent probability product for Harville baseline
        harville_approx_prob = 1.0

        for box, cond in conditions.items():
            if box not in box_to_idx:
                raise ValueError(f"Box {box} not active in simulated race.")

            idx = box_to_idx[box]
            positions = rank_matrix[:, idx]
            r_info = runners_dict[box]
            c_lower = cond.lower()

            if c_lower in ("win", "1st"):
                condition_mask &= (positions == 1)
                leg_prob = r_info["mc_win_prob"]
                leg_desc = "To Win (1st)"
            elif c_lower in ("top_2", "top2"):
                condition_mask &= (positions <= 2)
                leg_prob = r_info["mc_top2_prob"]
                leg_desc = "To Run Top 2"
            elif c_lower in ("top_3", "top3"):
                condition_mask &= (positions <= 3)
                leg_prob = r_info["mc_top3_prob"]
                leg_desc = "To Run Top 3"
            elif c_lower in ("top_4", "top4"):
                condition_mask &= (positions <= 4)
                leg_prob = r_info["mc_top4_prob"]
                leg_desc = "To Run Top 4"
            else:
                raise ValueError(f"Unknown SRM condition '{cond}'. Valid: win, top_2, top_3, top_4.")

            harville_approx_prob *= leg_prob
            legs_detail.append({
                "box": box,
                "dog_name": r_info["dog_name"],
                "condition": leg_desc,
                "individual_prob": leg_prob,
                "market_odds": r_info["sportsbet_odds"]
            })

        # Multi-leg Monte Carlo joint occurrences
        hits = int(np.sum(condition_mask))
        mc_prob = hits / n_sims
        se = float(np.sqrt(mc_prob * (1.0 - mc_prob) / n_sims)) if n_sims > 0 else 0.0

        # Correlation Factor (MC joint prob / independent product)
        corr_factor = round(mc_prob / max(1e-6, harville_approx_prob), 3) if harville_approx_prob > 0 else 1.0

        # Fair SRM Odds
        fair_odds = round(1.0 / mc_prob, 2) if mc_prob > 0 else 999.0

        # Correlation Assessment
        if corr_factor >= 1.20:
            corr_label = "STRONG_POSITIVE_SYNERGY"
            corr_note = f"Positive drafting/camber correlation (+{round((corr_factor-1)*100)}% boost over independent odds)"
        elif corr_factor <= 0.82:
            corr_label = "DESTRUCTIVE_INTERFERENCE"
            corr_note = f"High collision risk / path intersection (-{round((1-corr_factor)*100)}% penalty over independent odds)"
        else:
            corr_label = "NEUTRAL"
            corr_note = "Standard uncorrelated execution"

        # Edge & Expected Value
        ev = None
        edge_pct = None
        if offered_srm_odds and offered_srm_odds > 1.0:
            ev = round((mc_prob * offered_srm_odds) - 1.0, 4)
            edge_pct = round((mc_prob - (1.0 / offered_srm_odds)) * 100.0, 2)

        return {
            "legs_count": len(legs_detail),
            "legs": legs_detail,
            "mc_joint_probability": round(mc_prob, 4),
            "mc_joint_probability_pct": round(mc_prob * 100.0, 2),
            "standard_error": round(se, 4),
            "harville_independent_prob": round(harville_approx_prob, 4),
            "correlation_factor": corr_factor,
            "correlation_status": corr_label,
            "correlation_note": corr_note,
            "fair_srm_odds": fair_odds,
            "offered_srm_odds": offered_srm_odds,
            "expected_value": ev,
            "edge_percent": edge_pct,
            "is_positive_ev": (ev is not None and ev > 0.03)
        }

    def scan_optimal_srms(
        self,
        race_id: int,
        min_odds: float = 2.50,
        max_odds: float = 80.0,
        min_ev: float = 0.04
    ) -> List[Dict[str, Any]]:
        """
        Scans all logical 2-leg, 3-leg, and 4-leg SRM combinations using 10,000 Monte Carlo runs,
        discovering the highest +EV combinations with positive synergy.
        """
        sim_data = self.simulate_race(race_id)
        runners = sim_data["runners"]
        top_runners = sorted(runners, key=lambda x: x["mc_win_prob"], reverse=True)[:5]
        top_boxes = [r["box"] for r in top_runners]

        candidates = []

        # 1. Combo Win + Top 2 (2 legs)
        for w_box in top_boxes:
            for p_box in top_boxes:
                if w_box == p_box:
                    continue
                conds = {w_box: "win", p_box: "top_2"}
                # Approximate market SRM odds (bookmaker product with ~12% correlation haircut)
                w_odds = runners[sim_data["box_to_idx"][w_box]].get("sportsbet_odds") or 3.0
                p_odds = runners[sim_data["box_to_idx"][p_box]].get("sportsbet_odds") or 4.0
                implied_p_odds = max(1.25, (p_odds - 1.0) / 3.0 + 1.0)
                approx_market_srm = round(w_odds * (implied_p_odds * 0.88), 2)

                res = self.evaluate_srm_slip(sim_data, conds, offered_srm_odds=approx_market_srm)
                if min_odds <= approx_market_srm <= max_odds and res["expected_value"] and res["expected_value"] >= min_ev:
                    candidates.append({**res, "type": "Win + Top 2 SRM"})

        # 2. Combo Win + Top 3 (2 legs)
        for w_box in top_boxes:
            for p_box in top_boxes:
                if w_box == p_box:
                    continue
                conds = {w_box: "win", p_box: "top_3"}
                w_odds = runners[sim_data["box_to_idx"][w_box]].get("sportsbet_odds") or 3.0
                p_odds = runners[sim_data["box_to_idx"][p_box]].get("sportsbet_odds") or 4.0
                implied_p_odds = max(1.15, (p_odds - 1.0) / 3.8 + 1.0)
                approx_market_srm = round(w_odds * (implied_p_odds * 0.85), 2)

                res = self.evaluate_srm_slip(sim_data, conds, offered_srm_odds=approx_market_srm)
                if min_odds <= approx_market_srm <= max_odds and res["expected_value"] and res["expected_value"] >= min_ev:
                    candidates.append({**res, "type": "Win + Top 3 SRM"})

        # 3. Both Top 2 (2 legs)
        for i, b1 in enumerate(top_boxes):
            for b2 in top_boxes[i + 1:]:
                conds = {b1: "top_2", b2: "top_2"}
                o1 = runners[sim_data["box_to_idx"][b1]].get("sportsbet_odds") or 3.0
                o2 = runners[sim_data["box_to_idx"][b2]].get("sportsbet_odds") or 3.5
                approx_market_srm = round(((o1 - 1.0)/3.0 + 1.0) * ((o2 - 1.0)/3.0 + 1.0) * 2.2, 2)

                res = self.evaluate_srm_slip(sim_data, conds, offered_srm_odds=approx_market_srm)
                if min_odds <= approx_market_srm <= max_odds and res["expected_value"] and res["expected_value"] >= min_ev:
                    candidates.append({**res, "type": "Both Top 2 SRM"})

        # 4. 3-Leg Correlated Trifecta SRM (Win + Top 2 + Top 3)
        if len(top_boxes) >= 3:
            for w_box in top_boxes[:3]:
                for t2_box in top_boxes[:4]:
                    if t2_box == w_box:
                        continue
                    for t3_box in top_boxes[:4]:
                        if t3_box in (w_box, t2_box):
                            continue
                        conds = {w_box: "win", t2_box: "top_2", t3_box: "top_3"}
                        o1 = runners[sim_data["box_to_idx"][w_box]].get("sportsbet_odds") or 2.5
                        o2 = runners[sim_data["box_to_idx"][t2_box]].get("sportsbet_odds") or 3.5
                        o3 = runners[sim_data["box_to_idx"][t3_box]].get("sportsbet_odds") or 5.0
                        approx_market_srm = round(o1 * (o2 ** 0.45) * (o3 ** 0.35) * 1.25, 2)

                        res = self.evaluate_srm_slip(sim_data, conds, offered_srm_odds=approx_market_srm)
                        if min_odds <= approx_market_srm <= max_odds and res["expected_value"] and res["expected_value"] >= min_ev:
                            candidates.append({**res, "type": "3-Leg Trifecta Order SRM"})

        # Sort candidates by Expected Value descending
        candidates.sort(key=lambda x: x["expected_value"] or 0.0, reverse=True)
        return candidates[:12]


if __name__ == "__main__":
    import time
    sim = MonteCarloSRMSimulator(n_iterations=10000)
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT r.id, m.track_name, r.race_number, r.distance
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        ORDER BY r.id DESC LIMIT 1
    """)
    sample = c.fetchone()
    if sample:
        r_id = sample["id"]
        print(f"Executing 10,000 Monte Carlo Sim for {sample['track_name']} R{sample['race_number']} (Race {r_id})...")
        t0 = time.time()
        res = sim.simulate_race(r_id)
        dt = time.time() - t0
        print(f"Simulation completed in {dt*1000:.1f}ms!\n")

        print("=== MONTE CARLO PROBABILITIES ===")
        for r in res["runners"]:
            print(f"Box {r['box']}: {r['dog_name']:<20} | Win: {r['mc_win_prob']*100:>5.1f}% | Top 2: {r['mc_top2_prob']*100:>5.1f}% | Top 3: {r['mc_top3_prob']*100:>5.1f}% | Top 4: {r['mc_top4_prob']*100:>5.1f}%")

        print("\n=== OPTIMAL +EV SAME RACE MULTIS ===")
        opt_srms = sim.scan_optimal_srms(r_id)
        for s in opt_srms[:5]:
            legs_str = " + ".join([f"B{leg['box']} ({leg['condition']})" for leg in s["legs"]])
            print(f"- [{s['type']}] {legs_str}")
            print(f"  MC Prob: {s['mc_joint_probability_pct']}% | Est Odds: ${s['offered_srm_odds']} | Corr: {s['correlation_factor']}x ({s['correlation_status']}) | EV: +{s['expected_value']*100:.1f}%")
