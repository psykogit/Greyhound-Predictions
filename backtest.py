"""
Historical Backtesting & Strategy Optimization Harness.
Simulates quantitative betting strategies on completed Australian greyhound races.
Measures ROI, Strike Rate, Yield, Drawdown, and Brier Score probability calibration.
Supports Flat Staking, Fractional Kelly Staking, and Parameter Grid Search.
"""

import math
import sqlite3
from typing import Dict, Any, List, Optional
from datetime import datetime

from db import get_connection
from analytics import GreyhoundAnalytics


class HistoricalBacktester:
    def __init__(self):
        self.analytics = GreyhoundAnalytics()

    def load_completed_races(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieves all races that have finalized finish positions and odds.
        """
        conn = get_connection()
        c = conn.cursor()
        query = """
        SELECT r.id, r.race_number, r.race_name, r.distance, r.grade,
               m.track_name, m.meeting_date,
               count(rr.id) as runners_count,
               sum(case when rr.finish_position = 1 then 1 else 0 end) as has_winner
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        JOIN race_runners rr ON rr.race_id = r.id
        WHERE rr.finish_position IS NOT NULL
        GROUP BY r.id
        HAVING has_winner > 0 AND runners_count >= 4
        ORDER BY m.meeting_date ASC, r.id ASC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        c.execute(query)
        races = [dict(row) for row in c.fetchall()]
        conn.close()
        return races

    def run_backtest(
        self,
        min_edge: float = 0.03,
        min_odds: float = 1.50,
        max_odds: float = 20.0,
        staking_method: str = "flat",  # 'flat' or 'kelly'
        flat_stake: float = 10.0,
        initial_bankroll: float = 1000.0,
        kelly_fraction: float = 0.25,
        odds_source: str = "best",     # 'best', 'sportsbet', or 'sp'
        limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Runs full historical simulation across all completed races.
        """
        races = self.load_completed_races(limit=limit)
        if not races:
            return {
                "status": "no_data",
                "message": "No finished races with verified winner found in database.",
                "total_races": 0,
                "total_bets": 0
            }

        conn = get_connection()
        c = conn.cursor()

        bankroll = initial_bankroll
        peak_bankroll = initial_bankroll
        max_drawdown_dollars = 0.0
        max_drawdown_pct = 0.0

        total_staked = 0.0
        total_returned = 0.0
        winning_bets = 0
        losing_bets = 0

        brier_sum = 0.0
        brier_count = 0

        bet_history: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = [{
            "bet_num": 0,
            "bankroll": round(initial_bankroll, 2),
            "pnl": 0.0
        }]

        for race in races:
            race_id = race["id"]

            # Load actual finish positions
            c.execute("""
            SELECT rr.id, rr.box_number, rr.finish_position, rr.sp_odds, rr.sportsbet_odds,
                   g.dog_name
            FROM race_runners rr
            JOIN greyhounds g ON rr.dog_id = g.id
            WHERE rr.race_id = ?
            """, (race_id,))
            actuals_rows = c.fetchall()
            actuals_map = {}
            for row in actuals_rows:
                # Map by box and dog_name
                actuals_map[row["box_number"]] = row
                actuals_map[row["dog_name"].lower()] = row

            # Generate model predictions for this race
            try:
                predictions = self.analytics.predict_race_probabilities(race_id)
            except Exception:
                continue

            for p in predictions:
                # Resolve market odds based on selected odds source
                if odds_source == "sportsbet":
                    odds = p.get("sportsbet_odds") or p.get("market_odds")
                elif odds_source == "sp":
                    odds = p.get("market_odds")
                else:  # 'best'
                    odds = p.get("best_odds") or p.get("sportsbet_odds") or p.get("market_odds")

                if not odds or odds < min_odds or odds > max_odds:
                    continue

                prob = p["model_win_prob"]
                implied_prob = 1.0 / odds
                edge = prob - implied_prob

                # Lookup actual finish position
                actual_runner = actuals_map.get(p["box"]) or actuals_map.get(p["dog_name"].lower())
                if not actual_runner or actual_runner["finish_position"] is None:
                    continue

                is_winner = (actual_runner["finish_position"] == 1)

                # Brier Score contribution: (P - Actual)^2
                brier_sum += (prob - (1.0 if is_winner else 0.0)) ** 2
                brier_count += 1

                # Check if this dog qualifies for a bet
                if edge >= min_edge:
                    # Calculate Stake
                    if staking_method == "kelly":
                        # Full Kelly: (p*b - q) / b
                        b = odds - 1.0
                        q = 1.0 - prob
                        full_kelly = (prob * b - q) / b if b > 0 else 0.0
                        f_star = max(0.005, min(0.15, full_kelly * kelly_fraction))
                        stake = round(bankroll * f_star, 2)
                        stake = max(1.0, min(stake, bankroll * 0.15))
                    else:  # 'flat'
                        stake = flat_stake

                    if bankroll < stake:
                        continue  # Bankrupt or insufficient funds

                    total_staked += stake

                    if is_winner:
                        payout = round(stake * odds, 2)
                        profit = round(payout - stake, 2)
                        winning_bets += 1
                        total_returned += payout
                        bankroll += profit
                    else:
                        profit = -stake
                        losing_bets += 1
                        bankroll -= stake

                    # Peak and Drawdown Tracking
                    if bankroll > peak_bankroll:
                        peak_bankroll = bankroll
                    dd_dollars = peak_bankroll - bankroll
                    dd_pct = (dd_dollars / peak_bankroll * 100.0) if peak_bankroll > 0 else 0.0
                    if dd_dollars > max_drawdown_dollars:
                        max_drawdown_dollars = dd_dollars
                    if dd_pct > max_drawdown_pct:
                        max_drawdown_pct = dd_pct

                    bet_num = len(bet_history) + 1
                    bet_history.append({
                        "bet_num": bet_num,
                        "date": race["meeting_date"],
                        "track": race["track_name"],
                        "race_number": race["race_number"],
                        "dog_name": p["dog_name"],
                        "box": p["box"],
                        "odds": odds,
                        "model_prob": round(prob, 4),
                        "edge": round(edge, 4),
                        "stake": stake,
                        "won": is_winner,
                        "profit": profit,
                        "bankroll": round(bankroll, 2)
                    })

                    equity_curve.append({
                        "bet_num": bet_num,
                        "bankroll": round(bankroll, 2),
                        "pnl": round(bankroll - initial_bankroll, 2)
                    })

        conn.close()

        total_bets = winning_bets + losing_bets
        strike_rate = round((winning_bets / total_bets * 100.0), 2) if total_bets > 0 else 0.0
        net_profit = round(total_returned - total_staked, 2)
        roi_pct = round((net_profit / total_staked * 100.0), 2) if total_staked > 0 else 0.0
        brier_score = round(brier_sum / brier_count, 4) if brier_count > 0 else 0.0

        return {
            "status": "success",
            "total_races_analyzed": len(races),
            "total_bets": total_bets,
            "winning_bets": winning_bets,
            "losing_bets": losing_bets,
            "strike_rate_pct": strike_rate,
            "initial_bankroll": initial_bankroll,
            "final_bankroll": round(bankroll, 2),
            "total_staked": round(total_staked, 2),
            "total_returned": round(total_returned, 2),
            "net_profit": net_profit,
            "roi_pct": roi_pct,
            "max_drawdown_dollars": round(max_drawdown_dollars, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "brier_score": brier_score,
            "config": {
                "min_edge": min_edge,
                "min_odds": min_odds,
                "max_odds": max_odds,
                "staking_method": staking_method,
                "odds_source": odds_source,
                "flat_stake": flat_stake,
                "kelly_fraction": kelly_fraction
            },
            "recent_bets": bet_history[-15:],
            "equity_curve": equity_curve[::max(1, len(equity_curve) // 40)]  # Sample 40 points for fast UI render
        }

    def optimize_hyperparameters(self) -> List[Dict[str, Any]]:
        """
        Grid search over key trading parameters (min_edge, min_odds, max_odds, staking)
        to identify maximum ROI and optimal risk-adjusted yield.
        """
        edge_grid = [0.02, 0.04, 0.06, 0.08]
        odds_min_grid = [1.50, 1.80, 2.20]
        staking_grid = ["flat", "kelly"]

        results = []
        for edge in edge_grid:
            for min_o in odds_min_grid:
                for st in staking_grid:
                    res = self.run_backtest(
                        min_edge=edge,
                        min_odds=min_o,
                        max_odds=18.0,
                        staking_method=st,
                        flat_stake=10.0,
                        kelly_fraction=0.20
                    )
                    if res.get("total_bets", 0) >= 5:
                        results.append({
                            "min_edge": edge,
                            "min_odds": min_o,
                            "staking": st,
                            "bets": res["total_bets"],
                            "strike_rate": res["strike_rate_pct"],
                            "net_profit": res["net_profit"],
                            "roi_pct": res["roi_pct"],
                            "max_drawdown_pct": res["max_drawdown_pct"],
                            "brier_score": res["brier_score"]
                        })

        # Sort by ROI descending, then net profit
        return sorted(results, key=lambda x: (x["roi_pct"], x["net_profit"]), reverse=True)


if __name__ == "__main__":
    backtester = HistoricalBacktester()
    print("Running initial backtest with default parameters...")
    res = backtester.run_backtest(min_edge=0.03, staking_method="flat", flat_stake=10.0)
    print("--- Backtest Results ---")
    print(f"Races Analyzed: {res.get('total_races_analyzed')}")
    print(f"Total Bets:     {res.get('total_bets')}")
    print(f"Strike Rate:    {res.get('strike_rate_pct')}%")
    print(f"Net Profit:     ${res.get('net_profit')}")
    print(f"ROI:            {res.get('roi_pct')}%")
    print(f"Max Drawdown:   {res.get('max_drawdown_pct')}% (${res.get('max_drawdown_dollars')})")
    print(f"Brier Score:    {res.get('brier_score')}")
