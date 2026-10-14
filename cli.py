"""
Greyhound Betting Engine - Command-Line Orchestrator & CLI Runner.
Usage:
    python cli.py collect-fasttrack    # Collects upcoming race cards & past starts from GRV
    python cli.py collect-thedogs      # Collects today's race cards, speedmaps & odds from TheDogs
    python cli.py collect-weather      # Updates current meteorological observations for tracks
    python cli.py list-upcoming        # Shows all upcoming races scheduled for today/tonight
    python cli.py analyze-race <id>    # Analyzes a race and displays runner probabilities & edges
    python cli.py scan-multis          # Finds +EV legs and constructs optimized high-odds multis
"""

import sys
import argparse
from db import init_db, get_connection
from fasttrack_scraper import FastTrackScraper
from thedogs_scraper import TheDogsScraper
from weather_collector import WeatherCollector
from analytics import GreyhoundAnalytics
from multi_optimizer import MultiBetOptimizer
from dog_hydration_daemon import DogHydrationWorker
from daily_learning_engine import DailyLearningEngine
from walk_forward_evaluation import WalkForwardEvaluator
from typing import Optional


def cmd_init():
    init_db()
    print("Database schema successfully verified/initialized.")


def cmd_collect_fasttrack(limit: int = 5):
    print("=== Scraping FastTrack (GRV) Race Fields & Sectional History ===")
    scraper = FastTrackScraper()
    links = scraper.fetch_meeting_links()
    print(f"Found {len(links)} meeting links.")
    count = 0
    for link in links:
        if count >= limit:
            break
        data = scraper.parse_meeting(link)
        if data and data["races"]:
            m_id = scraper.save_to_db(data)
            print(f"  [+] Saved {data['track_name']} ({data['meeting_date']}) - {len(data['races'])} races (Meeting ID: {m_id})")
            count += 1
    print(f"Finished collecting {count} FastTrack meetings.")


def cmd_collect_thedogs(limit: int = 30):
    print("=== Scraping TheDogs.com.au Upcoming & Today's Race Cards ===")
    scraper = TheDogsScraper()
    links = scraper.fetch_today_race_links()
    print(f"Found {len(links)} race links today.")
    count = 0
    upcoming_count = 0
    for link in links[:limit]:
        data = scraper.parse_race(link)
        if data and data["runners"]:
            r_id = scraper.save_race_to_db(data)
            is_upcoming = any(r.get("finish_position") is None for r in data["runners"])
            status_tag = "UPCOMING" if is_upcoming else "Completed"
            if is_upcoming:
                upcoming_count += 1
            print(f"  [+] [{status_tag}] Saved {data['track_name']} R{data['race_number']} ({data['distance']}m) - {len(data['runners'])} runners (Race ID: {r_id})")
            count += 1
    print(f"Finished collecting {count} races ({upcoming_count} upcoming).")


def cmd_list_upcoming():
    print("\n=== Upcoming Scheduled Races in Database ===")
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT r.id, m.track_name, r.race_number, r.distance, r.start_time,
           count(rr.id) as total_runners,
           sum(case when rr.finish_position is null then 1 else 0 end) as remaining_runners
    FROM races r
    JOIN meetings m ON r.meeting_id = m.id
    JOIN race_runners rr ON rr.race_id = r.id
    GROUP BY r.id
    HAVING remaining_runners > 0
    ORDER BY r.id ASC
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No upcoming races found. Run: python cli.py collect-thedogs")
        return

    print(f"{'Race ID':<8} | {'Track':<18} | {'Race #':<8} | {'Dist':<7} | {'Start Time':<12} | {'Runners'}")
    print("-" * 75)
    for row in rows:
        st = row["start_time"] or "TBD"
        print(f"{row['id']:<8} | {row['track_name']:<18} | R{row['race_number']:<7} | {str(row['distance'])+'m':<7} | {st:<12} | {row['remaining_runners']}")


def cmd_collect_weather():
    print("=== Fetching Live Track Weather from Open-Meteo ===")
    collector = WeatherCollector()
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, name FROM tracks")
    tracks = c.fetchall()
    conn.close()

    for t in tracks:
        rec_id = collector.record_track_weather(t["name"])
        if rec_id:
            print(f"  [+] Weather recorded for {t['name']}")
    print("Finished updating track weather records.")


