"""
Automated Background Scheduler & Live Odds Poller Daemon.
Periodically fetches real-time Sportsbet and market odds, updates late scratchings,
and monitors races as they approach jump time (T-30m, T-15m, T-5m, T-2m).
"""

import threading
import time
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from sportsbet_scraper import SportsbetScraper
from scratchings_manager import ScratchingsManager
from db import get_connection

AEDT_TZ = timezone(timedelta(hours=11))


class PollerDaemon:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(PollerDaemon, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, poll_interval: int = 60):
        if getattr(self, "_initialized", False):
            return
        self.poll_interval = poll_interval  # seconds between cycles
        self.enabled = True
        self.auto_paused = False
        self.is_polling = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        self.last_poll_time: Optional[str] = None
        self.next_poll_time: Optional[str] = None
        self.last_status_message: str = "Initialized (Idle)"
        self.last_result: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self._initialized = True

    def get_status(self) -> Dict[str, Any]:
        """Return the current daemon telemetry and state."""
        return {
            "enabled": self.enabled,
            "is_polling": self.is_polling,
            "poll_interval_seconds": self.poll_interval,
            "last_poll_time": self.last_poll_time,
            "next_poll_time": self.next_poll_time,
            "last_status_message": self.last_status_message,
            "last_result": self.last_result,
            "history": self.history[-8:]
        }

    def set_enabled(self, enabled: bool) -> bool:
        """Enable or disable the daemon loop."""
        self.enabled = enabled
        self.last_status_message = "Enabled" if enabled else "Disabled (Paused)"
        return self.enabled

    def set_interval(self, seconds: int) -> int:
        """Set polling interval (min 15s)."""
        self.poll_interval = max(15, seconds)
        return self.poll_interval

    def start(self):
        """Start the background daemon thread if not already running."""
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(target=self._run_loop, name="PollerDaemonThread", daemon=True)
            self._worker_thread.start()
            self.last_status_message = "Poller background worker started"

    def stop(self):
        """Stop the background worker."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        self.last_status_message = "Stopped"

    def poll_now(self) -> Dict[str, Any]:
        """Execute an immediate synchronization cycle."""
        return self._execute_cycle(reason="manual_trigger")

    def _execute_cycle(self, reason: str = "scheduled") -> Dict[str, Any]:
        """Executes one polling cycle across Sportsbet and scratchings."""
        if self.is_polling:
            return {"status": "busy", "message": "Poll cycle already in progress"}

        self.is_polling = True

        start_ts = datetime.now(AEDT_TZ)
        
        # --- SMART AUTO-PAUSE LOGIC ---
        try:
            conn = get_connection()
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            today_str = start_ts.strftime("%Y-%m-%d")
            c.execute("SELECT r.start_time, m.meeting_date FROM races r JOIN meetings m ON r.meeting_id = m.id WHERE m.meeting_date >= ? ORDER BY m.meeting_date ASC, r.start_time ASC", (today_str,))
            rows = c.fetchall()
            conn.close()
            
            valid_races = []
            for r in rows:
                if not r["start_time"] or not r["meeting_date"]: continue
                try:
                    start_str = str(r["start_time"]).strip()
                    if ":" not in start_str: continue
                    h, m_val = map(int, start_str.split(":")[:2])
                    d_parts = list(map(int, str(r["meeting_date"]).split("-")))
                
                    dt_aedt = datetime(d_parts[0], d_parts[1], d_parts[2], h, m_val, tzinfo=AEDT_TZ)
                
                    # If race is midnight-8am, it belongs to the meeting program's following calendar day
                    if h < 8:
                        dt_aedt += timedelta(days=1)
                    
                    if dt_aedt > start_ts:
                        valid_races.append(dt_aedt)
                except: pass
            
            next_race_time = None
            if valid_races:
                valid_races.sort()
                next_race_time = valid_races[0]

            if next_race_time:
                delta = (next_race_time - start_ts).total_seconds() / 60.0
                if delta > 60:
                    self.auto_paused = True
                elif delta <= 30:
                    self.auto_paused = False
                    
                if self.auto_paused and reason != "manual_trigger":
                    self.is_polling = False
                    self.last_poll_time = start_ts.strftime("%Y-%m-%d %H:%M:%S AEDT")
                    self.last_status_message = f"Sleeping (Zzz). Next race in {int(delta)}m"
                    return {"status": "auto_paused", "message": "No imminent races"}
            else:
                self.auto_paused = True
                if reason != "manual_trigger":
                    self.is_polling = False
                    self.last_poll_time = start_ts.strftime("%Y-%m-%d %H:%M:%S AEDT")
                    self.last_status_message = "Sleeping (Zzz). No upcoming races found."
                    return {"status": "auto_paused", "message": "No races"}
        except Exception as e:
            print(f"[PollerDaemon] Auto-pause check error: {e}")
            self.auto_paused = False
        # ------------------------

        self.last_poll_time = start_ts.strftime("%Y-%m-%d %H:%M:%S AEDT")

        self.last_status_message = f"Polling Sportsbet odds & scratchings ({reason})..."

        summary: Dict[str, Any] = {
            "timestamp": self.last_poll_time,
            "reason": reason,
            "sportsbet": {},
            "scratchings_purged": 0,
            "duration_seconds": 0.0,
            "error": None
        }

        try:
            # 1. Synchronize live Sportsbet fixed win & place odds
            sb_scraper = SportsbetScraper()
            sb_res = sb_scraper.sync_sportsbet_odds()
            summary["sportsbet"] = sb_res

            # 2. Synchronize official scratchings
            try:
                sm = ScratchingsManager()
                purged = sm.sync_daily_scratchings()
                summary["scratchings_purged"] = purged
            except Exception as e_scratch:
                summary["scratchings_error"] = str(e_scratch)

            # 3. Auto-scrape missing results for races completed > 10 mins ago
            try:
                from daily_learning_engine import DailyLearningEngine
                le = DailyLearningEngine()
                
                # Check both today and yesterday to handle midnight rollover races gracefully
                now_aedt_local = datetime.now(AEDT_TZ)
                today_str = now_aedt_local.strftime("%Y-%m-%d")
                yesterday_str = (now_aedt_local - timedelta(days=1)).strftime("%Y-%m-%d")
                
                res_updated = le.scrape_missing_results(yesterday_str, enforce_delay=True)
                res_updated += le.scrape_missing_results(today_str, enforce_delay=True)
                
                summary["results_scraped"] = res_updated
            except Exception as e_res:
                summary["results_error"] = str(e_res)

            # 4. Auto-sync pre-race weather for imminent tracks (T-35m to T-0m)
            try:
                from weather_collector import WeatherCollector
                wc = WeatherCollector()
                weather_tracks = wc.sync_imminent_race_weather(within_minutes=35)
                summary["weather_tracks_updated"] = weather_tracks
            except Exception as e_w:
                summary["weather_error"] = str(e_w)

            end_ts = datetime.now(AEDT_TZ)
            duration = round((end_ts - start_ts).total_seconds(), 2)
            summary["duration_seconds"] = duration

            runners_up = sb_res.get("runners_updated", 0)
            res_up = summary.get("results_scraped", 0)
            w_count = len(summary.get("weather_tracks_updated", []))
            self.last_status_message = f"Completed in {duration}s: {runners_up} odds updated, {res_up} results fetched, {w_count} tracks weather refreshed"
            self.last_result = summary

        except Exception as e:
            self.last_status_message = f"Error during poll: {e}"
            summary["error"] = str(e)
            self.last_result = summary
        finally:
            self.is_polling = False
            self.history.append(summary)
            if len(self.history) > 50:
                self.history = self.history[-50:]

            next_dt = datetime.now(AEDT_TZ) + timedelta(seconds=self.poll_interval)
            self.next_poll_time = next_dt.strftime("%Y-%m-%d %H:%M:%S AEDT")

        return summary

    def _run_loop(self):
        """Internal background loop running at configured interval."""
        # Initial sleep of 5 seconds to let server start smoothly
        time.sleep(5)

        while not self._stop_event.is_set():
            if self.enabled:
                try:
                    self._execute_cycle(reason="scheduled_loop")
                except Exception as e:
                    print(f"[PollerDaemon] Unhandled error in cycle: {e}")

            # Wait for next interval or stop signal
            for _ in range(self.poll_interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)


# Global singleton instance
daemon_instance = PollerDaemon()
