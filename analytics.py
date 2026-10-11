"""
Advanced Feature Engineering & Probability Engine for Greyhound Racing.
Includes:
- Exponential recency-weighted Last-4 form scoring
- Conditional Box Bias (form & pace gating to prevent false Box 1 inflation)
- Distance-dependent split-1 weighting (Sprints vs 500m+ Staying races)
- Running style synergy & market deviation guardrails
- Trainer Strike Rate Intelligence (Bayesian shrinkage, 3-tier track/band/global lookup)
"""

import math
import re
import json
import statistics
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
import sqlite3
from db import get_connection

AEDT_TZ = timezone(timedelta(hours=11))


def parse_flexible_date(d_str: Optional[str]) -> Optional[datetime]:
    if not d_str:
        return None
    s = str(d_str).strip()
    m = re.match(r"^\d{1,2}:\d{2}\s+(.*)$", s)
    if m:
        s = m.group(1).strip()
    formats = [
        "%d-%b-%y",
        "%d-%b-%Y",
        "%d/%m/%y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# TRAINER INTELLIGENCE ENGINE
# ---------------------------------------------------------------------------

class TrainerIntelligence:
    """
    Builds and queries a Bayesian-shrunk trainer strike rate model.

    Lookup hierarchy (most specific → least specific):
      1. trainer × track × distance_band  (if >= MIN_STARTS_SPECIFIC samples)
      2. trainer × track                  (if >= MIN_STARTS_TRACK samples)
      3. trainer × distance_band          (global, cross-track)
      4. Neutral (no signal — return factor 1.0)

    Bayesian shrinkage formula:
        shrunk_rate = (wins + α * prior) / (starts + α)
    where:
        prior = observed field-average win rate (~0.125 for 8-dog fields)
        α     = effective prior sample size (controls how quickly we trust data)

    This prevents a trainer with 2-for-2 starts from getting a 100% boost —
    at α=8 that becomes (2+8*0.125)/(2+8) = 3/10 = 30%, much more honest.
    """

    # Minimum observed starts before we trust a specific-level stat
    MIN_STARTS_SPECIFIC = 4   # track + band level
    MIN_STARTS_TRACK = 3      # track-only level
    MIN_STARTS_GLOBAL = 4     # global trainer level

    # Bayesian prior strength (equivalent prior observations)
    # Higher = more conservative, slower to trust small samples
    ALPHA = 12.0

    # Multiplier clamp: never boost more than +15% or penalise more than -12%
    MAX_FACTOR = 1.15
    MIN_FACTOR = 0.88

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._field_avg: Optional[float] = None
        self._cache: Optional[Dict] = None

    def _get_field_avg(self) -> float:
        """Global average win rate across all completed race runners."""
        if self._field_avg is None:
            c = self.conn.cursor()
            c.execute(
                "SELECT COUNT(*), SUM(CASE WHEN finish_position = 1 THEN 1 ELSE 0 END) "
                "FROM race_runners WHERE finish_position IS NOT NULL AND finish_position != 0"
            )
            row = c.fetchone()
            total, wins = row[0] or 1, row[1] or 0
            self._field_avg = wins / total if total > 0 else 0.125
        return self._field_avg

    @staticmethod
    def distance_band(distance: int) -> str:
        """Categorise distance into sprint / middle / staying."""
        if distance <= 400:
            return "sprint"
        elif distance <= 450:
            return "middle"
        else:
            return "staying"

    @staticmethod
    def box_band(box: Optional[int]) -> str:
        """Categorise box into inside / middle / outside."""
        if not box:
            return "middle"
        if box in (1, 2):
            return "inside"
        elif box in (7, 8):
            return "outside"
        else:
            return "middle"

    def _build_cache(self) -> Dict:
        """
        Build a nested dict of trainer stats from all completed race_runners.
        Structure:
            cache["track"][trainer_name] = {"starts": N, "wins": W}
            cache["track_band"][trainer_name] = {"starts": N, "wins": W}
            cache["trainer_box"][trainer_name|box_band] = {"starts": N, "wins": W}
            cache["recent_form"][trainer_name] = [is_win, ...] (chronological)
            cache["global"][trainer_name] = {"starts": N, "wins": W}
        """
        c = self.conn.cursor()
        c.execute("""
            SELECT t.trainer_name, m.track_name, r.distance,
                   rr.finish_position, rr.box_number, m.meeting_date
            FROM race_runners rr
            JOIN races r ON rr.race_id = r.id
            JOIN meetings m ON r.meeting_id = m.id
            JOIN trainers t ON rr.trainer_id = t.id
            WHERE rr.finish_position IS NOT NULL
              AND rr.finish_position != 0
              AND rr.is_scratched = 0
            ORDER BY m.meeting_date ASC, rr.id ASC
        """)
        rows = c.fetchall()

        cache: Dict[str, Dict] = {
            "track": {},
            "track_band": {},
            "trainer_box": {},
            "recent_form": {},
            "global": {},
        }

        for row in rows:
            trainer = row[0]
            track = row[1]
            dist = row[2] or 400
            pos = row[3]
            box_num = row[4]
            band = self.distance_band(int(dist))
            b_band = self.box_band(box_num)
            is_win = 1 if pos == 1 else 0

            # track-level
            tk = track
            if tk not in cache["track"]:
                cache["track"][tk] = {}
            if trainer not in cache["track"][tk]:
                cache["track"][tk][trainer] = {"starts": 0, "wins": 0}
            cache["track"][tk][trainer]["starts"] += 1
            cache["track"][tk][trainer]["wins"] += is_win

            # track+band level
            tb_key = f"{track}|{band}"
            if tb_key not in cache["track_band"]:
                cache["track_band"][tb_key] = {}
            if trainer not in cache["track_band"][tb_key]:
                cache["track_band"][tb_key][trainer] = {"starts": 0, "wins": 0}
            cache["track_band"][tb_key][trainer]["starts"] += 1
            cache["track_band"][tb_key][trainer]["wins"] += is_win

            # trainer box synergy
            tbox_key = f"{trainer}|{b_band}"
            if tbox_key not in cache["trainer_box"]:
                cache["trainer_box"][tbox_key] = {"starts": 0, "wins": 0}
            cache["trainer_box"][tbox_key]["starts"] += 1
            cache["trainer_box"][tbox_key]["wins"] += is_win

            # trainer rolling form
            if trainer not in cache["recent_form"]:
                cache["recent_form"][trainer] = []
            cache["recent_form"][trainer].append(is_win)

            # global level
            if trainer not in cache["global"]:
                cache["global"][trainer] = {"starts": 0, "wins": 0}
            cache["global"][trainer]["starts"] += 1
            cache["global"][trainer]["wins"] += is_win

        return cache

    def _get_cache(self) -> Dict:
        if self._cache is None:
            self._cache = self._build_cache()
        return self._cache

    def _shrunk_rate(self, wins: int, starts: int) -> float:
        """Apply Bayesian shrinkage toward the field prior."""
        prior = self._get_field_avg()
        alpha = self.ALPHA
        return (wins + alpha * prior) / (starts + alpha)

    def get_trainer_factor(
        self,
        trainer_name: Optional[str],
        track_name: str,
        distance: int,
        box: Optional[int] = None,
    ) -> Tuple[float, str]:
        """
        Returns (multiplier, source_label).
        multiplier: float clamped to [MIN_FACTOR, MAX_FACTOR]
        source_label: one of 'track+band', 'track', 'global', 'none'
        Integrates:
        1. Hierarchical Bayesian win rate (track+band -> track -> global)
        2. Box-Trainer Synergy (inside/outside specialist bias)
        3. Rolling Kennel Momentum (recent 8-10 starts strike rate)
        """
        if not trainer_name:
            return 1.0, "none"

        cache = self._get_cache()
        prior = self._get_field_avg()
        band = self.distance_band(distance)

        factor = 1.0
        source_label = "none"

        # --- Tier 1: track + distance band ---
        tb_key = f"{track_name}|{band}"
        tb_data = cache["track_band"].get(tb_key, {}).get(trainer_name)
        if tb_data and tb_data["starts"] >= self.MIN_STARTS_SPECIFIC:
            rate = self._shrunk_rate(tb_data["wins"], tb_data["starts"])
            raw_factor = rate / prior
            conf = min(1.0, tb_data["starts"] / 15.0)
            factor = 1.0 + (raw_factor - 1.0) * conf
            source_label = "track+band"
        else:
            # --- Tier 2: track only ---
            tk_data = cache["track"].get(track_name, {}).get(trainer_name)
            if tk_data and tk_data["starts"] >= self.MIN_STARTS_TRACK:
                rate = self._shrunk_rate(tk_data["wins"], tk_data["starts"])
                raw_factor = rate / prior
                conf = min(1.0, tk_data["starts"] / 15.0)
                factor = 1.0 + (raw_factor - 1.0) * conf
                source_label = "track"
            else:
                # --- Tier 3: global trainer record ---
                gl_data = cache["global"].get(trainer_name)
                if gl_data and gl_data["starts"] >= self.MIN_STARTS_GLOBAL:
                    rate = self._shrunk_rate(gl_data["wins"], gl_data["starts"])
                    # Dampen global signal further — cross-track patterns are noisier
                    raw_factor = 1.0 + (rate / prior - 1.0) * 0.50
                    conf = min(1.0, gl_data["starts"] / 15.0)
                    factor = 1.0 + (raw_factor - 1.0) * conf
                    source_label = "global"

        # --- Box-Trainer Synergy Modifier ---
        if box is not None:
            b_band = self.box_band(box)
            tbox_data = cache["trainer_box"].get(f"{trainer_name}|{b_band}")
            if tbox_data and tbox_data["starts"] >= 4:
                b_rate = self._shrunk_rate(tbox_data["wins"], tbox_data["starts"])
                if b_rate >= prior * 1.4:
                    box_synergy = min(1.08, 1.0 + (b_rate - prior) * 0.35)
                    factor *= box_synergy
                elif b_rate <= prior * 0.6:
                    box_synergy = max(0.94, 1.0 - (prior - b_rate) * 0.40)
                    factor *= box_synergy

        # --- Rolling Kennel Momentum (Last 8-10 Starts) ---
        recent = cache["recent_form"].get(trainer_name, [])[-10:]
        if len(recent) >= 8:
            recent_wins = sum(recent)
            recent_rate = recent_wins / len(recent)
            if recent_rate >= 0.30:
                factor *= 1.05  # Hot stable
            elif recent_wins == 0 and len(recent) >= 10:
                factor *= 0.95  # Cold stable slump

        clamped_factor = max(self.MIN_FACTOR, min(self.MAX_FACTOR, factor))
        return clamped_factor, source_label

    def get_field_stats(
        self,
        trainer_names: List[Optional[str]],
        track_name: str,
        distance: int,
        boxes: Optional[List[Optional[int]]] = None,
    ) -> List[Dict]:
        """
        Returns a list of dicts per runner:
          {"factor": float, "source": str, "trainer": str}
        """
        if not boxes:
            boxes = [None] * len(trainer_names)
        return [
            {
                "trainer": name or "Unknown",
                "factor": self.get_trainer_factor(name, track_name, distance, box=b)[0],
                "source": self.get_trainer_factor(name, track_name, distance, box=b)[1],
            }
            for name, b in zip(trainer_names, boxes)
        ]


class GreyhoundAnalytics:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or get_connection()
        self._track_temp_cache: Dict[str, float] = {}

    def get_track_temperature(self, track_name: Optional[str]) -> float:
        """
        Retrieves empirically calibrated softmax temperature for track.
        Defaults to 2.4 if uncalibrated.
        """
        if not track_name:
            return 2.4
        t_key = track_name.strip().upper()
        if t_key in self._track_temp_cache:
            return self._track_temp_cache[t_key]
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT temperature FROM track_calibrations WHERE UPPER(track_name) = ? OR ? LIKE ('%' || UPPER(track_name) || '%')", (t_key, t_key))
            row = cursor.fetchone()
            if row and row[0]:
                cal_temp = float(max(1.2, min(3.5, row[0])))
                self._track_temp_cache[t_key] = cal_temp
                return cal_temp
        except Exception:
            pass
        self._track_temp_cache[t_key] = 2.4
        return 2.4

    def parse_form_score(self, form_str: Optional[str]) -> float:
        """
        Parses Last-4/Last-5 form strings (e.g. '5552', '86', '2226') using exponential recency weighting.
        The most recent run (rightmost digit) carries the highest weight.
        Points: 1st=10, 2nd=7, 3rd=5, 4th=3, 5th+=0.8
        """
        if not form_str:
            return 2.0  # Neutral baseline for unraced/unknown dogs

        digits = [int(ch) for ch in str(form_str) if ch.isdigit()]
        if not digits:
            return 2.0

        # Weights emphasizing the most recent starts: [oldest -> most recent]
        weight_schedule = [0.8, 1.2, 1.8, 3.0]
        weights = weight_schedule[-len(digits):]

        scores = []
        for d, w in zip(digits, weights):
            if d == 1:
                pts = 10.0
            elif d == 2:
                pts = 7.0
            elif d == 3:
                pts = 5.0
            elif d == 4:
                pts = 3.0
            else:
                pts = 0.8
            scores.append(pts * w)

        return sum(scores) / sum(weights)

    def parse_grade_value(self, grade_str: Optional[str]) -> float:
        """
        Converts a grade string into a numerical value for class transition analysis.
        Lower number = Higher class.
        
        Handles messy real-world formats like:
          '5th Grade 300m', 'Free For All 275m', 'Mixed 4/5 390m', 'NG1-4 324m',
          'Grade 5', '3rd Grade', 'Maiden', 'Restricted Win', 'Bottom Up - Grade 5 400m'
        """
        import re
        if not grade_str:
            return 5.0
        # Strip trailing distance (e.g. '300m', '411m') to avoid false number matches
        g = re.sub(r'\s*\d{2,4}m?\s*$', '', str(grade_str), flags=re.IGNORECASE).upper().strip()
        
        # NG1-4, TG1-4W style (Novice/Trial grades — treat as ~restricted) — check BEFORE G1/G2/G3 patterns
        if re.search(r'(?:NG|TG)\d', g):
            return 6.5
        
        # Top-tier / Group / FFA
        if any(x in g for x in ["GRP", "GROUP", "FFA", "FREE FOR ALL", "BEST 8", "LISTED", "OPEN CLASS"]):
            return 1.0
        if re.search(r'G[123]\b', g) or re.search(r'GROUP\s*[123]', g):
            return 1.0
        
        # Bottom-Up grades (map to their underlying grade)
        bu = re.search(r'BOTTOM.UP.*(MAIDEN|GRADE\s*(\d)|RESTRICTED)', g)
        if bu:
            if "MAIDEN" in bu.group(0): return 8.0
            if "RESTRICTED" in bu.group(0): return 6.5
            num = re.search(r'GRADE\s*(\d)', bu.group(0))
            if num: return float(num.group(1))

        # Named grade numbers: '1st Grade', '2nd Grade', 'Grade 3', '5th Grade'
        named = re.search(r'(?:GRADE\s*(\d)|(\d)(?:ST|ND|RD|TH)\s*GRADE)', g)
        if named:
            n = named.group(1) or named.group(2)
            return float(n)

        # Mixed grades: 'Mixed 4/5', '4/5', '3/4'
        mixed = re.search(r'(\d)\s*/\s*(\d)', g)
        if mixed:
            return (float(mixed.group(1)) + float(mixed.group(2))) / 2.0

        # NG1-4, TG1-4W style (Novice/Trial grades — treat as ~grade 6)
        if re.search(r'(?:NG|TG)\d', g):
            return 6.5
        
        # Maiden / Novice
        if any(x in g for x in ["MDN", "MAIDEN"]):
            return 8.0
        if any(x in g for x in ["NOV", "M1", "M2", "M3", "M4", "M5"]):
            return 7.0
        
        # Restricted Win / R/W / Tier 3
        if any(x in g for x in ["R/W", "RESTRICT", "TIER 3", "T3", "RANK LIMIT"]):
            return 6.5
        
        # Misc
        if "MASTERS" in g or "VET" in g:
            return 4.5
        if "SE" == g.strip() or "SPECIAL EVENT" in g:
            return 3.0
        if "INV" in g:
            return 3.0

        return 5.0  # Default fallback


    def get_dog_profile(self, dog_id: int, target_box: Optional[int] = None, track_name: Optional[str] = None, distance: Optional[int] = None, reference_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculates deep historical sectional metrics, individual box affinity, and track personal bests.
        """
        cursor = self.conn.cursor()

        # 1. Fetch dog master records (PBs, Box records, Profile URL)
        cursor.execute("""
        SELECT dog_name, career_starts, career_wins, career_seconds, career_thirds,
               pb_times_json, box_record_json, profile_url
        FROM greyhounds WHERE id = ?
        """, (dog_id,))
        g_row = cursor.fetchone()

        # 2. Fetch past starts (newest first by ISO run date)
        cursor.execute("""
        SELECT track_code, run_date, run_date_iso, distance, box, weight, finish_pos, race_time, split_1, pir, sp_odds, margin, grade
        FROM past_performances
        WHERE dog_id = ?
        ORDER BY coalesce(run_date_iso, run_date) DESC, id ASC
        LIMIT 15
        """, (dog_id,))
        starts = cursor.fetchall()

        # Parse PB times
        pbs = []
        if g_row and g_row["pb_times_json"]:
            try:
                pbs = json.loads(g_row["pb_times_json"])
            except Exception:
                pbs = []

        # Parse Box records
        box_stats = {}
        if g_row and g_row["box_record_json"]:
            try:
                box_stats = json.loads(g_row["box_record_json"])
            except Exception:
                box_stats = {}

        # Track-specific PB lookup with comprehensive track alias expansion and distance matching
        track_pb = None
        if track_name and pbs:
            t_upper = track_name.upper()
            aliases = [t_upper.replace("PARK", "").replace("STRAIGHT", "").strip()]
            if any(k in t_upper for k in ["CAPALABA", "CAPA"]):
                aliases = ["CAPALABA", "CAPA", "BETDELUXE CAPALABA"]
            elif "Q STRAIGHT" in t_upper or "THE Q STRAIGHT" in t_upper:
                aliases = ["Q STRAIGHT", "LADBROKES Q STRAIGHT", "THE Q STRAIGHT", "PURGA STRAIGHT"]
            elif "LAKESIDE" in t_upper or "Q1" in t_upper:
                aliases = ["LAKESIDE", "Q1 LAKESIDE", "LADBROKES Q1 LAKESIDE", "Q1"]
            elif "PARKLANDS" in t_upper or "Q2" in t_upper:
                aliases = ["PARKLANDS", "Q2 PARKLANDS", "LADBROKES Q2 PARKLANDS", "Q2"]
            elif "RICHMOND" in t_upper:
                if "STRAIGHT" in t_upper:
                    aliases = ["RICHMOND STRAIGHT"]
                else:
                    aliases = ["RICHMOND"]
            elif "HEALESVILLE" in t_upper:
                aliases = ["HEALESVILLE"]
            elif "MURRAY BRIDGE" in t_upper:
                if "STRAIGHT" in t_upper:
                    aliases = ["MURRAY BRIDGE STRAIGHT"]
                else:
                    aliases = ["MURRAY BRIDGE"]

            for pb in pbs:
                pb_track = pb.get("track", "").upper()
                pb_dist_str = pb.get("distance", "")
                dist_ok = True
                if distance and pb_dist_str:
                    d_m = re.findall(r"\d+", pb_dist_str)
                    if d_m:
                        pb_d = int(d_m[0])
                        if abs(pb_d - distance) > (distance * 0.12):
                            dist_ok = False
                if dist_ok and any(a in pb_track or pb_track in a for a in aliases if len(a) >= 2):
                    track_pb = pb.get("best_time")
                    break

        # Individual Dog Box Affinity Factor with Box Kryptonite scaling
        box_affinity = 1.0
        if target_box is not None and box_stats:
            b_info = box_stats.get(str(target_box)) or box_stats.get(target_box)
            if b_info:
                b_starts = b_info.get("starts", 0)
                b_wins = b_info.get("wins", 0)
                if b_starts >= 5 and b_wins == 0:
                    # Verified box kryptonite (e.g. 0 wins in 13 starts from Box 8)
                    # Severe box aversion penalty scaling with sample size: 0.76 down to 0.68
                    box_affinity = max(0.68, 0.78 - (0.01 * min(10, b_starts - 5)))
                elif b_starts >= 2:
                    dog_b_win_rate = b_wins / b_starts
                    career_starts = g_row["career_starts"] or len(starts) or 1
                    career_wins = g_row["career_wins"] or sum(1 for s in starts if str(s["finish_pos"] or "").startswith("1"))
                    career_win_rate = career_wins / max(1, career_starts)
                    
                    rate_diff = dog_b_win_rate - career_win_rate
                    box_affinity = max(0.80, min(1.25, 1.0 + (rate_diff * 0.45)))

        if not starts:
            return {
                "starts_count": 0,
                "avg_split_1": None,
                "median_split_1": None,
                "split_stdev": None,
                "min_split_1": None,
                "median_run_home": None,
                "best_time": None,
                "track_pb": track_pb,
                "box_affinity": box_affinity,
                "win_rate": 0.0,
                "place_rate": 0.0,
                "db_form_score": 0.0,
                "optimal_weight": None,
                "last_weight": None,
                "days_since_last_start": None,
                "career_starts": g_row["career_starts"] if g_row else 0,
                "straight_starts": 0,
                "capalaba_starts": 0
            }

        splits = []
        times = []
        run_homes = []
        
        for s in starts:
            # Timing beams are placed differently at every track, and surfaces vary in speed.
            # We MUST NEVER mix `split_1` or `race_time` from a different track or different distance!
            if distance is not None and s["distance"] is not None:
                if abs(s["distance"] - distance) > 25:
                    continue # Skip times from different race distances (e.g. 488m vs 520m)
                    
            if track_name and "track_code" in s.keys() and s["track_code"]:
                t_code = str(s["track_code"]).upper()[:3]
                if t_code not in track_name.upper():
                    continue # Skip times from different tracks

            if s["split_1"] is not None:
                splits.append(s["split_1"])
            if s["race_time"] is not None:
                times.append(s["race_time"])
            if s["race_time"] is not None and s["split_1"] is not None and s["race_time"] > s["split_1"]:
                run_homes.append(s["race_time"] - s["split_1"])

        wins = sum(1 for s in starts if str(s["finish_pos"] or "").startswith("1"))
        places = sum(1 for s in starts if any(str(s["finish_pos"] or "").startswith(p) for p in ["1", "2", "3"]))

        # Interference Forgiveness via PIR strings
        interference_markers = ['c', 'f', 'v', 'x', 'w']
        form_pts = 0.0
        for s in starts[:5]:
            p_str = str(s["finish_pos"] or "")
            trk_code = str(s["track_code"] if "track_code" in s.keys() else "").upper()
            pir_str = str(s["pir"] or "").lower()
            is_straight_run = any(st in trk_code for st in ["HEA", "CAP", "RBS", "MBS"])
            
            # Did the dog suffer severe interference? (checked severely, fell, carted wide)
            had_excuse = any(marker in pir_str for marker in ['cc', 'f', 'v', 'cx'])
            had_minor_excuse = 'c' in pir_str and not had_excuse

            if p_str.startswith("1"):
                form_pts += 10.0
            elif p_str.startswith("2"):
                form_pts += 7.0
            elif p_str.startswith("3"):
                form_pts += 5.0
            elif p_str.startswith("4"):
                form_pts += 3.0 + (1.5 if had_excuse else 0)
            else:
                if had_excuse:
                    form_pts += 4.5  # Forgive entirely, treat as unplaced but fully excused
                elif had_minor_excuse:
                    form_pts += 2.5
                elif track_name and not any(k in track_name.upper() for k in ["STRAIGHT", "CAPALABA", "HEALESVILLE"]) and is_straight_run:
                    form_pts += 2.2
                else:
                    form_pts += 0.8

        median_split = statistics.median(splits[:5]) if splits else None
        split_stdev = statistics.stdev(splits[:5]) if len(splits[:5]) > 1 else None
        median_rh = statistics.median(run_homes[:5]) if run_homes else None
        
        # Days Since Last Start
        dsls = None
        if starts:
            try:
                date_val = starts[0]["run_date_iso"] if ("run_date_iso" in starts[0].keys() and starts[0]["run_date_iso"]) else starts[0]["run_date"]
                rd_dt = parse_flexible_date(date_val)
                if rd_dt:
                    if reference_date:
                        ref_dt = parse_flexible_date(reference_date)
                    else:
                        ref_dt = datetime.now(AEDT_TZ).replace(tzinfo=None)
                    
                    if ref_dt:
                        diff = (ref_dt.date() - rd_dt.date()).days
                        if diff >= 0:
                            dsls = diff
            except Exception:
                pass

        # Calculate recent grade
        recent_grades = [self.parse_grade_value(s["grade"]) for s in starts[:3] if "grade" in s.keys() and s["grade"]]
        avg_recent_grade = sum(recent_grades) / len(recent_grades) if recent_grades else None

        # Calculate proven stamina (max distance where dog finished Top 4)
        proven_dists = [s["distance"] for s in starts if s["distance"] and str(s["finish_pos"]).startswith(("1", "2", "3", "4"))]
        if not proven_dists:
            proven_dists = [s["distance"] for s in starts if s["distance"]]
        proven_stamina = max(proven_dists) if proven_dists else None

        # Calculate optimal weight corridor (median winning weight, falling back to median historical weight)
        win_weights = [s["weight"] for s in starts if str(s["finish_pos"] or "").startswith("1") and s["weight"] is not None]
        all_weights = [s["weight"] for s in starts if s["weight"] is not None]
        optimal_weight = statistics.median(win_weights) if win_weights else (statistics.median(all_weights) if all_weights else None)

        straight_starts = sum(1 for s in starts if any(st in str(s["track_code"] if "track_code" in s.keys() else "").upper() for st in ["HEA", "CAP", "RBS", "MBS", "QST"]))
        capalaba_starts = sum(1 for s in starts if "CAP" in str(s["track_code"] if "track_code" in s.keys() else "").upper())

        return {
            "starts_count": len(starts),
            "avg_split_1": sum(splits) / len(splits) if splits else None,
            "median_split_1": median_split,
            "split_stdev": split_stdev,
            "min_split_1": min(splits) if splits else None,
            "median_run_home": median_rh,
            "best_time": min(times) if times else None,
            "track_pb": track_pb,
            "box_affinity": box_affinity,
            "win_rate": wins / len(starts),
            "place_rate": places / len(starts),
            "db_form_score": form_pts / max(1, len(starts[:5])),
            "optimal_weight": optimal_weight,
            "last_weight": starts[0]["weight"] if starts else None,
            "days_since_last_start": dsls,
            "career_starts": g_row["career_starts"] if g_row else len(starts),
            "straight_starts": straight_starts,
            "capalaba_starts": capalaba_starts,
            "avg_recent_grade": avg_recent_grade,
            "proven_stamina": proven_stamina
        }

    def calculate_conditional_box_bias(self, box: int, form_score: float, early_speed_rank: int, is_straight: bool = False) -> float:
        """
        Calculates box multiplier with CONDITIONAL GATING and Track Geometry.
        - Circular tracks: Box 1 provides a huge advantage ONLY if the dog has adequate form/speed to hold the rail.
        - Straight tracks (e.g. Q Straight, Healesville): NO turn, NO rail bias. Outside boxes (7 & 8) hold massive clean-air drag advantage!
        """
        if is_straight:
            # Straight track geometry:
            # Outside boxes (7, 8) only hold advantage when accompanied by top early pace!
            # If Box 8 is slower early than inside dogs (speed rank >= 4), it gets marooned wide.
            if box == 8:
                return 1.14 if early_speed_rank <= 2 else (1.06 if early_speed_rank == 3 else 0.98)
            elif box == 7:
                return 1.10 if early_speed_rank <= 3 else 1.02
            elif box == 6:
                return 1.06
            elif box in (1, 2, 3):
                # Inside boxes with elite early speed (rank 1 or 2) command the clear central runway
                return 1.06 if early_speed_rank <= 2 else 0.99
            elif box in (4, 5):
                return 1.00
            return 1.00

        # Baseline track geometry bias for circular tracks
        base_bias = {
            1: 1.25,
            2: 1.10,
            3: 1.00,
            4: 0.90,
            5: 0.88,
            6: 0.92,
            7: 1.02,
            8: 1.05
        }.get(box, 1.0)

        # Conditional gating for Box 1 on circular tracks
        if box == 1:
            if form_score < 1.5:
                # Poor form dog in Box 1 gets swamped: penalize rather than boost
                return 0.85
            elif early_speed_rank > 4:
                # Slow beginner in Box 1 gets crossed over: negate rail bonus
                return 0.95
            elif form_score >= 3.5:
                # Quality runner with rail advantage
                return 1.30

        # Conditional gating for Box 2:
        if box == 2:
            if form_score < 1.5:
                return 0.90
            elif early_speed_rank <= 2:
                # Elite early speed from Box 2 controls the fence directly!
                return 1.15

        # Conditional gating for Box 3:
        # Inside draw command bonus when dog carries top-2 early pace!
        if box == 3:
            if early_speed_rank <= 2:
                return 1.10
            elif form_score < 1.5:
                return 0.92

        # Strict Pace-Gating for Circular Box 7:
        if box == 7:
            if early_speed_rank <= 2 and form_score >= 2.5:
                return 1.15
            elif early_speed_rank <= 3:
                return 1.02
            else:
                return 0.95

        # Strict Pace-Gating for Circular Box 8:
        # Outside box only sweeps cleanly if carrying elite early speed (rank 1 or 2)!
        # If slower than inside runners (rank >= 3), it gets marooned wide around the turn.
        if box == 8:
            if early_speed_rank <= 2 and form_score >= 2.5:
                return 1.18
            elif early_speed_rank <= 3:
                return 1.05
            else:
                return 0.98

        return base_bias

    def predict_race_probabilities(self, race_id: int, hydrate_profiles: bool = False, temperature: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Advanced prediction model combining:
        1. Track geometry (Straight vs Circular turn dynamics)
        2. Reserve reallocation ('into box X' mapping)
        3. Recency-weighted form score
        4. Distance-adjusted early pace weighting
        5. Conditionally gated box bias & best track time
        6. Running style synergy
        7. Calibrated softmax normalization
        8. Trainer Strike Rate Intelligence (Bayesian shrinkage, 3-tier lookup)
        """
        cursor = self.conn.cursor()

        # Instantiate trainer intelligence (builds cache once per call)
        trainer_intel = TrainerIntelligence(self.conn)

        # Fetch race distance, track name, grade, track condition, expert tips, and meeting date
        cursor.execute("SELECT r.distance, m.track_name, r.grade, r.track_condition, r.expert_tips, r.tab_tips, m.meeting_date FROM races r JOIN meetings m ON r.meeting_id = m.id WHERE r.id = ?", (race_id,))
        race_info = cursor.fetchone()
        distance = 400
        track_name = ""
        grade = ""
        track_condition = "Fast"
        expert_tips_list = []
        meeting_date = None
        if race_info:
            raw_dist = race_info["distance"] if (hasattr(race_info, "__getitem__") and isinstance(race_info, sqlite3.Row)) else race_info[0]
            distance = int(raw_dist) if raw_dist is not None else 400
            track_name = race_info["track_name"] if (hasattr(race_info, "__getitem__") and isinstance(race_info, sqlite3.Row)) else (race_info[1] or "")
            grade = race_info["grade"] if (hasattr(race_info, "__getitem__") and isinstance(race_info, sqlite3.Row)) else (race_info[2] or "")
            track_condition = race_info["track_condition"] if (hasattr(race_info, "__getitem__") and isinstance(race_info, sqlite3.Row)) else (race_info[3] or "Fast")
            raw_tips = race_info["expert_tips"] if (hasattr(race_info, "__getitem__") and isinstance(race_info, sqlite3.Row)) else (race_info[4] if len(race_info) > 4 else None)
            meeting_date = race_info["meeting_date"] if (hasattr(race_info, "__getitem__") and isinstance(race_info, sqlite3.Row)) else (race_info[5] if len(race_info) > 5 else None)
            if raw_tips:
                try:
                    expert_tips_list = [int(x.strip()) for x in str(raw_tips).split(",") if x.strip().isdigit()]
                except Exception:
                    expert_tips_list = []

        current_grade_val = self.parse_grade_value(grade)

        # Check real-time meteorological weather observations for this track
        cursor.execute("""
            SELECT temperature, humidity, precipitation, wind_speed, condition_desc, apparent_temperature
            FROM weather_records
            WHERE track_name LIKE ? OR ? LIKE ('%' || track_name || '%')
            ORDER BY id DESC LIMIT 1
        """, (f"%{track_name}%", track_name))
        w_row = cursor.fetchone()
        weather_precip = 0.0
        weather_desc = ""
        weather_temp = 22.0
        apparent_temp = 22.0
        if w_row:
            weather_temp = float((w_row["temperature"] if hasattr(w_row, "__getitem__") and isinstance(w_row, sqlite3.Row) else w_row[0]) or 22.0)
            apparent_temp = float((w_row["apparent_temperature"] if (hasattr(w_row, "__getitem__") and isinstance(w_row, sqlite3.Row) and "apparent_temperature" in w_row.keys()) else (w_row[5] if len(w_row) > 5 else None)) or weather_temp)
            weather_precip = (w_row["precipitation"] if hasattr(w_row, "__getitem__") and isinstance(w_row, sqlite3.Row) else w_row[2]) or 0.0
            weather_desc = str((w_row["condition_desc"] if hasattr(w_row, "__getitem__") and isinstance(w_row, sqlite3.Row) else w_row[4]) or "").upper()

        # Track is wet if official condition is Wet/Slow/Heavy, OR recent rainfall >= 0.2mm, OR weather code is rain/storm
        is_wet_track = (
            any(w in (track_condition or "").upper() for w in ["WET", "SLOW", "HEAVY", "MUD"])
            or (weather_precip >= 0.2)
            or any(r in weather_desc for r in ["RAIN", "DRIZZLE", "SHOWER", "THUNDERSTORM"])
        )

        # Identify restricted-win or maiden races where sectional positioning outweighs form strings
        is_restricted_or_maiden = any(g in (grade or "").upper() for g in ["R/W", "RESTRICTED", "MAIDEN", "MDN", "TIER 3", "GRADE 5 TIER 3"])

        # Identify straight tracks (Capalaba, Q Straight, Healesville, Richmond Straight, Murray Bridge Straight)
        is_straight = any(st in (track_name or "").upper() for st in ["STRAIGHT", "HEALESVILLE", "CAPALABA"])

        # Detect scratched / vacant boxes to calculate lane clearance buffers
        cursor.execute("SELECT box_number FROM race_runners WHERE race_id = ? AND is_scratched = 1", (race_id,))
        scratched_boxes = {row["box_number"] if hasattr(row, "__getitem__") and isinstance(row, sqlite3.Row) else row[0] for row in cursor.fetchall()}

        # Fetch runners (excluding scratched dogs)
        cursor.execute("PRAGMA table_info(race_runners)")
        rr_cols = {col["name"] for col in cursor.fetchall()}
        has_into_box = "into_box" in rr_cols

        into_box_col = "rr.into_box," if has_into_box else ""
        cursor.execute(f"""
        SELECT rr.id, rr.box_number, {into_box_col} rr.running_style, rr.form_string, rr.split_1, rr.best_time,
               rr.sp_odds, rr.sportsbet_odds, rr.sportsbet_place_odds, rr.weight, rr.is_scratched, rr.finish_position, rr.margin, rr.race_time,
               g.id as dog_id, g.dog_name, t.trainer_name
        FROM race_runners rr
        JOIN greyhounds g ON rr.dog_id = g.id
        LEFT JOIN trainers t ON rr.trainer_id = t.id
        WHERE rr.race_id = ? AND (rr.is_scratched = 0 OR rr.is_scratched IS NULL)
        ORDER BY rr.box_number ASC
        """, (race_id,))
        runners = cursor.fetchall()

        if not runners:
            return []

        # Pre-flight hydration: Only trigger web scraper if explicitly requested
        if hydrate_profiles:
            for r in runners:
                if r["dog_id"]:
                    p_check = self.get_dog_profile(r["dog_id"], reference_date=meeting_date)
                    if p_check["starts_count"] == 0:
                        try:
                            from dog_history_collector import DogHistoryCollector
                            collector = DogHistoryCollector()
                            collector.get_dog_dossier_from_db(r["dog_id"])
                        except Exception:
                            pass

        active_boxes = {r["box_number"] for r in runners}
        vacant_or_scratched = (set(range(1, 9)) - active_boxes) | scratched_boxes
        sorted_scratched = sorted([b for b in scratched_boxes if 1 <= b <= 8])

        # 1. First pass: Collect speed metrics, handle reserve box reallocation, and rank early speed
        runner_metrics = []
        allocated_scratched = []
        for r in runners:
            raw_name = r["dog_name"]
            
            # Skip vacant boxes and dummy non-runners
            if any(v in raw_name.upper() for v in ["VACANT", "NO RESERVE", "VACANT BOX"]):
                if r["box_number"] and 1 <= r["box_number"] <= 8:
                    scratched_boxes.add(r["box_number"])
                    vacant_or_scratched.add(r["box_number"])
                    if r["box_number"] not in sorted_scratched:
                        sorted_scratched = sorted(list(scratched_boxes))
                continue

            # Check for reserve into box reallocation
            into_box_match = re.search(r"\(into box (\d+)\)", raw_name, re.IGNORECASE)
            into_b = r["into_box"] if has_into_box and r["into_box"] else None

            if into_b:
                effective_box = into_b
                allocated_scratched.append(into_b)
            elif into_box_match:
                effective_box = int(into_box_match.group(1))
                allocated_scratched.append(effective_box)
            elif r["box_number"] in (9, 10):
                # Official Australian greyhound reserve reallocation rule:
                # Reserve 9 replaces first unallocated scratched box, Reserve 10 replaces next
                avail = [b for b in sorted_scratched if b not in allocated_scratched]
                if avail:
                    effective_box = avail[0]
                    allocated_scratched.append(effective_box)
                else:
                    # Unallocated reserve did not gain a start! Skip runner.
                    continue
            else:
                effective_box = r["box_number"] or 1
            
            # Best time: check db column or extract from name string
            best_time = r["best_time"]
            if not best_time:
                time_m = re.search(r"\b(\d{2}\.\d{2})\b", raw_name)
                if time_m:
                    best_time = float(time_m.group(1))

            # Clean dog name for display
            clean_name = re.sub(r"\(into box \d+\)", "", raw_name, flags=re.IGNORECASE)
            clean_name = re.sub(r"\b(?:NBT|\d{2}\.\d{2})\b", "", clean_name).strip()
            display_name = f"{clean_name} (into Box {effective_box})" if (effective_box != r["box_number"] and r["box_number"] in (9, 10)) else clean_name

            profile = self.get_dog_profile(r["dog_id"], target_box=effective_box, track_name=track_name, distance=distance, reference_date=meeting_date)

            # Form score: use direct Last-4 form string first, fallback to DB history
            form_score = self.parse_form_score(r["form_string"])
            if profile["starts_count"] > 0:
                form_score = (form_score * 0.6) + (profile["db_form_score"] * 0.4)

            # Early split 1: prioritize speedmap split from race card (specifically calibrated for today's track and distance)
            split_1 = r["split_1"]
            if not split_1 and profile.get("median_split_1"):
                med_sp = profile.get("median_split_1")
                # Sanity check: do not mix a 4.10s bend split into a 300m race where the sensor is at 6.60s
                if distance <= 400 and med_sp >= 5.5:
                    split_1 = med_sp
                elif distance > 350 and med_sp >= 4.0:
                    split_1 = med_sp

            # Track PB time: prioritize card best time, fallback to distance-matched track PB
            resolved_best_time = best_time or profile.get("track_pb") or profile.get("best_time")

            # Calculate weight metrics (corridor & variance)
            today_weight = float(r["weight"]) if r["weight"] is not None else (float(profile["last_weight"]) if profile.get("last_weight") is not None else None)
            optimal_weight = float(profile["optimal_weight"]) if profile.get("optimal_weight") is not None else None
            last_weight = float(profile["last_weight"]) if profile.get("last_weight") is not None else None

            weight_var = 0.0
            if r["weight"] and profile.get("last_weight"):
                weight_var = abs(float(r["weight"]) - float(profile["last_weight"]))

            runner_metrics.append({
                "runner_id": r["id"],
                "dog_id": r["dog_id"],
                "box": effective_box,
                "orig_box": r["box_number"],
                "dog_name": display_name,
                "trainer": r["trainer_name"],
                "form_string": r["form_string"],
                "form_score": form_score,
                "split_1": split_1,
                "split_stdev": profile.get("split_stdev"),
                "median_run_home": profile.get("median_run_home"),
                "best_time": resolved_best_time,
                "box_affinity": profile.get("box_affinity", 1.0),
                "running_style": r["running_style"] or "",
                "market_odds": r["sp_odds"],
                "sportsbet_odds": r["sportsbet_odds"],
                "sportsbet_place_odds": r["sportsbet_place_odds"],
                "today_weight": today_weight,
                "optimal_weight": optimal_weight,
                "last_weight": last_weight,
                "weight_var": weight_var,
                    "finish_position": r["finish_position"] if "finish_position" in r.keys() else None,
                    "margin": r["margin"] if "margin" in r.keys() else None,
                    "race_time": r["race_time"] if "race_time" in r.keys() else None,
                "dsls": profile.get("days_since_last_start"),
                "starts_count": profile.get("starts_count", 0),
                "career_starts": profile.get("career_starts", 0),
                "straight_starts": profile.get("straight_starts", 0),
                "capalaba_starts": profile.get("capalaba_starts", 0),
                "place_rate": profile.get("place_rate", 0.0),
                "win_rate": profile.get("win_rate", 0.0),
                "avg_recent_grade": profile.get("avg_recent_grade"),
                "proven_stamina": profile.get("proven_stamina")
            })

        # Assess data quality for this race:
        has_splits_count = sum(1 for r in runner_metrics if r["split_1"] is not None)
        has_form_count = sum(1 for r in runner_metrics if r["form_string"] and r["form_string"].strip() != "")
        total_runners = len(runner_metrics)
        data_is_sparse = (has_splits_count < total_runners * 0.5) or (has_form_count < total_runners * 0.5)

        # Rank split-1 among runners (lower split = faster rank)
        valid_splits = [m["split_1"] for m in runner_metrics if m["split_1"] is not None]
        avg_race_split = sum(valid_splits) / len(valid_splits) if valid_splits else 5.25
        fastest_split = min(valid_splits) if valid_splits else 5.20

        # Rank run-home among runners (lower time = faster rank)
        valid_run_homes = [m["median_run_home"] for m in runner_metrics if m["median_run_home"] is not None]
        fastest_run_home = min(valid_run_homes) if valid_run_homes else 15.0

        for m in runner_metrics:
            if m["split_1"] is not None:
                rank = sum(1 for s in valid_splits if s < m["split_1"]) + 1
            else:
                m["split_1"] = avg_race_split
                rank = 4
            m["speed_rank"] = rank

            if m["median_run_home"] is not None:
                rh_rank = sum(1 for rh in valid_run_homes if rh < m["median_run_home"]) + 1
            else:
                rh_rank = 4
            m["run_home_rank"] = rh_rank

        # 2. Second pass: Calculate Composite Power Score
        # Dynamic distance & track geometry weighting:
        # Dynamic distance & track geometry weighting:
        # On straight tracks: sectional acceleration & top speed dictate outcome
        # On circular short sprints (<=350m): balance 1st split (15m) with whole-race motor & run home (w_speed=0.52, w_form=0.48)
        if is_straight:
            w_speed = 0.65
            w_form = 0.35
        elif distance <= 400:
            w_speed = 0.52
            w_form = 0.48
        elif distance <= 440:
            w_speed = 0.50
            w_form = 0.50
        elif distance >= 550:
            w_speed = 0.40
            w_form = 0.60
        elif is_restricted_or_maiden:
            w_speed = 0.52
            w_form = 0.48
        else:
            w_speed = 0.48
            w_form = 0.52

        if is_wet_track:
            # Emphasize early speed on wet tracks (kickback penalty for backmarkers)
            w_speed = min(0.80, w_speed + 0.12)
            w_form = max(0.20, w_form - 0.12)

        # Check best times among runners
        valid_best_times = [m["best_time"] for m in runner_metrics if m["best_time"]]
        avg_best_time = sum(valid_best_times) / len(valid_best_times) if valid_best_times else None
        min_best_time = min(valid_best_times) if valid_best_times else None

        # Track Debutante Inter-Track Par Proxy (Synthesize missing PBs based on Global Class)
        if avg_best_time is not None:
            valid_grades = [m["avg_recent_grade"] for m in runner_metrics if m.get("avg_recent_grade") is not None]
            avg_race_grade = sum(valid_grades) / len(valid_grades) if valid_grades else 5.0
            
            for m in runner_metrics:
                if m["best_time"] is None:
                    dog_grade = m.get("avg_recent_grade") or 5.0
                    # Lower grade number = better class. If dog_grade < avg_race_grade, dog is dropping in class.
                    grade_delta = avg_race_grade - dog_grade
                    
                    # 1 grade difference = ~0.12s class gap
                    time_adjustment = -(grade_delta * 0.12)
                    
                    # Synthesize PB: Track average + class adjustment + 0.08s "unproven/green" tax
                    m["best_time"] = avg_best_time + time_adjustment + 0.08
                    m["is_synthesized_pb"] = True
                    
            # Recalculate mins after synthesis
            valid_best_times = [m["best_time"] for m in runner_metrics if m["best_time"]]
            min_best_time = min(valid_best_times) if valid_best_times else None

        # Check Box 1 & Box 2 early speed parity (prevents Box 1 monopoly when Box 2 breaks alongside)
        b1 = next((m for m in runner_metrics if m["box"] == 1), None)
        b2 = next((m for m in runner_metrics if m["box"] == 2), None)
        b1_b2_parity = bool(b1 and b2 and b1.get("split_1") and b2.get("split_1") and abs(b1["split_1"] - b2["split_1"]) <= 0.02)

        scored_runners = []
        for m in runner_metrics:
            # Normalized Early Speed Score: lower split relative to average is rewarded
            speed_delta = avg_race_split - m["split_1"]
            split_multiplier = 15.0 if (is_straight or distance <= 400) else 14.0
            speed_score = max(1.0, 5.0 + (speed_delta * split_multiplier))

            delta_to_min = (m["best_time"] - min_best_time) if (m["best_time"] and min_best_time) else 99.0
            # Split-PB Coherence Check (Outlier split floor for proven motors & class):
            if delta_to_min <= 0.15 and m["form_score"] >= 5.0:
                speed_score = max(3.2, speed_score)

            # Continuous Proportional Best Time Bonus (with Form Coherence & Stale PB Dampening)
            best_time_bonus = 1.0
            has_motor_deficit = False
            if m["best_time"] and avg_best_time and min_best_time:
                time_delta = avg_best_time - m["best_time"]
                if is_straight:
                    # On 300m-350m straight sprints, every 0.10s is a length advantage (~1.7m)
                    raw_bonus = time_delta * 0.65
                else:
                    raw_bonus = time_delta * 0.42

                # Form Coherence: If recent form is weak (< 3.5), dampen historical PB bonus
                # (prevents dogs with poor recent form riding a 6-month-old stale PB)
                if raw_bonus > 0 and m["form_score"] < 3.5:
                    raw_bonus *= max(0.40, m["form_score"] / 3.5)

                # Motor Deficit Dampener:
                # If dog is markedly slower than the field's top proven benchmark (>= 0.28s slower):
                if not is_straight and delta_to_min >= 0.28:
                    has_motor_deficit = True
                    # Inside railers (Boxes 1 & 2) save up to 3-4 lengths in running distance around circular bends,
                    # so their physical race time easily outperforms older paper PBs. Cap penalty for inside boxes.
                    max_pen = 0.06 if (m["box"] in (1, 2) and distance >= 400) else 0.20
                    deficit_penalty = min(max_pen, (delta_to_min - 0.20) * 0.50)
                    raw_bonus -= deficit_penalty
                    min_floor = 0.90 if (m["box"] in (1, 2) and distance >= 400) else 0.80
                    best_time_bonus = max(min_floor, min(1.20, 1.0 + raw_bonus))
                elif is_straight:
                    best_time_bonus = max(0.90, min(1.25, 1.0 + raw_bonus))
                else:
                    best_time_bonus = max(0.90, min(1.20, 1.0 + raw_bonus))

                # Benchmark Engine Advantage:
                # If the dog possesses the fastest or near-fastest proven time at this track & distance (within 0.08s of min_best_time):
                # It possesses the superior proven motor on the track, PROVIDED its recent form is not cold!
                if delta_to_min <= 0.08:
                    if m["form_score"] < 3.5:
                        # Cold-form stale PB dampener: do not reward an unplaced, out-of-form dog riding an old fast time
                        pass
                    elif delta_to_min <= 0.03:
                        best_time_bonus *= 1.08
                    else:
                        best_time_bonus *= 1.05

            # Form Score component with Sprint Distance Step-Up Dampener
            form_score = m["form_score"]
            # Form floor for dogs with verified top motor & competitive split (prevents single-run over-penalization):
            # CRITICAL: Only applies if the runner has already shown respectable form (>= 3.8).
            # A dog with cold recent form (< 3.5, e.g. '8526') must NEVER have its poor form erased by an old paper PB!
            if min_best_time and m["best_time"] and (delta_to_min <= 0.20) and (m["split_1"] <= fastest_split + 0.12):
                if 3.8 <= form_score < 4.8:
                    form_score = 4.8

            # Longshot paper form dampener:
            # If a runner is completely disregarded by the market (odds >= $14.00, p_mkt <= 0.07) but has a high form score (>= 5.0)
            # from low-grade / country tracks (e.g. '2512'), deflate paper form to prevent phantom +EV.
            sb_o = m.get("sportsbet_odds") or m.get("market_odds")
            if sb_o and sb_o >= 14.0 and form_score >= 5.0:
                form_score = 4.0 + (form_score - 4.0) * 0.40

            # If stepping up from pure sprint (<= 340m) to a middle-sprint (>= 380m) and card PB is not top-tier:
            if distance >= 380 and m.get("dog_id"):
                try:
                    cursor.execute("SELECT distance FROM past_performances WHERE dog_id = ? ORDER BY coalesce(run_date_iso, run_date) DESC, id ASC LIMIT 3", (m["dog_id"],))
                    rec_dists = [row[0] for row in cursor.fetchall() if row[0]]
                    if rec_dists and (sum(rec_dists) / len(rec_dists) <= 340):
                        if m["best_time"] and min_best_time and (m["best_time"] > min_best_time + 0.20):
                            form_score *= 0.82
                except Exception:
                    pass

            # Perennial Runner-Up (Second-Place Syndrome) Detection:
            f_digits = [int(c) for c in str(m.get("form_string") or "") if c.isdigit()]
            is_second_place_specialist = bool(f_digits and f_digits.count(1) == 0 and f_digits.count(2) >= 2)
            if is_second_place_specialist:
                # High reliability to place, but struggles to finish off the win
                form_score *= 0.88

            # Box Multiplier (Straight track vs Circular track)
            box_factor = self.calculate_conditional_box_bias(m["box"], form_score, m["speed_rank"], is_straight=is_straight)
            if not is_straight and b1_b2_parity:
                if m["box"] == 1 and box_factor > 1.20:
                    box_factor = 1.18  # Reduce monopoly
                elif m["box"] == 2:
                    box_factor = max(1.18, box_factor)  # Elevate Box 2 parity

            # If Box 1 has proven motor and good form, don't drop below 1.18
            if m["box"] == 1 and delta_to_min <= 0.15 and form_score >= 5.0:
                box_factor = max(1.18, box_factor)

            # Geometry calibration for Circular Short Sprints (<= 400m):
            if not is_straight and distance <= 400:
                if m["box"] == 1:
                    # Box 1 holds rail command unless decisively outbroken by > 0.08s.
                    # If split is missing / synthesized, but the dog is well-favored or in strong form,
                    # preserve the golden rail command!
                    sb_o = m.get("sportsbet_odds") or m.get("market_odds")
                    is_favored_box1 = bool((sb_o and sb_o <= 3.50) or (form_score >= 5.0 and m.get("split_1") == avg_race_split))
                    if (m["split_1"] - fastest_split) <= 0.08 or is_favored_box1:
                        box_factor = 1.30
                    else:
                        box_factor = 1.15
                elif m["box"] == 2:
                    # Box 2 drafts rail smoothly
                    if (m["split_1"] - fastest_split) <= 0.12:
                        box_factor = 1.20
                elif m["box"] == 8:
                    # Circular Box 8 can only sweep if carrying elite split AND proven PB!
                    is_elite_pb = bool(min_best_time and m["best_time"] and (delta_to_min <= 0.12))
                    if (m["split_1"] - fastest_split) <= 0.03 and is_elite_pb:
                        box_factor = 1.12
                    else:
                        box_factor = 0.96


            # Track-Specific Metropolitan & Bend Geometry Calibrations:
            track_upper = (track_name or "").upper()
            
            # --- VICTORIA ---
            if "MEADOWS" in track_upper:
                if distance == 525:
                    # Calibrated Meadows 525m: Inside Rail parity (B1: 17.8%, B2: 15.2%) and outside sweep (B8: 14.1%)
                    if m["box"] == 1:
                        box_factor = 1.20 if form_score >= 3.0 else 1.12
                    elif m["box"] == 2:
                        box_factor = 1.15 if form_score >= 3.0 else 1.08
                    elif m["box"] == 8:
                        box_factor = 1.12 if form_score >= 3.0 else 1.06
                    elif m["box"] == 7:
                        box_factor = 1.04
                    elif m["box"] == 5 and m.get("speed_rank", 5) > 2:
                        box_factor = min(0.90, box_factor)
                    elif m["box"] == 4:
                        # Box 4 tracks inside railers smoothly at The Meadows; only discount if tail-end slow
                        if m.get("speed_rank", 5) <= 3:
                            box_factor = max(1.04, box_factor)
                        elif m.get("speed_rank", 5) >= 6:
                            box_factor = min(0.96, box_factor)
                    elif m["box"] == 3 and m.get("speed_rank", 5) > 4:
                        box_factor = min(0.95, box_factor)
                elif distance == 600:
                    # Meadows 600m Back Straight start: Long run into turn, Box 1 & 2 rail advantage
                    if m["box"] in (1, 2):
                        box_factor = max(1.18, box_factor)
                    elif m["box"] in (4, 5):
                        box_factor = min(0.90, box_factor)
                    elif m["box"] == 8:
                        box_factor = max(1.08, box_factor)
            elif "SANDOWN" in track_upper:
                # Premier Melbourne Metropolitan Circuit
                if distance <= 520:  # 515m Flagship Metro Sprint
                    # 180m back-straight charge into sweeping turn 1. Box 1 Golden Rail.
                    if m["box"] == 1: box_factor = max(1.26, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 8: box_factor = max(1.14, box_factor)
                    elif m["box"] == 5:
                        box_factor = min(0.85, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                    elif m["box"] in (4, 6):
                        box_factor = min(0.92, box_factor) if m.get("speed_rank", 5) > 4 else box_factor
                elif distance <= 600:  # 595m Middle Distance
                    # Grandstand start: Box 1 & 2 rail dominance past winning post
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.10, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                elif distance >= 700:  # 715m Marathon Stayers
                    # 2 full laps: stamina and split floors override box position
                    if m["box"] in (1, 2): box_factor = max(1.08, box_factor)

            elif "SHEPPARTON" in track_upper:
                # Fast U-turn / Chute Provincial Oval
                if distance <= 400:  # 385m / 390m Short Course Sprint
                    # Inside rail vs wide clean air runway: Boxes 1 & 8 dominate (54%+ win strike combined)
                    if m["box"] == 1: box_factor = max(1.26, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 8: box_factor = max(1.25, box_factor)
                    elif m["box"] == 7:
                        # Box 7 trapped inside Box 8 sweep
                        box_factor = min(0.84, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                    elif m["box"] in (4, 5, 6):
                        box_factor = min(0.82, box_factor) if m.get("speed_rank", 5) > 2 else min(0.92, box_factor)
                elif distance <= 500:  # 450m Standard Distance
                    # Back straight run into sweeping home bend
                    if m["box"] == 1: box_factor = max(1.22, box_factor)
                    elif m["box"] == 2: box_factor = max(1.16, box_factor)
                    elif m["box"] == 8: box_factor = max(1.18, box_factor)
                    elif m["box"] == 5: box_factor = min(0.86, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                    elif m["box"] in (4, 6): box_factor = min(0.92, box_factor) if m.get("speed_rank", 5) > 4 else box_factor
                else:  # 650m Stayers
                    if m["box"] in (1, 2): box_factor = max(1.14, box_factor)

            elif "BALLARAT" in track_upper:
                # Morshead Park Sharp Two-Turn Circuit
                if distance <= 400:  # 390m Short Sprint
                    # Short run to turn 1: Box 1 dominant rail, Box 8 clear sweep
                    if m["box"] == 1: box_factor = max(1.28, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 8: box_factor = max(1.24, box_factor)
                    elif m["box"] == 7:
                        box_factor = min(0.85, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                    elif m["box"] in (4, 5):
                        # Extreme middle pinch
                        box_factor = min(0.82, box_factor) if m.get("speed_rank", 5) > 2 else min(0.92, box_factor)
                    elif m["box"] in (3, 6):
                        box_factor = min(0.92, box_factor) if m.get("speed_rank", 5) > 4 else box_factor
                elif distance <= 500:  # 450m Standard Distance
                    # Box 1 rail dominance and outside sweepers (Box 7 & 8)
                    if m["box"] == 1: box_factor = max(1.26, box_factor)
                    elif m["box"] == 2: box_factor = max(1.16, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.18, box_factor)
                    elif m["box"] in (4, 5, 6):
                        box_factor = min(0.82, box_factor) if m.get("speed_rank", 5) > 2 else min(0.92, box_factor)
                elif distance <= 600:  # 545m Middle Distance
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.90, box_factor)
                    elif m["box"] == 8: box_factor = max(1.10, box_factor)
                else:  # 660m Stayers
                    if m["box"] in (1, 2): box_factor = max(1.08, box_factor)

            elif "WARRAGUL" in track_upper:
                if m["box"] in (1, 2, 3):
                    box_factor = max(1.18, box_factor)
                elif m["box"] in (7, 8):
                    box_factor = max(1.20, box_factor)
                elif m["box"] in (4, 5, 6):
                    if m.get("speed_rank", 5) <= 2:
                        box_factor = min(0.95, box_factor)
                    else:
                        box_factor = min(0.85, box_factor)
            elif "HORSHAM" in track_upper:
                # Fast sweeping U-turn one-turn provincial circuit
                if distance <= 420:  # 410m Sprint
                    if m["box"] == 1: box_factor = max(1.24, box_factor)
                    elif m["box"] == 2: box_factor = max(1.16, box_factor)
                    elif m["box"] == 8: box_factor = max(1.20, box_factor)
                    elif m["box"] in (4, 5, 6):
                        box_factor = min(0.84, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                else:  # 485m Main Distance
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.15, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
            elif "SALE" in track_upper:
                # Gippsland Two-Turn / Sweeping Oval
                if distance <= 450:  # 435m Sprint
                    if m["box"] == 8: box_factor = max(1.24, box_factor)  # Empirical 36.4% win rate on outer sweep
                    elif m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                    elif m["box"] in (5, 6, 7): box_factor = min(0.85, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                else:  # 510m / 640m
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.15, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
            elif "HEALESVILLE" in track_upper:
                # Victoria Premier Straight Track (300m / 350m)
                # Zero turns: Outside fence (Box 8) and inside rail (Boxes 1 & 2) dominate
                if m["box"] in (1, 2): box_factor = max(1.24, box_factor)
                elif m["box"] == 8: box_factor = max(1.24, box_factor)
                elif m["box"] in (4, 5, 6, 7):
                    box_factor = min(0.82, box_factor) if m.get("speed_rank", 5) > 2 else min(0.92, box_factor)
            elif any(t in track_upper for t in ["WARRNAMBOOL", "GEELONG", "TRARALGON"]):
                if m["box"] in (1, 2, 3):
                    box_factor = max(1.18, box_factor)
                elif m["box"] in (7, 8):
                    box_factor = max(1.15, box_factor)
                elif m["box"] in (4, 5, 6):
                    if m.get("speed_rank", 5) <= 2:
                        box_factor = min(0.95, box_factor)
                    else:
                        box_factor = min(0.86, box_factor)
            
            elif "BENDIGO" in track_upper:
                if distance <= 425:
                    # Extreme Bendigo 425m Bias: Track Geometry is King over raw ability
                    if m["box"] in (1, 2): box_factor = max(1.30, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.15, box_factor)
                    elif m["box"] == 3: box_factor = min(0.90, box_factor) # The Nutcracker Trap
                    elif m["box"] in (4, 5, 6): box_factor = min(0.85, box_factor)
                else:
                    if m["box"] in (1, 2, 3): box_factor = max(1.18, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.15, box_factor)
                    elif m["box"] in (4, 5, 6): box_factor = min(0.92, box_factor)

            # --- NEW SOUTH WALES ---
            elif "GARDENS" in track_upper:
                if distance == 600:
                    # The Gardens 600m: Chute start with short run (~85m) directly into bend.
                    # Middle and outside dogs crash hard to the rail.
                    # Box 1 is an extreme Squeeze Trap unless it possesses standalone early pace!
                    if m["box"] == 1:
                        if m.get("speed_rank", 5) <= 2:
                            box_factor = max(1.15, box_factor)
                        else:
                            box_factor = min(0.85, box_factor)
                    elif m["box"] == 2:
                        box_factor = min(0.90, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                    elif m["box"] in (6, 7, 8):
                        # Wide runners have clear camber runway to swoop around the rail congestion
                        box_factor = max(1.12, box_factor)
                    elif m["box"] in (4, 5):
                        box_factor = min(0.92, box_factor)
                elif distance <= 400: # 272m and 400m
                    # Back straight chute for 400m / sprint chute for 272m: Box 1 and 2 rail command
                    if m["box"] in (1, 2): box_factor = max(1.22, box_factor)
                    elif m["box"] == 8: box_factor = max(1.10, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.90, box_factor)
                elif distance <= 515: # 515m
                    # Standard 2-turn layout: strong rail advantage
                    if m["box"] == 1: box_factor = max(1.24, box_factor)
                    elif m["box"] == 2: box_factor = max(1.15, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.90, box_factor)
            elif "WENTWORTH PARK" in track_upper:
                if distance <= 350: # 280m
                    if m["box"] in (1, 2): box_factor = max(1.24, box_factor)
                    elif m["box"] == 3: box_factor = max(1.10, box_factor)
                    elif m["box"] in (4, 5, 6): box_factor = min(0.94, box_factor) if m.get("speed_rank", 5) <= 2 else min(0.85, box_factor)
                    elif m["box"] == 8 and m.get("speed_rank", 5) <= 3: box_factor = max(1.10, box_factor)
                elif distance <= 600: # 520m
                    if m["box"] == 1: box_factor = max(1.28, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 3 and m.get("speed_rank", 5) <= 3: box_factor = max(1.08, box_factor)
                    elif m["box"] == 4:
                        # Box 4 at WP has straight run to turn; not an extreme trap like Box 5
                        box_factor = max(1.02, box_factor) if m.get("speed_rank", 5) <= 3 else min(0.98, box_factor)
                    elif m["box"] == 5:
                        # Box 5 is the primary middle squeeze trap
                        box_factor = min(0.90, box_factor) if m.get("speed_rank", 5) <= 2 else min(0.84, box_factor)
                    elif m["box"] == 6:
                        box_factor = min(0.96, box_factor) if m.get("speed_rank", 5) <= 2 else min(0.90, box_factor)
                    elif m["box"] == 7:
                        if m.get("speed_rank", 5) <= 2: box_factor = max(1.06, box_factor)
                        elif m.get("speed_rank", 5) >= 5: box_factor = min(0.95, box_factor)
                    elif m["box"] == 8:
                        if m.get("speed_rank", 5) <= 3: box_factor = max(1.10, box_factor)
            elif "DUBBO" in track_upper:
                if is_restricted_or_maiden:
                    # Dubbo Maiden Carnage Override: Inside boxes are a trap, middle boxes swoop
                    if m["box"] in (1, 2): box_factor = min(0.88, box_factor) 
                    elif m["box"] in (3, 4, 5, 6): box_factor = max(1.22, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.08, box_factor)
                    # For maidens, we also want to reward highly rated trainers over box draws.
                    if m.get("trainer_factor", 1.0) > 1.05:
                        box_factor *= 1.15
                else:
                    if distance <= 350:  # 318m Sprint
                        if m["box"] == 1: box_factor = max(1.24, box_factor)
                        elif m["box"] in (2, 8): box_factor = max(1.15, box_factor)
                        elif m["box"] == 5: box_factor = min(0.88, box_factor)
                        elif m["box"] in (4, 6): box_factor = max(1.02, box_factor) if m.get("speed_rank", 5) <= 3 else min(0.95, box_factor)
                    elif distance <= 450:  # 400m
                        if m["box"] == 1: box_factor = max(1.20, box_factor)
                        elif m["box"] == 2: box_factor = max(1.14, box_factor)
                        elif m["box"] in (7, 8): box_factor = max(1.08, box_factor)
                        elif m["box"] == 5: box_factor = min(0.90, box_factor)
                        elif m["box"] in (4, 6): box_factor = min(0.96, box_factor) if m.get("speed_rank", 5) > 4 else box_factor
                    elif distance >= 500:  # 516m Back-straight start (long 150m run)
                        if m["box"] in (1, 2): box_factor = max(1.16, box_factor)
                        elif m["box"] in (3, 4, 6): box_factor = max(1.02, box_factor)
                        elif m["box"] == 8: box_factor = max(1.08, box_factor)
            elif "TAREE" in track_upper:
                # Extremely strong inside bias at Taree, especially the 300m and 392m starts.
                # Box 2 is a massive winning trap. Middle boxes (4, 5) get totally swamped.
                if distance <= 392:
                    if m["box"] == 2: box_factor = max(1.40, box_factor)
                    elif m["box"] == 1: box_factor = max(1.30, box_factor)
                    elif m["box"] == 3: box_factor = max(1.15, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.80, box_factor) if m.get("speed_rank", 5) > 2 else min(0.92, box_factor)
                    elif m["box"] in (6, 7): box_factor = max(1.08, box_factor) if m.get("speed_rank", 5) <= 3 else min(0.95, box_factor)
                    elif m["box"] == 8: box_factor = min(0.92, box_factor) if m.get("speed_rank", 5) > 3 else max(1.02, box_factor)
                else:
                    if m["box"] in (1, 2): box_factor = max(1.22, box_factor)
                    elif m["box"] in (4, 5, 6): box_factor = min(0.85, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
            elif "CASINO" in track_upper:
                if distance <= 411:
                    if m["box"] in (1, 2, 3) and m["speed_rank"] <= 2: box_factor = max(1.28, box_factor)
                    elif m["box"] in (6, 7, 8) and m["speed_rank"] > 3: box_factor = min(0.84, box_factor)
            elif "NOWRA" in track_upper:
                # Tight South Coast NSW loop
                if distance <= 400:  # 365m Sprint
                    # Short rush to turn 1. Inside railers dominate, wide runners flung wide
                    if m["box"] in (1, 2): box_factor = max(1.26, box_factor)
                    elif m["box"] == 3: box_factor = max(1.12, box_factor)
                    elif m["box"] in (6, 7, 8):
                        box_factor = min(0.82, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                else:  # 520m / 630m
                    if m["box"] in (1, 2): box_factor = max(1.22, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor)
            elif "RICHMOND" in track_upper and not "STRAIGHT" in track_upper:
                # Western Sydney Circular Track
                if distance <= 410:  # 320m / 401m
                    if m["box"] in (1, 2): box_factor = max(1.22, box_factor)
                    elif m["box"] == 8: box_factor = max(1.18, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.86, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                else:  # 520m / 622m
                    if m["box"] == 1: box_factor = max(1.26, box_factor)
                    elif m["box"] == 2: box_factor = max(1.16, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.86, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
            elif "RICHMOND STRAIGHT" in track_upper:
                # 324m Straight Track
                if m["box"] in (1, 8): box_factor = max(1.22, box_factor)
                elif m["box"] in (4, 5): box_factor = min(0.86, box_factor)
            elif "BULLI" in track_upper:
                # One-turn sweeping track. Very fair. Middle boxes perform better here than tight tracks.
                if m["box"] in (1, 2): box_factor = max(1.15, box_factor)
                elif m["box"] == 8: box_factor = max(1.12, box_factor)
                elif m["box"] in (4, 5, 6) and m.get("speed_rank", 5) <= 3: box_factor = max(1.05, box_factor)
            elif "MAITLAND" in track_upper:
                # One-turn U-shape. Extremely fast. Early speed dominates, rail is excellent.
                if m["box"] in (1, 2) and m.get("speed_rank", 5) <= 3: box_factor = max(1.25, box_factor)
                elif m["box"] in (6, 7, 8) and m.get("speed_rank", 5) > 3: box_factor = min(0.88, box_factor)
            elif "DAPTO" in track_upper:
                if m["box"] == 1: box_factor = max(1.22, box_factor)
                elif m["box"] in (4, 5, 6): box_factor = min(0.90, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
            elif "GUNNEDAH" in track_upper:
                # Country NSW Provincial Circuit
                if distance <= 350:  # 340m Sprint
                    if m["box"] == 1: box_factor = max(1.26, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 8: box_factor = max(1.14, box_factor)
                    elif m["box"] in (4, 5, 6):
                        box_factor = min(0.85, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                else:  # 431m / 527m
                    if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                    elif m["box"] in (5, 6): box_factor = min(0.88, box_factor)
            elif "TEMORA" in track_upper:
                # Riverina NSW Provincial
                if distance <= 350:  # 330m Sprint
                    if m["box"] in (1, 2): box_factor = max(1.26, box_factor)
                    elif m["box"] == 8: box_factor = max(1.14, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.84, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                else:  # 457m / 570m
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.15, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor)
            elif any(t in track_upper for t in ["GRAFTON", "GOSFORD", "GOULBURN", "WAGGA"]):
                if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                elif m["box"] in (5, 6): box_factor = min(0.88, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
            
            # --- QUEENSLAND ---
            elif "CAPALABA" in track_upper:
                # Queensland Premier 366m Straight Track
                # Guide rails on Box 1 & 8 dictate clean galloping lines
                if m["box"] in (1, 8): box_factor = max(1.22, box_factor)
                elif m["box"] in (4, 5): box_factor = min(0.86, box_factor)
            elif "Q STRAIGHT" in track_upper:
                # 350m straight track at Purga QLD. Guide rail dynamics:
                if m["box"] in (1, 8):
                    box_factor = max(1.22, box_factor)
                elif m["box"] in (4, 5):
                    box_factor = min(0.88, box_factor)
            elif "Q1 LAKESIDE" in track_upper:
                # Brand new wide-radius 2-turn track (Greater Brisbane)
                # Built for safety, so massive sweeping turns favour the rail but allow outside sweeps.
                if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                elif m["box"] in (7, 8): box_factor = max(1.12, box_factor)
                elif m["box"] in (4, 5, 6): box_factor = min(0.92, box_factor) if m.get("speed_rank", 5) > 3 else max(0.98, box_factor)
            elif "Q2 PARKLANDS" in track_upper:
                # The Q One-Turn Circuit (Purga QLD)
                if distance <= 530:  # 350m / 457m / 520m
                    # One-turn sweeping circuit: Early speed to the fence dictates outcome
                    if m["box"] == 1: box_factor = max(1.25, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 8: box_factor = max(1.14, box_factor)
                    elif m["box"] in (5, 6):
                        box_factor = min(0.86, box_factor) if m.get("speed_rank", 5) > 2 else min(0.94, box_factor)
                else:  # 600m / 710m Stayers
                    if m["box"] in (1, 2): box_factor = max(1.14, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.10, box_factor)
            elif "ALBION PARK" in track_upper:
                if distance <= 331:
                    # Very short run to turn. Box 1 is an absolute trap if slow, but massive advantage if fast.
                    if m["box"] == 1: box_factor = max(1.30, box_factor) if m.get("speed_rank", 5) <= 3 else min(0.90, box_factor)
                    elif m["box"] in (6, 7, 8): box_factor = min(0.85, box_factor) if m.get("speed_rank", 5) > 3 else max(1.05, box_factor)
                elif distance == 395:
                    if m["box"] == 1: box_factor = max(1.25, box_factor)
                    elif m["box"] == 8: box_factor = max(1.15, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor)
                elif distance >= 520:
                    # Fair starting position
                    if m["box"] == 1: box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
            elif "IPSWICH" in track_upper:
                if distance == 288:
                    if m["box"] in (1, 2, 3): box_factor = max(1.22, box_factor)
                    elif m["box"] in (6, 7, 8): box_factor = min(0.88, box_factor)
                elif distance == 431:
                    # Notorious for Box 8 advantage (weird starting angle provides a dream wide run)
                    if m["box"] == 8: box_factor = max(1.28, box_factor)
                    elif m["box"] == 1: box_factor = max(1.20, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.85, box_factor)
                else:
                    if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                    elif m["box"] in (5, 6): box_factor = min(0.90, box_factor)
            elif "ROCKHAMPTON" in track_upper:
                # Callaghan Park (Rockhampton QLD)
                if distance <= 420:  # 407m Sprint: severe cornering radius and high rail bias
                    if m["box"] == 1: box_factor = max(1.30, box_factor)
                    elif m["box"] == 2: box_factor = max(1.20, box_factor)
                    elif m["box"] == 3 and m.get("speed_rank", 5) <= 3: box_factor = max(1.12, box_factor)
                    elif m["box"] in (5, 6, 8):
                        box_factor = min(0.82, box_factor) if m.get("speed_rank", 5) > 3 else min(0.90, box_factor)
                    elif m["box"] in (4, 7): box_factor = min(0.92, box_factor)
                elif distance <= 550:  # 510m
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
                    elif m["box"] in (4, 5, 6): box_factor = min(0.88, box_factor)
                else:
                    if m["box"] in (1, 2): box_factor = max(1.15, box_factor)
                    elif m["box"] in (5, 6): box_factor = min(0.92, box_factor)
            elif any(t in track_upper for t in ["TOWNSVILLE", "BUNDABERG"]):
                if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                elif m["box"] in (4, 5, 6): box_factor = min(0.92, box_factor)
            
            # --- SOUTH AUSTRALIA ---
            elif "ANGLE PARK" in track_upper:
                # Adelaide Premier Metropolitan Circuit (2021 Rebuilt Track)
                if distance <= 350:  # 342m Super Sprint
                    # Treacherous ~70m dash to turn 1. Extreme rail bias on Boxes 1 & 2
                    if m["box"] == 1: box_factor = max(1.30, box_factor)
                    elif m["box"] == 2: box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.15, box_factor)
                    elif m["box"] in (6, 7):
                        # Centrifugal swing penalty around sharp first corner
                        box_factor = min(0.80, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                    elif m["box"] in (4, 5):
                        box_factor = min(0.85, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                elif distance <= 550:  # 530m Flagship Metropolitan
                    # Grandstand start: Box 1 "Red Rug Royalty" (~21% win rate), Box 2 & 3 inside tracking
                    if m["box"] == 1: box_factor = max(1.28, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 3 and m.get("speed_rank", 5) <= 3: box_factor = max(1.08, box_factor)
                    elif m["box"] == 8: box_factor = max(1.15, box_factor)
                    elif m["box"] in (5, 6):
                        # Severe mid-track squeeze trap (Boxes 5 & 6)
                        box_factor = min(0.84, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                    elif m["box"] == 4:
                        box_factor = min(0.90, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                elif distance <= 650:  # 595m Middle Distance
                    # Back-straight start into turn. Fairer run-up.
                    if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                    elif m["box"] == 8: box_factor = max(1.10, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.90, box_factor) if m.get("speed_rank", 5) > 3 else box_factor
                else:  # 730m Distance Championship
                    # 2 laps: split floors and stamina override draw position
                    if m["box"] in (1, 2): box_factor = max(1.08, box_factor)
            elif "GAWLER" in track_upper:
                # Gawler SA: 400m / 531m / 643m
                if distance <= 420:  # 400m sprint: inside rail control and box 8 outer swoop
                    if m["box"] == 1: box_factor = max(1.25, box_factor)
                    elif m["box"] == 2: box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.18, box_factor)
                    elif m["box"] in (4, 5, 6):
                        box_factor = min(0.84, box_factor) if m.get("speed_rank", 5) > 2 else min(0.92, box_factor)
                elif distance <= 550:  # 531m
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.14, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor)
                else:  # 643m stayers
                    if m["box"] in (1, 2): box_factor = max(1.12, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.08, box_factor)
            elif "MOUNT GAMBIER" in track_upper or "MT GAMBIER" in track_upper:
                # Tara Raceway (Mount Gambier SA)
                if distance <= 320:  # 305m super sprint: shortest run to turn in SA (empirical 50% box 1 win rate)
                    if m["box"] == 1: box_factor = max(1.32, box_factor)
                    elif m["box"] == 2: box_factor = max(1.22, box_factor)
                    elif m["box"] == 3 and m.get("speed_rank", 5) <= 3: box_factor = max(1.10, box_factor)
                    elif m["box"] in (6, 7, 8):
                        box_factor = min(0.82, box_factor) if m.get("speed_rank", 5) > 2 else min(0.92, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.86, box_factor)
                elif distance <= 420:  # 400m short course
                    if m["box"] == 1: box_factor = max(1.22, box_factor)
                    elif m["box"] == 2: box_factor = max(1.16, box_factor)
                    elif m["box"] == 8: box_factor = max(1.14, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor)
                elif distance <= 550:  # 512m
                    if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
                    elif m["box"] in (4, 5, 6): box_factor = min(0.90, box_factor)
                else:  # 600m stayers
                    if m["box"] in (1, 2): box_factor = max(1.12, box_factor)
            elif "MURRAY BRIDGE" in track_upper and not "STRAIGHT" in track_upper:
                if m["box"] in (1, 2): box_factor = max(1.15, box_factor)
                elif m["box"] in (5, 6): box_factor = min(0.90, box_factor)
            
            # --- WESTERN AUSTRALIA ---
            elif "CANNINGTON" in track_upper:
                if distance <= 300:  # 275m Super Sprint
                    # Extreme inside rail skew from home straight chute
                    if m["box"] == 1: box_factor = max(1.30, box_factor)
                    elif m["box"] == 2: box_factor = max(1.20, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.86, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                elif distance <= 400:  # 380m / 381m Short Course
                    # Chute start: inside rail dominance and clean outside sweep
                    if m["box"] == 1: box_factor = max(1.22, box_factor)
                    elif m["box"] == 2: box_factor = max(1.16, box_factor)
                    elif m["box"] == 8: box_factor = max(1.12, box_factor)
                    elif m["box"] == 5: box_factor = min(0.90, box_factor)
                    elif m["box"] in (4, 6): box_factor = min(0.95, box_factor) if m.get("speed_rank", 5) > 4 else box_factor
                elif distance <= 550:  # 520m Premier Metropolitan
                    # 80m dash to turn 1 in front of grandstand. Box 1 is the Golden Box (~18.5% win rate)
                    if m["box"] == 1: box_factor = max(1.28, box_factor)
                    elif m["box"] == 2: box_factor = max(1.18, box_factor)
                    elif m["box"] == 3 and m.get("speed_rank", 5) <= 3: box_factor = max(1.06, box_factor)
                    elif m["box"] == 8: box_factor = max(1.15, box_factor)
                    elif m["box"] == 5:
                        # Box 5 is the primary middle squeeze trap
                        box_factor = min(0.88, box_factor)
                    elif m["box"] in (4, 6):
                        box_factor = min(0.95, box_factor) if m.get("speed_rank", 5) > 4 else box_factor
                elif distance <= 650:  # 601m Middle Distance
                    # Back-straight start with a long run into home turn. Middle boxes are unpinched!
                    if m["box"] in (1, 2): box_factor = max(1.16, box_factor)
                    elif m["box"] in (3, 4, 6): box_factor = max(1.02, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.08, box_factor)
                elif distance >= 700:  # 715m Marathon Stayers
                    # 1.5 laps: pure motor and stamina override draw position
                    if m["box"] in (1, 2): box_factor = max(1.08, box_factor)
            elif "MANDURAH" in track_upper:
                if distance <= 320:
                    if m["box"] == 1: box_factor = max(1.18, box_factor) if m.get("speed_rank", 5) <= 3 and not has_motor_deficit else min(0.95, box_factor)
                    elif m["box"] == 2: box_factor = max(1.14, box_factor) if m.get("speed_rank", 5) <= 3 and not has_motor_deficit else min(0.96, box_factor)
                    elif m["box"] == 3: box_factor = max(1.10, box_factor) if m.get("speed_rank", 5) <= 3 else box_factor
                    elif m["box"] in (4, 5): box_factor = max(1.02, box_factor) if m.get("speed_rank", 5) <= 2 else min(0.94, box_factor)
                    elif m["box"] in (6, 7): box_factor = max(1.06, box_factor) if m.get("speed_rank", 5) <= 4 else min(0.96, box_factor)
                    elif m["box"] == 8: box_factor = max(1.16, box_factor)
                elif distance <= 420:
                    if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
                    elif m["box"] in (4, 5): box_factor = max(1.04, box_factor) if m.get("speed_rank", 5) <= 3 else min(0.92, box_factor)
                    elif m["box"] in (7, 8): box_factor = max(1.08, box_factor) if m.get("speed_rank", 5) <= 3 else box_factor
                    elif m["box"] == 6: box_factor = min(0.92, box_factor)
                elif distance >= 480:
                    if m["box"] in (1, 2): box_factor = max(1.22, box_factor)
                    elif m["box"] in (5, 6): box_factor = min(0.88, box_factor)
            elif "NORTHAM" in track_upper:
                if distance == 509:
                    if m["box"] in (1, 8): box_factor = max(1.20, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.88, box_factor)
                else:
                    if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
            
            # --- TASMANIA ---
            elif "HOBART" in track_upper:
                if distance <= 360:
                    if m["box"] == 8: box_factor = max(1.22, box_factor)
                    elif m["box"] in (1, 2): box_factor = max(1.12, box_factor) if m.get("speed_rank", 5) <= 3 and not has_motor_deficit else min(0.94, box_factor)
                    elif m["box"] in (3, 4, 5, 6): box_factor = min(0.88, box_factor) if m.get("speed_rank", 5) > 2 else box_factor
                elif distance >= 460:
                    if m["box"] in (1, 2): box_factor = max(1.20, box_factor)
                    elif m["box"] in (6, 7, 8): box_factor = max(1.06, box_factor) if m.get("speed_rank", 5) <= 3 else min(0.95, box_factor)
                    elif m["box"] in (4, 5): box_factor = min(0.86, box_factor)
            elif "LAUNCESTON" in track_upper:
                if distance <= 278:
                    if m["box"] in (1, 2): box_factor = max(1.25, box_factor)
                    elif m["box"] in (6, 7, 8) and m.get("speed_rank", 5) > 3: box_factor = min(0.85, box_factor)
                else:
                    if m["box"] in (1, 2, 3): box_factor = max(1.15, box_factor)
                    elif m["box"] in (5, 6): box_factor = min(0.90, box_factor)
            elif "DEVONPORT" in track_upper:
                if m["box"] in (1, 2): box_factor = max(1.18, box_factor)
            # 2. Universal Straight Track Bias
            if is_straight or any(t in track_upper for t in ["HEALESVILLE", "CAPALABA", "RICHMOND STRAIGHT", "MURRAY BRIDGE STRAIGHT", "Q STRAIGHT"]):
                if "CAPALABA" in track_upper:
                    # Capalaba 366m Grass Straight: Outside Lure mounted along the grandstand fence (Box 8).
                    # Outside boxes (7, 8) enjoy direct sightlines and unimpeded straight line along the lure rail.
                    # Box 6 also has clean outer runway air.
                    # Box 1 does NOT enjoy circular rail advantage (the lure is on the opposite fence 25m away).
                    if m["box"] in (7, 8):
                        box_factor = max(1.18, box_factor)
                    elif m["box"] == 6:
                        box_factor = max(1.08, box_factor)
                    elif m["box"] in (1, 2):
                        box_factor = min(0.96, box_factor)
                    elif m["box"] in (4, 5):
                        # Middle pinch mitigated if runner has top-2 proven track motor
                        is_top_engine = bool(min_best_time and m.get("best_time") and m["best_time"] <= min_best_time + 0.35)
                        if is_top_engine:
                            box_factor = max(1.02, box_factor)
                        else:
                            box_factor = min(0.94, box_factor)
                else:
                    # Healesville, Richmond Straight, Murray Bridge Straight:
                    # Inside rail (Box 1) and outside fence (Box 8) provide visual guide rails.
                    if m["box"] in (1, 8):
                        box_factor = max(1.15, box_factor)
                    elif m["box"] in (4, 5):
                        box_factor = min(0.92, box_factor)

                # Straight Track Debutante / Circular-to-Straight Transition Check:
                # Dogs with extensive circular career starts (>=3) but ZERO straight starts often race greenly
                # without physical bends to guide their momentum.
                if m.get("straight_starts", 0) == 0 and m.get("career_starts", 0) >= 3:
                    box_factor = min(0.90, box_factor * 0.90)


            # Maiden / Sparse Field Geometry Dynamics
            if data_is_sparse and not is_straight:
                # Check for specific track geometries that completely break normal Maiden physics
                if any(t in track_upper for t in ["MEADOWS", "SANDOWN", "Q1 LAKESIDE"]):
                    # Big sweeping camber tracks: Outside dogs get a massive slingshot advantage 
                    # in maidens because they avoid the chaotic inside crush.
                    if m["box"] in (6, 7, 8):
                        box_factor = max(1.15, box_factor * 1.15)
                    elif m["box"] in (1, 2):
                        box_factor = min(0.95, box_factor * 0.90)
                elif "DUBBO" in track_upper:
                    pass # Dubbo Maiden override is handled heavily in the track-specific block
                else:
                    # Tight standard turns: The rail provides a physical guiding barrier.
                    # Wide-drawn young dogs run erratically and swing wide into the fence.
                    if m["box"] in (1, 2, 3):
                        box_factor = max(1.15, box_factor * 1.10)
                    elif m["box"] in (6, 7, 8):
                        box_factor = min(0.90, box_factor * 0.90)
            # If dog has severe motor deficit, it cannot exploit rail box bonus down the straight
            if has_motor_deficit and not is_straight and m["box"] in (1, 2):
                box_factor = min(1.08, box_factor)

            # Proven Same-Box Winner Trap Mitigation:
            # When a runner drew Box 4, 5, or 6 but has won from this exact box recently,
            # it has proven trap competency and exits without traffic hesitation:
            if m.get("dog_id") and m["box"] in (4, 5, 6):
                try:
                    cursor.execute("SELECT finish_pos, box FROM past_performances WHERE dog_id = ? ORDER BY coalesce(run_date_iso, run_date) DESC, id ASC LIMIT 5", (m["dog_id"],))
                    rec_starts = [dict(row) for row in cursor.fetchall()]
                    if any(str(st["finish_pos"]).startswith("1") and st["box"] == m["box"] for st in rec_starts):
                        box_factor = max(0.96, box_factor)
                except Exception:
                    pass

            # Style Match Bonus: Supports RR (Hard Railer), FR (Fast Railer), ER (Early Railer), RAIL, WIDE, WM
            style_bonus = 1.0
            style = m.get("running_style", "").upper()
            is_railer = any(rk in style for rk in ["RR", "FR", "ER", "RAIL"])
            is_wide = any(rk in style for rk in ["WIDE", "WM"])
            
            if is_straight:
                # On straight tracks: Wide runners drawn wide get huge clean air.
                # Railers drawn wide lose ground running diagonally, UNLESS they have elite early speed to cross cleanly.
                if is_wide and m["box"] in (7, 8):
                    style_bonus = 1.12
                elif is_wide and m["box"] in (1, 2, 3):
                    style_bonus = 0.90 # Will veer right across the pack
                elif is_railer and m["box"] in (7, 8):
                    if m["speed_rank"] <= 3:
                        # Elite speed allows them to slice across and dominate the rail
                        style_bonus = 1.08
                    else:
                        # Slow railer drawn wide gets marooned and runs a long diagonal
                        style_bonus = 0.85
                elif is_railer and m["box"] in (1, 2):
                    style_bonus = 1.10
            else:
                # Circular Tracks
                if is_railer:
                    if m["box"] in (1, 2):
                        style_bonus = 1.15
                    elif m["box"] in (4, 5, 6):
                        if m["speed_rank"] <= 3:
                            style_bonus = 0.98 # Has early speed to cross and find the rail safely
                        else:
                            style_bonus = 0.90 # Trapped in squeeze zone cutting across
                    elif m["box"] in (7, 8):
                        # Contextual Wide Railer Squeeze
                        inside_railer_conflict = False
                        if m.get("split_1"):
                            for other in runner_metrics:
                                if other["box"] in (4, 5, 6, 7) and other["box"] < m["box"]:
                                    other_style = other.get("running_style", "").upper()
                                    other_is_railer = any(rk in other_style for rk in ["RR", "FR", "ER", "RAIL"])
                                    if other_is_railer and other.get("split_1") and abs(other["split_1"] - m["split_1"]) <= 0.15:
                                        inside_railer_conflict = True
                                        break
                        
                        if inside_railer_conflict:
                            style_bonus = 0.80 # Mathematically doomed: will collide with inside railer crashing left
                        elif m["speed_rank"] <= 2:
                            style_bonus = 0.95
                        else:
                            style_bonus = 0.86
                elif is_wide:
                    if m["box"] in (7, 8):
                        style_bonus = 1.15
                    elif m["box"] in (1, 2):
                        style_bonus = 0.88

            # Composite Power Score
            base_power = (speed_score * w_speed) + (form_score * w_form)

            # --- BIOLOGICAL & PREPARATION MODIFIERS ---
            # 1. Canine Weight Corridors & Kinetic Inertia Modifier
            # Deviation from dog's historical median winning weight corridor
            today_w = m.get("today_weight")
            opt_w = m.get("optimal_weight")
            last_w = m.get("last_weight")
            if today_w and opt_w:
                delta_w = today_w - opt_w
                if delta_w >= 1.0:
                    # Excess mass increases kinetic inertia around turns, forcing wide drift
                    weight_factor = max(0.92, 1.0 - (delta_w - 0.7) * 0.05)
                    base_power *= weight_factor
                elif delta_w <= -1.2:
                    # Severe weight drop indicates loss of condition, dehydration or muscle wasting
                    weight_factor = max(0.94, 1.0 - (abs(delta_w) - 1.0) * 0.05)
                    base_power *= weight_factor
                elif abs(delta_w) <= 0.4:
                    # Dog is in prime peak winning physical corridor
                    base_power *= 1.02

            # Rapid weight fluctuation from immediate prior start
            if today_w and last_w:
                run_diff = abs(today_w - last_w)
                if run_diff >= 1.2:
                    base_power *= 0.92
                elif run_diff >= 0.8:
                    base_power *= 0.96
            elif m.get("weight_var", 0.0) >= 1.2:
                base_power *= 0.92
            elif m.get("weight_var", 0.0) >= 0.8:
                base_power *= 0.96

            # 2. Days Since Last Start (Freshness vs Fatigue)
            dsls = m.get("dsls")
            if dsls is not None:
                if dsls <= 3:
                    # Quick backup fatigue
                    base_power *= 0.92
                elif dsls >= 60:
                    # Returning from spell / lacking match fitness
                    base_power *= 0.85
                elif dsls >= 35:
                    # Slightly stale
                    base_power *= 0.94

            # 3. Career Upside (Young dogs vs Veterans)
            starts_count = m.get("career_starts", 0)
            if starts_count > 0 and starts_count <= 8 and m.get("best_time") and min_best_time and (m["best_time"] <= min_best_time + 0.15):
                # Young dog with huge upside potential running competitive times
                base_power *= 1.08
            elif starts_count >= 75:
                # Aging veteran (no upside, physically declining)
                base_power *= 0.95

            # 4. Micro-Sectional Velocity Profiling (Complete Engine vs Stoppers vs Swoopers)
            # Profile A: Complete Engine (Top 3 Early Speed AND Top 3 Run Home Finish)
            if m.get("speed_rank", 5) <= 3 and m.get("run_home_rank", 5) <= 3:
                base_power *= 1.06

            # Profile B: Front Runner Fader / Stopper (Leader early but poor run-home stamina)
            # Vulnerable over middle and staying trips (>= 440m)
            elif distance >= 440 and m.get("speed_rank", 5) <= 2 and m.get("run_home_rank", 5) >= 6:
                fader_penalty = 0.92 if distance >= 500 else 0.95
                base_power *= fader_penalty

            # Profile C: Sustained Run Home Swooper (Slow early, huge motor home)
            elif not is_straight and m.get("run_home_rank") in (1, 2) and m.get("speed_rank") > 4:
                # Dog is a verified Swooper (slow early, huge motor home).
                # If there is heavy contested pace up front (multiple rank 1-3 dogs fighting), the Swooper gets a massive boost
                contested_leaders = sum(1 for other in runner_metrics if other["speed_rank"] <= 3)
                if is_wet_track:
                    # Swooping is extremely difficult in mud/kickback; negate the bonus
                    base_power *= 0.95
                elif contested_leaders >= 3:
                    base_power *= 1.12
                elif contested_leaders == 2:
                    base_power *= 1.06

            # 5. Class / Grade Transition Modifier
            if m.get("avg_recent_grade") is not None:
                # class_transition > 0 means class drop (easier race). class_transition < 0 means class rise (harder race).
                class_transition = current_grade_val - m["avg_recent_grade"]
                if class_transition > 0:
                    # Class Drop Bonus (Massive Class Relief for dogs dropping from FFA down to Grade 5)
                    class_bonus = 1.0 + min(0.20, class_transition * 0.06)
                    base_power *= class_bonus
                elif class_transition < 0:
                    # Class Rise Penalty (Tougher competition)
                    class_penalty = 1.0 + max(-0.16, class_transition * 0.05)
                    base_power *= class_penalty

            # 6. Distance Jump / Stamina Modifier
            proven_stamina = m.get("proven_stamina")
            if proven_stamina and distance > 0:
                distance_diff = distance - proven_stamina
                if distance_diff >= 40:
                    # Stepping up in distance significantly (e.g. 400m to 488m)
                    # The dog is unproven at this trip. Severe risk of fading late (Gas-Out Penalty).
                    stamina_penalty = 0.85
                    
                    # If they are a front-runner (speed rank 1 or 2), the fade risk is even higher 
                    # as they expend their anaerobic energy early.
                    if m.get("speed_rank", 5) <= 2:
                        stamina_penalty -= 0.04
                    
                    base_power *= stamina_penalty
                    
                elif distance_diff <= -60:
                    # Dropping back in distance significantly (e.g. 515m to 400m)
                    # Dog has massive stamina (stayer dropping to sprint) but might lack early zip.
                    # Boost their overall class/strength
                    base_power *= 1.04
                    if m.get("run_home_rank") in (1, 2, 3):
                        base_power *= 1.05 # Stayer will storm home late
                    
                    # But penalize early speed score slightly if they aren't naturally fast
                    if m.get("speed_rank", 5) >= 4:
                        base_power *= 0.96

            # 7. Maiden Racecraft vs Unraced Debutante Modifier
            if is_restricted_or_maiden:
                f_str = str(m.get("form_string") or "").strip().upper()
                c_starts = m.get("career_starts", 0)
                st_starts = m.get("straight_starts", 0)
                cap_starts = m.get("capalaba_starts", 0)
                is_capalaba = "CAPALABA" in (track_name or "").upper()
                is_debutant = (not f_str or f_str == "N/A" or c_starts == 0)

                if is_straight:
                    # Straight Track Maiden Dynamics (Capalaba Turf, Healesville Sand, etc.):
                    # 1. Unraced debutants lack corner rails to guide them and frequently run greenly/veer off line.
                    if is_debutant:
                        base_power *= 0.84  # Green debutant discount down the straight
                    # 2. Exposed maidens with prior straight/Capalaba starts understand the lure line and maintain stride:
                    elif st_starts >= 2 or cap_starts >= 1 or c_starts >= 3:
                        turf_bonus = 1.15 if is_capalaba else 1.12
                        base_power *= turf_bonus
                    elif form_score >= 4.5:
                        base_power *= 1.10
                else:
                    # Circular Tracks with Turns
                    if form_score >= 4.5 and (c_starts >= 2 or len(f_str) >= 2):
                        # Proven maiden racecraft (handled box noise, crowding, repeated placings)
                        base_power *= 1.10
                    elif is_debutant:
                        # Unraced maiden debutante (prone to greenness, missing kick in competitive packs)
                        base_power *= 0.92
            squeeze_factor = 1.0
            if is_straight:
                # On straight tracks: outside advantage is fully captured in calibrated box bias above!
                # Avoid double-counting outside boxes; apply minor squeeze only to congested slow mid-pack runners
                if m["box"] in (3, 4, 5, 6):
                    # Are there hard railers outside cutting down across the drag strip?
                    outer_cuts_in = any(other["box"] > m["box"] and ("R" in other.get("running_style", "").upper()) for other in runner_metrics)
                    if outer_cuts_in:
                        # Middle dog gets chopped off by outer railers veering left
                        squeeze_factor *= 0.92
                    elif m["speed_rank"] > 5:
                        squeeze_factor *= 0.95
            else:
                # Circular tracks with turns
                # A. Vacant / Scratched Clearance Bonus (clear runway at box rise)
                if (m["box"] - 1) in vacant_or_scratched or (m["box"] + 1) in vacant_or_scratched:
                    squeeze_factor *= 1.06
                    
                # B. Immediate Neighbor Pace Mapping (The Cut-Off & Draft Rules)
                inside_neighbor = next((other for other in runner_metrics if other["box"] == m["box"] - 1), None)
                if not inside_neighbor and (m["box"] - 1) in vacant_or_scratched:
                    # Look further inside if immediate is scratched
                    inside_neighbor = next((other for other in runner_metrics if other["box"] == m["box"] - 2), None)
                    
                if inside_neighbor and inside_neighbor.get("split_1") and m.get("split_1"):
                    # If inside dog is a notoriously slow beginner (jumped > 0.12s slower than us)
                    # We get a "Phantom Vacant Box" bonus because we easily cross them and get clear air to the rail.
                    if inside_neighbor["split_1"] > m["split_1"] + 0.12:
                        squeeze_factor *= 1.05
                    # If inside dog is much faster than us (> 0.06s), they push out and we get the "Neighbor Cut-Off" penalty.
                    elif inside_neighbor["split_1"] < m["split_1"] - 0.06:
                        squeeze_factor *= 0.94

                # C. Kinetic Collision Physics (The Bully Factor)
                # If dog is in a tight race to the first turn with its neighbors, body mass dictates who holds the line.
                my_weight = m.get("today_weight")
                if my_weight:
                    outside_neighbor = next((other for other in runner_metrics if other["box"] == m["box"] + 1), None)
                    if not outside_neighbor and (m["box"] + 1) in vacant_or_scratched:
                        outside_neighbor = next((other for other in runner_metrics if other["box"] == m["box"] + 2), None)
                    
                    # Evaluate Inside Neighbor Weight Differential
                    if inside_neighbor and inside_neighbor.get("today_weight") and m.get("split_1") and inside_neighbor.get("split_1"):
                        # Only applies if they are contesting the same space (splits within 0.15s)
                        if abs(m["split_1"] - inside_neighbor["split_1"]) <= 0.15:
                            weight_diff = my_weight - inside_neighbor["today_weight"]
                            if weight_diff <= -3.5: # We are massively outweighed by the inside dog
                                squeeze_factor *= 0.94
                            elif weight_diff >= 3.5: # We are the bully
                                squeeze_factor *= 1.04
                    
                    # Evaluate Outside Neighbor Weight Differential
                    if outside_neighbor and outside_neighbor.get("today_weight") and m.get("split_1") and outside_neighbor.get("split_1"):
                        if abs(m["split_1"] - outside_neighbor["split_1"]) <= 0.15:
                            weight_diff = my_weight - outside_neighbor["today_weight"]
                            if weight_diff <= -3.5: # Massively outweighed by the outside dog cutting down
                                squeeze_factor *= 0.94
                            elif weight_diff >= 3.5: # We hold them off
                                squeeze_factor *= 1.04

                # B. Inside Pinch & Outside Cross-Over Penalty:
                # On circular tracks, outer runners need extra margin to overcome the geometric arc to the bend
                cross_margin = 0.08 if distance <= 400 else 0.05
                if m["box"] in (1, 2):
                    outer_faster_runner = False
                    for other in runner_metrics:
                        if other["box"] > m["box"] and other["split_1"] is not None and m["split_1"] is not None:
                            # Blistering early pace advantage (> 0.08s) allows crossing regardless of overall PB
                            if other["split_1"] < m["split_1"] - 0.08:
                                outer_faster_runner = True
                                break
                            # Marginal early pace advantage needs overall motor to complete the cross
                            elif other["split_1"] < m["split_1"] - cross_margin:
                                if other["best_time"] and m["best_time"] and other["best_time"] <= m["best_time"] + 0.05:
                                    outer_faster_runner = True
                                    break
                    if outer_faster_runner:
                        squeeze_factor *= 0.88
                elif m["box"] == 3:
                    outer_faster_runner = any(
                        other["box"] > m["box"] and (other["split_1"] < m["split_1"] - 0.05)
                        for other in runner_metrics
                    )
                    if outer_faster_runner:
                        squeeze_factor *= 0.92

                # C. Middle Box Sandwich into first bend
                if m["box"] in (3, 4, 5):
                    inner_faster = any(other["split_1"] < (m["split_1"] - 0.03) for other in runner_metrics if other["box"] < m["box"])
                    outer_cuts_in = any(other["box"] > m["box"] and ("R" in other["running_style"].upper()) for other in runner_metrics)
                    if inner_faster and outer_cuts_in:
                        squeeze_factor *= 0.88
                    elif inner_faster:
                        squeeze_factor *= 0.93

                # C2. Box 6 Awkward Bend Trap on Circular Tracks:
                # If Box 6 lacks early speed to clear dogs on its inside (inside runners within 0.02s of its split),
                # it cannot cross to the rail and gets trapped 3-wide around the first turn
                is_chute_or_wide_track = any(t in track_upper for t in ["MANDURAH", "HOBART"])
                if m["box"] == 6 and not is_chute_or_wide_track:
                    inside_pace_contested = any(
                        other["split_1"] <= (m["split_1"] + 0.02)
                        for other in runner_metrics if other["box"] < 6
                    )
                    if inside_pace_contested:
                        squeeze_factor *= 0.92

                # C3. "Carted Wide" Penalty for Outside Boxes (7, 8):
                # On tight circular tracks without long straight run-ups, if an outside dog lacks pace,
                # it gets carried wide on the bend. Chute/wide starts (e.g. Mandurah 300m/400m, Hobart 340m/461m) feature straight runs
                # where outside runners enjoy unimpeded accelerating lanes and do not suffer this penalty.
                if m["box"] in (7, 8) and not is_chute_or_wide_track:
                    immediate_inside = m["box"] - 1
                    while immediate_inside in vacant_or_scratched and immediate_inside > 1:
                        immediate_inside -= 1
                    
                    if immediate_inside not in vacant_or_scratched:
                        inside_dog = next((other for other in runner_metrics if other["box"] == immediate_inside), None)
                        if inside_dog and inside_dog["split_1"] is not None and m["split_1"] is not None:
                            if inside_dog["split_1"] <= m["split_1"] + 0.04:
                                squeeze_factor *= 0.82

                # Compound Middle Box Penalty Protection for Benchmark Engine:
                # If a middle box runner possesses the verified #1 or #2 engine in the field,
                # do not double-penalize it into oblivion.
                is_benchmark_engine = bool(min_best_time and m["best_time"] and (m["best_time"] <= min_best_time + 0.08))
                if m["box"] in (4, 5) and is_benchmark_engine:
                    squeeze_factor = max(0.96, squeeze_factor)

                # D. Outside Clean-Air Sweep Bonus for Box 8 (and Box 7):
                # Only granted if the dog possesses elite early pace AND proven motor AND isn't getting carted wide!
                is_carted_wide = False
                immediate_inside = m["box"] - 1
                while immediate_inside in vacant_or_scratched and immediate_inside > 1:
                    immediate_inside -= 1
                
                if immediate_inside not in vacant_or_scratched:
                    inside_dog = next((other for other in runner_metrics if other["box"] == immediate_inside), None)
                    if inside_dog and inside_dog["split_1"] is not None and m["split_1"] is not None:
                        if inside_dog["split_1"] <= m["split_1"] + 0.04:
                            is_carted_wide = True

                if m["box"] == 8 and not is_carted_wide:
                    if "MEADOWS" in track_upper and distance == 525:
                        squeeze_factor *= 1.08  # Clear outer camber runway at The Meadows 525m
                    else:
                        is_elite_pb = bool(min_best_time and m["best_time"] and (delta_to_min <= 0.12))
                        if (m["split_1"] - fastest_split) <= 0.03 and is_elite_pb:
                            squeeze_factor *= 1.10
                        elif (m["box"] - 1) in vacant_or_scratched and is_elite_pb:
                            squeeze_factor *= 1.05
                elif m["box"] == 7 and not is_carted_wide and (m["split_1"] - fastest_split) <= 0.03:
                    squeeze_factor *= 1.08

            # --- BENDIGO FIRST TURN CRUSH OVERRIDE ---
            if "BENDIGO" in track_upper and distance <= 425:
                # Determine if there is speed drawn inside/outside
                inside_speed = False
                outside_speed = False
                for sr in scored_runners:
                    if sr["speed_rank"] <= 3:
                        if sr["box"] < m["box"]: inside_speed = True
                        if sr["box"] > m["box"]: outside_speed = True
                
                # Box 1 Trap: If Box 1 is a slow starter, it gets totally buried.
                if m["box"] == 1:
                    if m.get("speed_rank", 5) >= 4:
                        squeeze_factor *= 0.65  # Massive "Buried on Rail" penalty
                    elif outside_speed:
                        squeeze_factor *= 1.10
                # Box 3 Nutcracker Trap:
                elif m["box"] == 3:
                    if inside_speed and outside_speed:
                        squeeze_factor *= 0.70  # Complete crush from both sides
                    else:
                        squeeze_factor *= 0.85
                elif m["box"] in (4, 5, 6):
                    if inside_speed:
                        squeeze_factor *= 0.88  # Softened to allow some class dogs to cross
                    else:
                        squeeze_factor *= 0.95
                elif m["box"] == 2:
                    if outside_speed:
                        squeeze_factor *= 1.10

            # --- THE GARDENS 600M FIRST TURN SQUEEZE ---
            if "GARDENS" in track_upper and distance == 600:
                outside_speed = any(sr["speed_rank"] <= 3 for sr in scored_runners if sr["box"] > m["box"])
                if m["box"] == 1 and outside_speed:
                    squeeze_factor *= 0.82  # Squeezed against the rail by converging outside traffic
                elif m["box"] in (6, 7, 8):
                    squeeze_factor *= 1.08  # Swoop lane bonus around the rail crunch

            # --- BALLARAT 390M & 450M FIRST TURN DYNAMICS ---
            if "BALLARAT" in track_upper and distance <= 450:
                inside_speed = any(sr.get("speed_rank", 5) <= 3 for sr in scored_runners if sr["box"] < m["box"])
                outside_speed = any(sr.get("speed_rank", 5) <= 3 for sr in scored_runners if sr["box"] > m["box"])
                # Slow Box 1 gets swamped against the rail on the sharp first corner
                if m["box"] == 1 and m.get("speed_rank", 5) >= 4:
                    squeeze_factor *= 0.72
                # Box 8 clean outer air runway
                elif m["box"] == 8 and m.get("speed_rank", 5) <= 4:
                    squeeze_factor *= 1.10
                # Middle pinch trap
                elif m["box"] in (4, 5) and inside_speed and outside_speed:
                    squeeze_factor *= 0.86

            # --- ANGLE PARK 342M FIRST TURN DYNAMICS ---
            if "ANGLE PARK" in track_upper and distance <= 350:
                # Ultra-short 70m run to bend: early-speed Box 1 hugs rail cleanly
                if m["box"] == 1 and m.get("speed_rank", 5) <= 3:
                    squeeze_factor *= 1.10
                # Slow wide runners get pushed out centrifugally
                elif m["box"] in (6, 7) and m.get("speed_rank", 5) > 3:
                    squeeze_factor *= 0.88

            # --- SHEPPARTON 385M/390M CHUTE SWEEP ---
            if "SHEPPARTON" in track_upper and distance <= 400:
                if m["box"] == 8 and m.get("speed_rank", 5) <= 4:
                    squeeze_factor *= 1.10  # Clean outside sweeping runway
                elif m["box"] == 7 and m.get("speed_rank", 5) > 3:
                    squeeze_factor *= 0.90  # Sandwiched inside the pink rug sweep

            # --- SANDOWN 515M SWEEPING CAMBER ---
            if "SANDOWN" in track_upper and distance <= 520:
                if m["box"] == 1 and m.get("speed_rank", 5) <= 3:
                    squeeze_factor *= 1.08  # Golden rail hold down back straight
                elif m["box"] == 8 and m.get("speed_rank", 5) <= 4:
                    squeeze_factor *= 1.08  # Slingshot outer camber momentum

            # --- HORSHAM 410M FIRST TURN SWEEP ---
            if "HORSHAM" in track_upper and distance <= 420:
                if m["box"] == 8 and m.get("speed_rank", 5) <= 4:
                    squeeze_factor *= 1.08  # Wide sweeping momentum around outer bend
                elif m["box"] == 1 and m.get("speed_rank", 5) >= 4:
                    squeeze_factor *= 0.76  # Slow Box 1 swamped by outside speed

            # --- GUNNEDAH 340M SHORT CHARGE ---
            if "GUNNEDAH" in track_upper and distance <= 350:
                if m["box"] == 1 and m.get("speed_rank", 5) <= 3:
                    squeeze_factor *= 1.10  # Railer lock into the tight bend
                elif m["box"] in (4, 5) and m.get("speed_rank", 5) > 2:
                    squeeze_factor *= 0.88  # Traffic sandwich

            # --- SALE 435M FIRST TURN SWEEP ---
            if "SALE" in track_upper and distance <= 450:
                if m["box"] == 8 and m.get("speed_rank", 5) <= 4:
                    squeeze_factor *= 1.10  # Sweeping outer galloping arc around Gippsland bend
                elif m["box"] == 1 and m.get("speed_rank", 5) >= 4:
                    squeeze_factor *= 0.78  # Trapped on rail if slow out

            # --- NOWRA 365M FIRST TURN DYNAMICS ---
            if "NOWRA" in track_upper and distance <= 380:
                if m["box"] == 1 and m.get("speed_rank", 5) <= 3:
                    squeeze_factor *= 1.10  # Rail hold into tight South Coast first turn
                elif m["box"] in (4, 5) and m.get("speed_rank", 5) > 2:
                    squeeze_factor *= 0.88  # Severe middle pinch

            # --- ROCKHAMPTON 407M FIRST TURN DYNAMICS ---
            if "ROCKHAMPTON" in track_upper and distance <= 420:
                if m["box"] == 1 and m.get("speed_rank", 5) <= 3:
                    squeeze_factor *= 1.12  # Dominant rail lock into the sharp turn
                elif m["box"] in (6, 7, 8) and m.get("speed_rank", 5) > 3:
                    squeeze_factor *= 0.84  # Swung wide centrifugally

            # --- MOUNT GAMBIER 305M SHORT RUN TO TURN ---
            if ("MOUNT GAMBIER" in track_upper or "MT GAMBIER" in track_upper) and distance <= 320:
                if m["box"] == 1 and m.get("speed_rank", 5) <= 3:
                    squeeze_factor *= 1.12  # Absolute rail lead
                elif m["box"] in (6, 7, 8) and m.get("speed_rank", 5) > 3:
                    squeeze_factor *= 0.82  # Cannot cross in short run-up

            # --- GAWLER 400M FIRST TURN SWOOP ---
            if "GAWLER" in track_upper and distance <= 420:
                if m["box"] == 8 and m.get("speed_rank", 5) <= 4:
                    squeeze_factor *= 1.08  # Outer sweep runway around Gawler bend
                elif m["box"] in (4, 5) and m.get("speed_rank", 5) > 3:
                    squeeze_factor *= 0.88  # Middle pinch

            # --- TEMORA 330M RIVERINA SHORT SPRINT ---
            if "TEMORA" in track_upper and distance <= 350:
                if m["box"] == 1 and m.get("speed_rank", 5) <= 3:
                    squeeze_factor *= 1.10  # Tight rail control
                elif m["box"] in (4, 5) and m.get("speed_rank", 5) > 2:
                    squeeze_factor *= 0.88  # Riverina mid-pack squeeze

            box_affinity = m.get("box_affinity", 1.0)

            # --- TRAINER STRIKE RATE INTELLIGENCE ---
            # Look up Bayesian-shrunk trainer win rate at this track/distance band.
            # Factor is clamped to [0.88, 1.15] and degrades gracefully through
            # track+band → track → global → neutral (1.0) if no data available.
            trainer_factor, trainer_source = trainer_intel.get_trainer_factor(
                m.get("trainer"), track_name, distance, box=m.get("box")
            )

            # 8. Smart Money Detection (Market Mover Modifier)
            sp = m.get("market_odds")
            sb = m.get("sportsbet_odds")
            market_mover_bonus = 1.0
            if sp and sb and sb > 0:
                market_ratio = sp / sb
                if market_ratio <= 0.75:
                    # Massive crunch in odds (e.g. $8.00 into $5.00) -> Smart Money / Syndicate Support
                    crunch_severity = 0.75 - market_ratio
                    market_mover_bonus = 1.06 + min(0.12, crunch_severity * 0.3)
                elif market_ratio >= 1.40:
                    # Massive drift (e.g. $3.00 out to $4.50) -> Unwanted / Sweating / Poor Kennel Condition
                    drift_severity = market_ratio - 1.40
                    market_mover_bonus = 0.95 - min(0.15, drift_severity * 0.08)

            # --- CLIMATIC & SEASONAL AFFINITY ---
            climatic_affinity = 1.0
            raw_weight = m.get("last_weight")
            if raw_weight:
                try:
                    wgt = float(raw_weight)
                    # The "Bog Track" Multiplier (Winter/Wet)
                    if track_is_wet:
                        if wgt > 33.0:
                            climatic_affinity *= 1.04  # Extra torque in mud
                        elif wgt < 28.0:
                            climatic_affinity *= 0.94  # Bogs down in deep sand
                    
                    # The "Heat Fade" & Thermal Regulation Penalty (Driven by "Feels Like" Apparent Temperature)
                    effective_temp = apparent_temp if apparent_temp is not None else weather_temp
                    if effective_temp >= 28.0:
                        if wgt > 33.0:
                            climatic_affinity *= 0.93  # High mass, rapid overheating under high humidity, fade risk
                        elif wgt < 28.0:
                            climatic_affinity *= 1.02  # Low mass, superior respiratory evaporative cooling
                    elif effective_temp <= 8.0:
                        # Cold snap / wind chill: higher glycogen consumption, power dogs hold edge
                        if wgt > 32.0 and form_score >= 4.0:
                            climatic_affinity *= 1.02  # Seasoned fitness & muscle mass advantage in cold
                        elif wgt < 27.0:
                            climatic_affinity *= 0.97  # Rapid thermal loss for very light dogs
                except:
                    pass

            power_score = base_power * box_factor * style_bonus * squeeze_factor * best_time_bonus * box_affinity * trainer_factor * market_mover_bonus * climatic_affinity

            scored_runners.append({
                **m,
                "power_score": power_score,
                "is_second_place_specialist": is_second_place_specialist,
                "trainer_factor": round(trainer_factor, 4),
                "trainer_source": trainer_source,
            })

        # 3. Softmax Normalization for True Probability
        # Empirically calibrated track temperature (fallback T = 2.4)
        if temperature is None:
            temperature = self.get_track_temperature(track_name)
        max_score = max(sr["power_score"] for sr in scored_runners)
        exp_scores = [math.exp((sr["power_score"] - max_score) / temperature) for sr in scored_runners]
        sum_exp = sum(exp_scores)

        raw_win_probs = [exp_val / sum_exp for exp_val in exp_scores]
        # Cap Perennial Runner-Up win probability (shifting probability to the field while keeping place prob high)
        adjusted_win_probs = []
        for sr, wp in zip(scored_runners, raw_win_probs):
            if sr.get("is_second_place_specialist") and wp > 0.32:
                adjusted_win_probs.append(0.32)
            else:
                adjusted_win_probs.append(wp)

        tot_wp = sum(adjusted_win_probs)
        final_win_probs = [p / tot_wp for p in adjusted_win_probs]

        # 4. Adaptive Bayesian Market Prior Integration
        # Blends market intelligence (Sportsbet / SP odds) with algorithmic power score:
        # - Full data races: 22% market prior (grounds edge detection and removes blind-spot variance)
        # - Sparse / missing data races: 60% market prior (prevents treating $71 outsiders equal to favorites)
        market_probs = []
        has_market_odds = False
        for sr in scored_runners:
            odds = sr.get("sportsbet_odds") or sr.get("market_odds")
            if odds and odds > 1.0:
                market_probs.append(1.0 / odds)
                has_market_odds = True
            else:
                market_probs.append(None)

        if has_market_odds and any(p is not None for p in market_probs):
            valid_m_sum = sum(p for p in market_probs if p is not None)
            norm_market_probs = []
            for p in market_probs:
                if p is not None:
                    norm_market_probs.append(p / valid_m_sum)
                else:
                    norm_market_probs.append(1.0 / len(scored_runners))

            w_market = 0.60 if data_is_sparse else 0.22
            w_model = 1.0 - w_market

            blended_win_probs = []
            for sr, p_model, p_mkt in zip(scored_runners, final_win_probs, norm_market_probs):
                runner_w_mkt = w_market
                # Adaptive runner-level market weighting:
                sr_starts = sr.get("starts_count", 0)
                sr_cstarts = sr.get("career_starts", 0)
                sr_st_starts = sr.get("straight_starts", 0)
                sr_cap_starts = sr.get("capalaba_starts", 0)
                is_debutant = (sr_starts == 0 or sr_cstarts == 0)

                # Straight Track & Maiden Market Adjustments:
                if is_straight and is_restricted_or_maiden:
                    if is_debutant:
                        # Cap debutant market prior: don't let trial/stable hype override high rookie straight variance
                        runner_w_mkt = min(runner_w_mkt, 0.30)
                    elif sr_st_starts >= 2 or sr_cap_starts >= 1 or sr_cstarts >= 3:
                        # Exposed maidens with straight experience: protect their racecraft from market outlier crush
                        runner_w_mkt = min(runner_w_mkt, 0.28)
                else:
                    # General Adaptive runner-level market weighting:
                    if p_mkt >= 0.30:
                        runner_w_mkt = max(runner_w_mkt, 0.48)
                    elif (sr.get("best_time") is None or sr.get("is_synthesized_pb") or sr.get("split_1") == avg_race_split) and p_mkt >= 0.18:
                        runner_w_mkt = max(runner_w_mkt, 0.45)
                    elif p_mkt <= 0.03 and sr.get("form_score", 5.0) < 3.5:
                        runner_w_mkt = max(runner_w_mkt, 0.60)
                    elif p_mkt <= 0.05 and sr.get("form_score", 5.0) < 3.2:
                        runner_w_mkt = max(runner_w_mkt, 0.50)
                    elif p_mkt <= 0.07 and sr.get("form_score", 5.0) >= 4.5:
                        runner_w_mkt = max(runner_w_mkt, 0.42)
                runner_w_model = 1.0 - runner_w_mkt
                blended_win_probs.append((p_model * runner_w_model) + (p_mkt * runner_w_mkt))

            tot_bp = sum(blended_win_probs)
            final_win_probs = [p / tot_bp for p in blended_win_probs]

        # 5. Expert Consensus Tie-Breaker Engine
        # When two top contenders (within top 3 spots) are in a statistical deadlock (|ΔP| <= 0.025),
        # inspect TheDogs / FastTrack expert tips. If the subordinate contender has a distinctly superior
        # expert tip consensus rank, apply a subtle 2.5% tie-break multiplier to resolve the deadlock
        # in favor of expert consensus without overriding large mathematical +EV edges.
        DELTA_TIE = 0.025
        tie_breaker_notes = {sr["box"]: None for sr in scored_runners}
        tie_breaker_applied = {sr["box"]: False for sr in scored_runners}

        if expert_tips_list and len(final_win_probs) >= 2:
            # We allow up to 2 passes to resolve rank 1-2 or rank 2-3 marginal ties
            for _ in range(2):
                pairs = sorted(
                    list(enumerate(final_win_probs)),
                    key=lambda x: x[1],
                    reverse=True
                )
                changed = False
                for i in range(min(2, len(pairs) - 1)):
                    idx_top, p_top = pairs[i]
                    idx_sub, p_sub = pairs[i + 1]
                    box_top = scored_runners[idx_top]["box"]
                    box_sub = scored_runners[idx_sub]["box"]

                    diff = p_top - p_sub
                    if 0.0 <= diff <= DELTA_TIE:
                        rank_top = expert_tips_list.index(box_top) + 1 if box_top in expert_tips_list else 99
                        rank_sub = expert_tips_list.index(box_sub) + 1 if box_sub in expert_tips_list else 99

                        if rank_sub < rank_top:
                            # Subordinate contender has superior expert tip endorsement!
                            nudge_factor = 1.025
                            final_win_probs[idx_sub] *= nudge_factor
                            final_win_probs[idx_top] *= (1.0 / nudge_factor)
                            # Re-normalize
                            tot_p = sum(final_win_probs)
                            final_win_probs = [p / tot_p for p in final_win_probs]

                            tie_breaker_applied[box_sub] = True
                            top_rank_str = f"#{rank_top}" if rank_top <= 8 else "unranked"
                            tie_breaker_notes[box_sub] = (
                                f"Broke deadlock vs Box {box_top} via Expert Consensus (Tip #{rank_sub} vs {top_rank_str})"
                            )
                            changed = True
                            break
                if not changed:
                    break

        # --- 6. Multi-Leg Exotic & Podium Engine (Place & Top-4 Probabilities) ---
        # Real-world place and top-4 outcomes differ systematically from raw win probabilities:
        # 1. Outsiders / longshots finish 2nd, 3rd, and 4th significantly more often than Harville predicts.
        # 2. Strong closers / swoopers (high run-home speed) steadily pass tiring contenders into the placings.
        # 3. Wide boxes (7, 8) and railers (1) avoid first-turn pileups.
        # 4. Pure front-running sprinters who are headed often fold completely out of the top 4.
        n_runners = len(scored_runners)
        raw_place_scores = []
        raw_top4_scores = []

        for sr, win_prob in zip(scored_runners, final_win_probs):
            # Softened power transformation: p**0.68 reflects the wider dispersion of 2nd/3rd/4th finishers
            p_base_place = (win_prob ** 0.68)
            p_base_top4 = (win_prob ** 0.62)

            # Podium / Top 4 propensity factor
            podium_mod = 1.0
            rh_rank = sr.get("run_home_rank", 4)
            sp_rank = sr.get("speed_rank", 4)

            # Sectional run-home stamina
            if rh_rank <= 2:
                podium_mod *= 1.15
            elif rh_rank <= 3:
                podium_mod *= 1.08
            elif rh_rank >= 6 and sp_rank <= 2:
                # Vulnerable front-runner stopper who gases out late if challenged
                podium_mod *= 0.82
            elif rh_rank >= 7:
                podium_mod *= 0.88

            # Traffic survival & box geometry
            if sr["box"] in (7, 8):
                podium_mod *= 1.10
            elif sr["box"] == 1 and not is_straight:
                # Inside rail saver
                podium_mod *= 1.08
            elif sr["box"] in (4, 5) and sp_rank > 3:
                podium_mod *= 0.90

            # Second-place specialist & career place consistency
            if sr.get("is_second_place_specialist"):
                podium_mod *= 1.25
            pr = sr.get("place_rate") or 0.0
            wr = sr.get("win_rate") or 0.0
            if pr >= 0.40 and (wr == 0 or pr >= wr * 2.2):
                podium_mod *= 1.12

            # Market Place Prior Anchor (if sportsbet place odds available)
            sb_p = sr.get("sportsbet_place_odds")
            if sb_p and sb_p > 1.0:
                implied_p = 1.0 / sb_p
                score_p = (p_base_place * podium_mod * 0.80) + (implied_p * 0.20)
                score_t4 = (p_base_top4 * podium_mod * 0.80) + (min(0.95, implied_p * 1.22) * 0.20)
            else:
                score_p = p_base_place * podium_mod
                score_t4 = p_base_top4 * podium_mod

            raw_place_scores.append(score_p)
            raw_top4_scores.append(score_t4)

        # Australian racing place dividend rules:
        # 8+ runners: 3 places paid (1st, 2nd, 3rd)
        # 5-7 runners: 2 places paid (1st, 2nd only; NO dividend for 3rd place)
        # 1-4 runners: Win only (1 place; no place dividends paid)
        places_paid = 3 if n_runners >= 8 else (2 if n_runners >= 5 else 1)
        no_third_place_dividend = (places_paid < 3)

        sum_raw_place = sum(raw_place_scores)
        target_place_sum = float(places_paid)
        scale_place = target_place_sum / sum_raw_place if sum_raw_place > 0 else 1.0
        final_place_probs = [min(0.95, max(0.04, round(s * scale_place, 4))) for s in raw_place_scores]

        sum_raw_top4 = sum(raw_top4_scores)
        target_top4_sum = min(4.0, float(n_runners))
        scale_top4 = target_top4_sum / sum_raw_top4 if sum_raw_top4 > 0 else 1.0
        final_top4_probs = [min(0.98, max(0.08, round(s * scale_top4, 4))) for s in raw_top4_scores]

        results = []
        for sr, win_prob, place_prob, top4_prob in zip(scored_runners, final_win_probs, final_place_probs, final_top4_probs):
            fair_win_odds = round(1.0 / win_prob, 2) if win_prob > 0 else 999.0
            fair_place_odds = round(1.0 / place_prob, 2) if place_prob > 0 else 999.0
            fair_top4_odds = round(1.0 / top4_prob, 2) if top4_prob > 0 else 999.0

            # Composite Exotic Score for Boxed First 4 & Exotic ticket selection
            # Balances high win probability for 1st place with high top-4 probability for 2nd/3rd/4th
            exotic_score = round((win_prob * 0.40) + (top4_prob * 0.60), 4)

            edge_win = None
            if sr["market_odds"] and sr["market_odds"] > 1.0:
                implied_market_prob = 1.0 / sr["market_odds"]
                edge_win = round(win_prob - implied_market_prob, 4)

            sb_odds = sr.get("sportsbet_odds")
            sb_place = sr.get("sportsbet_place_odds")
            edge_sb = None
            if sb_odds and sb_odds > 1.0:
                implied_sb_prob = 1.0 / sb_odds
                edge_sb = round(win_prob - implied_sb_prob, 4)

            available_odds = [o for o in [sr["market_odds"], sb_odds] if o and o > 1.0]
            best_market_odds = max(available_odds) if available_odds else None

            exp_rank = expert_tips_list.index(sr["box"]) + 1 if sr["box"] in expert_tips_list else None

            # Calculate Hype % (Market Implied Win Probability %)
            # Derived from Sportsbet fixed odds (or TheDogs SP / market odds)
            hype_score = None
            if sb_odds and sb_odds > 1.0:
                hype_score = round((1.0 / sb_odds) * 100.0, 1)
            elif sr.get("market_odds") and sr["market_odds"] > 1.0:
                hype_score = round((1.0 / sr["market_odds"]) * 100.0, 1)

            results.append({
                "box": sr["box"],
                "dog_id": sr.get("dog_id"),
                "dog_name": sr["dog_name"],
                "trainer": sr["trainer"],
                "running_style": sr.get("running_style", ""),
                "form_string": sr["form_string"],
                "market_odds": sr["market_odds"],
                "sp_odds": sr["market_odds"],
                "sportsbet_odds": sb_odds,
                "sportsbet_place_odds": sb_place,
                "hype_score": hype_score,
                "finish_position": sr.get("finish_position"),
                "margin": sr.get("margin"),
                "race_time": sr.get("race_time"),
                "best_odds": best_market_odds,
                "box_affinity": round(sr.get("box_affinity", 1.0), 2),
                "trainer_factor": sr.get("trainer_factor", 1.0),
                "trainer_source": sr.get("trainer_source", "none"),
                "expert_tips": expert_tips_list,
                "expert_tip_rank": exp_rank,
                "tie_breaker_applied": tie_breaker_applied.get(sr["box"], False),
                "tie_breaker_note": tie_breaker_notes.get(sr["box"]),
                "model_win_prob": round(win_prob, 4),
                "model_place_prob": place_prob,
                "model_top4_prob": top4_prob,
                "exotic_score": exotic_score,
                "fair_win_odds": fair_win_odds,
                "fair_place_odds": fair_place_odds,
                "fair_top4_odds": fair_top4_odds,
                "edge": edge_win,
                "sportsbet_edge": edge_sb,
                "speed_rank": sr.get("speed_rank"),
                "run_home_rank": sr.get("run_home_rank"),
                "split_1": sr.get("split_1"),
                "best_time": sr.get("best_time"),
                "dsls": sr.get("dsls"),
                "last_weight": sr.get("last_weight"),
                "starts_count": sr.get("starts_count", 0),
                "career_starts": sr.get("career_starts", 0),
                "straight_starts": sr.get("straight_starts", 0),
                "capalaba_starts": sr.get("capalaba_starts", 0),
                "places_paid": places_paid,
                "no_third_place_dividend": no_third_place_dividend,
                "place_terms": f"Paying {places_paid} Places (No 3rd Place Dividend)" if no_third_place_dividend else "Paying 3 Places"
            })

        sorted_results = sorted(results, key=lambda x: x["model_win_prob"], reverse=True)

        # Identify #1 Value Roughie (5th dog that might get up)
        roughie_info = self.select_value_roughie(sorted_results, track_name=track_name, distance=distance)
        if roughie_info:
            roughie_box = roughie_info["box"]
            for r in sorted_results:
                if r["box"] == roughie_box:
                    r["is_roughie"] = True
                    r["roughie_score"] = roughie_info["roughie_score"]
                    r["roughie_insight"] = roughie_info["insight"]
                    r["roughie_tags"] = roughie_info["upside_tags"]
                    r["upside_tags"] = roughie_info["upside_tags"]
                else:
                    r["is_roughie"] = False
        else:
            for r in sorted_results:
                r["is_roughie"] = False

        return sorted_results

    @staticmethod
    def select_value_roughie(
        sorted_predictions: List[Dict[str, Any]],
        track_name: str = "",
        distance: int = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Identifies the optimal 'Value Roughie' (5th Dog) outside the primary Top 4 contenders.
        Evaluates candidates on:
        1. Top-4 Podium Projection (Harville-adjusted placing probability)
        2. Early Speed & Front-Running Threat (split 1 sectional rank)
        3. Closing Run-Home Stamina (swooper capability)
        4. Box Camber & Runway Geometry (Boxes 1 & 8, outer sweep arcs)
        5. Value Juice & Overlay (+EV odds discrepancy)
        6. Recent Form Placings
        """
        if not sorted_predictions or len(sorted_predictions) <= 4:
            return None

        candidates = sorted_predictions[4:]  # Runners outside top 4

        scored_candidates = []
        for r in candidates:
            score = 0.0
            reasons = []

            # 1. Base Top-4 Capability (0 - 45 pts)
            t4_prob = r.get("model_top4_prob", 0.0)
            score += min(45.0, t4_prob * 90.0)
            if t4_prob >= 0.35:
                reasons.append(f"{round(t4_prob * 100)}% Top-4 Projection")

            # 2. Market Price & Overlay Value (0 - 25 pts)
            odds = r.get("best_odds") or r.get("sportsbet_odds") or r.get("market_odds") or 0.0
            edge = r.get("edge") or r.get("sportsbet_edge") or 0.0
            if odds >= 14.0:
                score += 15.0
                reasons.append(f"Big Value (${odds:.2f})")
            elif odds >= 8.0:
                score += 10.0
                reasons.append(f"Value Odds (${odds:.2f})")
            elif odds >= 5.0:
                score += 5.0
            if edge and edge > 0.015:
                score += 10.0
                reasons.append(f"+EV Overlay (+{round(edge * 100, 1)}%)")

            # 3. Early Speed / Front-Runner Threat (0 - 25 pts)
            sp_rank = r.get("speed_rank", 5)
            style = (r.get("running_style") or "").upper()
            if sp_rank <= 2:
                score += 22.0
                reasons.append("Elite Early Speed (Rank #1-2)")
            elif sp_rank <= 3:
                score += 14.0
                reasons.append("Fast Split 1 Pace")
            elif "FR" in style or "FM" in style or "RR" in style:
                score += 5.0

            # 4. Closing Run-Home Stamina / Swooper (0 - 18 pts)
            rh_rank = r.get("run_home_rank", 5)
            if rh_rank <= 2:
                score += 16.0
                reasons.append("Strong Closing Run-Home")
            elif rh_rank <= 3:
                score += 8.0

            # 5. Box Draw Camber & Runway (0 - 15 pts)
            box = r.get("box", 1)
            track_upper = (track_name or "").upper()
            if "CAPALABA" in track_upper:
                # Capalaba: Outside lure mounted on grandstand fence (Box 8)
                if box in (7, 8):
                    score += 15.0
                    reasons.append(f"Outside Lure Rail Advantage (Box {box})")
                elif box == 6:
                    score += 10.0
                    reasons.append("Clear Outer Runway (Box 6)")
                elif box in (1, 2):
                    score += 2.0
            else:
                if box == 1:
                    score += 12.0
                    reasons.append("Inside Rail Advantage (Box 1)")
                elif box == 8:
                    score += 12.0
                    reasons.append("Clear Outer Swoop Runway (Box 8)")
                    if any(t in track_upper for t in ["SALE", "SHEPPARTON", "BALLARAT", "GAWLER"]):
                        score += 5.0
                elif box == 2:
                    score += 6.0

            # 5b. Proven Track Engine / Motor Capability (0 - 20 pts)
            min_field_time = min((cand.get("best_time") for cand in sorted_predictions if cand.get("best_time")), default=None)
            if r.get("best_time") and min_field_time and not r.get("is_synthesized_pb"):
                pb_delta = r["best_time"] - min_field_time
                if pb_delta <= 0.35:
                    score += 20.0
                    reasons.append(f"Proven Track Engine (PB {r['best_time']:.2f}s)")
                elif pb_delta <= 0.50:
                    score += 10.0

            # 5c. Major Class Drop (0 - 15 pts)
            dog_class = r.get("avg_recent_grade")
            if dog_class and dog_class <= 2.5:
                score += 16.0
                reasons.append("Major Class Drop from Free For All (FFA)")

            # 6. Recent Form Consistency (0 - 12 pts)
            form = str(r.get("form_string") or "")
            recent_places = sum(1 for ch in form[-3:] if ch in "1234")
            if recent_places >= 2:
                score += 10.0
                reasons.append(f"Recent Placings ({form})")

            # 7. Straight Track Racecraft (0 - 21 pts)
            is_straight_trk = any(st in track_upper for st in ["STRAIGHT", "CAPALABA", "HEALESVILLE"])
            st_starts = r.get("straight_starts", 0)
            c_starts = r.get("career_starts", 0)
            if is_straight_trk:
                if st_starts >= 2:
                    score += 14.0
                    reasons.append("Proven Straight Racecraft")
                    if "CAPALABA" in track_upper:
                        score += 4.0
                        reasons.append("Grass Turf Experience")
                elif st_starts == 0 and c_starts >= 3:
                    score -= 12.0  # Unproven circular runner down straight
            elif is_straight_trk and c_starts == 0:
                score -= 10.0

            scored_candidates.append({
                "runner": r,
                "roughie_score": round(score, 1),
                "reasons": reasons,
                "odds": odds
            })

        if not scored_candidates:
            return None

        scored_candidates.sort(key=lambda x: x["roughie_score"], reverse=True)
        best = scored_candidates[0]
        r = best["runner"]

        reasons_summary = " • ".join(best["reasons"][:3]) if best["reasons"] else "High place capability"
        insight = f"Recommended 5th dog to add to First 4 & Trifecta box tickets. {reasons_summary}."

        return {
            "box": r["box"],
            "dog_name": r["dog_name"],
            "dog_id": r.get("dog_id"),
            "trainer": r.get("trainer"),
            "best_odds": best["odds"] if best["odds"] > 0 else None,
            "sportsbet_odds": r.get("sportsbet_odds"),
            "market_odds": r.get("market_odds"),
            "model_win_prob": r.get("model_win_prob", 0.0),
            "model_place_prob": r.get("model_place_prob", 0.0),
            "model_top4_prob": r.get("model_top4_prob", 0.0),
            "exotic_score": r.get("exotic_score", 0.0),
            "roughie_score": best["roughie_score"],
            "upside_tags": best["reasons"][:4],
            "insight": insight,
            "rank_in_field": next((idx + 1 for idx, p in enumerate(sorted_predictions) if p["box"] == r["box"]), 5)
        }



if __name__ == "__main__":
    analytics = GreyhoundAnalytics()
    print("--- Testing Upgraded Model on Launceston R3 (Race ID: 30) ---")
    preds = analytics.predict_race_probabilities(30)
    for p in preds:
        print(f"Box {p['box']}: {p['dog_name']:<20} | Form: {p['form_string']:<6} | WinProb: {p['model_win_prob']*100:.1f}% | Fair: ${p['fair_win_odds']}")
