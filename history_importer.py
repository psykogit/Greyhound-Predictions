"""
Historical Race Data Importer for Greyhound Betting Engine.
Supports importing months/years of historical race results and past performances
from standard CSV or JSON files into SQLite.

Automatically normalizes track names, dates, boxes, times, sectionals, and margins,
populates meetings, races, runners, greyhound masters, and past_performances,
and automatically triggers empirical track calibration and exotic parameter fitting.
"""

import os
import csv
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Union
import sqlite3

from db import get_connection
from track_calibration_engine import TrackCalibrationEngine
from exotic_bet_optimizer import ExoticBetOptimizer


# Column header alias mappings (case-insensitive and stripped)
HEADER_ALIASES = {
    "meeting_date": ["meeting_date", "date", "race_date", "run_date", "meetingdate", "racedate"],
    "track_name": ["track_name", "track", "venue", "trackname", "course"],
    "race_number": ["race_number", "race_no", "raceno", "race_num", "racenum", "race"],
    "race_name": ["race_name", "racename", "event_name", "title"],
    "distance": ["distance", "dist", "race_distance", "length"],
    "grade": ["grade", "race_grade", "class"],
    "box_number": ["box_number", "box", "trap", "rug", "boxno", "barrier"],
    "dog_name": ["dog_name", "dog", "greyhound", "runner_name", "runner", "dogname"],
    "trainer_name": ["trainer_name", "trainer", "trainername"],
    "weight": ["weight", "dog_weight", "kg", "wgt"],
    "finish_position": ["finish_position", "finish_pos", "pos", "position", "place", "fin_pos", "fin_pos_num"],
    "margin": ["margin", "beaten_margin", "lengths", "marg"],
    "race_time": ["race_time", "time", "run_time", "racetime", "win_time"],
    "split_1": ["split_1", "split1", "split", "first_split", "sectional_1", "sec_1", "card_split_1", "early_split"],
    "split_2": ["split_2", "split2", "second_split", "sectional_2", "sec_2"],
    "sp_odds": ["sp_odds", "sp", "odds", "starting_price", "fixed_odds", "price", "spodds", "win_odds"],
    "running_style": ["running_style", "style", "run_style", "runningstyle"],
    "pir": ["pir", "in_running", "running_position", "inrunning"],
    "is_scratched": ["is_scratched", "scratched", "scr"],
    "sex": ["sex", "gender", "dog_gender", "dog_sex", "bitch_dog"]
}


