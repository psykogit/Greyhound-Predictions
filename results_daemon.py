"""
Live Auto-Reconciliation Results Daemon (Phase 3.1)
Author: DeepMind / Greyhound Precision Betting Engine

Features:
1. Autonomous background daemon monitoring completed/jumped races (T > 8-10m post-jump).
2. Automatically downloads official finish positions, times, margins, and SP odds.
3. Updates race_runners and past_performances in real-time.
4. Auto-reconciles against model predictions in historical_predictions & reconciled_results.
5. Computes live, persistent P&L, win/place strike rates, and ROI metrics.
"""

import threading
import time
import datetime
from typing import Dict, Any, List, Optional
import sqlite3

from db import get_connection
from thedogs_scraper import TheDogsScraper
from analytics import GreyhoundAnalytics

AEDT_TZ = datetime.timezone(datetime.timedelta(hours=11))


class ResultsDaemon:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ResultsDaemon, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, check_interval: int = 90):
        if getattr(self, "_initialized", False):
            return
        self.check_interval = check_interval  # seconds between cycles
        self.enabled = True
        self.is_reconciling = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        self.last_run_time: Optional[str] = None
        self.next_run_time: Optional[str] = None
        self.last_status_message: str = "Initialized (Idle)"
        self.reconciled_today_count: int = 0
        self.last_reconciled_races: List[Dict[str, Any]] = []

        self.scraper = TheDogsScraper()
        self._init_tables()
        self._initialized = True

    def _init_tables(self):
        """Initializes database tables for historical predictions and reconciled results."""
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS historical_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id INTEGER NOT NULL UNIQUE,
            predicted_top4 TEXT NOT NULL,
            actual_top4 TEXT,
            snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(race_id) REFERENCES races(id)
        );
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS reconciled_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id INTEGER NOT NULL UNIQUE,
            track_name TEXT,
            meeting_date TEXT,
            race_number INTEGER,
            distance INTEGER,
            predicted_top4 TEXT,
            actual_top4 TEXT,
            winner_box INTEGER,
            winner_name TEXT,
            winner_odds REAL,
            predicted_winner_box INTEGER,
            win_hit INTEGER,
            place_hit INTEGER,
            top2_hit INTEGER,
            top4_count INTEGER,
            exact_top4_hit INTEGER,
            flat_stake REAL,
            flat_return REAL,
            net_profit REAL,
            reconciled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(race_id) REFERENCES races(id)
        );
        """)
        conn.commit()
        conn.close()

    def get_status(self) -> Dict[str, Any]:
        """Returns current daemon state and telemetry."""
        return {
            "enabled": self.enabled,
            "is_reconciling": self.is_reconciling,
            "check_interval_seconds": self.check_interval,
            "last_run_time": self.last_run_time,
            "next_run_time": self.next_run_time,
            "last_status_message": self.last_status_message,
            "reconciled_today_count": self.reconciled_today_count,
            "recent_reconciliations": self.last_reconciled_races[-5:]
        }

    def start(self):
        """Starts the background worker thread."""
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(target=self._run_loop, name="ResultsDaemonThread", daemon=True)
            self._worker_thread.start()
            self.last_status_message = "Results background daemon started"

    def stop(self):
        """Stops the background worker thread."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        self.last_status_message = "Results daemon stopped"

    def reconcile_now(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Forces an immediate synchronization and reconciliation pass."""
        return self._execute_cycle(reason="manual_trigger", target_date=target_date)

    def _run_loop(self):
        """Main background monitoring loop."""
        while not self._stop_event.is_set():
            if self.enabled:
                try:
                    self._execute_cycle(reason="scheduled")
                except Exception as e:
                    self.last_status_message = f"Error in reconciliation cycle: {e}"

            # Calculate next run timestamp
            next_ts = datetime.datetime.now(AEDT_TZ) + datetime.timedelta(seconds=self.check_interval)
            self.next_run_time = next_ts.strftime("%Y-%m-%d %H:%M:%S AEDT")

            # Sleep in short increments for responsive shutdown
            slept = 0
            while slept < self.check_interval and not self._stop_event.is_set():
                time.sleep(1.0)
                slept += 1

    def _execute_cycle(self, reason: str = "scheduled", target_date: Optional[str] = None) -> Dict[str, Any]:
        """Executes a complete results download, database update, and model reconciliation pass."""
        if self.is_reconciling:
            return {"status": "busy", "message": "Reconciliation cycle already in progress"}

        self.is_reconciling = True
        start_ts = datetime.datetime.now(AEDT_TZ)
        today_iso = target_date or start_ts.strftime("%Y-%m-%d")
        self.last_run_time = start_ts.strftime("%Y-%m-%d %H:%M:%S AEDT")
        self.last_status_message = f"Scanning pending races for {today_iso} ({reason})..."

        try:
            reconciled = self.scrape_and_reconcile_pending(today_iso)
            self.reconciled_today_count += len(reconciled)
            self.last_reconciled_races.extend(reconciled)
            msg = f"Reconciled {len(reconciled)} races on {today_iso} ({reason})"
            self.last_status_message = msg
            return {
                "status": "success",
                "message": msg,
                "reconciled_count": len(reconciled),
                "reconciled_races": reconciled
            }
        except Exception as e:
            err_msg = f"Failed reconciliation pass: {e}"
            self.last_status_message = err_msg
            return {"status": "error", "message": err_msg}
        finally:
            self.is_reconciling = False

    def scrape_and_reconcile_pending(self, target_date: str) -> List[Dict[str, Any]]:
        """
        1. Identifies races on target_date that are > 8 mins past start_time and missing official finish positions.
        2. Scrapes official race finish positions from TheDogs.
        3. Updates race_runners in database.
        4. Reconciles against predicted Top 4 and writes to reconciled_results.
        """
        conn = get_connection()
        c = conn.cursor()

        # Find races scheduled on or before target_date that lack official finish_position = 1
        c.execute("""
            SELECT r.id, r.race_url, r.start_time, r.race_number, r.distance,
                   m.track_name, m.meeting_date
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            WHERE m.meeting_date <= ?
              AND r.race_url IS NOT NULL
              AND r.id NOT IN (
                  SELECT race_id FROM race_runners WHERE finish_position = 1
              )
            ORDER BY m.meeting_date ASC, r.id ASC
        """, (target_date,))
        pending = c.fetchall()

        now_aedt = datetime.datetime.now(AEDT_TZ)
        eligible_races = []

        for row in pending:
            r_id = row["id"]
            url = row["race_url"]
            st_str = row["start_time"]
            r_num = row["race_number"]
            m_date = row["meeting_date"]

            # Check time delay: must be >= 8 mins past scheduled jump time if meeting is today
            if m_date == target_date and st_str and ":" in str(st_str):
                try:
                    h, m = map(int, str(st_str).split(":"))
                    d_parts = list(map(int, str(m_date).split("-")))
                    race_dt = datetime.datetime(d_parts[0], d_parts[1], d_parts[2], h, m, tzinfo=AEDT_TZ)
                    if h < 8 and (r_num or 1) >= 4:
                        race_dt += datetime.timedelta(days=1)
                    diff_mins = (now_aedt - race_dt).total_seconds() / 60.0
                    if diff_mins < 8.0:
                        continue  # Too early, race may still be running
                except Exception:
                    pass

            eligible_races.append(row)

        analytics = GreyhoundAnalytics(conn)
        reconciled_records = []

        for row in eligible_races:
            race_id = row["id"]
            url = row["race_url"]
            track = row["track_name"]
            race_num = row["race_number"]
            dist = row["distance"]
            m_date = row["meeting_date"]

            try:
                # 1. Scrape official race results
                data = self.scraper.parse_race(url)
                if not data or not data.get("runners"):
                    continue

                has_results = any(
                    (r.get("finish_position") is not None or r.get("finish_pos") is not None)
                    for r in data["runners"]
                )
                if not has_results:
                    continue

                # 2. Update race_runners
                for r_data in data["runners"]:
                    box = r_data.get("box_number") if r_data.get("box_number") is not None else r_data.get("box")
                    pos = r_data.get("finish_position") if r_data.get("finish_position") is not None else r_data.get("finish_pos")
                    sp = r_data.get("sp_odds")
                    time_sec = r_data.get("race_time")
                    margin = r_data.get("margin")
                    if box and pos:
                        c.execute("""
                            UPDATE race_runners
                            SET finish_position = ?,
                                sp_odds = COALESCE(?, sp_odds),
                                race_time = COALESCE(?, race_time),
                                margin = COALESCE(?, margin)
                            WHERE race_id = ? AND box_number = ?
                        """, (pos, sp, time_sec, margin, race_id, box))
                conn.commit()

                conn.commit()

                rec_entry = self._reconcile_single_race(
                    conn, race_id, track, m_date, race_num, dist, analytics
                )
                if rec_entry:
                    reconciled_records.append(rec_entry)
            except Exception as e:
                pass

        conn.close()
        return reconciled_records

    def _reconcile_single_race(
        self, conn: sqlite3.Connection, race_id: int, track: str,
        m_date: str, race_num: int, dist: int, analytics: GreyhoundAnalytics
    ) -> Optional[Dict[str, Any]]:
        """Reconciles a single race: extracts actuals, checks against predicted Top 4, and records P&L."""
        c = conn.cursor()
        c.execute("""
            SELECT rr.box_number, rr.finish_position, rr.sp_odds, rr.sportsbet_odds,
                   COALESCE(g.dog_name, 'Dog ' || rr.box_number) as dog_name
            FROM race_runners rr
            LEFT JOIN greyhounds g ON rr.dog_id = g.id
            WHERE rr.race_id = ? AND rr.finish_position IS NOT NULL
            ORDER BY CAST(rr.finish_position AS INTEGER) ASC
        """, (race_id,))
        finishers = c.fetchall()
        if not finishers or finishers[0]["finish_position"] != 1:
            return None

        actual_top4_boxes = [f["box_number"] for f in finishers[:4] if f["box_number"] is not None]
        actual_top4_str = ",".join(str(b) for b in actual_top4_boxes)
        actual_places = set(actual_top4_boxes[:3])

        winner_box = finishers[0]["box_number"]
        winner_name = finishers[0]["dog_name"]
        winner_odds = finishers[0]["sportsbet_odds"] or finishers[0]["sp_odds"] or 2.50

        # Fetch or generate predicted Top 4 snapshot
        c.execute("SELECT predicted_top4 FROM historical_predictions WHERE race_id = ?", (race_id,))
        existing_snap = c.fetchone()
        if existing_snap and existing_snap["predicted_top4"]:
            pred_str = existing_snap["predicted_top4"]
        else:
            preds = analytics.predict_race_probabilities(race_id)
            if preds:
                pred_str = ",".join(str(p["box"]) for p in preds[:4])
                c.execute("""
                    INSERT OR REPLACE INTO historical_predictions (race_id, predicted_top4, actual_top4)
                    VALUES (?, ?, ?)
                """, (race_id, pred_str, actual_top4_str))
                conn.commit()
            else:
                pred_str = ""

        if not pred_str:
            return None

        c.execute("UPDATE historical_predictions SET actual_top4 = ? WHERE race_id = ?", (actual_top4_str, race_id))
        conn.commit()

        pred_boxes = [int(b) for b in pred_str.split(",") if b.strip().isdigit()]
        pick1_box = pred_boxes[0] if pred_boxes else None

        win_hit = 1 if pick1_box == winner_box else 0
        place_hit = 1 if pick1_box in actual_places else 0
        top2_hit = 1 if winner_box in pred_boxes[:2] else 0

        top4_count = sum(1 for b in pred_boxes[:4] if b in actual_top4_boxes)
        exact_top4_hit = 1 if pred_boxes[:4] == actual_top4_boxes else 0

        flat_stake = 10.0
        flat_return = (flat_stake * winner_odds) if win_hit else 0.0
        net_profit = flat_return - flat_stake

        c.execute("""
            INSERT OR REPLACE INTO reconciled_results (
                race_id, track_name, meeting_date, race_number, distance,
                predicted_top4, actual_top4, winner_box, winner_name, winner_odds,
                predicted_winner_box, win_hit, place_hit, top2_hit, top4_count,
                exact_top4_hit, flat_stake, flat_return, net_profit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            race_id, track, m_date, race_num, dist,
            pred_str, actual_top4_str, winner_box, winner_name, winner_odds,
            pick1_box, win_hit, place_hit, top2_hit, top4_count,
            exact_top4_hit, flat_stake, flat_return, net_profit
        ))
        conn.commit()

        return {
            "race_id": race_id,
            "track_name": track,
            "race_number": race_num,
            "predicted": pred_str,
            "actual": actual_top4_str,
            "winner": f"Box {winner_box} ({winner_name}) @ ${winner_odds:.2f}",
            "win_hit": bool(win_hit),
            "place_hit": bool(place_hit),
            "top4_count": top4_count,
            "net_profit": net_profit
        }

    def reconcile_completed_races(self, target_date: Optional[str] = None, limit: int = 150) -> List[Dict[str, Any]]:
        """
        Reconciles completed races that already have official finish_position = 1 in race_runners,
        ensuring historical_predictions and reconciled_results tables are completely synchronized.
        """
        conn = get_connection()
        c = conn.cursor()
        query = """
            SELECT DISTINCT r.id, r.race_number, r.distance, m.track_name, m.meeting_date
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            JOIN race_runners rr ON rr.race_id = r.id
            WHERE rr.finish_position = 1
              AND r.id NOT IN (SELECT race_id FROM reconciled_results)
        """
        params = []
        if target_date:
            query += " AND m.meeting_date = ?"
            params.append(target_date)
        query += " ORDER BY m.meeting_date DESC, r.id DESC LIMIT ?"
        params.append(limit)

        c.execute(query, params)
        completed_races = c.fetchall()

        analytics = GreyhoundAnalytics(conn)
        reconciled = []
        for row in completed_races:
            rec = self._reconcile_single_race(
                conn, row["id"], row["track_name"], row["meeting_date"],
                row["race_number"], row["distance"], analytics
            )
            if rec:
                reconciled.append(rec)

        conn.close()
        return reconciled

    def get_pnl_summary(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Calculates real-time P&L, win/place strike rates, and ROI from reconciled_results."""
        conn = get_connection()
        c = conn.cursor()

        query = """
            SELECT count(*) as total_races,
                   sum(win_hit) as win_hits,
                   sum(place_hit) as place_hits,
                   sum(top2_hit) as top2_hits,
                   sum(case when top4_count >= 3 then 1 else 0 end) as srm_3of4_hits,
                   sum(case when top4_count = 4 then 1 else 0 end) as srm_4of4_hits,
                   sum(exact_top4_hit) as exact_first4_hits,
                   sum(flat_stake) as total_staked,
                   sum(flat_return) as total_returned,
                   sum(net_profit) as total_profit
            FROM reconciled_results
        """
        params = []
        if target_date:
            query += " WHERE meeting_date = ?"
            params.append(target_date)

        c.execute(query, params)
        row = c.fetchone()
        conn.close()

        total = row["total_races"] or 0
        if total == 0:
            return {
                "target_date": target_date or "All-Time",
                "total_races": 0,
                "win_strike_rate_pct": 0.0,
                "place_strike_rate_pct": 0.0,
                "srm_podium_3of4_pct": 0.0,
                "total_staked": 0.0,
                "total_returned": 0.0,
                "net_profit": 0.0,
                "roi_percent": 0.0
            }

        staked = row["total_staked"] or 0.0
        profit = row["total_profit"] or 0.0
        roi = round((profit / staked) * 100.0, 2) if staked > 0 else 0.0

        return {
            "target_date": target_date or "All-Time",
            "total_races": total,
            "win_hits": row["win_hits"] or 0,
            "win_strike_rate_pct": round(((row["win_hits"] or 0) / total) * 100.0, 1),
            "place_hits": row["place_hits"] or 0,
            "place_strike_rate_pct": round(((row["place_hits"] or 0) / total) * 100.0, 1),
            "top2_hits": row["top2_hits"] or 0,
            "srm_podium_3of4_hits": row["srm_3of4_hits"] or 0,
            "srm_podium_3of4_pct": round(((row["srm_3of4_hits"] or 0) / total) * 100.0, 1),
            "srm_podium_4of4_hits": row["srm_4of4_hits"] or 0,
            "exact_first4_hits": row["exact_first4_hits"] or 0,
            "total_staked": round(staked, 2),
            "total_returned": round(row["total_returned"] or 0.0, 2),
            "net_profit": round(profit, 2),
            "roi_percent": roi
        }


# Singleton daemon instance
results_daemon_instance = ResultsDaemon()


if __name__ == "__main__":
    daemon = ResultsDaemon()
    print("Testing Live Results Reconciliation...")
    res = daemon.reconcile_now()
    print(f"Status: {res['status']} | {res['message']}")
    pnl = daemon.get_pnl_summary()
    print("\n=== RECONCILED PERFORMANCE SUMMARY ===")
    print(f"Total Races:        {pnl['total_races']}")
    print(f"Win Strike Rate:    {pnl['win_strike_rate_pct']}% ({pnl.get('win_hits', 0)} wins)")
    print(f"Place Strike Rate:  {pnl['place_strike_rate_pct']}% ({pnl.get('place_hits', 0)} places)")
    print(f"Top 4 / SRM 3-of-4: {pnl['srm_podium_3of4_pct']}%")
    print(f"Total Staked:       ${pnl['total_staked']:,.2f}")
    print(f"Total Returned:     ${pnl['total_returned']:,.2f}")
    print(f"Net Profit / Loss:  ${pnl['net_profit']:+,.2f}")
    print(f"ROI:                {pnl['roi_percent']:+,.2f}%")
