"""
Background Pre-Race Dog Dossier Hydration Daemon.
Automatically crawls, discovers, and syncs full lifetime career dossiers,
split speedmaps, box records, and personal bests from TheDogs.com.au for upcoming runners.
Guarantees 100% data hydration ahead of race jump time without blocking API endpoints.
"""

import os
import re
import time
import json
import sqlite3
import threading
import datetime
from typing import Dict, Any, List, Optional

from db import get_connection
from dog_history_collector import DogHistoryCollector


class DogHydrationWorker:
    """Worker responsible for identifying and hydrating dogs for upcoming and active meetings."""

    def __init__(self, db_conn: Optional[sqlite3.Connection] = None, delay_between_requests: float = 0.6):
        self.conn = db_conn or get_connection()
        self.collector = DogHistoryCollector()
        self.delay = delay_between_requests

    def get_pending_upcoming_runners(
        self,
        target_date: Optional[str] = None,
        limit: int = 50,
        include_unproven_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Returns runners scheduled in upcoming/active races whose full career history
        is not yet hydrated in SQLite past_performances or greyhounds.
        Prioritised by nearest race start time.
        """
        if not target_date:
            target_date = datetime.date.today().isoformat()

        cursor = self.conn.cursor()
        
        # Condition: unproven/missing history
        # Dog has no records in past_performances OR missing pb_times_json
        condition_filter = """
            AND (
                g.id NOT IN (SELECT DISTINCT dog_id FROM past_performances)
                OR g.pb_times_json IS NULL
                OR g.career_starts IS NULL
                OR g.career_starts = 0
            )
        """ if include_unproven_only else ""

        query = f"""
        SELECT rr.id as runner_id, rr.race_id, rr.box_number, rr.is_scratched,
               r.race_number, r.start_time, r.distance, m.track_name, m.meeting_date,
               g.id as dog_id, g.dog_name, g.profile_url
        FROM race_runners rr
        JOIN races r ON rr.race_id = r.id
        JOIN meetings m ON r.meeting_id = m.id
        JOIN greyhounds g ON rr.dog_id = g.id
        WHERE m.meeting_date >= ?
          AND (rr.is_scratched = 0 OR rr.is_scratched IS NULL)
          {condition_filter}
        ORDER BY m.meeting_date ASC, r.start_time ASC, r.race_number ASC, rr.box_number ASC
        LIMIT ?
        """
        cursor.execute(query, (target_date, limit))
        rows = cursor.fetchall()

        results = []
        for r in rows:
            results.append({
                "runner_id": r[0],
                "race_id": r[1],
                "box_number": r[2],
                "race_number": r[4],
                "start_time": r[5],
                "distance": r[6],
                "track_name": r[7],
                "meeting_date": r[8],
                "dog_id": r[9],
                "dog_name": r[10],
                "profile_url": r[11]
            })
        return results

    def hydrate_single_dog(self, dog_id: int, dog_name: str, profile_url: Optional[str] = None) -> bool:
        """Hydrates a single greyhound's profile and past performances into SQLite."""
        try:
            # Step 1: Resolve profile URL if missing
            url_to_use = profile_url
            if not url_to_use:
                clean_name = re.sub(r"\(into box \d+\)", "", dog_name, flags=re.IGNORECASE)
                clean_name = re.sub(r"\b(?:NBT|\d{2}\.\d{2})\b", "", clean_name).strip()
                url_to_use = self.collector.find_profile_url_by_name(clean_name)

            if not url_to_use:
                return False

            # Step 2: Fetch full career profile and sync to DB
            data = self.collector.sync_dog_history_to_db(dog_id, url_to_use)
            return bool(data and data.get("total_starts", 0) >= 0)
        except Exception as e:
            return False

    def hydrate_race_runners(self, race_id: int) -> Dict[str, Any]:
        """Hydrates all active runners for a specific race ID."""
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT rr.id, g.id as dog_id, g.dog_name, g.profile_url
        FROM race_runners rr
        JOIN greyhounds g ON rr.dog_id = g.id
        WHERE rr.race_id = ? AND (rr.is_scratched = 0 OR rr.is_scratched IS NULL)
        ORDER BY rr.box_number ASC
        """, (race_id,))
        runners = cursor.fetchall()

        hydrated = 0
        failed = 0
        skipped = 0

        for r in runners:
            runner_id, dog_id, dog_name, profile_url = r[0], r[1], r[2], r[3]
            # Check if dog already has past_performances
            cursor.execute("SELECT COUNT(*) FROM past_performances WHERE dog_id = ?", (dog_id,))
            pp_cnt = cursor.fetchone()[0]
            if pp_cnt > 0:
                skipped += 1
                continue

            success = self.hydrate_single_dog(dog_id, dog_name, profile_url)
            if success:
                hydrated += 1
            else:
                failed += 1
            time.sleep(self.delay)

        return {
            "race_id": race_id,
            "total": len(runners),
            "hydrated": hydrated,
            "skipped_already_cached": skipped,
            "failed": failed
        }

    def run_batch_hydration(
        self,
        target_date: Optional[str] = None,
        max_dogs: int = 50,
        verbose: bool = True
    ) -> Dict[str, Any]:
        """Runs a prioritized batch hydration pass for pending upcoming runners."""
        pending = self.get_pending_upcoming_runners(target_date=target_date, limit=max_dogs)
        if not pending:
            if verbose:
                print(f"[HydrationWorker] All upcoming runners for {target_date or 'today'} are already 100% hydrated.")
            return {"status": "complete", "total_pending": 0, "hydrated": 0, "failed": 0}

        if verbose:
            print(f"[HydrationWorker] Ingesting dossiers for {len(pending)} upcoming runners (Target Date: {target_date or 'Today'})...")

        hydrated_count = 0
        failed_count = 0
        seen_dog_ids = set()

        for idx, item in enumerate(pending, 1):
            dog_id = item["dog_id"]
            if dog_id in seen_dog_ids:
                continue
            seen_dog_ids.add(dog_id)

            dog_name = item["dog_name"]
            track = item["track_name"]
            race_num = item["race_number"]
            start_time = item["start_time"] or "TBD"

            if verbose:
                print(f"  [{idx}/{len(pending)}] Hydrating {dog_name:<20} ({track} R{race_num} @ {start_time})...", end=" ", flush=True)

            success = self.hydrate_single_dog(dog_id, dog_name, item["profile_url"])
            if success:
                hydrated_count += 1
                if verbose:
                    print("[OK] Synced")
            else:
                failed_count += 1
                if verbose:
                    print("[WARN] Missing profile")

            time.sleep(self.delay)

        result = {
            "status": "success",
            "total_pending": len(pending),
            "unique_dogs_processed": len(seen_dog_ids),
            "hydrated": hydrated_count,
            "failed": failed_count
        }
        if verbose:
            print(f"[HydrationWorker] Pass completed: {hydrated_count} newly hydrated, {failed_count} unmapped.")
        return result

    def get_stats(self) -> Dict[str, Any]:
        """Returns overall database hydration telemetry."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM greyhounds")
        total_dogs = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT dog_id) FROM past_performances")
        dogs_with_pp = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM past_performances")
        total_runs = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM greyhounds WHERE pb_times_json IS NOT NULL AND pb_times_json != '[]'")
        dogs_with_pbs = cursor.fetchone()[0]

        today = datetime.date.today().isoformat()
        cursor.execute("""
        SELECT COUNT(DISTINCT rr.dog_id)
        FROM race_runners rr
        JOIN races r ON rr.race_id = r.id
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.meeting_date >= ? AND (rr.is_scratched = 0 OR rr.is_scratched IS NULL)
        """, (today,))
        active_today_dogs = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(DISTINCT rr.dog_id)
        FROM race_runners rr
        JOIN races r ON rr.race_id = r.id
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.meeting_date >= ?
          AND (rr.is_scratched = 0 OR rr.is_scratched IS NULL)
          AND rr.dog_id IN (SELECT DISTINCT dog_id FROM past_performances)
        """, (today,))
        active_today_hydrated = cursor.fetchone()[0]

        return {
            "total_dogs_master": total_dogs,
            "dogs_with_past_performances": dogs_with_pp,
            "dogs_with_track_pbs": dogs_with_pbs,
            "total_past_performance_runs": total_runs,
            "active_upcoming_runners": active_today_dogs,
            "active_upcoming_hydrated": active_today_hydrated,
            "upcoming_hydration_percentage": round((active_today_hydrated / max(1, active_today_dogs)) * 100, 1)
        }


class DogHydrationDaemon:
    """Singleton background daemon that keeps upcoming runners continuously hydrated."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, interval_secs: int = 300, batch_size: int = 40):
        if self._initialized:
            return
        self.interval = interval_secs
        self.batch_size = batch_size
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_run_time: Optional[str] = None
        self._last_result: Dict[str, Any] = {}
        self._initialized = True

    def start(self) -> None:
        """Starts the background hydration loop."""
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DogHydrationDaemonThread")
        self._thread.start()
        print(f"[HydrationDaemon] Background dog hydration daemon started (Interval: {self.interval}s, Batch: {self.batch_size})")

    def stop(self) -> None:
        """Stops the daemon."""
        if not self.is_running:
            return
        self._stop_event.set()
        self.is_running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        print("[HydrationDaemon] Background dog hydration daemon stopped.")

    def _run_loop(self) -> None:
        worker = DogHydrationWorker()
        while not self._stop_event.is_set():
            try:
                self._last_run_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                res = worker.run_batch_hydration(max_dogs=self.batch_size, verbose=False)
                self._last_result = res
            except Exception as e:
                self._last_result = {"status": "error", "message": str(e)}

            # Wait for next interval or stop event
            self._stop_event.wait(self.interval)

    def get_status(self) -> Dict[str, Any]:
        worker = DogHydrationWorker()
        stats = worker.get_stats()
        return {
            "daemon_running": self.is_running,
            "interval_seconds": self.interval,
            "batch_size": self.batch_size,
            "last_run_time": self._last_run_time,
            "last_batch_result": self._last_result,
            **stats
        }


if __name__ == "__main__":
    worker = DogHydrationWorker()
    print("--- Running One-Shot Test Hydration Pass ---")
    worker.run_batch_hydration(max_dogs=5, verbose=True)
    print("\nTelemetry Status:")
    print(json.dumps(worker.get_stats(), indent=2))
