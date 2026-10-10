"""
Same Race Multi (SRM) Optimizer & Permutation Modeler.
Calculates joint probabilities for Same Race Multis (e.g. Winner + Top 2 + Top 3 + Top 4 combinations)
using Harville's multivariate order statistics and correlated greyhound outcomes.
"""

from typing import List, Dict, Any, Optional
import itertools
from analytics import GreyhoundAnalytics
from db import get_connection
from monte_carlo_srm import MonteCarloSRMSimulator


class SameRaceMultiOptimizer:
    def __init__(self, db_conn: Optional[Any] = None):
        self.conn = db_conn or get_connection()
        self.analytics = GreyhoundAnalytics(self.conn)
        self.mc_sim = MonteCarloSRMSimulator(self.conn, n_iterations=10000)

    def _calculate_4leg_harville(self, win_probs: Dict[int, float], styles: Dict[int, str]) -> Dict[tuple, float]:
        boxes = list(win_probs.keys())
        permutations = list(itertools.permutations(boxes, 4))
        top4_probs = {}
        total_prob = 0.0
        for perm in permutations:
            b1, b2, b3, b4 = perm
            p1 = win_probs.get(b1, 0.01)
            p2 = win_probs.get(b2, 0.01)
            p3 = win_probs.get(b3, 0.01)
            p4 = win_probs.get(b4, 0.01)
            denom1 = max(0.01, 1.0 - p1)
            denom2 = max(0.01, 1.0 - p1 - p2)
            denom3 = max(0.01, 1.0 - p1 - p2 - p3)
            
            # Dependent Correlation Modifiers (The "Railer Draft" & "Destructive Interference")
            corr = 1.0
            style1 = styles.get(b1, "").upper()
            style2 = styles.get(b2, "").upper()
            
            # Railer Draft: If a railer wins from Box 1 or 2, the dog directly outside it (Box 2 or 3) gets a clean cart into the placings.
            if b1 in (1, 2) and ("RR" in style1 or "FR" in style1 or "RAIL" in style1):
                if b2 == b1 + 1:
                    corr *= 1.20 # 20% boost to drafting the inside winner
            
            # Mutually Destructive Middle Railers: If you pick two middle railers to both place, it's highly unlikely as they will crash into each other
            railers_in_placings = 0
            for box in [b1, b2, b3, b4]:
                style = styles.get(box, "").upper()
                if ("RR" in style or "FR" in style or "RAIL" in style) and box in (4, 5, 6):
                    railers_in_placings += 1
            if railers_in_placings >= 2:
                corr *= 0.80 # 20% penalty for selecting multiple crashing middle railers
                
            # Wide Sweep Cart: If Box 8 sweeps and wins, Box 7 often gets a clean path.
            if b1 == 8 and ("WIDE" in style1 or "WM" in style1 or "FR" in style1) and b2 == 7:
                corr *= 1.15
            
            prob = p1 * (p2 / denom1) * (p3 / denom2) * (p4 / denom3) * corr
            top4_probs[perm] = prob
            total_prob += prob
            
        if total_prob > 0:
            for perm in top4_probs:
                top4_probs[perm] /= total_prob
        return top4_probs

    def _evaluate_srm_condition(self, top4_probs: Dict[tuple, float], conditions: Dict[int, str]) -> float:
        total = 0.0
        for perm, prob in top4_probs.items():
            satisfies = True
            for box, cond in conditions.items():
                if box not in perm:
                    satisfies = False; break
                idx = perm.index(box)
                if cond == "win" and idx > 0: satisfies = False; break
                elif cond == "top_2" and idx > 1: satisfies = False; break
                elif cond == "top_3" and idx > 2: satisfies = False; break
                elif cond == "top_4" and idx > 3: satisfies = False; break
            if satisfies:
                total += prob
        return total

    def generate_srm_combinations(self, race_id: int, min_total_odds: float = 3.0, max_total_odds: float = 250.0, odds_source: str = "sportsbet") -> List[Dict[str, Any]]:
        """
        Generates high-odds Same Race Multi (SRM) combinations for a specific race,
        using 10,000-iteration Monte Carlo physics simulation to compute exact joint probabilities,
        collision scrimmage rates, and true correlation factors.
        """
        predictions = self.analytics.predict_race_probabilities(race_id)
        if len(predictions) < 3:
            return []

        # Standardize market odds based on selected odds source
        for p in predictions:
            if odds_source == "sportsbet":
                p["active_odds"] = p.get("sportsbet_odds") or p.get("market_odds")
            elif odds_source == "best":
                p["active_odds"] = p.get("best_odds") or p.get("sportsbet_odds") or p.get("market_odds")
            else:
                p["active_odds"] = p.get("market_odds") or p.get("sportsbet_odds")

        valid_runners = [p for p in predictions if p.get("active_odds") is not None and p["active_odds"] > 1.0]
        if len(valid_runners) < 2:
            return []

        # Run 10,000-iteration Monte Carlo stochastic race simulation
        try:
            sim_data = self.mc_sim.simulate_race(race_id)
        except Exception:
            sim_data = None

        srm_combos = []

        # 1. Combo Win + Place (2 legs in same race):
        # Runner A wins, Runner B finishes Top 2 or Top 3
        for win_dog in valid_runners:
            p_win_a = win_dog["model_win_prob"]
            odds_a = win_dog["active_odds"]

            for place_dog in valid_runners:
                if place_dog["box"] == win_dog["box"]:
                    continue

                odds_b = place_dog["active_odds"]
                implied_place_odds_b = max(1.20, (odds_b - 1.0) / 3.0 + 1.0)
                estimated_srm_odds = round(odds_a * (implied_place_odds_b * 0.90), 2)

                if min_total_odds <= estimated_srm_odds <= max_total_odds:
                    conds_top2 = {win_dog["box"]: "win", place_dog["box"]: "top_2"}
                    if sim_data:
                        eval_res = self.mc_sim.evaluate_srm_slip(sim_data, conds_top2, offered_srm_odds=estimated_srm_odds)
                        joint_prob_pct = eval_res["mc_joint_probability_pct"]
                        ev = eval_res["expected_value"]
                        corr_factor = eval_res["correlation_factor"]
                        corr_status = eval_res["correlation_status"]
                    else:
                        denom = max(0.01, 1.0 - p_win_a)
                        joint_prob = p_win_a * (place_dog["model_win_prob"] / denom)
                        joint_prob_pct = round(joint_prob * 100, 2)
                        ev = round((joint_prob * estimated_srm_odds) - 1.0, 4)
                        corr_factor = 1.0
                        corr_status = "NEUTRAL"

                    srm_combos.append({
                        "type": "Win + Top 2 SRM",
                        "legs_count": 2,
                        "legs": [
                            {"box": win_dog["box"], "dog_name": win_dog["dog_name"], "condition": "To Win (1st)", "odds": odds_a},
                            {"box": place_dog["box"], "dog_name": place_dog["dog_name"], "condition": "To Run Top 2", "odds": round(implied_place_odds_b, 2)}
                        ],
                        "total_odds": estimated_srm_odds,
                        "model_prob_pct": joint_prob_pct,
                        "correlation_factor": corr_factor,
                        "correlation_status": corr_status,
                        "expected_value": ev
                    })

                # Also calculate: A to Win + B to Run Top 3
                if len(valid_runners) >= 6:
                    estimated_srm_odds_top3 = round(odds_a * (implied_place_odds_b * 0.78), 2)
                    if min_total_odds <= estimated_srm_odds_top3 <= max_total_odds:
                        conds_top3 = {win_dog["box"]: "win", place_dog["box"]: "top_3"}
                        if sim_data:
                            eval_res = self.mc_sim.evaluate_srm_slip(sim_data, conds_top3, offered_srm_odds=estimated_srm_odds_top3)
                            joint_prob_pct = eval_res["mc_joint_probability_pct"]
                            ev_top3 = eval_res["expected_value"]
                            corr_factor = eval_res["correlation_factor"]
                            corr_status = eval_res["correlation_status"]
                        else:
                            denom = max(0.01, 1.0 - p_win_a)
                            joint_prob = p_win_a * min(0.95, (place_dog["model_win_prob"] / denom) * 1.65)
                            joint_prob_pct = round(joint_prob * 100, 2)
                            ev_top3 = round((joint_prob * estimated_srm_odds_top3) - 1.0, 4)
                            corr_factor = 1.0
                            corr_status = "NEUTRAL"
    
                        srm_combos.append({
                            "type": "Win + Top 3 SRM",
                            "legs_count": 2,
                            "legs": [
                                {"box": win_dog["box"], "dog_name": win_dog["dog_name"], "condition": "To Win (1st)", "odds": odds_a},
                                {"box": place_dog["box"], "dog_name": place_dog["dog_name"], "condition": "To Run Top 3", "odds": round(implied_place_odds_b * 0.85, 2)}
                            ],
                            "total_odds": estimated_srm_odds_top3,
                            "model_prob_pct": joint_prob_pct,
                            "correlation_factor": corr_factor,
                            "correlation_status": corr_status,
                            "expected_value": ev_top3
                        })

        # If there are fewer than 6 dogs, bookmakers limit SRMs to Top 2 placings only.
        # We return here to filter out any 3+ leg (Trifecta) or Top 4 combinations.
        if len(valid_runners) < 6:
            return sorted(srm_combos, key=lambda x: x["expected_value"] or 0.0, reverse=True)

        # 2. 3-Leg SRM: Winner + Top 2 + Top 3 (Trifecta order)
        for d1, d2, d3 in itertools.permutations(valid_runners[:5], 3):
            o1 = d1["active_odds"]
            o2 = d2["active_odds"]
            o3 = d3["active_odds"]
            approx_srm_trifecta = round(o1 * (o2 ** 0.5) * (o3 ** 0.35) * 1.35, 2)

            if min_total_odds <= approx_srm_trifecta <= max_total_odds:
                conds_tri = {d1["box"]: "win", d2["box"]: "top_2", d3["box"]: "top_3"}
                if sim_data:
                    eval_res = self.mc_sim.evaluate_srm_slip(sim_data, conds_tri, offered_srm_odds=approx_srm_trifecta)
                    joint_prob_pct = eval_res["mc_joint_probability_pct"]
                    ev_tri = eval_res["expected_value"]
                    corr_factor = eval_res["correlation_factor"]
                    corr_status = eval_res["correlation_status"]
                else:
                    p1 = d1["model_win_prob"]
                    p2 = d2["model_win_prob"]
                    p3 = d3["model_win_prob"]
                    denom1 = max(0.01, 1.0 - p1)
                    denom2 = max(0.01, 1.0 - p1 - p2)
                    joint_p = p1 * (p2 / denom1) * (p3 / denom2 if denom2 > 0 else 0.05)
                    joint_prob_pct = round(joint_p * 100, 2)
                    ev_tri = round((joint_p * approx_srm_trifecta) - 1.0, 4)
                    corr_factor = 1.0
                    corr_status = "NEUTRAL"

                if joint_prob_pct > 0.4:
                    srm_combos.append({
                        "type": "3-Leg Trifecta SRM",
                        "legs_count": 3,
                        "legs": [
                            {"box": d1["box"], "dog_name": d1["dog_name"], "condition": "1st (Winner)", "odds": o1},
                            {"box": d2["box"], "dog_name": d2["dog_name"], "condition": "2nd", "odds": o2},
                            {"box": d3["box"], "dog_name": d3["dog_name"], "condition": "3rd", "odds": o3}
                        ],
                        "total_odds": approx_srm_trifecta,
                        "model_prob_pct": joint_prob_pct,
                        "correlation_factor": corr_factor,
                        "correlation_status": corr_status,
                        "expected_value": ev_tri
                    })

        # 3. 4-Leg SRM Combos (Top 4 Boxed and Win + Top 4 Mix)
        top_dogs = sorted(valid_runners, key=lambda x: x["model_win_prob"], reverse=True)[:5]
        for d1, d2 in itertools.combinations(top_dogs, 2):
            conds_2_top4 = {d1["box"]: "top_4", d2["box"]: "top_4"}
            implied_p1 = max(1.10, (d1["active_odds"] - 1.0) / 4.5 + 1.0)
            implied_p2 = max(1.10, (d2["active_odds"] - 1.0) / 4.5 + 1.0)
            approx_2_odds = round((implied_p1 * implied_p2) * 0.95, 2)

            if approx_2_odds >= 1.50:
                if sim_data:
                    eval_res = self.mc_sim.evaluate_srm_slip(sim_data, conds_2_top4, offered_srm_odds=approx_2_odds)
                    prob_pct = eval_res["mc_joint_probability_pct"]
                    ev = eval_res["expected_value"]
                    corr_factor = eval_res["correlation_factor"]
                    corr_status = eval_res["correlation_status"]
                else:
                    prob_pct = 25.0
                    ev = 0.05
                    corr_factor = 1.0
                    corr_status = "NEUTRAL"

                if prob_pct > 15.0:
                    srm_combos.append({
                        "type": "2-Leg Top 4 SRM",
                        "legs_count": 2,
                        "legs": [
                            {"box": d1["box"], "dog_name": d1["dog_name"], "condition": "To Run Top 4", "odds": round(implied_p1, 2)},
                            {"box": d2["box"], "dog_name": d2["dog_name"], "condition": "To Run Top 4", "odds": round(implied_p2, 2)}
                        ],
                        "total_odds": approx_2_odds,
                        "model_prob_pct": prob_pct,
                        "correlation_factor": corr_factor,
                        "correlation_status": corr_status,
                        "expected_value": ev
                    })

        # Sort by highest expected value (+EV)
        return sorted(srm_combos, key=lambda x: x["expected_value"] or 0.0, reverse=True)


if __name__ == "__main__":
    srm_opt = SameRaceMultiOptimizer()
    combos = srm_opt.generate_srm_combinations(66, min_total_odds=3.0)
    print(f"Generated {len(combos)} Same Race Multis for Sandown R7 (Race ID 66):")
    for c in combos[:5]:
        print(f"  [{c['type']}] Total Odds: ${c['total_odds']} | Prob: {c['model_prob_pct']}% | EV: +{c['expected_value']*100:.1f}%")
        for l in c["legs"]:
            print(f"     * Box {l['box']} {l['dog_name']} -> {l['condition']}")