def cmd_analyze_race(race_id: int):
    analytics = GreyhoundAnalytics()
    preds = analytics.predict_race_probabilities(race_id)
    if not preds:
        print(f"No runners found for Race ID: {race_id}")
        return

    print(f"\n=== Model Assessment for Race ID: {race_id} ===")
    if preds and preds[0].get("expert_tips"):
        print(f"Expert Tips: {', '.join(map(str, preds[0]['expert_tips']))}")
    print(f"{'Box':<4} | {'Greyhound':<22} | {'Trainer':<20} | {'Model Win%':<10} | {'Tip Rank':<8} | {'Fair Odds':<10} | {'Market Odds':<12} | {'Edge'}")
    print("-" * 105)
    for p in preds:
        m_odds = f"${p['market_odds']:.2f}" if p['market_odds'] else "N/A"
        edge_str = f"{p['edge']*100:+.1f}%" if p['edge'] is not None else "N/A"
        tip_str = f"#{p['expert_tip_rank']}" if p.get('expert_tip_rank') else "-"
        tb_str = f" [TB: {p['tie_breaker_note']}]" if p.get('tie_breaker_applied') else ""
        print(f"{p['box']:<4} | {p['dog_name']:<22} | {(p['trainer'] or ''):<20} | {p['model_win_prob']*100:>8.1f}% | {tip_str:<8} | ${p['fair_win_odds']:<9.2f} | {m_odds:<12} | {edge_str}{tb_str}")


def cmd_scan_multis():
    print("\n=== Scanning Races for Positive Expected Value (+EV) Legs ===")
    opt = MultiBetOptimizer()
    v_legs = opt.find_value_legs(min_edge=0.01)
    if not v_legs:
        print("No positive edge legs found with the current database and threshold.")
        return

    print(f"Found {len(v_legs)} +EV leg candidates:")
    for l in v_legs:
        print(f"  * {l['track']} R{l['race_number']} | Box {l['box']} {l['dog_name']} @ ${l['odds']} (Model: {l['model_prob']*100:.1f}%, EV: +{l['expected_value']*100:.1f}%)")

    for legs_num in [2, 3, 4]:
        multis = opt.generate_optimal_multis(v_legs, leg_count=legs_num, min_total_odds=5.0)
        if multis:
            print(f"\n=== Top {legs_num}-Leg High-Odds Multis ===")
            for idx, m in enumerate(multis[:3], start=1):
                print(f"Multi Option #{idx} | Total Odds: ${m['total_odds']} | Prob: {m['model_prob_pct']}% | Multi EV: +{m['expected_value']*100:.1f}%")
                for leg in m["legs"]:
                    print(f"   -> {leg['track']} R{leg['race_number']}: Box {leg['box']} {leg['dog_name']} @ ${leg['odds']}")


def cmd_collect_sportsbet():
    print("=== Synchronizing Live Sportsbet Greyhound Odds & Scratchings ===")
    from sportsbet_scraper import SportsbetScraper
    scraper = SportsbetScraper()
    res = scraper.sync_sportsbet_odds()
    print("Sportsbet Sync Summary:")
    print(f"  * Events scanned:    {res.get('events_scanned', 0)}")
    print(f"  * Races matched:     {res.get('races_matched', 0)}")
    print(f"  * Runners updated:   {res.get('runners_updated', 0)}")
    print(f"  * Scratchings noted: {res.get('scratched_updated', 0)}")


def cmd_daemon(interval: int = 60):
    print(f"=== Starting Greyhound Live Poller Daemon (Cycle: every {interval}s) ===")
    print("Press Ctrl+C to stop.")
    from poller_daemon import daemon_instance
    import time
    daemon_instance.set_interval(interval)
    daemon_instance.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Poller Daemon...")
        daemon_instance.stop()
        print("Daemon stopped gracefully.")


