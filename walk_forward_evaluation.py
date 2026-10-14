"""
Walk-Forward Out-Of-Sample Model Evaluation Engine.

Strictly trains / calibrates on historical data before day D,
and evaluates out-of-sample performance on day D against the de-vigged market benchmark.
"""

import math
import sqlite3
from typing import Dict, Any, List, Optional
from db import get_connection
from analytics import GreyhoundAnalytics


class WalkForwardEvaluator:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or get_connection()
        self.analytics = GreyhoundAnalytics(self.conn)

    def get_completed_meeting_dates(self) -> List[str]:
        """
        Retrieves all valid chronological meeting dates with completed races.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT DISTINCT m.meeting_date
            FROM meetings m
            JOIN races r ON m.id = r.meeting_id
            JOIN race_runners rr ON r.id = rr.race_id
            WHERE rr.finish_position = 1
              AND m.meeting_date IS NOT NULL
              AND length(m.meeting_date) = 10
              AND m.meeting_date NOT LIKE '%Guides%'
            ORDER BY m.meeting_date ASC
        """)
        return [row[0] for row in cursor.fetchall()]

    def evaluate_race(self, race_id: int, meeting_date: str) -> Optional[Dict[str, Any]]:
        """
        Evaluates a single completed race strictly using pre-race data and compares
        model log-loss against the de-vigged market log-loss.
        """
        cursor = self.conn.cursor()

        # 1. Fetch completed runners and odds
        cursor.execute("""
            SELECT rr.id, rr.box_number, rr.finish_position, rr.sp_odds, rr.sportsbet_odds
            FROM race_runners rr
            WHERE rr.race_id = ?
              AND (rr.is_scratched = 0 OR rr.is_scratched IS NULL)
        """, (race_id,))
        runners = cursor.fetchall()
        if len(runners) < 4:
            return None

        # Find the winner
        winner_box = None
        market_odds_map: Dict[int, float] = {}

        for r in runners:
            box = r["box_number"]
            pos_raw = r["finish_position"]
            try:
                f_pos = int(str(pos_raw).strip()) if pos_raw is not None else None
            except (ValueError, TypeError):
                f_pos = None

            if f_pos == 1:
                winner_box = box

            odds = r["sp_odds"] or r["sportsbet_odds"]
            if odds and odds > 1.0:
                market_odds_map[box] = float(odds)
            else:
                market_odds_map[box] = 15.0  # Conservative unquoted longshot fallback

        if winner_box is None:
            return None

        # 2. De-vig the market to obtain true implied market probabilities
        # p_devig(i) = (1 / odds_i) / sum_j (1 / odds_j)
        tot_implied = sum(1.0 / o for o in market_odds_map.values())
        if tot_implied <= 0:
            return None

        market_probs = {b: (1.0 / o) / tot_implied for b, o in market_odds_map.items()}

        # 3. Compute Model Out-Of-Sample Probabilities (strictly point-in-time filtered)
        try:
            preds = self.analytics.predict_race_probabilities(race_id, hydrate_profiles=False)
        except Exception:
            return None

        if not preds or len(preds) < 4:
            return None

        model_probs = {p["box"]: max(1e-6, p.get("model_win_prob") or p.get("win_probability", 0.0)) for p in preds}
        m_tot = sum(model_probs.values())
        model_probs = {b: p / m_tot for b, p in model_probs.items()}

        # Verify winner is present in both probability spaces
        if winner_box not in model_probs or winner_box not in market_probs:
            return None

        p_model_w = model_probs[winner_box]
        p_market_w = market_probs[winner_box]

        # Log-Loss: -ln(P(winner))
        model_log_loss = -math.log(max(1e-12, p_model_w))
        market_log_loss = -math.log(max(1e-12, p_market_w))
        delta_log_loss = model_log_loss - market_log_loss  # Negative = model beats market!

        # Brier Score: sum_i (P_i - y_i)^2
        model_brier = sum((model_probs[b] - (1.0 if b == winner_box else 0.0)) ** 2 for b in model_probs)
        market_brier = sum((market_probs.get(b, 0.0) - (1.0 if b == winner_box else 0.0)) ** 2 for b in model_probs)

        # Winner hit detection
        top_model_box = max(model_probs.keys(), key=lambda b: model_probs[b])
        top_market_box = min(market_odds_map.keys(), key=lambda b: market_odds_map[b])

        return {
            "race_id": race_id,
            "meeting_date": meeting_date,
            "winner_box": winner_box,
            "p_model_winner": p_model_w,
            "p_market_winner": p_market_w,
            "model_log_loss": model_log_loss,
            "market_log_loss": market_log_loss,
            "delta_log_loss": delta_log_loss,
            "model_brier": model_brier,
            "market_brier": market_brier,
            "model_hit": (top_model_box == winner_box),
            "market_hit": (top_market_box == winner_box),
        }

    def run_walk_forward(
        self,
        min_history_days: int = 2,
        max_test_days: Optional[int] = None,
        verbose: bool = True
    ) -> Dict[str, Any]:
        """
        Executes a full chronological walk-forward out-of-sample evaluation.
        """
        dates = self.get_completed_meeting_dates()
        if len(dates) <= min_history_days:
            return {
                "status": "INSUFFICIENT_HISTORY",
                "message": f"Need at least {min_history_days + 1} dates, found {len(dates)}"
            }

        test_dates = dates[min_history_days:]
        if max_test_days:
            test_dates = test_dates[:max_test_days]

        if verbose:
            print("=" * 80)
            print("WALK-FORWARD OUT-OF-SAMPLE EVALUATION ENGINE")
            print("Benchmark: Proportional De-Vigged Market Odds (p = (1/O) / sum(1/O))")
            print(f"Total Available Dates: {len(dates)} | Evaluation Test Dates: {len(test_dates)}")
            print("=" * 80)

        cursor = self.conn.cursor()
        daily_summaries = []
        all_evals = []

        for d_idx, test_date in enumerate(test_dates, 1):
            # Fetch all completed races on test_date
            cursor.execute("""
                SELECT DISTINCT r.id
                FROM races r
                JOIN meetings m ON r.meeting_id = m.id
                JOIN race_runners rr ON r.id = rr.race_id
                WHERE m.meeting_date = ? AND rr.finish_position = 1
                ORDER BY r.id ASC
            """, (test_date,))
            races = [row[0] for row in cursor.fetchall()]

            day_evals = []
            for r_id in races:
                res = self.evaluate_race(r_id, test_date)
                if res:
                    day_evals.append(res)
                    all_evals.append(res)

            if not day_evals:
                continue

            n_races = len(day_evals)
            d_model_ll = sum(e["model_log_loss"] for e in day_evals) / n_races
            d_mkt_ll = sum(e["market_log_loss"] for e in day_evals) / n_races
            d_delta_ll = d_model_ll - d_mkt_ll
            d_model_brier = sum(e["model_brier"] for e in day_evals) / n_races
            d_mkt_brier = sum(e["market_brier"] for e in day_evals) / n_races
            d_model_hits = sum(1 for e in day_evals if e["model_hit"])
            d_mkt_hits = sum(1 for e in day_evals if e["market_hit"])

            daily_summaries.append({
                "date": test_date,
                "races_count": n_races,
                "model_log_loss": round(d_model_ll, 4),
                "market_log_loss": round(d_mkt_ll, 4),
                "delta_log_loss": round(d_delta_ll, 4),
                "model_brier": round(d_model_brier, 4),
                "market_brier": round(d_mkt_brier, 4),
                "model_strike_rate": round((d_model_hits / n_races) * 100.0, 1),
                "market_strike_rate": round((d_mkt_hits / n_races) * 100.0, 1),
            })

            if verbose:
                edge_indicator = "BETTER (-)" if d_delta_ll < 0 else "WORSE (+)"
                print(
                    f"[{d_idx}/{len(test_dates)}] Day {test_date} | Races: {n_races:3d} | "
                    f"Model LL: {d_model_ll:.4f} | Mkt LL: {d_mkt_ll:.4f} | "
                    f"Delta: {d_delta_ll:+.4f} ({edge_indicator}) | "
                    f"Strike: Model {d_model_hits}/{n_races} vs Mkt {d_mkt_hits}/{n_races}"
                )

        if not all_evals:
            return {"status": "NO_EVALUATIONS"}

        total_races = len(all_evals)
        mean_model_ll = sum(e["model_log_loss"] for e in all_evals) / total_races
        mean_market_ll = sum(e["market_log_loss"] for e in all_evals) / total_races
        mean_delta_ll = mean_model_ll - mean_market_ll
        mean_model_brier = sum(e["model_brier"] for e in all_evals) / total_races
        mean_market_brier = sum(e["market_brier"] for e in all_evals) / total_races
        total_model_hits = sum(1 for e in all_evals if e["model_hit"])
        total_market_hits = sum(1 for e in all_evals if e["market_hit"])

        beats_market = (mean_delta_ll < 0.0)
        verdict = (
            "OUTPERFORMING MARKET: Out-of-sample log-loss is superior to de-vigged market. Edges are mathematically grounded."
            if beats_market else
            "MARKET DOMINANT: De-vigged market is sharper than raw model. Recommend using shrinkage / market-anchor blending before live staking."
        )

        result = {
            "status": "SUCCESS",
            "test_dates_count": len(daily_summaries),
            "total_races_evaluated": total_races,
            "mean_model_log_loss": round(mean_model_ll, 4),
            "mean_market_log_loss": round(mean_market_ll, 4),
            "mean_delta_log_loss": round(mean_delta_ll, 4),
            "beats_market_out_of_sample": beats_market,
            "mean_model_brier": round(mean_model_brier, 4),
            "mean_market_brier": round(mean_market_brier, 4),
            "model_strike_rate_pct": round((total_model_hits / total_races) * 100.0, 2),
            "market_strike_rate_pct": round((total_market_hits / total_races) * 100.0, 2),
            "verdict": verdict,
            "daily_breakdown": daily_summaries,
        }

        if verbose:
            print("=" * 80)
            print("WALK-FORWARD AGGREGATE SUMMARY")
            print(f"Total Out-Of-Sample Races Evaluated: {total_races}")
            print(f"Mean Model Log-Loss:               {mean_model_ll:.4f}")
            print(f"Mean De-Vigged Market Log-Loss:    {mean_market_ll:.4f}")
            print(f"Delta Log-Loss (Model - Market):   {mean_delta_ll:+.4f}")
            print(f"Model Brier Score:                 {mean_model_brier:.4f}")
            print(f"Market Brier Score:                {mean_market_brier:.4f}")
            print(f"Model Winner Strike Rate:          {(total_model_hits / total_races) * 100.0:.2f}% ({total_model_hits}/{total_races})")
            print(f"Market Favorite Strike Rate:       {(total_market_hits / total_races) * 100.0:.2f}% ({total_market_hits}/{total_races})")
            print(f"VERDICT: {verdict}")
            print("=" * 80)

        return result