def normalize_date_to_iso(date_str: Any) -> Optional[str]:
    """Converts diverse date formats (YYYY-MM-DD, DD/MM/YYYY, DD-Mon-YYYY, etc.) to YYYY-MM-DD."""
    if not date_str:
        return None
    s = str(date_str).strip()
    # Strip any time component
    s = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?.*$", "", s)

    # Match YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s

    formats = [
        "%d/%m/%Y", "%d/%m/%y",
        "%d-%m-%Y", "%d-%m-%y",
        "%Y/%m/%d",
        "%d-%b-%Y", "%d-%b-%y",
        "%d %B %Y", "%d %b %Y",
        "%Y%m%d"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def clean_numeric(val: Any, is_int: bool = False) -> Optional[Union[float, int]]:
    """Cleans numeric values from strings like '$2.50', '460m', '1st', '32.4kg'."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val) if is_int else float(val)
    s = str(val).strip()
    if not s or s.lower() in ("none", "null", "nan", "-", "nbt", "scr"):
        return None

    # Handle scratched / did not finish
    if is_int and s.upper() in ("SCR", "SC"):
        return None

    # Clean currency, units, ordinal suffixes
    s = re.sub(r"[$kgm]", "", s, flags=re.IGNORECASE).strip()
    m = re.search(r"[-+]?\d*\.?\d+", s)
    if not m:
        return None
    try:
        num = float(m.group(0))
        return int(round(num)) if is_int else num
    except ValueError:
        return None


def clean_text(val: Any) -> Optional[str]:
    """Strips and standardizes string fields."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("none", "null", "nan", "-") else None


class HistoryImporter:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or get_connection()

    def parse_csv(self, file_path: str) -> List[Dict[str, Any]]:
        """Parses a CSV file and maps headers using alias dictionary."""
        records = []
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            header_row = next(reader, None)
            if not header_row:
                return []

            # Normalize headers
            cleaned_headers = [re.sub(r"[^\w]", "", h.strip().lower()) for h in header_row]
            field_map: Dict[str, int] = {}

            for canon_key, aliases in HEADER_ALIASES.items():
                canon_norm = re.sub(r"[^\w]", "", canon_key.lower())
                alias_norms = [re.sub(r"[^\w]", "", a.lower()) for a in aliases]
                for idx, h in enumerate(cleaned_headers):
                    if h in alias_norms or h == canon_norm:
                        field_map[canon_key] = idx
                        break

            for row in reader:
                if not row or not any(row):
                    continue
                rec: Dict[str, Any] = {}
                for canon_key, col_idx in field_map.items():
                    if col_idx < len(row):
                        rec[canon_key] = row[col_idx]
                records.append(rec)

        return records

    def parse_json(self, file_path: str) -> List[Dict[str, Any]]:
        """Parses a JSON file (either a flat array of rows or hierarchical meetings)."""
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)

        records = []
        if isinstance(data, list):
            for item in data:
                if "races" in item:
                    # Hierarchical Meeting format
                    meeting_date = item.get("meeting_date") or item.get("date")
                    track_name = item.get("track_name") or item.get("track")
                    for race in item.get("races", []):
                        r_num = race.get("race_number") or race.get("race_no")
                        r_dist = race.get("distance")
                        r_grade = race.get("grade")
                        r_name = race.get("race_name")
                        for runner in race.get("runners", []):
                            rec = {
                                "meeting_date": meeting_date,
                                "track_name": track_name,
                                "race_number": r_num,
                                "distance": r_dist,
                                "grade": r_grade,
                                "race_name": r_name,
                                **runner
                            }
                            records.append(rec)
                else:
                    # Flat row
                    records.append(item)
        elif isinstance(data, dict):
            if "races" in data:
                return self.parse_json_from_obj([data])
            elif "records" in data or "data" in data:
                rows = data.get("records") or data.get("data")
                return self.parse_json_from_obj(rows)

        return records

    def parse_json_from_obj(self, data_list: List[Any]) -> List[Dict[str, Any]]:
        """Flattens hierarchical structures if provided in memory."""
        records = []
        for item in data_list:
            if isinstance(item, dict) and "races" in item:
                m_date = item.get("meeting_date") or item.get("date")
                t_name = item.get("track_name") or item.get("track")
                for race in item.get("races", []):
                    r_num = race.get("race_number") or race.get("race_no")
                    r_dist = race.get("distance")
                    r_grade = race.get("grade")
                    r_name = race.get("race_name")
                    for runner in race.get("runners", []):
                        records.append({
                            "meeting_date": m_date,
                            "track_name": t_name,
                            "race_number": r_num,
                            "distance": r_dist,
                            "grade": r_grade,
                            "race_name": r_name,
                            **runner
                        })
            elif isinstance(item, dict):
                records.append(item)
        return records

    def import_records(self, raw_records: List[Dict[str, Any]], auto_calibrate: bool = True) -> Dict[str, Any]:
        """
        Ingests parsed raw runner records into SQLite across meetings, races,
        greyhounds, trainers, race_runners, and past_performances.
        """
        if not raw_records:
            return {"status": "EMPTY", "message": "No records to import"}

        cursor = self.conn.cursor()

        # Track import metrics
        meetings_created = 0
        races_created = 0
        runners_inserted = 0
        pp_inserted = 0
        tracks_seen = set()
        dates_seen = set()

        # Cache meeting_id and race_id to avoid redundant SELECTs
        meeting_cache: Dict[Tuple[str, str], int] = {}
        race_cache: Dict[Tuple[int, int], int] = {}
        dog_cache: Dict[str, int] = {}
        trainer_cache: Dict[str, int] = {}

        for row in raw_records:
            # 1. Normalize core identifiers
            m_date = normalize_date_to_iso(row.get("meeting_date") or row.get("date"))
            raw_track = clean_text(row.get("track_name") or row.get("track"))
            r_num = clean_numeric(row.get("race_number") or row.get("race_no") or 1, is_int=True)
            box_num = clean_numeric(row.get("box_number") or row.get("box"), is_int=True)
            dog_name = clean_text(row.get("dog_name") or row.get("dog"))

            if not m_date or not raw_track or not dog_name or not box_num:
                continue

            # Standardize track name capitalization
            track_name = raw_track.strip().title()
            for prefix in ["Ladbrokes ", "Sportsbet ", "Tab "]:
                if track_name.startswith(prefix):
                    track_name = track_name[len(prefix):].strip()

            tracks_seen.add(track_name)
            dates_seen.add(m_date)

            # 2. Upsert Meeting
            m_key = (track_name, m_date)
            if m_key in meeting_cache:
                meeting_id = meeting_cache[m_key]
            else:
                source_id = f"import_{track_name}_{m_date}"
                cursor.execute("""
                    INSERT INTO meetings (source_id, track_name, meeting_date, source)
                    VALUES (?, ?, ?, 'history_import')
                    ON CONFLICT(source_id) DO UPDATE SET track_name=excluded.track_name
                """, (source_id, track_name, m_date))
                cursor.execute("SELECT id FROM meetings WHERE source_id = ?", (source_id,))
                m_row = cursor.fetchone()
                meeting_id = m_row["id"] if isinstance(m_row, sqlite3.Row) else m_row[0]
                meeting_cache[m_key] = meeting_id
                meetings_created += 1

            # 3. Upsert Race
            r_key = (meeting_id, r_num)
            distance = clean_numeric(row.get("distance"), is_int=True)
            grade = clean_text(row.get("grade")) or ""
            race_name = clean_text(row.get("race_name")) or f"Race {r_num}"

            if r_key in race_cache:
                race_id = race_cache[r_key]
            else:
                source_race_id = f"{meeting_id}_R{r_num}"
                cursor.execute("""
                    INSERT INTO races (source_race_id, meeting_id, race_number, race_name, distance, grade)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_race_id) DO UPDATE SET
                        distance = COALESCE(excluded.distance, races.distance),
                        grade = COALESCE(excluded.grade, races.grade),
                        race_name = COALESCE(excluded.race_name, races.race_name)
                """, (source_race_id, meeting_id, r_num, race_name, distance, grade))
                cursor.execute("SELECT id FROM races WHERE source_race_id = ?", (source_race_id,))
                r_row = cursor.fetchone()
                race_id = r_row["id"] if isinstance(r_row, sqlite3.Row) else r_row[0]
                race_cache[r_key] = race_id
                races_created += 1

            # 4. Upsert Greyhound Master
            d_norm = dog_name.upper()
            if d_norm in dog_cache:
                dog_id = dog_cache[d_norm]
            else:
                cursor.execute("INSERT OR IGNORE INTO greyhounds (dog_name) VALUES (?)", (dog_name,))
                cursor.execute("SELECT id FROM greyhounds WHERE UPPER(dog_name) = ?", (d_norm,))
                d_row = cursor.fetchone()
                dog_id = d_row["id"] if isinstance(d_row, sqlite3.Row) else d_row[0]
                dog_cache[d_norm] = dog_id

            # 5. Upsert Trainer Master (if provided)
            trainer_id = None
            raw_trainer = clean_text(row.get("trainer_name") or row.get("trainer"))
            if raw_trainer:
                t_norm = raw_trainer.upper()
                if t_norm in trainer_cache:
                    trainer_id = trainer_cache[t_norm]
                else:
                    cursor.execute("INSERT OR IGNORE INTO trainers (trainer_name) VALUES (?)", (raw_trainer,))
                    cursor.execute("SELECT id FROM trainers WHERE UPPER(trainer_name) = ?", (t_norm,))
                    t_row = cursor.fetchone()
                    if t_row:
                        trainer_id = t_row["id"] if isinstance(t_row, sqlite3.Row) else t_row[0]
                        trainer_cache[t_norm] = trainer_id

            # 6. Runner Metrics & Sex Normalization
            weight = clean_numeric(row.get("weight"))
            raw_sex = clean_text(row.get("sex") or row.get("gender") or row.get("dog_sex") or row.get("dog_gender"))
            sex_val = None
            if raw_sex:
                s_clean = raw_sex.strip().upper()
                if s_clean in ("B", "BITCH", "FEMALE", "F"):
                    sex_val = "B"
                elif s_clean in ("D", "DOG", "MALE", "M"):
                    sex_val = "D"
            if not sex_val and weight:
                sex_val = "B" if weight < 29.2 else "D"

            if sex_val:
                cursor.execute("UPDATE greyhounds SET sex = COALESCE(sex, ?) WHERE id = ?", (sex_val, dog_id))

            finish_pos = clean_numeric(row.get("finish_position"), is_int=True)
            margin = clean_numeric(row.get("margin"))
            race_time = clean_numeric(row.get("race_time"))
            split_1 = clean_numeric(row.get("split_1"))
            split_2 = clean_numeric(row.get("split_2"))
            sp_odds = clean_numeric(row.get("sp_odds"))
            running_style = clean_text(row.get("running_style"))
            pir = clean_text(row.get("pir"))

            # Scratched detection
            is_scratched = 1 if (
                clean_numeric(row.get("is_scratched"), is_int=True) == 1
                or str(row.get("finish_position") or "").strip().upper() in ("SCR", "SCRATCHED")
            ) else 0

            # 7. Upsert Race Runner
            cursor.execute("""
                INSERT INTO race_runners (
                    race_id, dog_id, trainer_id, box_number, weight, sex, running_style,
                    finish_position, margin, race_time, split_1, split_2, sp_odds, is_scratched, card_split_1
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(race_id, box_number) DO UPDATE SET
                    dog_id = excluded.dog_id,
                    trainer_id = COALESCE(excluded.trainer_id, race_runners.trainer_id),
                    weight = COALESCE(excluded.weight, race_runners.weight),
                    sex = COALESCE(excluded.sex, race_runners.sex),
                    running_style = COALESCE(excluded.running_style, race_runners.running_style),
                    finish_position = COALESCE(excluded.finish_position, race_runners.finish_position),
                    margin = COALESCE(excluded.margin, race_runners.margin),
                    race_time = COALESCE(excluded.race_time, race_runners.race_time),
                    split_1 = COALESCE(excluded.split_1, race_runners.split_1),
                    split_2 = COALESCE(excluded.split_2, race_runners.split_2),
                    sp_odds = COALESCE(excluded.sp_odds, race_runners.sp_odds),
                    is_scratched = excluded.is_scratched,
                    card_split_1 = COALESCE(excluded.card_split_1, race_runners.card_split_1)
            """, (
                race_id, dog_id, trainer_id, box_num, weight, sex_val, running_style,
                finish_pos, margin, race_time, split_1, split_2, sp_odds, is_scratched, split_1
            ))
            runners_inserted += 1

            # 8. Crucial: Populate past_performances for finished runs
            # This enables deep sectional feature calculation and personal best extraction!
            if finish_pos is not None and not is_scratched:
                cursor.execute("""
                    INSERT OR IGNORE INTO past_performances (
                        dog_id, run_date, run_date_iso, track_code, distance, box, weight, sex,
                        finish_pos, race_time, margin, split_1, pir, sp_odds, grade
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    dog_id, m_date, m_date, track_name, distance, box_num, weight, sex_val,
                    str(finish_pos), race_time, margin, split_1, pir, sp_odds, grade
                ))
                if cursor.rowcount > 0:
                    pp_inserted += 1

        self.conn.commit()

        # Update career starts/wins counters on greyhounds
        cursor.execute("""
            UPDATE greyhounds
            SET career_starts = (SELECT count(*) FROM past_performances WHERE past_performances.dog_id = greyhounds.id),
                career_wins = (SELECT count(*) FROM past_performances WHERE past_performances.dog_id = greyhounds.id AND (finish_pos = '1' OR finish_pos LIKE '1/%'))
            WHERE id IN (SELECT DISTINCT dog_id FROM race_runners)
        """)
        self.conn.commit()

        calibration_summary = {}
        if auto_calibrate and runners_inserted > 0:
            print("=== Automatically Recalibrating Track Dynamics & Exotic Parameters ===")
            tce = TrackCalibrationEngine()
            calib_reports = tce.calibrate_all_tracks()
            calibration_summary["tracks_calibrated"] = len(calib_reports)

            opt = ExoticBetOptimizer(self.conn)
            gammas = opt.calibrate_discount_factors()
            calibration_summary["empirical_discount_gammas"] = gammas
            print(f"Calibrated {len(calib_reports)} tracks. Empirical Harville Gammas: {gammas}")

        sorted_dates = sorted(list(dates_seen))
        date_range = f"{sorted_dates[0]} to {sorted_dates[-1]}" if sorted_dates else "None"

        return {
            "status": "SUCCESS",
            "runners_imported": runners_inserted,
            "meetings_count": len(meeting_cache),
            "races_count": len(race_cache),
            "past_performances_created": pp_inserted,
            "tracks_count": len(tracks_seen),
            "tracks": sorted(list(tracks_seen)),
            "date_range": date_range,
            "calibration": calibration_summary
        }

    def import_file(self, file_path: str, auto_calibrate: bool = True) -> Dict[str, Any]:
        """Imports historical race data from a single CSV or JSON file."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        print(f"--> Reading historical data from: {file_path}")
        if p.suffix.lower() == ".csv":
            records = self.parse_csv(str(p))
        elif p.suffix.lower() == ".json":
            records = self.parse_json(str(p))
        else:
            raise ValueError(f"Unsupported file format '{p.suffix}'. Please provide a .csv or .json file.")

        print(f"--> Found {len(records)} runner rows. Ingesting into database...")
        res = self.import_records(records, auto_calibrate=auto_calibrate)
        res["source_file"] = str(p.name)
        return res

    def import_directory(self, dir_path: str, auto_calibrate: bool = True) -> Dict[str, Any]:
        """Scans a directory for all .csv and .json files and batch-imports them."""
        p = Path(dir_path)
        if not p.is_dir():
            raise NotADirectoryError(f"Directory not found: {dir_path}")

        data_files = sorted(list(p.glob("*.csv")) + list(p.glob("*.json")))
        if not data_files:
            return {"status": "EMPTY", "message": f"No .csv or .json files found in {dir_path}"}

        print(f"=== Found {len(data_files)} data files in {dir_path} ===")
        all_records = []
        for df in data_files:
            try:
                if df.suffix.lower() == ".csv":
                    recs = self.parse_csv(str(df))
                else:
                    recs = self.parse_json(str(df))
                print(f"  [+] Loaded {len(recs)} rows from {df.name}")
                all_records.extend(recs)
            except Exception as e:
                print(f"  [-] Error reading {df.name}: {e}")

        print(f"--> Ingesting aggregate {len(all_records)} historical rows...")
        res = self.import_records(all_records, auto_calibrate=auto_calibrate)
        res["files_processed"] = [f.name for f in data_files]
        return res

    @staticmethod
    def generate_template(output_csv_path: str = "sample_race_history.csv", output_json_path: str = "sample_race_history.json") -> Tuple[str, str]:
        """
        Creates clean, annotated sample CSV and JSON files demonstrating the exact standard format.
        """
        sample_rows = [
            {
                "meeting_date": "2026-05-15",
                "track_name": "Warragul",
                "race_number": 1,
                "race_name": "Barnetts Tyres Maiden",
                "distance": 460,
                "grade": "Maiden",
                "box_number": 1,
                "dog_name": "Aston Rupee",
                "trainer_name": "J Thompson",
                "weight": 34.2,
                "finish_position": 1,
                "margin": 0.0,
                "race_time": 25.65,
                "split_1": 6.48,
                "sp_odds": 1.80,
                "running_style": "Railer",
                "pir": "111",
                "is_scratched": 0
            },
            {
                "meeting_date": "2026-05-15",
                "track_name": "Warragul",
                "race_number": 1,
                "race_name": "Barnetts Tyres Maiden",
                "distance": 460,
                "grade": "Maiden",
                "box_number": 2,
                "dog_name": "Zara's Ivan",
                "trainer_name": "G O'Keeffe",
                "weight": 31.8,
                "finish_position": 2,
                "margin": 2.25,
                "race_time": 25.81,
                "split_1": 6.55,
                "sp_odds": 4.60,
                "running_style": "Mid",
                "pir": "222",
                "is_scratched": 0
            },
            {
                "meeting_date": "2026-05-15",
                "track_name": "Darwin",
                "race_number": 2,
                "race_name": "Top End Sprint",
                "distance": 312,
                "grade": "Grade 5",
                "box_number": 8,
                "dog_name": "Darwin Flyer",
                "trainer_name": "T Brown",
                "weight": 29.5,
                "finish_position": 1,
                "margin": 0.0,
                "race_time": 18.15,
                "split_1": 7.15,
                "sp_odds": 3.20,
                "running_style": "Wide",
                "pir": "11",
                "is_scratched": 0
            },
            {
                "meeting_date": "2026-05-15",
                "track_name": "Townsville",
                "race_number": 3,
                "race_name": "North QLD Cup",
                "distance": 380,
                "grade": "Free For All",
                "box_number": 4,
                "dog_name": "Tropical Storm",
                "trainer_name": "M Jones",
                "weight": 33.0,
                "finish_position": 1,
                "margin": 0.0,
                "race_time": 21.75,
                "split_1": 7.42,
                "sp_odds": 2.10,
                "running_style": "Mid",
                "pir": "111",
                "is_scratched": 0
            }
        ]

        # Write CSV
        fieldnames = [
            "meeting_date", "track_name", "race_number", "race_name", "distance", "grade",
            "box_number", "dog_name", "trainer_name", "weight", "finish_position", "margin",
            "race_time", "split_1", "sp_odds", "running_style", "pir", "is_scratched"
        ]
        with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(sample_rows)

        # Write JSON
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(sample_rows, f, indent=2)

        return output_csv_path, output_json_path