def cmd_backtest(min_edge: float = 0.03, min_odds: float = 1.5, max_odds: float = 20.0, staking: str = "flat", stake: float = 10.0, odds_source: str = "best"):
    print(f"=== Running Historical Strategy Backtest (Min Edge: +{min_edge*100:.1f}%, Staking: {staking.title()}, Odds: {odds_source.title()}) ===")
    from backtest import HistoricalBacktester
    tester = HistoricalBacktester()
    res = tester.run_backtest(
        min_edge=min_edge,
        min_odds=min_odds,
        max_odds=max_odds,
        staking_method=staking,
        flat_stake=stake,
        odds_source=odds_source
    )
    if res.get("status") == "no_data":
        print(res.get("message"))
        return

    print("\n-----------------------------------------------------------")
    print(f"Races Analyzed:       {res.get('total_races_analyzed')}")
    print(f"Total Bets Placed:    {res.get('total_bets')}")
    print(f"Winning Bets:         {res.get('winning_bets')}  |  Losing Bets: {res.get('losing_bets')}")
    print(f"Strike Rate:          {res.get('strike_rate_pct')}%")
    print(f"Total Wagered:        ${res.get('total_staked'):,.2f}")
    print(f"Total Returned:       ${res.get('total_returned'):,.2f}")
    print(f"Net Profit / Loss:    ${res.get('net_profit'):+,.2f}")
    print(f"Return on Inv (ROI):  {res.get('roi_pct'):+,.2f}%")
    print(f"Max Drawdown:         {res.get('max_drawdown_pct'):.2f}% (${res.get('max_drawdown_dollars'):,.2f})")
    print(f"Brier Calibration:    {res.get('brier_score')} (Lower = Better Calibrated)")
    print("-----------------------------------------------------------")

    recent = res.get("recent_bets", [])
    if recent:
        print("\nRecent Bet Sample:")
        for b in recent[:8]:
            res_tag = "WIN" if b["won"] else "LOSS"
            print(f"  [{res_tag:<4}] {b['date']} {b['track']} R{b['race_number']} Box {b['box']} {b['dog_name']} @ ${b['odds']} | Stake: ${b['stake']} -> PnL: ${b['profit']:+,.2f}")


def cmd_exotics(race_id: int, stake: float = 12.0):
    from exotic_bet_optimizer import ExoticBetOptimizer
    optimizer = ExoticBetOptimizer()
    try:
        res = optimizer.generate_race_exotics(race_id, custom_stake=stake)
    except Exception as e:
        print(f"Error generating exotics: {e}")
        return

    print(f"\n===========================================================")
    print(f"  EXOTIC BET OPTIMIZER - {res['track_name']} R{res['race_number']} ({res['distance']}m)")
    print(f"===========================================================")

    print("\n[RANKED CONTENDERS]")
    for r in res["ranked_runners"]:
        print(f"  Rank {r['rank']}: Box {r['box']} {r['name']:<20} | Prob: {r['win_prob_pct']:>5.1f}% | Trainer: {r['trainer']}")

    if res["banker_detected"]:
        b = res["banker"]
        print(f"\n>>> DOMINANT BANKER DETECTED <<<")
        print(f"  Box {b['box']} ({b['name']}) holding {b['win_prob_pct']}% win probability (+{b['margin_gap_pct']}% margin gap)")

    print("\n-----------------------------------------------------------")
    print("  RECOMMENDED FIRST 4 TICKETS")
    print("-----------------------------------------------------------")
    for t in res["first_4_tickets"]:
        print(f"  * {t['summary']}")
        print(f"    Win Prob: {t['win_probability_pct']}% | Fair Dividend: ${t['fair_dividend']:,.2f} | Payout on Stake: ${t['projected_payout']:,.2f}")

    print("\n-----------------------------------------------------------")
    print("  RECOMMENDED TRIFECTA TICKETS")
    print("-----------------------------------------------------------")
    for t in res["trifecta_tickets"]:
        print(f"  * {t['summary']}")
        print(f"    Win Prob: {t['win_probability_pct']}% | Fair Dividend: ${t['fair_dividend']:,.2f} | Payout on Stake: ${t['projected_payout']:,.2f}")

    print("\n-----------------------------------------------------------")
    print("  HIGH-CONFIDENCE DUTCHING COMBINATIONS ($30 STAKE)")
    print("-----------------------------------------------------------")
    for d in res["dutching_recommendations"]:
        print(f"  * {d['summary']}")
        print(f"    Coverage: {d['coverage_probability_pct']}% | Target Return: ${d['target_return']:,.2f} | Net Profit: +${d['projected_profit']:,.2f} (ROI: +{d['roi_percent']}%)")
        for c in d["combinations"][:4]:
            combo_str = "-".join(str(x) for x in c["combination"])
            print(f"      Combo {combo_str:<12} | Fair Odds: ${c['fair_odds']:>7.2f} | Stake: ${c['recommended_stake']:>5.2f} -> Ret: ${c['projected_return']:>6.2f}")


