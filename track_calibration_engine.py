"""
Autonomous Dynamic Track Calibration Engine
Self-learning, empirical track calibration system for Australian greyhound racing.
Calculates rolling Bayesian-shrunk box strike rates, rail-vs-sweep biases,
early split dominance factors, and surface benchmark times.
"""

import math
import json
import sqlite3
import datetime
from typing import Dict, Any, List, Optional, Tuple


class TrackCalibrationEngine:
    """
    Autonomous calibration engine that extracts empirical track geometry,
    box biases, and sectional speed weights directly from historical race outcomes.
    Uses Bayesian shrinkage to prevent small-sample overfitting while adapting
    dynamically as new race results land in the database.
    """

    ALPHA_PRIOR = 16.0  # Bayesian prior credibility weight
    FIELD_BOX_PRIOR = 0.125  # 1/8 baseline expectation for 8-box fields

    def __init__(self, db_path: str = "greyhound_racing.db"):
        self.db_path = db_path
        self._init_tables()
        self._memory_cache: Dict[str, Dict[str, Any]] = {}

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        """Ensure the dynamic calibration storage table exists."""
        conn = self._get_connection()
        try:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS dynamic_track_calibrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_name TEXT NOT NULL,
                    distance_band TEXT NOT NULL,
                    sample_starts INTEGER DEFAULT 0,
                    sample_races INTEGER DEFAULT 0,
                    rail_bias_index REAL DEFAULT 1.0,
                    outside_sweep_index REAL DEFAULT 1.0,
                    lead_dominance_pct REAL DEFAULT 50.0,
                    speed_weight_factor REAL DEFAULT 1.0,
                    box_multipliers_json TEXT NOT NULL,
                    benchmark_median_time REAL,
                    benchmark_top10_time REAL,
                    calibrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(track_name, distance_band)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_distance_band(distance: int) -> str:
        """Classify race distance into standard sprint/middle/staying bands."""
        if not distance or distance <= 420:
            return "sprint"
        elif distance <= 560:
            return "middle"
        else:
            return "staying"

    def calibrate_all_tracks(self) -> List[Dict[str, Any]]:
        """
        Scans all historical race runner finishes in the database and computes
        dynamic calibrations for all tracks and distance bands.
        Persists results into dynamic_track_calibrations.
        """
        conn = self._get_connection()
        reports = []
        try:
            c = conn.cursor()
            # Find all tracks and distance bands with finished runners
            c.execute("""
                SELECT DISTINCT m.track_name, 
                       CASE 
                           WHEN r.distance <= 420 THEN 'sprint'
                           WHEN r.distance <= 560 THEN 'middle'
                           ELSE 'staying'
                       END as band
                FROM race_runners rr
                JOIN races r ON rr.race_id = r.id
                JOIN meetings m ON r.meeting_id = m.id
                WHERE rr.finish_position IS NOT NULL
                  AND rr.box_number BETWEEN 1 AND 8
                  AND m.track_name IS NOT NULL
                  AND m.track_name != 'Form'
            """)
            combos = c.fetchall()

            for row in combos:
                track = row["track_name"]
                band = row["band"]
                calib = self.compute_track_calibration(track, band, conn=conn)
                if calib and calib.get("sample_starts", 0) >= 8:
                    self._save_calibration(calib, conn=conn)
                    reports.append(calib)

            conn.commit()
            # Clear memory cache
            self._memory_cache.clear()
        finally:
            conn.close()

        return reports

    def compute_track_calibration(
        self, track_name: str, distance_band: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Computes empirical stats and Bayesian shrunk multipliers for a specific track + band.
        """
        should_close = False
        if conn is None:
            conn = self._get_connection()
            should_close = True

        try:
            c = conn.cursor()

            # Define distance bounds for band
            dist_filter = {
                "sprint": "r.distance <= 420",
                "middle": "r.distance > 420 AND r.distance <= 560",
                "staying": "r.distance > 560"
            }.get(distance_band, "r.distance <= 420")

            # 1. Fetch finished runners
            query = f"""
                SELECT rr.race_id, rr.box_number, rr.finish_position, rr.split_1, rr.race_time
                FROM race_runners rr
                JOIN races r ON rr.race_id = r.id
                JOIN meetings m ON r.meeting_id = m.id
                WHERE UPPER(m.track_name) = UPPER(?)
                  AND {dist_filter}
                  AND rr.finish_position IS NOT NULL
                  AND rr.box_number BETWEEN 1 AND 8
            """
            c.execute(query, (track_name,))
            rows = c.fetchall()

            if not rows or len(rows) < 8:
                return None

            total_starts = len(rows)
            race_ids = set(r["race_id"] for r in rows)
            total_races = len(race_ids)

            # Box stats tally
            box_tally = {b: {"starts": 0, "wins": 0, "places": 0} for b in range(1, 9)}
            race_times = []
            
            # Map race to its winner's split and race's fastest split
            race_splits: Dict[int, List[Tuple[float, int]]] = {}

            for r in rows:
                b = r["box_number"]
                pos = r["finish_position"]
                tm = r["race_time"]
                sp1 = r["split_1"]
                rid = r["race_id"]

                box_tally[b]["starts"] += 1
                if pos == 1:
                    box_tally[b]["wins"] += 1
                    if tm and tm > 5.0:
                        race_times.append(tm)
                if pos in (1, 2, 3):
                    box_tally[b]["places"] += 1

                if sp1 and sp1 > 1.0:
                    if rid not in race_splits:
                        race_splits[rid] = []
                    race_splits[rid].append((sp1, pos))

            # Bayesian shrinkage for box multipliers
            box_multipliers = {}
            box_details = {}
            for b in range(1, 9):
                st = box_tally[b]["starts"]
                w = box_tally[b]["wins"]
                plc = box_tally[b]["places"]

                raw_win_rate = (w / st) if st > 0 else self.FIELD_BOX_PRIOR
                # Shrink toward 12.5% prior
                shrunk_win_rate = (w + self.ALPHA_PRIOR * self.FIELD_BOX_PRIOR) / (st + self.ALPHA_PRIOR)
                # Multiplier vs standard 12.5% baseline
                mult = round(shrunk_win_rate / self.FIELD_BOX_PRIOR, 3)
                # Clamp multiplier to realistic safety limits [0.75, 1.40]
                clamped_mult = max(0.75, min(1.40, mult))

                box_multipliers[b] = clamped_mult
                box_details[b] = {
                    "starts": st,
                    "wins": w,
                    "places": plc,
                    "raw_win_pct": round(raw_win_rate * 100.0, 1),
                    "shrunk_win_pct": round(shrunk_win_rate * 100.0, 1),
                    "multiplier": clamped_mult
                }

            # Rail vs Outside Sweep Bias:
            # Inside: Boxes 1 & 2 vs Outside: Boxes 7 & 8
            in_starts = box_tally[1]["starts"] + box_tally[2]["starts"]
            in_wins = box_tally[1]["wins"] + box_tally[2]["wins"]
            out_starts = box_tally[7]["starts"] + box_tally[8]["starts"]
            out_wins = box_tally[7]["wins"] + box_tally[8]["wins"]

            in_rate = (in_wins / max(1, in_starts))
            out_rate = (out_wins / max(1, out_starts))
            rail_bias_index = round(in_rate / max(0.01, out_rate), 2)
            outside_sweep_index = round(out_rate / max(0.01, in_rate), 2)

            # Early Split Dominance:
            # % of races where the dog with the fastest (or within 0.05s of fastest) split won
            leader_wins = 0
            scorable_races = 0
            for rid, splits in race_splits.items():
                if len(splits) >= 3:
                    scorable_races += 1
                    splits.sort(key=lambda x: x[0])  # fastest split first
                    fastest_sp = splits[0][0]
                    # Check if winner was amongst the top split runners
                    winner_sp = next((sp for sp, pos in splits if pos == 1), None)
                    if winner_sp is not None and (winner_sp - fastest_sp) <= 0.06:
                        leader_wins += 1

            lead_dominance_pct = round((leader_wins / max(1, scorable_races)) * 100.0, 1) if scorable_races > 0 else 50.0

            # Dynamic speed weight factor
            # If lead dominance > 60%, early speed is heavily rewarded (up to 1.15)
            # If lead dominance < 40%, run-home stamina is favored (speed factor drops to 0.90)
            speed_weight_factor = round(1.0 + (lead_dominance_pct - 50.0) / 250.0, 2)
            speed_weight_factor = max(0.88, min(1.18, speed_weight_factor))

            # Benchmark times
            median_time = None
            top10_time = None
            if race_times:
                race_times.sort()
                median_time = round(race_times[len(race_times) // 2], 2)
                top10_idx = max(0, int(len(race_times) * 0.10))
                top10_time = round(race_times[top10_idx], 2)

            return {
                "track_name": track_name,
                "distance_band": distance_band,
                "sample_starts": total_starts,
                "sample_races": total_races,
                "rail_bias_index": rail_bias_index,
                "outside_sweep_index": outside_sweep_index,
                "lead_dominance_pct": lead_dominance_pct,
                "speed_weight_factor": speed_weight_factor,
                "box_multipliers": box_multipliers,
                "box_details": box_details,
                "benchmark_median_time": median_time,
                "benchmark_top10_time": top10_time,
                "calibrated_at": datetime.datetime.now().isoformat()
            }
        finally:
            if should_close:
                conn.close()

    def _save_calibration(self, calib: Dict[str, Any], conn: sqlite3.Connection):
        """Upsert dynamic calibration profile into database."""
        c = conn.cursor()
        c.execute("""
            INSERT INTO dynamic_track_calibrations (
                track_name, distance_band, sample_starts, sample_races,
                rail_bias_index, outside_sweep_index, lead_dominance_pct,
                speed_weight_factor, box_multipliers_json,
                benchmark_median_time, benchmark_top10_time, calibrated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(track_name, distance_band) DO UPDATE SET
                sample_starts = excluded.sample_starts,
                sample_races = excluded.sample_races,
                rail_bias_index = excluded.rail_bias_index,
                outside_sweep_index = excluded.outside_sweep_index,
                lead_dominance_pct = excluded.lead_dominance_pct,
                speed_weight_factor = excluded.speed_weight_factor,
                box_multipliers_json = excluded.box_multipliers_json,
                benchmark_median_time = excluded.benchmark_median_time,
                benchmark_top10_time = excluded.benchmark_top10_time,
                calibrated_at = CURRENT_TIMESTAMP
        """, (
            calib["track_name"],
            calib["distance_band"],
            calib["sample_starts"],
            calib["sample_races"],
            calib["rail_bias_index"],
            calib["outside_sweep_index"],
            calib["lead_dominance_pct"],
            calib["speed_weight_factor"],
            json.dumps(calib["box_multipliers"]),
            calib.get("benchmark_median_time"),
            calib.get("benchmark_top10_time")
        ))

    def get_track_calibration(self, track_name: str, distance: int) -> Dict[str, Any]:
        """
        Retrieves the autonomous calibration profile for a track and distance.
        Checks memory cache first, then database. If missing or stale,
        computes on the fly and caches.
        """
        if not track_name:
            return self._default_fallback(distance)

        band = self.get_distance_band(distance)
        cache_key = f"{track_name.upper()}|{band}"

        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        conn = self._get_connection()
        try:
            c = conn.cursor()
            c.execute("""
                SELECT * FROM dynamic_track_calibrations
                WHERE UPPER(track_name) = UPPER(?) AND distance_band = ?
            """, (track_name, band))
            row = c.fetchone()

            if row:
                res = {
                    "track_name": row["track_name"],
                    "distance_band": row["distance_band"],
                    "sample_starts": row["sample_starts"],
                    "sample_races": row["sample_races"],
                    "rail_bias_index": row["rail_bias_index"],
                    "outside_sweep_index": row["outside_sweep_index"],
                    "lead_dominance_pct": row["lead_dominance_pct"],
                    "speed_weight_factor": row["speed_weight_factor"],
                    "box_multipliers": {int(k): v for k, v in json.loads(row["box_multipliers_json"]).items()},
                    "benchmark_median_time": row["benchmark_median_time"],
                    "benchmark_top10_time": row["benchmark_top10_time"],
                    "calibrated_at": row["calibrated_at"],
                    "is_calibrated": True
                }
                self._memory_cache[cache_key] = res
                return res

            # If not yet in table, compute dynamically
            fresh = self.compute_track_calibration(track_name, band, conn=conn)
            if fresh and fresh.get("sample_starts", 0) >= 8:
                self._save_calibration(fresh, conn=conn)
                conn.commit()
                fresh["is_calibrated"] = True
                self._memory_cache[cache_key] = fresh
                return fresh

        except Exception as e:
            print(f"[TrackCalibrationEngine] Error loading calibration for {track_name}: {e}")
        finally:
            conn.close()

        # Fallback to geometry defaults
        fallback = self._default_fallback(distance, track_name)
        self._memory_cache[cache_key] = fallback
        return fallback

    def _default_fallback(self, distance: int, track_name: str = "") -> Dict[str, Any]:
        """Prior fallback based on straight vs circular track geometry."""
        band = self.get_distance_band(distance)
        is_straight = any(k in (track_name or "").upper() for k in ["CAPALABA", "HEALESVILLE", "STRAIGHT"])
        
        if is_straight:
            # Straight track baseline: Box parity with slight wide-box clean air edge
            multipliers = {1: 1.05, 2: 1.02, 3: 0.98, 4: 0.95, 5: 0.96, 6: 1.00, 7: 1.02, 8: 1.08}
            rail_bias = 1.0
            sweep_bias = 1.05
            lead_dom = 62.0
        elif distance <= 400:
            # Short sprint circular: Golden rail Box 1 & 2 command
            multipliers = {1: 1.25, 2: 1.15, 3: 1.00, 4: 0.95, 5: 0.90, 6: 0.94, 7: 0.98, 8: 1.05}
            rail_bias = 1.45
            sweep_bias = 0.85
            lead_dom = 68.0
        else:
            # Two-turn standard sprint/middle
            multipliers = {1: 1.20, 2: 1.12, 3: 1.00, 4: 0.96, 5: 0.92, 6: 0.96, 7: 1.00, 8: 1.08}
            rail_bias = 1.30
            sweep_bias = 0.92
            lead_dom = 54.0

        return {
            "track_name": track_name or "Standard Circuit",
            "distance_band": band,
            "sample_starts": 0,
            "sample_races": 0,
            "rail_bias_index": rail_bias,
            "outside_sweep_index": sweep_bias,
            "lead_dominance_pct": lead_dom,
            "speed_weight_factor": 1.0,
            "box_multipliers": multipliers,
            "benchmark_median_time": None,
            "benchmark_top10_time": None,
            "calibrated_at": None,
            "is_calibrated": False
        }

    def get_calibration_summary(self) -> List[Dict[str, Any]]:
        """Returns all dynamically calibrated tracks and their metrics."""
        conn = self._get_connection()
        try:
            c = conn.cursor()
            c.execute("""
                SELECT track_name, distance_band, sample_starts, sample_races,
                       rail_bias_index, outside_sweep_index, lead_dominance_pct,
                       speed_weight_factor, box_multipliers_json,
                       benchmark_median_time, benchmark_top10_time, calibrated_at
                FROM dynamic_track_calibrations
                ORDER BY sample_starts DESC
            """)
            rows = c.fetchall()
            results = []
            for r in rows:
                results.append({
                    "track_name": r["track_name"],
                    "distance_band": r["distance_band"],
                    "sample_starts": r["sample_starts"],
                    "sample_races": r["sample_races"],
                    "rail_bias_index": r["rail_bias_index"],
                    "outside_sweep_index": r["outside_sweep_index"],
                    "lead_dominance_pct": r["lead_dominance_pct"],
                    "speed_weight_factor": r["speed_weight_factor"],
                    "box_multipliers": {int(k): v for k, v in json.loads(r["box_multipliers_json"]).items()},
                    "benchmark_median_time": r["benchmark_median_time"],
                    "benchmark_top10_time": r["benchmark_top10_time"],
                    "calibrated_at": r["calibrated_at"]
                })
            return results
        finally:
            conn.close()

