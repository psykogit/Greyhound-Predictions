"""
High-Odds Multi Bet Optimizer & Positive Expected Value (+EV) Engine.
Selects optimal legs with statistical edge and combines them into compound multis with calculated risk & EV.
"""

from typing import List, Dict, Any, Optional
import itertools
from analytics import GreyhoundAnalytics
from db import get_connection


class MultiBetOptimizer:
    def __init__(self):
        self.analytics = GreyhoundAnalytics()

    def find_value_legs(
        self,
        min_edge: float = 0.015,
        min_odds: float = 1.10,
        max_leg_odds: float = 25.0,
        odds_source: str = "sportsbet"
    ) -> List[Dict[str, Any]]:
        """
        Scans all available races in the database for positive edge (+EV) selections.
        Filters out extreme longshots (odds > max_leg_odds) to keep variance realistic.
        odds_source can be 'sportsbet' (default), 'best' (highest across books), or 'sp' (TheDogs/Ladbrokes).
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, race_number, race_name, distance, meeting_id FROM races")
        races = cursor.fetchall()

        value_legs = []
        for r in races:
            race_id = r["id"]
            cursor.execute("SELECT track_name, meeting_date FROM meetings WHERE id = ?", (r["meeting_id"],))
            meeting = cursor.fetchone()
            track_name = meeting["track_name"] if meeting else "Track"

            predictions = self.analytics.predict_race_probabilities(race_id)
            for p in predictions:
                if odds_source == "sportsbet":
                    odds = p.get("sportsbet_odds") or p.get("market_odds")
                elif odds_source == "best":
                    odds = p.get("best_odds") or p.get("sportsbet_odds") or p.get("market_odds")
                else:
                    odds = p.get("market_odds") or p.get("sportsbet_odds")

                if not odds or odds < min_odds or odds > max_leg_odds:
                    continue

                implied_prob = 1.0 / odds
                win_edge = p["model_win_prob"] - implied_prob

                if win_edge >= min_edge:
                    ev = (p["model_win_prob"] * odds) - 1.0
                    value_legs.append({
                        "race_id": race_id,
                        "track": track_name,
                        "race_number": r["race_number"],
                        "dog_name": p["dog_name"],
                        "box": p["box"],
                        "bet_type": "Win",
                        "odds": odds,
                        "odds_source": "Sportsbet" if (p.get("sportsbet_odds") and odds == p.get("sportsbet_odds")) else "SP",
                        "model_prob": p["model_win_prob"],
                        "edge": round(win_edge, 4),
                        "expected_value": round(ev, 4)
                    })

        conn.close()
        return sorted(value_legs, key=lambda x: x["expected_value"], reverse=True)

    def generate_optimal_multis(
        self,
        legs: List[Dict[str, Any]],
        leg_count: int = 3,
        min_total_odds: float = 5.0,
        max_total_odds: float = 500.0
    ) -> List[Dict[str, Any]]:
        """
        Combines positive-EV legs across distinct races into compound multis,
        calculating overall multi odds, joint probability, and total multi EV.
        """
        if len(legs) < leg_count:
            return []

        # Keep best leg per race
        best_per_race = {}
        for leg in legs:
            rid = leg["race_id"]
            if rid not in best_per_race or leg["expected_value"] > best_per_race[rid]["expected_value"]:
                best_per_race[rid] = leg

        unique_race_legs = list(best_per_race.values())

        multis = []
        for combo in itertools.combinations(unique_race_legs, leg_count):
            total_odds = 1.0
            joint_prob = 1.0
            for l in combo:
                total_odds *= l["odds"]
                joint_prob *= l["model_prob"]

            if min_total_odds <= total_odds <= max_total_odds:
                multi_ev = (joint_prob * total_odds) - 1.0
                multis.append({
                    "legs_count": leg_count,
                    "legs": list(combo),
                    "total_odds": round(total_odds, 2),
                    "model_prob_pct": round(joint_prob * 100, 3),
                    "expected_value": round(multi_ev, 4)
                })

        return sorted(multis, key=lambda x: x["expected_value"], reverse=True)


if __name__ == "__main__":
    opt = MultiBetOptimizer()
    v_legs = opt.find_value_legs(min_edge=0.01)
    print(f"Found {len(v_legs)} Value (+EV) Legs:")
    for l in v_legs[:5]:
        print(f"  {l['track']} R{l['race_number']} | Box {l['box']} {l['dog_name']} @ ${l['odds']} (Model Prob: {l['model_prob']*100:.1f}%, EV: +{l['expected_value']*100:.1f}%)")

    multis = opt.generate_optimal_multis(v_legs, leg_count=2, min_total_odds=5.0)
    print(f"\nGenerated {len(multis)} High-Odds Multi Combinations:")
    for m in multis[:3]:
        print(f"  Total Odds: ${m['total_odds']} | Compound Prob: {m['model_prob_pct']}% | Multi EV: +{m['expected_value']*100:.1f}%")
        for leg in m["legs"]:
            print(f"    - {leg['track']} R{leg['race_number']}: Box {leg['box']} {leg['dog_name']} @ ${leg['odds']}")