def cmd_srm(race_id: int, n_sims: int = 10000):
    import time
    from monte_carlo_srm import MonteCarloSRMSimulator
    sim = MonteCarloSRMSimulator(n_iterations=n_sims)
    try:
        t0 = time.time()
        sim_data = sim.simulate_race(race_id)
        dt = (time.time() - t0) * 1000.0
        srms = sim.scan_optimal_srms(race_id)
    except Exception as e:
        print(f"Error executing Monte Carlo SRM: {e}")
        return

    print(f"\n===========================================================")
    print(f"  MONTE CARLO SRM SIMULATOR ({n_sims:,} Trials in {dt:.1f}ms)")
    print(f"  {sim_data['track_name']} Race {race_id} ({sim_data['distance']}m)")
    print(f"===========================================================")

    print("\n[SIMULATED RUNNER PODIUM FREQUENCIES]")
    for r in sim_data["runners"]:
        print(f"  Box {r['box']}: {r['dog_name']:<20} | Win: {r['mc_win_prob']*100:>5.1f}% | Top 2: {r['mc_top2_prob']*100:>5.1f}% | Top 3: {r['mc_top3_prob']*100:>5.1f}% | Top 4: {r['mc_top4_prob']*100:>5.1f}%")

    print("\n-----------------------------------------------------------")
    print("  OPTIMAL +EV SAME RACE MULTIS (CORRELATED SYNERGY)")
    print("-----------------------------------------------------------")
    if not srms:
        print("  No +EV combinations met threshold.")
    else:
        for s in srms[:8]:
            legs_str = " + ".join([f"B{leg['box']} ({leg['condition']})" for leg in s["legs"]])
            print(f"  * [{s['type']}] {legs_str}")
            print(f"    MC Prob: {s['mc_joint_probability_pct']:>5.2f}% | Market Odds: ${s['offered_srm_odds']:>5.2f} | Corr: {s['correlation_factor']}x ({s['correlation_status']}) | EV: +{s['expected_value']*100:.1f}%")


def cmd_results_daemon(interval: int = 90):
    from results_daemon import results_daemon_instance
    import time
    print(f"=== Starting Autonomous Results Daemon (Interval: {interval}s) ===")
    results_daemon_instance.check_interval = interval
    results_daemon_instance.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Results Daemon...")
        results_daemon_instance.stop()
        print("Daemon stopped gracefully.")


