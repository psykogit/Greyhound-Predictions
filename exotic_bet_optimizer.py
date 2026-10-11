"""
Exotic Bet Optimizer & Automated Ticket Builder (Phase 2.1)
Author: DeepMind / Greyhound Precision Betting Engine

Provides mathematically optimized ticket construction for:
1. Boxed First 4 & Boxed Trifecta (with multi-tiered Flexi % calculations)
2. Standout & Roving Banker First 4 and Trifecta tickets
3. Dutching Exotic Calculators (equal profit & proportional probability staking)
4. Automated race recommendation engine driven by Bayesian win probabilities
"""

import itertools
import math
from typing import Dict, List, Tuple, Any, Optional
import sqlite3

from db import get_connection
from analytics import GreyhoundAnalytics


class ExoticBetOptimizer:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or get_connection()
        self.analytics = GreyhoundAnalytics(self.conn)

    @staticmethod
    def calculate_harville_permutations(win_probs: Dict[int, float], length: int = 4) -> Dict[Tuple[int, ...], float]:
        """
        Calculates joint probability distribution for all exact permutations of specified length
        using the Harville multi-stage conditional choice formula.
        P(i, j, k, l) = P(i) * P(j)/(1 - P(i)) * P(k)/(1 - P(i) - P(j)) * P(l)/(1 - P(i) - P(j) - P(k))
        """
        boxes = list(win_probs.keys())
        total_p = sum(win_probs.values())
        if total_p <= 0:
            return {}

        norm_p = {b: p / total_p for b, p in win_probs.items()}
        perms = list(itertools.permutations(boxes, length))

        dist = {}
        prob_sum = 0.0

        for perm in perms:
            prob = 1.0
            accum_p = 0.0
            valid = True

            for step, b in enumerate(perm):
                p_b = norm_p[b]
                if step == 0:
                    prob *= p_b
                    accum_p += p_b
                else:
                    rem = 1.0 - accum_p
                    if rem <= 1e-9:
                        valid = False
                        break
                    prob *= (p_b / rem)
                    accum_p += p_b

            if valid and prob > 0:
                dist[perm] = prob
                prob_sum += prob

        # Re-normalize to ensure 1.0 total probability space over all permutations
        if prob_sum > 0:
            for perm in dist:
                dist[perm] /= prob_sum

        return dist

    def build_boxed_ticket(
        self,
        exotic_type: str,
        selected_boxes: List[int],
        win_probs: Dict[int, float],
        stake: float = 20.0,
        runner_names: Optional[Dict[int, str]] = None,
        market_odds: Optional[Dict[int, float]] = None
    ) -> Dict[str, Any]:
        """
        Generates a Boxed Exotic Ticket (Trifecta or First 4) with projected tote dividends.
        - Trifecta: 3 legs (combinations = n * (n-1) * (n-2))
        - First 4: 4 legs (combinations = n * (n-1) * (n-2) * (n-3))
        """
        runner_names = runner_names or {}
        n = len(selected_boxes)
        req_len = 3 if exotic_type.lower() == "trifecta" else 4

        if n < req_len:
            raise ValueError(f"At least {req_len} boxes required for {exotic_type}, got {n}")

        # Total combinations: n! / (n - req_len)!
        combos = math.perm(n, req_len)
        flexi_ratio = stake / max(1, combos)
        flexi_pct = round(flexi_ratio * 100.0, 2)

        # Calculate joint probability of hitting this boxed ticket
        perm_dist = self.calculate_harville_permutations(win_probs, length=req_len)
        ticket_perms = list(itertools.permutations(selected_boxes, req_len))
        ticket_prob = sum(perm_dist.get(p, 0.0) for p in ticket_perms)

        # Market-implied permutations for dividend projection (22% TAB takeout factor = 0.78 distribution)
        divs = []
        if market_odds and len(market_odds) >= req_len:
            tot_mkt_p = sum(1.0 / max(1.01, market_odds.get(b, 10.0)) for b in market_odds)
            mkt_probs = {b: (1.0 / max(1.01, market_odds.get(b, 10.0))) / tot_mkt_p for b in market_odds}
            mkt_perm_dist = self.calculate_harville_permutations(mkt_probs, length=req_len)
            for p in ticket_perms:
                mp = mkt_perm_dist.get(p, 1e-6)
                tote_div = round(0.78 / max(1e-6, mp), 2)
                divs.append((tote_div, perm_dist.get(p, 0.0)))
        else:
            for p in ticket_perms:
                tote_div = round(0.78 / max(1e-6, perm_dist.get(p, 1e-6)), 2)
                divs.append((tote_div, perm_dist.get(p, 0.0)))

        divs.sort(key=lambda x: x[0])
        min_div = divs[0][0] if divs else 0.0
        max_div = divs[-1][0] if divs else 0.0
        weighted_div = round(sum(d[0] * d[1] for d in divs) / max(1e-6, ticket_prob), 2) if divs else 0.0

        projected_payout = round(weighted_div * flexi_ratio, 2)
        projected_min_payout = round(min_div * flexi_ratio, 2)
        projected_max_payout = round(max_div * flexi_ratio, 2)
        expected_value = round((ticket_prob * projected_payout) - stake, 2)
        is_positive_ev = bool((ticket_prob * projected_payout) > stake)
        break_even_div = round(combos * 1.0, 2)

        return {
            "exotic_type": exotic_type.upper(),
            "ticket_style": "BOXED",
            "boxes": sorted(selected_boxes),
            "runner_names": [runner_names.get(b, f"Box {b}") for b in sorted(selected_boxes)],
            "combinations_count": combos,
            "stake": stake,
            "flexi_percent": flexi_pct,
            "win_probability": round(ticket_prob, 4),
            "win_probability_pct": round(ticket_prob * 100.0, 2),
            "fair_dividend": round(1.0 / max(1e-6, ticket_prob), 2),
            "est_min_dividend": min_div,
            "est_avg_dividend": weighted_div,
            "est_max_dividend": max_div,
            "projected_payout": projected_payout,
            "projected_min_payout": projected_min_payout,
            "projected_max_payout": projected_max_payout,
            "break_even_dividend": break_even_div,
            "expected_value": expected_value,
            "is_positive_ev": is_positive_ev,
            "summary": f"{n}-Dog Boxed {exotic_type.title()} ({combos} combos) @ {flexi_pct}% Flexi (${stake:.2f} stake) | Est Avg Div: ${weighted_div:.2f} (Pays ${projected_payout:.2f})"
        }

    def _generate_exotic_permutations(
        self,
        exotic_type: str,  # "first4" or "trifecta"
        selected_boxes: List[int],
        ticket_structure: str = "boxed",
        anchor_box: Optional[int] = None,
        secondary_anchor: Optional[int] = None
    ) -> Tuple[List[Tuple[int, ...]], int, str, str, int, Optional[int]]:
        """
        Generates exact permutations for the chosen exotic type and ticket structure.
        Supported structures:
        - 'boxed': Full box of all selected runners
        - 'standout': Key runner locked in 1st position
        - 'dual_banker': Top 2 key runners locked in 1st & 2nd (either order)
        - 'roving_banker': Key runner must finish anywhere in the exotic podium
        """
        k = 4 if exotic_type == "first4" else 3
        anchor = anchor_box if (anchor_box and anchor_box in selected_boxes) else selected_boxes[0]
        sec_candidates = [b for b in selected_boxes if b != anchor]
        sec_anchor = secondary_anchor if (secondary_anchor and secondary_anchor in sec_candidates) else (
            sec_candidates[0] if sec_candidates else None
        )
        others = [b for b in selected_boxes if b != anchor]

        if ticket_structure == "standout":
            sub_k = k - 1
            perms = [(anchor, *p) for p in itertools.permutations(others, sub_k)]
            label = f"Standout Anchor (Box {anchor} Key 1st)"
            desc = f"Box {anchor} is anchored in 1st; remaining {len(others)} runners contest 2nd to {k}th."

        elif ticket_structure == "dual_banker":
            if not sec_anchor or len(selected_boxes) < (3 if exotic_type == "trifecta" else 4):
                return self._generate_exotic_permutations(exotic_type, selected_boxes, "standout", anchor)
            others_dual = [b for b in selected_boxes if b not in (anchor, sec_anchor)]
            sub_k = k - 2
            perms = []
            if sub_k == 0:
                perms = [(anchor, sec_anchor), (sec_anchor, anchor)]
            else:
                for rem in itertools.permutations(others_dual, sub_k):
                    perms.append((anchor, sec_anchor, *rem))
                    perms.append((sec_anchor, anchor, *rem))
            label = f"Dual Banker (Boxes {anchor} & {sec_anchor} 1st/2nd)"
            desc = f"Boxes {anchor} and {sec_anchor} are anchored in 1st & 2nd; others contest remaining positions."

        elif ticket_structure == "roving_banker":
            sub_k = k - 1
            perms = []
            for pos in range(k):
                for rem in itertools.permutations(others, sub_k):
                    t = list(rem)
                    t.insert(pos, anchor)
                    perms.append(tuple(t))
            label = f"Roving Banker (Box {anchor} Anywhere Top {k})"
            desc = f"Box {anchor} must finish anywhere in the top {k} positions."

        else:
            ticket_structure = "boxed"
            perms = list(itertools.permutations(selected_boxes, k))
            label = f"{len(selected_boxes)}-Dog Boxed {'First 4' if k == 4 else 'Trifecta'}"
            desc = f"Any permutation of the {len(selected_boxes)} selected runners in the top {k} positions."

        return perms, len(perms), label, desc, anchor, sec_anchor

    def estimate_boxed_first4_odds(
        self,
        race_id: int,
        selected_boxes: Optional[List[int]] = None,
        custom_stake: float = 6.0,
        ticket_structure: str = "boxed",
        anchor_box: Optional[int] = None,
        secondary_anchor: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calculates precise projected TAB / Pari-Mutuel tote odds, dividend ranges,
        break-even thresholds, and expected value for a 5-dog First 4 ticket.
        Supports Combinatorial Selective Boxing: Boxed, Standout Anchor (Key 1st),
        Dual Banker (1st & 2nd), and Roving Banker.
        Defaults to $6.00 stake (5% flexi on 120 combos, or 25% flexi on 24 standout combos).
        """
        preds = self.analytics.predict_race_probabilities(race_id)
        if not preds:
            raise ValueError(f"No active runners available for race {race_id}")

        # Fetch race details
        c = self.conn.cursor()
        c.execute("""
            SELECT r.race_number, r.distance, m.track_name, m.meeting_date
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            WHERE r.id = ?
        """, (race_id,))
        race_info = c.fetchone()
        track = race_info["track_name"] if race_info else "Unknown"
        race_num = race_info["race_number"] if race_info else 0
        dist = race_info["distance"] if race_info else 0

        n_active = len(preds)
        places_paid = 3 if n_active >= 8 else (2 if n_active >= 5 else 1)
        no_third_place_dividend = (places_paid < 3)
        place_terms_label = f"Paying {places_paid} Places (No 3rd Place Dividend)" if no_third_place_dividend else "Paying 3 Places (Standard 8-Runner Field)"

        # Australian TAB Rules: First 4 requires minimum 5 runners
        if n_active < 5:
            return {
                "race_id": race_id,
                "track_name": track,
                "race_number": race_num,
                "distance": dist,
                "is_available": False,
                "unavailable_reason": f"First 4 is abandoned / not offered by TAB on fields with fewer than 5 runners ({n_active} active runners).",
                "active_runners_count": n_active,
                "places_paid": places_paid,
                "no_third_place_dividend": no_third_place_dividend,
                "place_terms_note": "Win Only (No Place Dividends Paid)" if places_paid == 1 else "Paying 2 Places (No 3rd Place Dividend)",
                "ticket_structure": ticket_structure,
                "structure_label": "Not Offered",
                "structure_description": "First 4 betting is unavailable.",
                "anchor_box": None,
                "secondary_anchor": None,
                "recommended_structure": "none",
                "recommendation_reason": "Field size below TAB minimum of 5 runners.",
                "available_structures": [],
                "selected_boxes": [],
                "selected_runners": [],
                "combinations_count": 0,
                "total_race_permutations": 0,
                "field_coverage_pct": 0.0,
                "is_full_field_coverage": False,
                "stake": custom_stake,
                "flexi_percent": 0.0,
                "flexi_multiplier": 0.0,
                "hit_probability": 0.0,
                "hit_probability_pct": 0.0,
                "break_even_dividend": 0.0,
                "break_even_note": "First 4 betting is not available for this race.",
                "est_min_dividend": 0.0,
                "est_avg_dividend": 0.0,
                "est_median_dividend": 0.0,
                "est_max_dividend": 0.0,
                "projected_min_payout": 0.0,
                "projected_avg_payout": 0.0,
                "projected_max_payout": 0.0,
                "projected_net_profit": -custom_stake,
                "projected_roi_pct": -100.0,
                "expected_value": -custom_stake,
                "is_positive_ev": False,
                "profitable_combinations_pct": 0.0,
                "verdict": "NOT OFFERED (FIELD < 5)",
                "verdict_note": f"TAB rules require a minimum of 5 active runners for First 4 pools. Only {n_active} runners are active.",
                "top_combinations_preview": [],
                "summary": f"First 4 Not Available ({n_active} active runners; min 5 required by TAB)"
            }

        total_race_combos = math.perm(n_active, 4)

        # Auto-detect Top 4 + 5th Dog Value Roughie if not provided
        if not selected_boxes:
            top4_boxes = [p["box"] for p in preds[:4]]
            roughie = next((p for p in preds if p.get("is_roughie")), None)
            if roughie and roughie["box"] not in top4_boxes:
                selected_boxes = list(top4_boxes) + [roughie["box"]]
            elif len(preds) >= 5:
                selected_boxes = [p["box"] for p in preds[:5]]
            else:
                selected_boxes = [p["box"] for p in preds]

        win_probs = {p["box"]: p["model_win_prob"] for p in preds}
        runner_names = {p["box"]: p["dog_name"] for p in preds}
        market_odds = {
            p["box"]: float(p.get("best_odds") or p.get("sportsbet_odds") or p.get("market_odds") or p.get("fair_win_odds", 10.0))
            for p in preds
        }

        # Market-implied normalized probabilities for pool dividend estimation
        tot_mkt_p = sum(1.0 / max(1.01, market_odds.get(b, 10.0)) for b in market_odds)
        mkt_probs = {b: (1.0 / max(1.01, market_odds.get(b, 10.0))) / tot_mkt_p for b in market_odds}

        # Generate chosen combinatorial permutations
        perms, combos, struct_label, struct_desc, actual_anchor, actual_sec = self._generate_exotic_permutations(
            "first4", selected_boxes, ticket_structure, anchor_box, secondary_anchor
        )

        base_combos = math.perm(len(selected_boxes), 4)
        base_flexi = round((custom_stake / max(1, base_combos)) * 100.0, 2)
        flexi_ratio = custom_stake / max(1, combos)
        flexi_pct = round(flexi_ratio * 100.0, 2)
        flexi_multiplier = round(flexi_pct / max(0.1, base_flexi), 1)
        field_coverage_pct = round((combos / total_race_combos) * 100.0, 1)
        is_full_field = (combos == total_race_combos)

        div_items = []
        tot_prob = 0.0

        for perm in perms:
            b1, b2, b3, b4 = perm
            # Model probability
            p1 = win_probs.get(b1, 0.01)
            p2 = win_probs.get(b2, 0.01) / max(0.001, 1.0 - p1)
            p3 = win_probs.get(b3, 0.01) / max(0.001, 1.0 - p1 - win_probs.get(b2, 0.01))
            p4 = win_probs.get(b4, 0.01) / max(0.001, 1.0 - p1 - win_probs.get(b2, 0.01) - win_probs.get(b3, 0.01))
            prob = max(1e-7, p1 * p2 * p3 * p4)
            tot_prob += prob

            # Market probability
            mp1 = mkt_probs.get(b1, 0.01)
            mp2 = mkt_probs.get(b2, 0.01) / max(0.001, 1.0 - mp1)
            mp3 = mkt_probs.get(b3, 0.01) / max(0.001, 1.0 - mp1 - mkt_probs.get(b2, 0.01))
            mp4 = mkt_probs.get(b4, 0.01) / max(0.001, 1.0 - mp1 - mkt_probs.get(b2, 0.01) - mkt_probs.get(b3, 0.01))
            mkt_prob = max(1e-7, mp1 * mp2 * mp3 * mp4)

            # Standard TAB First 4 takeout 22% (0.78 payout factor)
            tote_div = round(0.78 / mkt_prob, 2)
            payout = round(tote_div * flexi_ratio, 2)

            div_items.append({
                "combination": list(perm),
                "runner_names": [runner_names.get(b, f"Box {b}") for b in perm],
                "model_prob": prob,
                "projected_dividend": tote_div,
                "projected_payout": payout,
                "is_profitable": payout > custom_stake
            })

        div_items.sort(key=lambda x: x["projected_dividend"])
        min_div = div_items[0]["projected_dividend"] if div_items else 0.0
        max_div = div_items[-1]["projected_dividend"] if div_items else 0.0
        median_div = div_items[len(div_items)//2]["projected_dividend"] if div_items else 0.0
        weighted_div = sum(d["projected_dividend"] * d["model_prob"] for d in div_items) / max(1e-6, tot_prob) if div_items else 0.0

        proj_min_payout = round(min_div * flexi_ratio, 2)
        proj_avg_payout = round(weighted_div * flexi_ratio, 2)
        proj_max_payout = round(max_div * flexi_ratio, 2)
        proj_net_profit = round(proj_avg_payout - custom_stake, 2)
        expected_value = round((tot_prob * proj_avg_payout) - custom_stake, 2)
        profitable_count = sum(1 for d in div_items if d["is_profitable"])
        break_even_div = round(combos * 1.0, 2)

        # Top 5 most likely finish combinations
        likely_sorted = sorted(div_items, key=lambda x: x["model_prob"], reverse=True)
        top5_preview = [
            {
                "order": "-".join(str(x) for x in d["combination"]),
                "names": d["runner_names"],
                "probability_pct": round(d["model_prob"] * 100.0, 2),
                "dividend": d["projected_dividend"],
                "payout": d["projected_payout"],
                "profit": round(d["projected_payout"] - custom_stake, 2)
            }
            for d in likely_sorted[:5]
        ]

        # Determine recommended structure based on model probabilities
        p1 = win_probs.get(selected_boxes[0], 0.0)
        p2 = win_probs.get(selected_boxes[1], 0.0) if len(selected_boxes) > 1 else 0.0

        if n_active == 5 and ticket_structure == "boxed":
            recommended_structure = "standout"
            recommendation_reason = f"5-runner field covers 100% in Boxed mode (-22% takeout drag). Standout 1st covers 24 combos @ 25% flexi (+400% payout boost)."
        elif p1 >= 0.40 or (p1 - p2) >= 0.15:
            recommended_structure = "standout"
            recommendation_reason = f"Dominant favorite detected (Box {selected_boxes[0]} win prob {p1*100:.1f}%). Standout yields 25% flexi (5x payout boost) vs 5% boxed."
        elif (p1 + p2) >= 0.65:
            recommended_structure = "dual_banker"
            recommendation_reason = f"Two dominant top dogs (Boxes {selected_boxes[0]} & {selected_boxes[1]}). Dual banker yields 50% flexi (10x payout boost)."
        else:
            recommended_structure = "boxed"
            recommendation_reason = "Open competitive race. Standard boxed ticket offers maximum coverage."

        # Available structure comparisons
        struct_options = ["boxed", "standout", "dual_banker", "roving_banker"]
        available_structures = []
        for s_code in struct_options:
            s_perms, s_combos, s_lbl, s_desc, _, _ = self._generate_exotic_permutations(
                "first4", selected_boxes, s_code, anchor_box, secondary_anchor
            )
            s_flexi = round((custom_stake / max(1, s_combos)) * 100.0, 2)
            s_mult = round(s_flexi / max(0.1, base_flexi), 1)
            is_rec = (s_code == recommended_structure)
            rec_badge = "⭐ Recommended" if is_rec else (f"{s_mult}x Flexi" if s_mult > 1.0 else "Base")
            available_structures.append({
                "structure": s_code,
                "label": s_lbl,
                "description": s_desc,
                "combinations_count": s_combos,
                "flexi_percent": s_flexi,
                "flexi_multiplier": s_mult,
                "is_recommended": is_rec,
                "recommendation_badge": rec_badge
            })

        # Recommendation verdict
        if is_full_field:
            verdict = "NEGATIVE EXPECTATION (100% COVERAGE)"
            verdict_note = f"Small field ({n_active} runners - Paying 2 Places, No 3rd Place Dividend). Boxing all 5 runners covers 100% of combinations (120/120) with TAB 22% takeout guaranteeing an average net loss on turnover. Recommend playing Standout Anchor (24 combos @ $1.20) or 4-dog box."
        elif ticket_structure == "standout":
            if expected_value >= 5.0:
                verdict = "STRONG VALUE (+EV STANDOUT)"
                verdict_note = f"High-yield Standout First 4 (Box {actual_anchor} Key 1st). Covers {combos} combos ({field_coverage_pct}% of race) at {flexi_pct}% flexi ({flexi_multiplier}x dividend multiplier) with projected net return +${expected_value:.2f}."
            elif expected_value > 0.0:
                verdict = "POSITIVE (+EV STANDOUT)"
                verdict_note = f"Positive expectation Standout First 4 (Box {actual_anchor} Key 1st). Yields {flexi_pct}% flexi ({flexi_multiplier}x multiplier)."
            else:
                verdict = "MARGINAL STANDOUT"
                verdict_note = f"Standout First 4 on Box {actual_anchor}. Projected return ${proj_avg_payout:.2f} on ${custom_stake:.2f} outlay."
        elif no_third_place_dividend:
            if expected_value >= 5.0:
                verdict = "STRONG VALUE (+EV)"
                verdict_note = f"Attractive overlay in {n_active}-runner field (Paying 2 Places, No 3rd Place Dividend). Ticket covers {combos}/{total_race_combos} combos ({field_coverage_pct}% of race) with expected net return of +${expected_value:.2f}."
            elif expected_value > 0.0:
                verdict = "POSITIVE (+EV)"
                verdict_note = f"Positive expectation in {n_active}-runner field (Paying 2 Places, No 3rd Place Dividend). Ticket covers {field_coverage_pct}% of race."
            else:
                verdict = "MARGINAL / DEFENSIVE"
                verdict_note = f"Compressed dividends in {n_active}-runner field (No 3rd Place Dividend). Ticket covers {field_coverage_pct}% of race."
        else:
            if expected_value >= 5.0:
                verdict = "STRONG VALUE (+EV)"
                verdict_note = f"Highly attractive overlay. Model projects an expected net return of +${expected_value:.2f} on a ${custom_stake:.2f} ticket."
            elif expected_value > 0.0:
                verdict = "POSITIVE (+EV)"
                verdict_note = f"Positive mathematical expectation. Projected return exceeds ${custom_stake:.2f} outlay."
            else:
                verdict = "MARGINAL / DEFENSIVE"
                verdict_note = f"Heavily dominated by low-paying favorites. Consider reducing stake or anchoring top pick."

        return {
            "race_id": race_id,
            "track_name": track,
            "race_number": race_num,
            "distance": dist,
            "is_available": True,
            "active_runners_count": n_active,
            "places_paid": places_paid,
            "no_third_place_dividend": no_third_place_dividend,
            "place_terms_note": place_terms_label,
            "ticket_structure": ticket_structure,
            "structure_label": struct_label,
            "structure_description": struct_desc,
            "anchor_box": actual_anchor,
            "secondary_anchor": actual_sec,
            "recommended_structure": recommended_structure,
            "recommendation_reason": recommendation_reason,
            "available_structures": available_structures,
            "total_race_permutations": total_race_combos,
            "field_coverage_pct": field_coverage_pct,
            "is_full_field_coverage": is_full_field,
            "selected_boxes": selected_boxes,
            "selected_runners": [
                {
                    "box": b,
                    "name": runner_names.get(b, f"Box {b}"),
                    "odds": market_odds.get(b, 0.0),
                    "win_prob_pct": round(win_probs.get(b, 0.0) * 100.0, 1),
                    "is_anchor": (b == actual_anchor),
                    "is_secondary_anchor": (b == actual_sec)
                }
                for b in selected_boxes
            ],
            "combinations_count": combos,
            "stake": custom_stake,
            "flexi_percent": flexi_pct,
            "flexi_multiplier": flexi_multiplier,
            "hit_probability": round(tot_prob, 4),
            "hit_probability_pct": round(tot_prob * 100.0, 1),
            "break_even_dividend": break_even_div,
            "break_even_note": f"Any First 4 dividend above ${break_even_div:.2f} guarantees a positive net profit on this {combos}-combination ticket.",
            "est_min_dividend": round(min_div, 2),
            "est_avg_dividend": round(weighted_div, 2),
            "est_median_dividend": round(median_div, 2),
            "est_max_dividend": round(max_div, 2),
            "projected_min_payout": proj_min_payout,
            "projected_avg_payout": proj_avg_payout,
            "projected_max_payout": proj_max_payout,
            "projected_net_profit": proj_net_profit,
            "projected_roi_pct": round((proj_net_profit / max(1.0, custom_stake)) * 100.0, 1),
            "expected_value": expected_value,
            "is_positive_ev": expected_value > 0,
            "profitable_combinations_pct": round((profitable_count / max(1, combos)) * 100.0, 1),
            "verdict": verdict,
            "verdict_note": verdict_note,
            "top_combinations_preview": top5_preview,
            "summary": f"{struct_label} ({len(selected_boxes)} dogs, {combos} combos) @ {flexi_pct}% Flexi (${custom_stake:.2f} stake) | Est Avg Div: ${weighted_div:.2f} (Pays ${proj_avg_payout:.2f}) | +EV: ${expected_value:.2f}"
        }

    def estimate_boxed_trifecta_odds(
        self,
        race_id: int,
        selected_boxes: Optional[List[int]] = None,
        custom_stake: float = 1.20,
        ticket_structure: str = "boxed",
        anchor_box: Optional[int] = None,
        secondary_anchor: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calculates precise projected TAB / Pari-Mutuel tote odds, dividend ranges,
        break-even thresholds, and expected value for a 4-dog Trifecta ticket.
        Supports Combinatorial Selective Boxing: Boxed, Standout Anchor (Key 1st),
        Dual Banker (1st & 2nd), and Roving Banker.
        Defaults to $1.20 stake (5% flexi on 24 combos, or 20% flexi on 6 standout combos).
        Enables punters to verify whether their stake is mathematically positive (+EV).
        """
        preds = self.analytics.predict_race_probabilities(race_id)
        if not preds:
            raise ValueError(f"No active runners available for race {race_id}")

        # Fetch race details
        c = self.conn.cursor()
        c.execute("""
            SELECT r.race_number, r.distance, m.track_name, m.meeting_date
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            WHERE r.id = ?
        """, (race_id,))
        race_info = c.fetchone()
        track = race_info["track_name"] if race_info else "Unknown"
        race_num = race_info["race_number"] if race_info else 0
        dist = race_info["distance"] if race_info else 0

        n_active = len(preds)
        places_paid = 3 if n_active >= 8 else (2 if n_active >= 5 else 1)
        no_third_place_dividend = (places_paid < 3)
        place_terms_label = f"Paying {places_paid} Places (No 3rd Place Dividend)" if no_third_place_dividend else "Paying 3 Places (Standard 8-Runner Field)"

        # Australian TAB Rules: Trifecta requires minimum 4 runners
        if n_active < 4:
            return {
                "race_id": race_id,
                "track_name": track,
                "race_number": race_num,
                "distance": dist,
                "is_available": False,
                "unavailable_reason": f"Trifecta is abandoned / not offered by TAB on fields with fewer than 4 runners ({n_active} active runners).",
                "active_runners_count": n_active,
                "places_paid": places_paid,
                "no_third_place_dividend": no_third_place_dividend,
                "place_terms_note": "Win Only (No Place Dividends Paid)",
                "ticket_structure": ticket_structure,
                "structure_label": "Not Offered",
                "structure_description": "Trifecta betting is unavailable.",
                "anchor_box": None,
                "secondary_anchor": None,
                "recommended_structure": "none",
                "recommendation_reason": "Field size below TAB minimum of 4 runners.",
                "available_structures": [],
                "selected_boxes": [],
                "selected_runners": [],
                "combinations_count": 0,
                "total_race_permutations": 0,
                "field_coverage_pct": 0.0,
                "is_full_field_coverage": False,
                "stake": custom_stake,
                "flexi_percent": 0.0,
                "flexi_multiplier": 0.0,
                "hit_probability": 0.0,
                "hit_probability_pct": 0.0,
                "break_even_dividend": 0.0,
                "break_even_note": "Trifecta betting is not available for this race.",
                "est_min_dividend": 0.0,
                "est_avg_dividend": 0.0,
                "est_median_dividend": 0.0,
                "est_max_dividend": 0.0,
                "projected_min_payout": 0.0,
                "projected_avg_payout": 0.0,
                "projected_max_payout": 0.0,
                "projected_net_profit": -custom_stake,
                "projected_roi_pct": -100.0,
                "expected_value": -custom_stake,
                "is_positive_ev": False,
                "profitable_combinations_pct": 0.0,
                "verdict": "NOT OFFERED (FIELD < 4)",
                "verdict_note": f"TAB rules require a minimum of 4 active runners for Trifecta pools. Only {n_active} runners are active.",
                "top_combinations_preview": [],
                "summary": f"Trifecta Not Available ({n_active} active runners; min 4 required by TAB)"
            }

        total_race_combos = math.perm(n_active, 3)

        # Auto-detect Top 4 contenders if not provided
        if not selected_boxes:
            if len(preds) >= 4:
                selected_boxes = [p["box"] for p in preds[:4]]
            else:
                selected_boxes = [p["box"] for p in preds]

        win_probs = {p["box"]: p["model_win_prob"] for p in preds}
        runner_names = {p["box"]: p["dog_name"] for p in preds}
        market_odds = {
            p["box"]: float(p.get("best_odds") or p.get("sportsbet_odds") or p.get("market_odds") or p.get("fair_win_odds", 10.0))
            for p in preds
        }

        # Market-implied normalized probabilities for pool dividend estimation
        tot_mkt_p = sum(1.0 / max(1.01, market_odds.get(b, 10.0)) for b in market_odds)
        mkt_probs = {b: (1.0 / max(1.01, market_odds.get(b, 10.0))) / tot_mkt_p for b in market_odds}

        # Generate chosen combinatorial permutations
        perms, combos, struct_label, struct_desc, actual_anchor, actual_sec = self._generate_exotic_permutations(
            "trifecta", selected_boxes, ticket_structure, anchor_box, secondary_anchor
        )

        base_combos = math.perm(len(selected_boxes), 3)
        base_flexi = round((custom_stake / max(1, base_combos)) * 100.0, 2)
        flexi_ratio = custom_stake / max(1, combos)
        flexi_pct = round(flexi_ratio * 100.0, 2)
        flexi_multiplier = round(flexi_pct / max(0.1, base_flexi), 1)
        field_coverage_pct = round((combos / total_race_combos) * 100.0, 1)
        is_full_field = (combos == total_race_combos)

        div_items = []
        tot_prob = 0.0

        for perm in perms:
            b1, b2, b3 = perm
            # Model probability
            p1 = win_probs.get(b1, 0.01)
            p2 = win_probs.get(b2, 0.01) / max(0.001, 1.0 - p1)
            p3 = win_probs.get(b3, 0.01) / max(0.001, 1.0 - p1 - win_probs.get(b2, 0.01))
            prob = max(1e-7, p1 * p2 * p3)
            tot_prob += prob

            # Market probability
            mp1 = mkt_probs.get(b1, 0.01)
            mp2 = mkt_probs.get(b2, 0.01) / max(0.001, 1.0 - mp1)
            mp3 = mkt_probs.get(b3, 0.01) / max(0.001, 1.0 - mp1 - mkt_probs.get(b2, 0.01))
            mkt_prob = max(1e-7, mp1 * mp2 * mp3)

            # Standard TAB Trifecta takeout is ~21% (0.79 payout factor)
            tote_div = round(0.79 / mkt_prob, 2)
            payout = round(tote_div * flexi_ratio, 2)

            div_items.append({
                "combination": list(perm),
                "runner_names": [runner_names.get(b, f"Box {b}") for b in perm],
                "model_prob": prob,
                "projected_dividend": tote_div,
                "projected_payout": payout,
                "is_profitable": payout > custom_stake
            })

        div_items.sort(key=lambda x: x["projected_dividend"])
        min_div = div_items[0]["projected_dividend"] if div_items else 0.0
        max_div = div_items[-1]["projected_dividend"] if div_items else 0.0
        median_div = div_items[len(div_items)//2]["projected_dividend"] if div_items else 0.0
        weighted_div = sum(d["projected_dividend"] * d["model_prob"] for d in div_items) / max(1e-6, tot_prob) if div_items else 0.0

        proj_min_payout = round(min_div * flexi_ratio, 2)
        proj_avg_payout = round(weighted_div * flexi_ratio, 2)
        proj_max_payout = round(max_div * flexi_ratio, 2)
        proj_net_profit = round(proj_avg_payout - custom_stake, 2)
        expected_value = round((tot_prob * proj_avg_payout) - custom_stake, 2)
        profitable_count = sum(1 for d in div_items if d["is_profitable"])
        break_even_div = round(combos * 1.0, 2)

        # Top 5 most likely finish combinations
        likely_sorted = sorted(div_items, key=lambda x: x["model_prob"], reverse=True)
        top5_preview = [
            {
                "order": "-".join(str(x) for x in d["combination"]),
                "names": d["runner_names"],
                "probability_pct": round(d["model_prob"] * 100.0, 2),
                "dividend": d["projected_dividend"],
                "payout": d["projected_payout"],
                "profit": round(d["projected_payout"] - custom_stake, 2)
            }
            for d in likely_sorted[:5]
        ]

        # Determine recommended structure based on model probabilities
        p1 = win_probs.get(selected_boxes[0], 0.0)
        p2 = win_probs.get(selected_boxes[1], 0.0) if len(selected_boxes) > 1 else 0.0

        if n_active == 4 and ticket_structure == "boxed":
            recommended_structure = "standout"
            recommendation_reason = f"4-runner field covers 100% in Boxed mode (-21% takeout drag). Standout 1st covers 6 combos @ 20% flexi (+300% payout boost)."
        elif p1 >= 0.40 or (p1 - p2) >= 0.15:
            recommended_structure = "standout"
            recommendation_reason = f"Dominant favorite detected (Box {selected_boxes[0]} win prob {p1*100:.1f}%). Standout yields 20% flexi (4x payout boost) vs 5% boxed."
        elif (p1 + p2) >= 0.65:
            recommended_structure = "dual_banker"
            recommendation_reason = f"Two dominant top dogs (Boxes {selected_boxes[0]} & {selected_boxes[1]}). Dual banker yields 30% flexi (6x payout boost)."
        else:
            recommended_structure = "boxed"
            recommendation_reason = "Open competitive race. Standard boxed ticket offers maximum coverage."

        # Available structure comparisons
        struct_options = ["boxed", "standout", "dual_banker", "roving_banker"]
        available_structures = []
        for s_code in struct_options:
            s_perms, s_combos, s_lbl, s_desc, _, _ = self._generate_exotic_permutations(
                "trifecta", selected_boxes, s_code, anchor_box, secondary_anchor
            )
            s_flexi = round((custom_stake / max(1, s_combos)) * 100.0, 2)
            s_mult = round(s_flexi / max(0.1, base_flexi), 1)
            is_rec = (s_code == recommended_structure)
            rec_badge = "⭐ Recommended" if is_rec else (f"{s_mult}x Flexi" if s_mult > 1.0 else "Base")
            available_structures.append({
                "structure": s_code,
                "label": s_lbl,
                "description": s_desc,
                "combinations_count": s_combos,
                "flexi_percent": s_flexi,
                "flexi_multiplier": s_mult,
                "is_recommended": is_rec,
                "recommendation_badge": rec_badge
            })

        # Recommendation verdict
        if is_full_field:
            verdict = "NEGATIVE EXPECTATION (100% COVERAGE)"
            verdict_note = f"Small field ({n_active} runners - Win Only, No Place Dividends). Boxing all 4 runners covers 100% of combinations (24/24) with TAB 21% takeout guaranteeing an average net loss on turnover. Recommend anchoring the top pick (6 combos @ 20% flexi)."
        elif ticket_structure == "standout":
            if expected_value >= 2.0:
                verdict = "STRONG VALUE (+EV STANDOUT)"
                verdict_note = f"High-yield Standout Trifecta (Box {actual_anchor} Key 1st). Covers {combos} combos ({field_coverage_pct}% of race) at {flexi_pct}% flexi ({flexi_multiplier}x dividend multiplier) with projected net return +${expected_value:.2f}."
            elif expected_value > 0.0:
                verdict = "POSITIVE (+EV STANDOUT)"
                verdict_note = f"Positive expectation Standout Trifecta (Box {actual_anchor} Key 1st). Yields {flexi_pct}% flexi ({flexi_multiplier}x multiplier)."
            else:
                verdict = "MARGINAL STANDOUT"
                verdict_note = f"Standout Trifecta on Box {actual_anchor}. Projected return ${proj_avg_payout:.2f} on ${custom_stake:.2f} outlay."
        elif no_third_place_dividend:
            if expected_value >= 2.0:
                verdict = "STRONG VALUE (+EV)"
                verdict_note = f"Attractive overlay in {n_active}-runner field (Paying 2 Places, No 3rd Place Dividend). Ticket covers {combos}/{total_race_combos} combos ({field_coverage_pct}% of race) with expected net return of +${expected_value:.2f}."
            elif expected_value > 0.0:
                verdict = "POSITIVE (+EV)"
                verdict_note = f"Positive expectation in {n_active}-runner field (Paying 2 Places, No 3rd Place Dividend). Ticket covers {field_coverage_pct}% of race."
            else:
                verdict = "MARGINAL / DEFENSIVE"
                verdict_note = f"Compressed pool in {n_active}-runner field (No 3rd Place Dividend). Ticket covers {field_coverage_pct}% of race."
        else:
            if expected_value >= 2.0:
                verdict = "STRONG VALUE (+EV)"
                verdict_note = f"Highly attractive overlay. Model projects an expected net return of +${expected_value:.2f} on a ${custom_stake:.2f} ticket."
            elif expected_value > 0.0:
                verdict = "POSITIVE (+EV)"
                verdict_note = f"Positive mathematical expectation. Projected return exceeds ${custom_stake:.2f} outlay."
            else:
                verdict = "MARGINAL / DEFENSIVE"
                verdict_note = f"Dominated by low-paying favorites. Consider reducing stake or anchoring top pick."

        return {
            "race_id": race_id,
            "track_name": track,
            "race_number": race_num,
            "distance": dist,
            "is_available": True,
            "active_runners_count": n_active,
            "places_paid": places_paid,
            "no_third_place_dividend": no_third_place_dividend,
            "place_terms_note": place_terms_label,
            "ticket_structure": ticket_structure,
            "structure_label": struct_label,
            "structure_description": struct_desc,
            "anchor_box": actual_anchor,
            "secondary_anchor": actual_sec,
            "recommended_structure": recommended_structure,
            "recommendation_reason": recommendation_reason,
            "available_structures": available_structures,
            "total_race_permutations": total_race_combos,
            "field_coverage_pct": field_coverage_pct,
            "is_full_field_coverage": is_full_field,
            "selected_boxes": selected_boxes,
            "selected_runners": [
                {
                    "box": b,
                    "name": runner_names.get(b, f"Box {b}"),
                    "odds": market_odds.get(b, 0.0),
                    "win_prob_pct": round(win_probs.get(b, 0.0) * 100.0, 1),
                    "is_anchor": (b == actual_anchor),
                    "is_secondary_anchor": (b == actual_sec)
                }
                for b in selected_boxes
            ],
            "combinations_count": combos,
            "stake": custom_stake,
            "flexi_percent": flexi_pct,
            "flexi_multiplier": flexi_multiplier,
            "hit_probability": round(tot_prob, 4),
            "hit_probability_pct": round(tot_prob * 100.0, 1),
            "break_even_dividend": break_even_div,
            "break_even_note": f"Any Trifecta dividend above ${break_even_div:.2f} guarantees a positive net profit on this {combos}-combination ticket.",
            "est_min_dividend": round(min_div, 2),
            "est_avg_dividend": round(weighted_div, 2),
            "est_median_dividend": round(median_div, 2),
            "est_max_dividend": round(max_div, 2),
            "projected_min_payout": proj_min_payout,
            "projected_avg_payout": proj_avg_payout,
            "projected_max_payout": proj_max_payout,
            "projected_net_profit": proj_net_profit,
            "projected_roi_pct": round((proj_net_profit / max(0.5, custom_stake)) * 100.0, 1),
            "expected_value": expected_value,
            "is_positive_ev": expected_value > 0,
            "profitable_combinations_pct": round((profitable_count / max(1, combos)) * 100.0, 1),
            "verdict": verdict,
            "verdict_note": verdict_note,
            "top_combinations_preview": top5_preview,
            "summary": f"{struct_label} ({len(selected_boxes)} dogs, {combos} combos) @ {flexi_pct}% Flexi (${custom_stake:.2f} stake) | Est Avg Div: ${weighted_div:.2f} (Pays ${proj_avg_payout:.2f}) | +EV: ${expected_value:.2f}"
        }

    def build_standout_ticket(
        self,
        exotic_type: str,
        banker_box: int,
        contender_boxes: List[int],
        win_probs: Dict[int, float],
        stake: float = 20.0,
        runner_names: Optional[Dict[int, str]] = None,
        roving: bool = False
    ) -> Dict[str, Any]:
        """
        Builds a Standout Banker or Roving Banker Exotic Ticket.
        - Standout (Banker 1st / Contenders fill remaining legs):
          Combos = perm(len(contenders), req_len - 1)
        - Roving Banker (Banker roves in 1st, 2nd, 3rd, or 4th leg):
          Combos = req_len * perm(len(contenders), req_len - 1)
        """
        runner_names = runner_names or {}
        req_len = 3 if exotic_type.lower() == "trifecta" else 4
        clean_contenders = [b for b in contender_boxes if b != banker_box]
        k = len(clean_contenders)

        if k < (req_len - 1):
            raise ValueError(f"Need at least {req_len - 1} contenders besides banker, got {k}")

        sub_combos = math.perm(k, req_len - 1)
        combos = sub_combos * req_len if roving else sub_combos
        flexi_pct = round((stake / combos) * 100.0, 2)

        # Generate ticket permutations
        ticket_perms = []
        if not roving:
            # Banker strictly 1st
            for rest in itertools.permutations(clean_contenders, req_len - 1):
                ticket_perms.append((banker_box, *rest))
        else:
            # Banker can finish at any position 0..req_len-1
            for pos in range(req_len):
                for rest in itertools.permutations(clean_contenders, req_len - 1):
                    p = list(rest)
                    p.insert(pos, banker_box)
                    ticket_perms.append(tuple(p))

        perm_dist = self.calculate_harville_permutations(win_probs, length=req_len)
        ticket_prob = sum(perm_dist.get(p, 0.0) for p in ticket_perms)

        fair_dividend = round(1.0 / ticket_prob, 2) if ticket_prob > 0 else 0.0
        projected_payout = round(fair_dividend * (flexi_pct / 100.0), 2)
        expected_value = round((ticket_prob * (fair_dividend * flexi_pct / 100.0)) - stake, 2)

        style_label = "ROVING_BANKER" if roving else "STANDOUT_BANKER"
        desc = (
            f"Roving Banker Box {banker_box} with Contenders {clean_contenders}"
            if roving
            else f"Standout 1st: Box {banker_box} / 2nd..{req_len}th: {clean_contenders}"
        )

        return {
            "exotic_type": exotic_type.upper(),
            "ticket_style": style_label,
            "banker_box": banker_box,
            "banker_name": runner_names.get(banker_box, f"Box {banker_box}"),
            "contender_boxes": clean_contenders,
            "contender_names": [runner_names.get(b, f"Box {b}") for b in clean_contenders],
            "combinations_count": combos,
            "stake": stake,
            "flexi_percent": flexi_pct,
            "win_probability": round(ticket_prob, 4),
            "win_probability_pct": round(ticket_prob * 100.0, 2),
            "fair_dividend": fair_dividend,
            "projected_payout": projected_payout,
            "expected_value": expected_value,
            "description": desc,
            "summary": f"{style_label.replace('_', ' ').title()} {exotic_type.title()} ({combos} combos) @ {flexi_pct}% Flexi (${stake:.2f} stake)"
        }

    def build_key_standout_ticket(
        self,
        exotic_type: str,
        banker_boxes: List[int],
        contender_boxes: List[int],
        win_probs: Dict[int, float],
        stake: float = 20.0,
        runner_names: Optional[Dict[int, str]] = None
    ) -> Dict[str, Any]:
        """
        Builds a Multi-Banker Key Standout Ticket.
        - First 4: Bankers (e.g. Top 2 dogs) locked for 1st & 2nd positions,
          with Contenders (e.g. 4 dogs) filling 3rd & 4th legs.
          Combos = perm(len(bankers), 2) * perm(len(contenders), 2)
          e.g. 2 bankers * 4 contenders = 2 * 12 = 24 combos!
          e.g. 2 bankers * 5 contenders = 2 * 20 = 40 combos!
        - Trifecta: Bankers (2 dogs) locked for 1st & 2nd, Contenders for 3rd.
          Combos = perm(len(bankers), 2) * len(contenders) = 2 * 4 = 8 combos!
        """
        runner_names = runner_names or {}
        req_len = 3 if exotic_type.lower() == "trifecta" else 4
        clean_contenders = [b for b in contender_boxes if b not in banker_boxes]

        if len(banker_boxes) < 2:
            raise ValueError(f"At least 2 bankers required for key standout ticket, got {len(banker_boxes)}")

        rem_legs = req_len - 2
        if len(clean_contenders) < rem_legs:
            raise ValueError(f"At least {rem_legs} contenders required besides bankers, got {len(clean_contenders)}")

        banker_perms = list(itertools.permutations(banker_boxes, 2))
        contender_perms = list(itertools.permutations(clean_contenders, rem_legs))

        ticket_perms = []
        for bp in banker_perms:
            for cp in contender_perms:
                ticket_perms.append((*bp, *cp))

        combos = len(ticket_perms)
        flexi_pct = round((stake / combos) * 100.0, 2)

        perm_dist = self.calculate_harville_permutations(win_probs, length=req_len)
        ticket_prob = sum(perm_dist.get(p, 0.0) for p in ticket_perms)

        fair_dividend = round(1.0 / ticket_prob, 2) if ticket_prob > 0 else 0.0
        projected_payout = round(fair_dividend * (flexi_pct / 100.0), 2)
        expected_value = round((ticket_prob * (fair_dividend * flexi_pct / 100.0)) - stake, 2)

        banker_names = [runner_names.get(b, f"Box {b}") for b in banker_boxes]
        contender_names = [runner_names.get(b, f"Box {b}") for b in clean_contenders]

        return {
            "exotic_type": exotic_type.upper(),
            "ticket_style": "KEY_STANDOUT",
            "tier_label": "Key Standout Banker",
            "banker_boxes": banker_boxes,
            "banker_names": banker_names,
            "contender_boxes": clean_contenders,
            "contender_names": contender_names,
            "combinations_count": combos,
            "stake": stake,
            "flexi_percent": flexi_pct,
            "win_probability": round(ticket_prob, 4),
            "win_probability_pct": round(ticket_prob * 100.0, 2),
            "fair_dividend": fair_dividend,
            "projected_payout": projected_payout,
            "expected_value": expected_value,
            "description": f"Key 1st/2nd: Boxes {banker_boxes} | Contenders 3rd..{req_len}th: Boxes {clean_contenders}",
            "summary": f"Key Standout {exotic_type.title()} ({combos} combos) @ {flexi_pct}% Flexi (${stake:.2f} stake)"
        }

    def build_dutching_ticket(
        self,
        exotic_type: str,
        target_permutations: List[Tuple[int, ...]],
        win_probs: Dict[int, float],
        total_stake: float = 50.0,
        runner_names: Optional[Dict[int, str]] = None
    ) -> Dict[str, Any]:
        """
        Calculates exact Dutching stakes across selected permutations.
        Stakes are distributed inversely proportional to each combination's fair odds,
        ensuring an equal payout regardless of which selected combination lands.
        """
        runner_names = runner_names or {}
        req_len = 3 if exotic_type.lower() == "trifecta" else 4
        perm_dist = self.calculate_harville_permutations(win_probs, length=req_len)

        valid_items = []
        for p in target_permutations:
            prob = perm_dist.get(p, 0.0)
            if prob > 0:
                odds = 1.0 / prob
                valid_items.append({"permutation": p, "prob": prob, "fair_odds": odds})

        if not valid_items:
            raise ValueError("No valid permutations with positive probability found.")

        # Total inverse odds (overround denominator)
        inv_sum = sum(1.0 / item["fair_odds"] for item in valid_items)
        target_return = total_stake / inv_sum if inv_sum > 0 else 0.0

        combos_breakdown = []
        coverage_prob = sum(item["prob"] for item in valid_items)

        for item in valid_items:
            # Individual stake: target_return / fair_odds
            ind_stake = round(target_return / item["fair_odds"], 2)
            combos_breakdown.append({
                "combination": list(item["permutation"]),
                "names": [runner_names.get(b, f"Box {b}") for b in item["permutation"]],
                "fair_probability": round(item["prob"], 4),
                "fair_odds": round(item["fair_odds"], 2),
                "recommended_stake": ind_stake,
                "projected_return": round(ind_stake * item["fair_odds"], 2)
            })

        return {
            "exotic_type": exotic_type.upper(),
            "ticket_style": "DUTCHING",
            "total_stake": total_stake,
            "combinations_count": len(valid_items),
            "coverage_probability": round(coverage_prob, 4),
            "coverage_probability_pct": round(coverage_prob * 100.0, 2),
            "target_return": round(target_return, 2),
            "projected_profit": round(target_return - total_stake, 2),
            "roi_percent": round(((target_return - total_stake) / max(1.0, total_stake)) * 100.0, 2),
            "combinations": combos_breakdown,
            "summary": f"Dutching {len(valid_items)} Exact {exotic_type.title()} Combos (${total_stake:.2f} Total Stake, Target Return: ${target_return:.2f})"
        }

    def generate_race_exotics(self, race_id: int, custom_stake: Optional[float] = None) -> Dict[str, Any]:
        """
        Master automated recommendation pipeline for a race.
        Extracts model probabilities, ranks contenders, identifies dominant bankers,
        and produces ready-to-bet First 4 & Trifecta ticket options.
        """
        preds = self.analytics.predict_race_probabilities(race_id)
        if not preds:
            raise ValueError(f"No active runners or predictions available for race {race_id}")

        # Fetch race details
        c = self.conn.cursor()
        c.execute("""
            SELECT r.race_number, r.distance, m.track_name, m.meeting_date
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            WHERE r.id = ?
        """, (race_id,))
        race_info = c.fetchone()
        track = race_info["track_name"] if race_info else "Unknown"
        race_num = race_info["race_number"] if race_info else 0
        dist = race_info["distance"] if race_info else 0

        # Sort runners by model win probability
        sorted_runners = sorted(preds, key=lambda x: x["model_win_prob"], reverse=True)
        win_probs = {r["box"]: r["model_win_prob"] for r in sorted_runners}
        runner_names = {r["box"]: r["dog_name"] for r in sorted_runners}
        market_odds = {
            r["box"]: float(r.get("best_odds") or r.get("sportsbet_odds") or r.get("market_odds") or r.get("fair_win_odds", 10.0))
            for r in sorted_runners
        }

        top_boxes = [r["box"] for r in sorted_runners]
        top_3_boxes = top_boxes[:3]
        top_4_boxes = top_boxes[:4]
        top_5_boxes = top_boxes[:5]
        top_6_boxes = top_boxes[:6]

        # Banker Detection Logic
        top_pick = sorted_runners[0]
        second_pick = sorted_runners[1] if len(sorted_runners) > 1 else None
        gap = top_pick["model_win_prob"] - (second_pick["model_win_prob"] if second_pick else 0.0)

        is_dominant_banker = (top_pick["model_win_prob"] >= 0.35 and gap >= 0.10) or (top_pick["model_win_prob"] >= 0.45)
        banker_box = top_pick["box"]
        contender_boxes = top_boxes[1:5] if len(top_boxes) >= 5 else top_boxes[1:]

        # Standard staking options (Default 5% Flexi for both: $6.00 for 120 combos, $1.20 for 24 combos)
        f4_stake = custom_stake or 6.0
        tri_stake = custom_stake or 1.20

        tickets: Dict[str, Any] = {
            "race_id": race_id,
            "track_name": track,
            "race_number": race_num,
            "distance": dist,
            "banker_detected": is_dominant_banker,
            "banker": {
                "box": banker_box,
                "name": top_pick["dog_name"],
                "win_prob_pct": round(top_pick["model_win_prob"] * 100.0, 1),
                "margin_gap_pct": round(gap * 100.0, 1)
            } if is_dominant_banker else None,
            "ranked_runners": [
                {
                    "rank": idx + 1,
                    "box": r["box"],
                    "name": r["dog_name"],
                    "win_prob_pct": round(r["model_win_prob"] * 100.0, 1),
                    "place_prob_pct": round(r.get("model_place_prob", 0.0) * 100.0, 1),
                    "top4_prob_pct": round(r.get("model_top4_prob", 0.0) * 100.0, 1),
                    "exotic_score": r.get("exotic_score", 0.0),
                    "trainer": r.get("trainer", "Unknown")
                }
                for idx, r in enumerate(sorted_runners)
            ],
            "first_4_tickets": [],
            "trifecta_tickets": [],
            "dutching_recommendations": [],
            "boxed_first4_odds_estimate": None,
            "boxed_trifecta_odds_estimate": None
        }

        # --- 1. MULTI-TIERED FIRST 4 TICKETS ---
        # Tier 1: 4-Dog Boxed First 4 (Precision Box - 24 combos)
        if len(top_4_boxes) == 4:
            t4 = self.build_boxed_ticket("first_4", top_4_boxes, win_probs, stake=f4_stake, runner_names=runner_names, market_odds=market_odds)
            t4["tier"] = "TIER_1_PRECISION"
            t4["tier_label"] = "Tier 1: 4-Dog Box (Precision)"
            t4["tier_description"] = "24 combinations • High flexi dividend • Primary contenders"
            tickets["first_4_tickets"].append(t4)

        # Find #1 Value Roughie (5th dog) from model predictions
        roughie = next((p for p in preds if p.get("is_roughie")), None)
        if roughie and roughie["box"] not in top_4_boxes:
            value_5_boxes = list(top_4_boxes) + [roughie["box"]]
            roughie_tag = f"Box {roughie['box']} ({roughie['dog_name']})"
        elif len(top_5_boxes) == 5:
            value_5_boxes = top_5_boxes
            roughie_tag = f"Box {top_5_boxes[4]} ({runner_names.get(top_5_boxes[4], '')})"
        else:
            value_5_boxes = []
            roughie_tag = ""

        # Tier 2: 5-Dog Boxed First 4 (Top 4 + Value Roughie - 120 combos)
        if len(value_5_boxes) == 5:
            t5 = self.build_boxed_ticket("first_4", value_5_boxes, win_probs, stake=f4_stake, runner_names=runner_names, market_odds=market_odds)
            t5["tier"] = "TIER_2_BALANCED"
            t5["tier_label"] = f"Tier 2: 5-Dog Box (Top 4 + Roughie {roughie_tag})"
            t5["tier_description"] = f"120 combinations • Includes dangerous 5th dog {roughie_tag} to catch massive upset dividends"
            t5["roughie_included"] = roughie_tag
            tickets["first_4_tickets"].append(t5)

            # Generate full Boxed First 4 Odds & Staking Analysis
            tickets["boxed_first4_odds_estimate"] = self.estimate_boxed_first4_odds(
                race_id, selected_boxes=value_5_boxes, custom_stake=f4_stake
            )

        # Tier 3: 6-Dog Boxed First 4 (Maximum Coverage - 360 combos)
        if len(top_6_boxes) == 6:
            # Staking: $18 gives 5% Flexi; $36 gives 10% Flexi
            cov_stake = max(f4_stake, 18.0) if custom_stake is None else custom_stake
            t6 = self.build_boxed_ticket("first_4", top_6_boxes, win_probs, stake=cov_stake, runner_names=runner_names, market_odds=market_odds)
            t6["tier"] = "TIER_3_MAX_COVERAGE"
            t6["tier_label"] = "Tier 3: 6-Dog Box (Maximum Coverage ~75% Target)"
            t6["tier_description"] = "360 combinations • ~65-75% hit coverage • Insulates against roughies in 3rd/4th"
            tickets["first_4_tickets"].append(t6)

        # Tier 4: Key Standout First 4 (Top 2 Bankers for 1st/2nd + Contenders for 3rd/4th)
        if len(top_boxes) >= 6:
            bankers = top_boxes[:2]
            contenders = top_boxes[2:6]
            ks = self.build_key_standout_ticket("first_4", bankers, contenders, win_probs, stake=f4_stake, runner_names=runner_names)
            ks["tier"] = "TIER_4_KEY_STANDOUT"
            ks["tier_label"] = "Tier 4: Key Standout Banker (2 Banked / 4 Roving)"
            ks["tier_description"] = "24 combinations @ 50% Flexi • Locks Top 2 for win/2nd, roves 4 contenders for 3rd/4th"
            tickets["first_4_tickets"].append(ks)

        # Tier 5: Dominant Banker Standout First 4 (if single dominant favorite)
        if is_dominant_banker and len(contender_boxes) >= 3:
            s_t = self.build_standout_ticket("first_4", banker_box, contender_boxes, win_probs, stake=f4_stake, runner_names=runner_names, roving=False)
            s_t["tier"] = "TIER_DOMINANT_STANDOUT"
            s_t["tier_label"] = f"Dominant Banker 1st (Box {banker_box})"
            tickets["first_4_tickets"].append(s_t)

            r_t = self.build_standout_ticket("first_4", banker_box, contender_boxes, win_probs, stake=f4_stake * 2.0, runner_names=runner_names, roving=True)
            r_t["tier"] = "TIER_ROVING_BANKER"
            r_t["tier_label"] = f"Roving Banker 1st-4th (Box {banker_box})"
            tickets["first_4_tickets"].append(r_t)


        # --- 2. TRIFECTA TICKETS ---
        # Option A: 4-Dog Boxed Trifecta (Top 4)
        if len(top_4_boxes) >= 4:
            tickets["trifecta_tickets"].append(
                self.build_boxed_ticket("trifecta", top_4_boxes, win_probs, stake=tri_stake, runner_names=runner_names)
            )
            try:
                tickets["boxed_trifecta_odds_estimate"] = self.estimate_boxed_trifecta_odds(
                    race_id, selected_boxes=top_4_boxes, custom_stake=tri_stake
                )
            except Exception:
                pass

        # Option B: 3-Dog Boxed Trifecta (Top 3)
        if len(top_3_boxes) >= 3:
            tickets["trifecta_tickets"].append(
                self.build_boxed_ticket("trifecta", top_3_boxes, win_probs, stake=6.0, runner_names=runner_names)
            )

        # Option C: Standout Banker Trifecta (if dominant)
        if is_dominant_banker and len(contender_boxes) >= 2:
            tickets["trifecta_tickets"].append(
                self.build_standout_ticket("trifecta", banker_box, contender_boxes, win_probs, stake=tri_stake, runner_names=runner_names, roving=False)
            )

        # --- 3. DUTCHING RECOMMENDATIONS ---
        # Find Top 6 exact First 4 permutations with highest Harville probability
        f4_dist = self.calculate_harville_permutations(win_probs, length=4)
        top_f4_perms = sorted(f4_dist.items(), key=lambda x: x[1], reverse=True)[:6]
        if top_f4_perms:
            dutch_perms = [p[0] for p in top_f4_perms]
            tickets["dutching_recommendations"].append(
                self.build_dutching_ticket("first_4", dutch_perms, win_probs, total_stake=30.0, runner_names=runner_names)
            )

        # Find Top 6 exact Trifecta permutations
        tri_dist = self.calculate_harville_permutations(win_probs, length=3)
        top_tri_perms = sorted(tri_dist.items(), key=lambda x: x[1], reverse=True)[:6]
        if top_tri_perms:
            dutch_tri_perms = [p[0] for p in top_tri_perms]
            tickets["dutching_recommendations"].append(
                self.build_dutching_ticket("trifecta", dutch_tri_perms, win_probs, total_stake=30.0, runner_names=runner_names)
            )

        return tickets


if __name__ == "__main__":
    import json
    optimizer = ExoticBetOptimizer()
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT r.id, m.track_name, r.race_number, r.distance
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        ORDER BY r.id DESC
        LIMIT 1
    """)
    sample_race = c.fetchone()
    if sample_race:
        race_id = sample_race["id"]
        print(f"Generating optimized exotics for {sample_race['track_name']} R{sample_race['race_number']} (Race ID {race_id})...\n")
        res = optimizer.generate_race_exotics(race_id)
        
        print("=== RANKED RUNNERS ===")
        for r in res["ranked_runners"]:
            print(f"Rank {r['rank']}: Box {r['box']} ({r['name']}) - {r['win_prob_pct']}%")

        if res["banker_detected"]:
            b = res["banker"]
            print(f"\nDOMINANT BANKER DETECTED: Box {b['box']} ({b['name']}) with {b['win_prob_pct']}% Win Probability (+{b['margin_gap_pct']}% gap)")

        print("\n=== FIRST 4 TICKETS ===")
        for t in res["first_4_tickets"]:
            print(f"- {t['summary']}")
            print(f"  Win Prob: {t['win_probability_pct']}% | Fair Div: ${t['fair_dividend']} | Projected: ${t['projected_payout']}")

        print("\n=== TRIFECTA TICKETS ===")
        for t in res["trifecta_tickets"]:
            print(f"- {t['summary']}")
            print(f"  Win Prob: {t['win_probability_pct']}% | Fair Div: ${t['fair_dividend']} | Projected: ${t['projected_payout']}")

        print("\n=== DUTCHING RECOMMENDATIONS ===")
        for d in res["dutching_recommendations"]:
            print(f"- {d['summary']}")
            print(f"  Coverage: {d['coverage_probability_pct']}% | Target Return: ${d['target_return']} (ROI: +{d['roi_percent']}%)")
