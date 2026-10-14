"""
Daily Learning & Continuous Calibration Engine for Greyhound Betting Engine.

Features:
1. Automatically audits all races from a given date against official results.
2. Calculates strike rates for Win (Top 1), Quinella/Exacta (Top 2), Trifecta (Top 3), and First 4 / SRM (Top 4).
3. Computes flat-staking and value-betting ROI.
4. Identifies systematic track-level and distance-level biases.
5. Calibrates model weights and track geometry profiles.
"""

import sys
import os
import math
import argparse
import sqlite3
import datetime
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple
from scipy.optimize import minimize_scalar

from db import get_connection
from analytics import GreyhoundAnalytics
from thedogs_scraper import TheDogsScraper


class DailyLearningEngine:
    def __init__(self, db_conn: sqlite3.Connection = None):
        self.conn = db_conn or get_connection()
        self.analytics = GreyhoundAnalytics(self.conn)
        self.scraper = TheDogsScraper()

    def scrape_missing_results(self, target_date: str, enforce_delay: bool = False) -> int:
        """Finds any races on target_date without finish positions and updates them via scraper.
        If enforce_delay is True, it will only scrape if the current time is >10 mins past start_time.
        """
        c = self.conn.cursor()
        c.execute("""
        SELECT r.id, r.race_url, r.start_time, r.race_number, m.meeting_date
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.meeting_date = ?
          AND r.race_url IS NOT NULL
          AND r.id NOT IN (
              SELECT race_id FROM race_runners WHERE finish_position = 1
          )
        """, (target_date,))
        pending = c.fetchall()
        
        now_aedt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=11)))
        
        to_scrape = []
        for row in pending:
            race_id, url, start_t, race_num, m_date = row
            if enforce_delay and start_t and ":" in str(start_t):
                try:
                    h, m = map(int, str(start_t).split(":"))
                    d_parts = list(map(int, str(m_date).split("-")))
                    dt_aedt = datetime.datetime(d_parts[0], d_parts[1], d_parts[2], h, m, tzinfo=now_aedt.tzinfo)
                    
                    if h < 8 and (race_num or 1) >= 4:
                        dt_aedt += datetime.timedelta(days=1)
                    
                    diff_mins = (now_aedt - dt_aedt).total_seconds() / 60.0
                    if diff_mins < 10.0:
                        continue  # Skip, hasn't been 10 mins since jump
                except Exception:
                    pass
            to_scrape.append((race_id, url))
        
        if len(to_scrape) > 0:
            print(f"[Learning Engine] Found {len(to_scrape)} eligible pending races without official results on {target_date}.")
        
        updated_count = 0
        for race_id, url in to_scrape:
            try:
                data = self.scraper.parse_race(url)
                if data and data.get("runners"):
                    has_results = any((r.get("finish_position") is not None or r.get("finish_pos") is not None) for r in data["runners"])
                    if has_results:
                        for r_data in data["runners"]:
                            box = r_data.get("box_number") if r_data.get("box_number") is not None else r_data.get("box")
                            pos = r_data.get("finish_position") if r_data.get("finish_position") is not None else r_data.get("finish_pos")
                            sp = r_data.get("sp_odds")
                            if box and pos:
                                c.execute("""
                                UPDATE race_runners
                                SET finish_position = ?, sp_odds = COALESCE(?, sp_odds)
                                WHERE race_id = ? AND box_number = ?
                                """, (pos, sp, race_id, box))
                        self.conn.commit()
                        updated_count += 1
            except Exception as e:
                pass
                
        if updated_count > 0:
            print(f"[Learning Engine] Successfully updated results for {updated_count} races on {target_date}.")
        return updated_count

    def run_daily_audit(self, target_date: str, current_only: bool = False) -> Dict[str, Any]:
        """Runs full audit of model predictions against actual results for target_date."""
        self.conn.row_factory = sqlite3.Row
        c = self.conn.cursor()

        c.execute("""
        SELECT r.id, m.track_name, r.race_number, r.distance, r.grade
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.meeting_date = ?
          AND r.id IN (
              SELECT race_id FROM race_runners WHERE finish_position = 1
          )
        ORDER BY m.track_name, r.race_number
        """, (target_date,))
        races = c.fetchall()

        if not races:
            print(f"[Learning Engine] No completed races with results found for {target_date}.")
            return {}

        total_races = len(races)
        top1_wins = 0
        top2_wins = 0
        top3_wins = 0
        top4_wins = 0
        pick1_placed = 0
        three_of_four_srm = 0
        exact_four_srm = 0

        flat_stake = 10.0
        outlay = 0.0
        returns_top1 = 0.0
        returns_value = 0.0
        value_bets_count = 0
        value_bets_won = 0

        track_stats = defaultdict(lambda: {"races": 0, "top1": 0, "top2": 0, "top3": 0, "roi_return": 0.0})
        dist_stats = defaultdict(lambda: {"races": 0, "top1": 0, "top2": 0, "top3": 0})

        for r in races:
            race_id = r["id"]
            track = r["track_name"]
            dist = r["distance"]

            c.execute("""
            SELECT box_number, finish_position, sp_odds, sportsbet_odds
            FROM race_runners
            WHERE race_id = ? AND finish_position IS NOT NULL
            ORDER BY finish_position ASC
            """, (race_id,))
            finishers = c.fetchall()
            if not finishers or finishers[0]["finish_position"] != 1:
                continue

            winner_box = finishers[0]["box_number"]
            win_odds = finishers[0]["sportsbet_odds"] or finishers[0]["sp_odds"] or 2.50
            actual_top4 = {f["box_number"] for f in finishers[:4] if f["box_number"] is not None}
            actual_places = {f["box_number"] for f in finishers[:3] if f["box_number"] is not None}

            preds = self.analytics.predict_race_probabilities(race_id)
            if not preds:
                continue

            predicted_boxes = [p["box"] for p in preds]
            pick1_box = predicted_boxes[0]

            is_top1 = (pick1_box == winner_box)
            is_top2 = (winner_box in predicted_boxes[:2])
            is_top3 = (winner_box in predicted_boxes[:3])
            is_top4 = (winner_box in predicted_boxes[:4])

            outlay += flat_stake
            if is_top1:
                top1_wins += 1
                returns_top1 += (flat_stake * win_odds)
            if is_top2: top2_wins += 1
            if is_top3: top3_wins += 1
            if is_top4: top4_wins += 1

            if pick1_box in actual_places:
                pick1_placed += 1

            overlap = len(set(predicted_boxes[:4]).intersection(actual_top4))
            if overlap == 4:
                exact_four_srm += 1
            elif overlap >= 3:
                three_of_four_srm += 1

            # Value bets
            for p in preds:
                edge = p.get("edge") or p.get("sportsbet_edge")
                odds = p.get("sportsbet_odds") or p.get("market_odds")
                if edge and edge >= 0.05 and odds and odds >= 1.50:
                    value_bets_count += 1
                    if p["box"] == winner_box:
                        value_bets_won += 1
                        returns_value += (flat_stake * win_odds)

            # Track stats
            track_stats[track]["races"] += 1
            if is_top1:
                track_stats[track]["top1"] += 1
                track_stats[track]["roi_return"] += (flat_stake * win_odds)
            if is_top2: track_stats[track]["top2"] += 1
            if is_top3: track_stats[track]["top3"] += 1

            # Distance stats
            d_bin = "<=350m" if dist <= 350 else ("351-450m" if dist <= 450 else "451m+")
            dist_stats[d_bin]["races"] += 1
            if is_top1: dist_stats[d_bin]["top1"] += 1
            if is_top2: dist_stats[d_bin]["top2"] += 1
            if is_top3: dist_stats[d_bin]["top3"] += 1

        print("\n" + "=" * 70)
        print(f"       DAILY PERFORMANCE & AUDIT REPORT — {target_date}")
        print("=" * 70)
        print(f"Total Completed Races:             {total_races}")
        print(f"Top-1 (Winner) Strike Rate:        {top1_wins}/{total_races} ({top1_wins/total_races*100:.1f}%)")
        print(f"Winner Captured in Top 2:          {top2_wins}/{total_races} ({top2_wins/total_races*100:.1f}%)")
        print(f"Winner Captured in Top 3:          {top3_wins}/{total_races} ({top3_wins/total_races*100:.1f}%)")
        print(f"Winner Captured in Top 4:          {top4_wins}/{total_races} ({top4_wins/total_races*100:.1f}%)")
        print(f"Top Pick Place Rate (Top 3):       {pick1_placed}/{total_races} ({pick1_placed/total_races*100:.1f}%)")
        print(f"SRM Coverage (3+ of Top 4 Picked): {three_of_four_srm + exact_four_srm}/{total_races} ({(three_of_four_srm+exact_four_srm)/total_races*100:.1f}%)")
        print(f"Exact 4 of 4 Finishers Captured:   {exact_four_srm}/{total_races} ({exact_four_srm/total_races*100:.1f}%)")
        print("-" * 70)
        print(f"Flat Staking Outlay ($10/race):    ${outlay:.2f}")
        print(f"Top-1 Win Returns:                 ${returns_top1:.2f} (ROI: {(returns_top1 - outlay)/outlay*100:+.1f}%)")
        if value_bets_count > 0:
            val_outlay = value_bets_count * flat_stake
            print(f"Value Bets Placed (Edge >= 5%):    {value_bets_count} bets ({value_bets_won} won, {value_bets_won/value_bets_count*100:.1f}%)")
            print(f"Value Betting Return:              ${returns_value:.2f} (ROI: {(returns_value - val_outlay)/val_outlay*100:+.1f}%)")
        print("=" * 70)

        print("\nTRACK BREAKDOWN:")
        print(f"{'Track Name':<22} | {'Races':<5} | {'Top-1 Win %':<11} | {'Top-2 Win %':<11} | {'Top-3 Win %':<11} | {'Win P&L':<8}")
        print("-" * 78)
        for t, st in sorted(track_stats.items(), key=lambda x: x[1]["races"], reverse=True):
            n = st["races"]
            pnl = st["roi_return"] - (n * flat_stake)
            pnl_str = f"{pnl:+.1f}"
            print(f"{t:<22} | {n:<5} | {st['top1']/n*100:<10.1f}% | {st['top2']/n*100:<10.1f}% | {st['top3']/n*100:<10.1f}% | ${pnl_str}")

        print("\nDISTANCE BREAKDOWN:")
        for d, st in sorted(dist_stats.items()):
            n = st["races"]
            print(f"{d:<10}: {n} races | Top-1 Win: {st['top1']/n*100:.1f}% | Top-2 Win: {st['top2']/n*100:.1f}% | Top-3 Win: {st['top3']/n*100:.1f}%")

        # Query meeting totals and remaining races for target_date and today
        today_str = datetime.date.today().isoformat()
        c.execute("""
        SELECT m.track_name,
               COUNT(r.id) as total_races,
               SUM(CASE WHEN EXISTS (
                   SELECT 1 FROM race_runners rr
                   WHERE rr.race_id = r.id AND rr.finish_position = 1
               ) THEN 1 ELSE 0 END) as completed_races
        FROM meetings m
        JOIN races r ON r.meeting_id = m.id
        WHERE m.meeting_date = ?
        GROUP BY m.track_name
        """, (target_date,))
        date_track_meetings = {}
        for row in c.fetchall():
            tot = row["total_races"] or 0
            comp = row["completed_races"] or 0
            rem = max(0, tot - comp)
            date_track_meetings[row["track_name"]] = {
                "total_races": tot,
                "completed_races": comp,
                "remaining_races": rem,
            }

        today_active_tracks = {}
        if target_date != today_str:
            c.execute("""
            SELECT m.track_name,
                   COUNT(r.id) as total_races,
                   SUM(CASE WHEN EXISTS (
                       SELECT 1 FROM race_runners rr
                       WHERE rr.race_id = r.id AND rr.finish_position = 1
                   ) THEN 1 ELSE 0 END) as completed_races
            FROM meetings m
            JOIN races r ON r.meeting_id = m.id
            WHERE m.meeting_date = ?
            GROUP BY m.track_name
            """, (today_str,))
            for row in c.fetchall():
                tot = row["total_races"] or 0
                comp = row["completed_races"] or 0
                rem = max(0, tot - comp)
                today_active_tracks[row["track_name"]] = {
                    "total_races": tot,
                    "completed_races": comp,
                    "remaining_races": rem,
                }

        track_stats_list = []
        for t, st in sorted(track_stats.items(), key=lambda x: x[1]["races"], reverse=True):
            d_info = date_track_meetings.get(t, {})
            tot = d_info.get("total_races", st["races"])
            rem = d_info.get("remaining_races", 0)
            if target_date == today_str:
                is_curr = (rem > 0)
                rem_today = rem
            else:
                t_today_info = today_active_tracks.get(t)
                if t_today_info:
                    rem_today = t_today_info.get("remaining_races", 0)
                    is_curr = (rem_today > 0)
                else:
                    rem_today = 0
                    is_curr = (rem > 0)

            item = {
                "track": t,
                "races": st["races"],
                "total_meeting_races": tot,
                "remaining_races": rem_today if (target_date != today_str and t in today_active_tracks) else rem,
                "is_current_location": is_curr,
                "top1_win_rate": round(st["top1"] / st["races"] * 100, 1),
                "top2_win_rate": round(st["top2"] / st["races"] * 100, 1),
                "top3_win_rate": round(st["top3"] / st["races"] * 100, 1),
                "pnl": round(st["roi_return"] - (st["races"] * flat_stake), 2)
            }
            track_stats_list.append(item)

        filtered_track_stats = [t for t in track_stats_list if t["is_current_location"]] if current_only else track_stats_list

        return {
            "date": target_date,
            "total_races": total_races,
            "top1_wins": top1_wins,
            "top1_rate": round(top1_wins / total_races * 100, 1) if total_races else 0,
            "top2_wins": top2_wins,
            "top2_rate": round(top2_wins / total_races * 100, 1) if total_races else 0,
            "top3_wins": top3_wins,
            "top3_rate": round(top3_wins / total_races * 100, 1) if total_races else 0,
            "top4_wins": top4_wins,
            "top4_rate": round(top4_wins / total_races * 100, 1) if total_races else 0,
            "pick1_placed": pick1_placed,
            "pick1_place_rate": round(pick1_placed / total_races * 100, 1) if total_races else 0,
            "srm_coverage": three_of_four_srm + exact_four_srm,
            "srm_rate": round((three_of_four_srm + exact_four_srm) / total_races * 100, 1) if total_races else 0,
            "exact_four": exact_four_srm,
            "exact_four_rate": round(exact_four_srm / total_races * 100, 1) if total_races else 0,
            "outlay": round(outlay, 2),
            "returns_top1": round(returns_top1, 2),
            "roi_percent": round((returns_top1 - outlay) / outlay * 100, 1) if outlay else 0,
            "value_bets_count": value_bets_count,
            "value_bets_won": value_bets_won,
            "value_returns": round(returns_value, 2),
            "value_roi": round((returns_value - (value_bets_count * flat_stake)) / (value_bets_count * flat_stake) * 100, 1) if value_bets_count else 0,
            "current_only": current_only,
            "current_tracks_count": len([t for t in track_stats_list if t["is_current_location"]]),
            "total_tracks_count": len(track_stats_list),
            "track_stats": filtered_track_stats,
            "all_track_stats": track_stats_list,
            "distance_stats": [
                {
                    "distance": d,
                    "races": st["races"],
                    "top1_win_rate": round(st["top1"] / st["races"] * 100, 1),
                    "top2_win_rate": round(st["top2"] / st["races"] * 100, 1),
                    "top3_win_rate": round(st["top3"] / st["races"] * 100, 1)
                }
                for d, st in sorted(dist_stats.items())
            ]
        }

    def compute_brier_and_log_loss(
        self,
        target_date: Optional[str] = None,
        track_filter: Optional[str] = None,
        limit_races: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Computes multi-class Brier Score, Log-Loss, and Expected Calibration Error (ECE)
        across completed races. Generates reliability diagram bins to assess over/under-confidence.
        """
        c = self.conn.cursor()

        date_clause = "AND m.meeting_date = ?" if target_date else ""
        track_clause = "AND UPPER(m.track_name) = UPPER(?)" if track_filter else ""
        limit_clause = f"LIMIT {int(limit_races)}" if limit_races else ""

        params = []
        if target_date:
            params.append(target_date)
        if track_filter:
            params.append(track_filter)

        c.execute(f"""
        SELECT r.id, m.track_name, r.race_number, m.meeting_date, r.distance
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        WHERE r.id IN (
            SELECT race_id FROM race_runners WHERE finish_position = 1
        )
        {date_clause}
        {track_clause}
        ORDER BY r.id DESC
        {limit_clause}
        """, tuple(params))
        races = c.fetchall()

        if not races:
            return {"status": "no_data", "total_races": 0}

        total_brier = 0.0
        total_log_loss = 0.0
        race_count = 0

        # Reliability decile bins: [0-0.05), [0.05-0.10), [0.10-0.15), [0.15-0.20), [0.20-0.30), [0.30-0.40), [0.40-0.50), [0.50-1.0]
        bins_def = [
            (0.00, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20),
            (0.20, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 1.00)
        ]
        bin_counts = [0] * len(bins_def)
        bin_sum_pred = [0.0] * len(bins_def)
        bin_sum_actual = [0.0] * len(bins_def)

        track_metrics = defaultdict(lambda: {"races": 0, "brier_sum": 0.0, "log_loss_sum": 0.0})

        for r_id, track, r_num, m_date, dist in races:
            # Official winner
            c.execute("SELECT box_number FROM race_runners WHERE race_id = ? AND finish_position = 1", (r_id,))
            w_row = c.fetchone()
            if not w_row or w_row[0] is None:
                continue
            winner_box = w_row[0]

            preds = self.analytics.predict_race_probabilities(r_id)
            if not preds:
                continue

            # Multi-class Brier score: sum_{i} (p_i - y_i)^2
            brier_race = sum((p["model_win_prob"] - (1.0 if p["box"] == winner_box else 0.0)) ** 2 for p in preds)
            
            # Log loss: -ln(max(eps, p_winner))
            winner_prob = next((p["model_win_prob"] for p in preds if p["box"] == winner_box), 1e-4)
            ll_race = -math.log(max(1e-6, winner_prob))

            total_brier += brier_race
            total_log_loss += ll_race
            race_count += 1

            track_metrics[track]["races"] += 1
            track_metrics[track]["brier_sum"] += brier_race
            track_metrics[track]["log_loss_sum"] += ll_race

            # Reliability binning
            for p in preds:
                prob = p["model_win_prob"]
                is_win = 1.0 if p["box"] == winner_box else 0.0
                for b_idx, (b_low, b_high) in enumerate(bins_def):
                    if (prob >= b_low and prob < b_high) or (b_idx == len(bins_def) - 1 and prob >= b_low):
                        bin_counts[b_idx] += 1
                        bin_sum_pred[b_idx] += prob
                        bin_sum_actual[b_idx] += is_win
                        break

        if race_count == 0:
            return {"status": "no_valid_predictions", "total_races": 0}

        avg_brier = round(total_brier / race_count, 4)
        avg_log_loss = round(total_log_loss / race_count, 4)

        # Compute Expected Calibration Error (ECE)
        total_eval_predictions = sum(bin_counts)
        ece = 0.0
        reliability_diagram = []
        for b_idx, (b_low, b_high) in enumerate(bins_def):
            cnt = bin_counts[b_idx]
            if cnt > 0:
                conf = bin_sum_pred[b_idx] / cnt
                acc = bin_sum_actual[b_idx] / cnt
                ece += (cnt / max(1, total_eval_predictions)) * abs(acc - conf)
                reliability_diagram.append({
                    "range": f"{int(b_low*100)}%-{int(b_high*100)}%",
                    "count": cnt,
                    "avg_confidence": round(conf * 100, 1),
                    "empirical_accuracy": round(acc * 100, 1),
                    "gap": round((acc - conf) * 100, 1)
                })
            else:
                reliability_diagram.append({
                    "range": f"{int(b_low*100)}%-{int(b_high*100)}%",
                    "count": 0,
                    "avg_confidence": round((b_low + b_high) / 2 * 100, 1),
                    "empirical_accuracy": 0.0,
                    "gap": 0.0
                })

        track_summary = []
        for t, st in sorted(track_metrics.items(), key=lambda x: x[1]["races"], reverse=True):
            n = st["races"]
            track_summary.append({
                "track": t,
                "races": n,
                "brier_score": round(st["brier_sum"] / n, 4),
                "log_loss": round(st["log_loss_sum"] / n, 4)
            })

        return {
            "status": "success",
            "sample_races": race_count,
            "overall_brier_score": avg_brier,
            "overall_log_loss": avg_log_loss,
            "expected_calibration_error": round(ece, 4),
            "reliability_diagram": reliability_diagram,
            "track_metrics": track_summary
        }

    def auto_tune_track_temperatures(self, min_races: int = 4, verbose: bool = True) -> Dict[str, Any]:
        """
        Continuous calibration loop:
        For each track with >= min_races completed races, finds the optimal softmax temperature
        T in [1.2, 3.5] that minimizes log-loss.
        Persists the tuned parameters into track_calibrations SQLite table.
        """
        c = self.conn.cursor()
        c.execute("""
        SELECT m.track_name, COUNT(DISTINCT r.id) as race_cnt
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        JOIN race_runners rr ON rr.race_id = r.id
        WHERE rr.finish_position = 1
        GROUP BY m.track_name
        HAVING COUNT(DISTINCT r.id) >= ?
        ORDER BY race_cnt DESC
        """, (min_races,))
        qualifying_tracks = c.fetchall()

        if verbose:
            print(f"[Calibration] Found {len(qualifying_tracks)} tracks meeting minimum sample threshold ({min_races}+ races).")

        calibrated_results = []

        for track_name, race_count in qualifying_tracks:
            # Fetch completed race IDs for this track
            c.execute("""
            SELECT DISTINCT r.id
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            JOIN race_runners rr ON rr.race_id = r.id
            WHERE UPPER(m.track_name) = UPPER(?) AND rr.finish_position = 1
            ORDER BY r.id DESC
            LIMIT 40
            """, (track_name,))
            race_ids = [row[0] for row in c.fetchall()]

            # Fetch winner box per race
            race_winners = {}
            for r_id in race_ids:
                c.execute("SELECT box_number FROM race_runners WHERE race_id = ? AND finish_position = 1", (r_id,))
                w_row = c.fetchone()
                if w_row and w_row[0]:
                    race_winners[r_id] = w_row[0]

            valid_race_ids = [r_id for r_id in race_ids if r_id in race_winners]
            if len(valid_race_ids) < min_races:
                continue

            # Baseline evaluation at default T = 2.4
            baseline_nll = 0.0
            baseline_brier = 0.0
            for r_id in valid_race_ids:
                w_box = race_winners[r_id]
                preds = self.analytics.predict_race_probabilities(r_id, temperature=2.4)
                w_prob = next((p["model_win_prob"] for p in preds if p["box"] == w_box), 1e-4)
                baseline_nll += -math.log(max(1e-6, w_prob))
                baseline_brier += sum((p["model_win_prob"] - (1.0 if p["box"] == w_box else 0.0)) ** 2 for p in preds)

            baseline_log_loss = baseline_nll / len(valid_race_ids)
            baseline_brier_avg = baseline_brier / len(valid_race_ids)

            # Optimization objective: minimize log-loss over temperature T
            def objective(t_val: float) -> float:
                nll = 0.0
                for r_id in valid_race_ids:
                    w_box = race_winners[r_id]
                    preds = self.analytics.predict_race_probabilities(r_id, temperature=t_val)
                    w_prob = next((p["model_win_prob"] for p in preds if p["box"] == w_box), 1e-4)
                    nll += -math.log(max(1e-6, w_prob))
                return nll / len(valid_race_ids)

            opt_res = minimize_scalar(objective, bounds=(1.4, 3.2), method="bounded")
            optimal_temp = round(float(opt_res.x), 2)
            calibrated_log_loss = round(float(opt_res.fun), 4)

            # Evaluate Brier Score at optimal temperature
            cal_brier = 0.0
            for r_id in valid_race_ids:
                w_box = race_winners[r_id]
                preds = self.analytics.predict_race_probabilities(r_id, temperature=optimal_temp)
                cal_brier += sum((p["model_win_prob"] - (1.0 if p["box"] == w_box else 0.0)) ** 2 for p in preds)
            calibrated_brier_avg = round(cal_brier / len(valid_race_ids), 4)

            # Save / update in track_calibrations
            c.execute("""
            INSERT INTO track_calibrations (
                track_name, sample_races, brier_score, log_loss, ece, temperature, last_calibrated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(track_name) DO UPDATE SET
                sample_races = excluded.sample_races,
                brier_score = excluded.brier_score,
                log_loss = excluded.log_loss,
                temperature = excluded.temperature,
                last_calibrated_at = CURRENT_TIMESTAMP
            """, (track_name, len(valid_race_ids), calibrated_brier_avg, calibrated_log_loss, None, optimal_temp))
            self.conn.commit()

            # Invalidate in-memory cache in analytics
            t_key = track_name.strip().upper()
            if hasattr(self.analytics, "_track_temp_cache") and t_key in self.analytics._track_temp_cache:
                self.analytics._track_temp_cache[t_key] = optimal_temp

            loss_delta = round(calibrated_log_loss - baseline_log_loss, 4)
            calibrated_results.append({
                "track": track_name,
                "sample_races": len(valid_race_ids),
                "baseline_temp": 2.4,
                "calibrated_temp": optimal_temp,
                "baseline_log_loss": round(baseline_log_loss, 4),
                "calibrated_log_loss": calibrated_log_loss,
                "loss_delta": loss_delta,
                "calibrated_brier": calibrated_brier_avg
            })

            if verbose:
                print(f"  {track_name:<20}: N={len(valid_race_ids):<3} | T: 2.4 -> {optimal_temp:.2f} | LogLoss: {baseline_log_loss:.3f} -> {calibrated_log_loss:.3f} ({loss_delta:+.3f})")

        return {
            "status": "success",
            "tracks_calibrated": len(calibrated_results),
            "calibrations": calibrated_results
        }

    def get_calibration_status(self) -> Dict[str, Any]:
        """Returns all persisted track calibration profiles from SQLite."""
        c = self.conn.cursor()
        c.execute("""
        SELECT track_name, sample_races, brier_score, log_loss, ece, temperature, last_calibrated_at
        FROM track_calibrations
        ORDER BY sample_races DESC
        """)
        rows = c.fetchall()
        return {
            "total_calibrated_tracks": len(rows),
            "tracks": [
                {
                    "track": r[0],
                    "sample_races": r[1],
                    "brier_score": r[2],
                    "log_loss": r[3],
                    "ece": r[4],
                    "temperature": r[5],
                    "last_calibrated_at": r[6]
                }
                for r in rows
            ]
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Greyhound Learning & Calibration Engine")
    parser.add_argument("--date", type=str, default=datetime.date.today().isoformat(), help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--scrape-missing", action="store_true", help="Scrape any missing results for this date")
    parser.add_argument("--brier", action="store_true", help="Compute Brier score, Log-Loss, and Expected Calibration Error")
    parser.add_argument("--auto-tune", action="store_true", help="Auto-tune track temperature parameters via continuous optimization")
    parser.add_argument("--all-races", action="store_true", help="Evaluate all historical races instead of single date")
    args = parser.parse_args()

    engine = DailyLearningEngine()
    if args.scrape_missing:
        engine.scrape_missing_results(args.date)
    if args.auto_tune:
        print("\n=== RUNNING TRACK TEMPERATURE AUTO-TUNING ===")
        engine.auto_tune_track_temperatures(min_races=4, verbose=True)
    elif args.brier:
        target_d = None if args.all_races else args.date
        res = engine.compute_brier_and_log_loss(target_date=target_d)
        print("\n=== PROBABILISTIC CALIBRATION AUDIT ===")
        print(f"Sample Races:               {res.get('sample_races', 0)}")
        print(f"Multi-class Brier Score:    {res.get('overall_brier_score')}")
        print(f"Cross-Entropy Log-Loss:     {res.get('overall_log_loss')}")
        print(f"Expected Calibration Error: {res.get('expected_calibration_error')}")
        print("\nReliability Curve Deciles:")
        for b in res.get("reliability_diagram", []):
            print(f"  {b['range']:<10}: N={b['count']:<4} | Confidence: {b['avg_confidence']}% | Empirical Acc: {b['empirical_accuracy']}% (Gap: {b['gap']:+0.1f}%)")
    else:
        engine.run_daily_audit(args.date)