def cmd_reconcile_now(target_date: str = None):
    from results_daemon import results_daemon_instance
    print(f"=== Triggering On-Demand Results Scrape & Reconciliation ===")
    scraped_reconciled = results_daemon_instance.scrape_and_reconcile_pending(target_date=target_date)
    print(f"Newly Scraped & Reconciled Races: {len(scraped_reconciled)}")
    
    # Also reconcile completed races in DB
    backfill_count = results_daemon_instance.reconcile_completed_races(target_date=target_date)
    print(f"Races Audited / Already Reconciled: {backfill_count}")

    pnl = results_daemon_instance.get_pnl_summary(target_date=target_date)
    print("\n-----------------------------------------------------------")
    print("  LIVE RECONCILIATION & P&L TELEMETRY")
    print("-----------------------------------------------------------")
    print(f"  Total Races Reconciled: {pnl['total_races']}")
    print(f"  Win Strike Rate:        {pnl['win_strike_rate_pct']}% ({pnl.get('win_hits', 0)}/{pnl['total_races']})")
    print(f"  Place Strike Rate:      {pnl['place_strike_rate_pct']}% ({pnl.get('place_hits', 0)}/{pnl['total_races']})")
    print(f"  Top 2 Strike:           {pnl.get('top2_hits', 0)} races")
    print(f"  SRM 3-of-4 Coverage:    {pnl['srm_podium_3of4_pct']}% ({pnl.get('srm_podium_3of4_hits', 0)}/{pnl['total_races']})")
    print(f"  SRM 4-of-4 Coverage:    {pnl.get('srm_podium_4of4_hits', 0)} races")
    print(f"  Exact First 4 Strike:   {pnl.get('exact_first4_hits', 0)} races")
    print(f"  Flat Turnover Wagered:  ${pnl['total_staked']:,.2f}")
    print(f"  Total Return:           ${pnl['total_returned']:,.2f}")
    print(f"  Net Profit/Loss:        ${pnl['net_profit']:,.2f}")
    print(f"  Return on Investment:   +{pnl['roi_percent']}%" if pnl['roi_percent'] >= 0 else f"  Return on Investment:   {pnl['roi_percent']}%")
    print("-----------------------------------------------------------\n")


def cmd_hydrate_upcoming(date: Optional[str] = None, max_dogs: int = 40):
    print("=== Background Pre-Race Dog Dossier Hydration ===")
    worker = DogHydrationWorker()
    res = worker.run_batch_hydration(target_date=date, max_dogs=max_dogs, verbose=True)
    stats = worker.get_stats()
    print(f"\nHydration Progress: {stats['active_upcoming_hydrated']}/{stats['active_upcoming_runners']} active dogs ({stats['upcoming_hydration_percentage']}%)")


def cmd_hydrate_status():
    print("=== Dog Dossier Hydration Telemetry ===")
    worker = DogHydrationWorker()
    stats = worker.get_stats()
    for k, v in stats.items():
        print(f"  {k:<35}: {v}")


def cmd_brier_audit(date: Optional[str] = None, track: Optional[str] = None):
    engine = DailyLearningEngine()
    print("=== Probabilistic Calibration Audit (Brier Score & Log-Loss) ===")
    res = engine.compute_brier_and_log_loss(target_date=date, track_filter=track)
    if res.get("status") == "no_data":
        print("No completed race data found for target filter.")
        return
    print(f"Sample Races:               {res.get('sample_races', 0)}")
    print(f"Multi-class Brier Score:    {res.get('overall_brier_score')}")
    print(f"Cross-Entropy Log-Loss:     {res.get('overall_log_loss')}")
    print(f"Expected Calibration Error: {res.get('expected_calibration_error')}")
    print("\nReliability Curve Deciles:")
    for b in res.get("reliability_diagram", []):
        print(f"  {b['range']:<10}: N={b['count']:<4} | Confidence: {b['avg_confidence']}% | Empirical Acc: {b['empirical_accuracy']}% (Gap: {b['gap']:+0.1f}%)")


def cmd_auto_tune(min_races: int = 4):
    engine = DailyLearningEngine()
    print("=== Auto-Tuning Track Softmax Temperatures ===")
    res = engine.auto_tune_track_temperatures(min_races=min_races, verbose=True)
    print(f"Successfully tuned {res.get('tracks_calibrated', 0)} tracks.")


def cmd_walk_forward(min_history_days: int = 2, max_test_days: Optional[int] = None):
    evaluator = WalkForwardEvaluator()
    evaluator.run_walk_forward(min_history_days=min_history_days, max_test_days=max_test_days, verbose=True)


