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
        runner_names: Optional[Dict[int, str]] = None
    ) -> Dict[str, Any]:
        """
        Generates a Boxed Exotic Ticket (Trifecta or First 4).
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
        flexi_pct = round((stake / combos) * 100.0, 2)

        # Calculate joint probability of hitting this boxed ticket
        perm_dist = self.calculate_harville_permutations(win_probs, length=req_len)
        ticket_perms = list(itertools.permutations(selected_boxes, req_len))
        ticket_prob = sum(perm_dist.get(p, 0.0) for p in ticket_perms)

        fair_dividend = round(1.0 / ticket_prob, 2) if ticket_prob > 0 else 0.0
        projected_payout = round(fair_dividend * (flexi_pct / 100.0), 2)
        expected_value = round((ticket_prob * (fair_dividend * flexi_pct / 100.0)) - stake, 2)

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
            "fair_dividend": fair_dividend,
            "projected_payout": projected_payout,
            "expected_value": expected_value,
            "summary": f"{n}-Dog Boxed {exotic_type.title()} ({combos} combos) @ {flexi_pct}% Flexi (${stake:.2f} stake)"
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

        # Standard staking options
        f4_stake = custom_stake or 12.0
        tri_stake = custom_stake or 12.0

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
            "dutching_recommendations": []
        }

        # --- 1. MULTI-TIERED FIRST 4 TICKETS ---
        # Tier 1: 4-Dog Boxed First 4 (Precision Box - 24 combos)
        if len(top_4_boxes) == 4:
            t4 = self.build_boxed_ticket("first_4", top_4_boxes, win_probs, stake=f4_stake, runner_names=runner_names)
            t4["tier"] = "TIER_1_PRECISION"
            t4["tier_label"] = "Tier 1: 4-Dog Box (Precision)"
            t4["tier_description"] = "24 combinations • High flexi dividend • Primary contenders"
            tickets["first_4_tickets"].append(t4)

        # Tier 2: 5-Dog Boxed First 4 (Balanced Value Box - 120 combos)
        if len(top_5_boxes) == 5:
            t5 = self.build_boxed_ticket("first_4", top_5_boxes, win_probs, stake=f4_stake, runner_names=runner_names)
            t5["tier"] = "TIER_2_BALANCED"
            t5["tier_label"] = "Tier 2: 5-Dog Box (Balanced Value)"
            t5["tier_description"] = "120 combinations • ~42% strike rate • Strong place protection"
            tickets["first_4_tickets"].append(t5)

        # Tier 3: 6-Dog Boxed First 4 (Maximum Coverage - 360 combos)
        if len(top_6_boxes) == 6:
            # Staking: $18 gives 5% Flexi; $36 gives 10% Flexi
            cov_stake = max(f4_stake, 18.0) if custom_stake is None else custom_stake
            t6 = self.build_boxed_ticket("first_4", top_6_boxes, win_probs, stake=cov_stake, runner_names=runner_names)
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