def cmd_import_history(path: str, no_calibrate: bool = False):
    from history_importer import HistoryImporter
    from pathlib import Path
    importer = HistoryImporter()
    p = Path(path)
    if p.is_dir():
        res = importer.import_directory(str(p), auto_calibrate=not no_calibrate)
    else:
        res = importer.import_file(str(p), auto_calibrate=not no_calibrate)

    print("\n" + "=" * 60)
    print("HISTORICAL RACE DATA IMPORT SUMMARY")
    print("=" * 60)
    print(f"Status:                      {res.get('status')}")
    print(f"Total Runner Rows Ingested:  {res.get('runners_imported', 0)}")
    print(f"Meetings Created/Updated:    {res.get('meetings_count', 0)}")
    print(f"Races Created/Updated:       {res.get('races_count', 0)}")
    print(f"Past Performances Logged:    {res.get('past_performances_created', 0)}")
    print(f"Date Range Covered:          {res.get('date_range', 'None')}")
    print(f"Tracks Imported ({res.get('tracks_count', 0)}):     {', '.join(res.get('tracks', []))}")
    if res.get("calibration"):
        cal = res["calibration"]
        print(f"Tracks Recalibrated:         {cal.get('tracks_calibrated', 0)}")
        print(f"Empirical Harville Gammas:   {cal.get('empirical_discount_gammas')}")
    print("=" * 60 + "\n")


def cmd_generate_template(csv_path: str = "sample_race_history.csv", json_path: str = "sample_race_history.json"):
    from history_importer import HistoryImporter
    c, j = HistoryImporter.generate_template(csv_path, json_path)
    print(f"[+] Successfully generated sample import templates:")
    print(f"    - CSV:  {c}")
    print(f"    - JSON: {j}")


def cmd_backfill_tips(limit: int = 50):
    print(f"=== Backfilling TheDogs Expert Tips (Limit: {limit}) ===")
    from backfill_tips import run_backfill
    run_backfill(limit=limit)


def main():
    parser = argparse.ArgumentParser(description="Greyhound Precision Multi-Betting CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Initialize database")
    subparsers.add_parser("collect-fasttrack", help="Scrape FastTrack meetings and sectionals")
    subparsers.add_parser("collect-thedogs", help="Scrape TheDogs race cards and odds")
    subparsers.add_parser("collect-sportsbet", help="Synchronize live Sportsbet odds and scratchings")
    subparsers.add_parser("collect-weather", help="Record weather observations")
    subparsers.add_parser("list-upcoming", help="List all upcoming races in the database")

    daemon_parser = subparsers.add_parser("daemon", help="Run automated live background poller")
    daemon_parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")

    results_daemon_parser = subparsers.add_parser("results-daemon", help="Run autonomous live post-race results & reconciliation daemon")
    results_daemon_parser.add_argument("--interval", type=int, default=90, help="Check interval in seconds")

    reconcile_parser = subparsers.add_parser("reconcile-now", help="Immediately scrape finished races, audit predictions and print P&L")
    reconcile_parser.add_argument("--date", type=str, default=None, help="Target date (YYYY-MM-DD), defaults to today")

    analyze_parser = subparsers.add_parser("analyze-race", help="Analyze race runners & probabilities")
    analyze_parser.add_argument("race_id", type=int, help="Race ID to analyze")

    exotics_parser = subparsers.add_parser("exotics", help="Build optimized First 4, Trifecta & Dutching tickets")
    exotics_parser.add_argument("race_id", type=int, help="Race ID to build exotics for")
    exotics_parser.add_argument("--stake", type=float, default=12.0, help="Base stake per ticket")

    srm_parser = subparsers.add_parser("srm", help="Simulate 10,000-trial Monte Carlo Same Race Multis")
    srm_parser.add_argument("race_id", type=int, help="Race ID to simulate")
    srm_parser.add_argument("--sims", type=int, default=10000, help="Number of Monte Carlo iterations")

    scan_parser = subparsers.add_parser("scan-multis", help="Scan and construct high-odds +EV multi bets")
    scan_parser.add_argument("--odds-source", default="sportsbet", choices=["sportsbet", "best", "sp"], help="Odds platform to use")

    bt_parser = subparsers.add_parser("backtest", help="Run historical backtesting simulation")
    bt_parser.add_argument("--min-edge", type=float, default=0.03, help="Minimum positive edge (e.g. 0.03 for +3 percent)")
    bt_parser.add_argument("--min-odds", type=float, default=1.50, help="Minimum decimal odds")
    bt_parser.add_argument("--max-odds", type=float, default=20.0, help="Maximum decimal odds")
    bt_parser.add_argument("--staking", default="flat", choices=["flat", "kelly"], help="Staking methodology")
    bt_parser.add_argument("--stake", type=float, default=10.0, help="Flat stake amount")
    bt_parser.add_argument("--odds-source", default="best", choices=["best", "sportsbet", "sp"], help="Odds feed to evaluate")

    hydrate_parser = subparsers.add_parser("hydrate-upcoming", help="Pre-race ingestion of full dog dossiers and PBs")
    hydrate_parser.add_argument("--date", type=str, default=None, help="Target date (default: today)")
    hydrate_parser.add_argument("--max", type=int, default=40, help="Maximum runners to hydrate")

    subparsers.add_parser("hydrate-status", help="Display dog dossier hydration statistics")

    brier_parser = subparsers.add_parser("brier-audit", help="Evaluate Multi-class Brier score, log-loss & calibration")
    brier_parser.add_argument("--date", type=str, default=None, help="Target date (default: all)")
    brier_parser.add_argument("--track", type=str, default=None, help="Filter by specific track")

    autotune_parser = subparsers.add_parser("auto-tune", help="Auto-tune track temperature parameters via continuous optimization")
    autotune_parser.add_argument("--min-races", type=int, default=4, help="Minimum sample races to qualify a track")

    backfill_tips_parser = subparsers.add_parser("backfill-tips", help="Backfill TheDogs expert tips for races in the database")
    backfill_tips_parser.add_argument("--limit", type=int, default=50, help="Maximum number of races to backfill")

    wf_parser = subparsers.add_parser("walk-forward", help="Walk-forward out-of-sample evaluation vs de-vigged market benchmark")
    wf_parser.add_argument("--min-history", type=int, default=2, help="Minimum days of historical data before starting evaluation")
    wf_parser.add_argument("--max-days", type=int, default=None, help="Maximum number of test days to evaluate")

    import_parser = subparsers.add_parser("import-history", help="Import months of historical race results from CSV or JSON file/directory")
    import_parser.add_argument("path", type=str, help="Path to .csv, .json file or directory of data files")
    import_parser.add_argument("--no-calibrate", action="store_true", help="Skip automatic post-import track recalibration")

    template_parser = subparsers.add_parser("generate-template", help="Generate sample CSV and JSON historical data templates")
    template_parser.add_argument("--csv", type=str, default="sample_race_history.csv", help="Output path for sample CSV")
    template_parser.add_argument("--json", type=str, default="sample_race_history.json", help="Output path for sample JSON")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "collect-fasttrack":
        cmd_collect_fasttrack()
    elif args.command == "collect-thedogs":
        cmd_collect_thedogs()
    elif args.command == "collect-sportsbet":
        cmd_collect_sportsbet()
    elif args.command == "collect-weather":
        cmd_collect_weather()
    elif args.command == "list-upcoming":
        cmd_list_upcoming()
    elif args.command == "daemon":
        cmd_daemon(args.interval)
    elif args.command == "results-daemon":
        cmd_results_daemon(args.interval)
    elif args.command == "reconcile-now":
        cmd_reconcile_now(args.date)
    elif args.command == "analyze-race":
        cmd_analyze_race(args.race_id)
    elif args.command == "exotics":
        cmd_exotics(args.race_id, args.stake)
    elif args.command == "srm":
        cmd_srm(args.race_id, args.sims)
    elif args.command == "scan-multis":
        cmd_scan_multis()
    elif args.command == "backtest":
        cmd_backtest(args.min_edge, args.min_odds, args.max_odds, args.staking, args.stake, args.odds_source)
    elif args.command == "hydrate-upcoming":
        cmd_hydrate_upcoming(args.date, args.max)
    elif args.command == "hydrate-status":
        cmd_hydrate_status()
    elif args.command == "brier-audit":
        cmd_brier_audit(args.date, args.track)
    elif args.command == "auto-tune":
        cmd_auto_tune(args.min_races)
    elif args.command == "backfill-tips":
        cmd_backfill_tips(args.limit)
    elif args.command == "walk-forward":
        cmd_walk_forward(args.min_history, args.max_days)
    elif args.command == "import-history":
        cmd_import_history(args.path, args.no_calibrate)
    elif args.command == "generate-template":
        cmd_generate_template(args.csv, args.json)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
