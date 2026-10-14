"""
Official Scratchings Collector & Reserve Reallocation Engine.
Fetches official scratching lists nationwide from TheDogs, marks non-runners,
and reallocates reserve runners (Boxes 9 & 10) into vacated boxes following
official Australian Greyhound Racing rules (GAR Rule 76).
"""

import re
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from db import get_connection

AEDT_TZ = timezone(timedelta(hours=11))


class ScratchingsManager:
    SCRATCHINGS_URL = "https://www.thedogs.com.au/racing/scratchings"

    def __init__(self, timeout: float = 20.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        self.client = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=True)

    @staticmethod
    def match_track_name(venue: str, db_tracks: List[str]) -> str:
        """
        Normalize sponsor prefixes (e.g. 'Ladbrokes Gardens' -> 'The Gardens',
        'BetDeluxe Capalaba' -> 'Capalaba', 'Ladbrokes Q2 Parklands' -> 'Q2 Parklands').
        """
        v_clean = re.sub(r"^(Ladbrokes|TAB|Sky Racing|BetDeluxe|Sportsbet|SEN|Greyhound Racing)\s+", "", venue, flags=re.I).strip()
        for t in db_tracks:
            t_clean = re.sub(r"^(The|Ladbrokes|TAB|BetDeluxe)\s+", "", t, flags=re.I).strip()
            if v_clean.lower() == t.lower() or v_clean.lower() == t_clean.lower():
                return t
            if v_clean.lower() in t.lower() or t_clean.lower() in v_clean.lower():
                return t
        return venue

    def reallocate_reserves(self, target_dates: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Reallocates reserves (Boxes 9 & 10) into vacated scratched boxes (1-8)
        across all races on the target dates following official Australian racing rules.
        - Reserve 9 replaces the first unallocated scratched box.
        - Reserve 10 replaces the second unallocated scratched box.
        - Any reserve that does not gain a start is marked is_scratched = 1 with into_box = NULL.
        """
        conn = get_connection()
        cursor = conn.cursor()

        if not target_dates:
            now_aedt = datetime.now(AEDT_TZ)
            target_dates = [
                (now_aedt - timedelta(days=1)).strftime("%Y-%m-%d"),
                now_aedt.strftime("%Y-%m-%d"),
                (now_aedt + timedelta(days=1)).strftime("%Y-%m-%d"),
            ]

        total_reallocated = 0
        total_reserves_purged = 0

        for d in target_dates:
            cursor.execute("""
                SELECT r.id, r.race_number, m.track_name
                FROM races r
                JOIN meetings m ON r.meeting_id = m.id
                WHERE m.meeting_date = ?
            """, (d,))
            races = cursor.fetchall()

            for race_row in races:
                race_id = race_row[0]

                # Fetch all runners for this race
                cursor.execute("""
                    SELECT rr.id, rr.box_number, rr.rug_number, rr.is_scratched, rr.into_box, g.dog_name
                    FROM race_runners rr
                    LEFT JOIN greyhounds g ON rr.dog_id = g.id
                    WHERE rr.race_id = ?
                    ORDER BY COALESCE(rr.box_number, rr.rug_number, 99) ASC
                """, (race_id,))
                runners = cursor.fetchall()

                # Clean dummy records like VACANT BOX or NO RESERVE
                for r_id, b_num, rug_num, is_scr, into_b, dog_name in runners:
                    d_name_upper = (dog_name or "").upper()
                    if any(v in d_name_upper for v in ["VACANT", "NO RESERVE"]):
                        if not is_scr:
                            cursor.execute("UPDATE race_runners SET is_scratched = 1 WHERE id = ?", (r_id,))

                # Re-query cleaned runners
                cursor.execute("""
                    SELECT rr.id, rr.box_number, rr.rug_number, rr.is_scratched, rr.into_box, g.dog_name
                    FROM race_runners rr
                    LEFT JOIN greyhounds g ON rr.dog_id = g.id
                    WHERE rr.race_id = ?
                    ORDER BY COALESCE(rr.box_number, rr.rug_number, 99) ASC
                """, (race_id,))
                runners = cursor.fetchall()

                # Inside runners (boxes 1-8)
                inside_runners = [r for r in runners if r[1] and 1 <= r[1] <= 8]
                scratched_boxes = sorted([r[1] for r in inside_runners if r[3] == 1])

                # Check for vacant boxes in 1-8 (boxes that have no runner entry at all)
                present_boxes = {r[1] for r in inside_runners}
                vacant_boxes = sorted(list(set(range(1, 9)) - present_boxes))
                all_vacant_or_scratched = sorted(list(set(scratched_boxes + vacant_boxes)))

                # Reserves (boxes 9 and 10)
                reserves = [r for r in runners if r[1] in (9, 10)]

                if not reserves:
                    continue

                available_vacancies = list(all_vacant_or_scratched)

                # Process reserves in order: box 9 first, then box 10
                reserves.sort(key=lambda x: x[1])

                for res in reserves:
                    r_id = res[0]
                    res_box = res[1]
                    res_is_scratched = res[3]
                    res_into_box = res[4]
                    res_name = res[5]

                    # If reserve was explicitly marked as scratched on the scratching list, it cannot take a box
                    # We check if it was marked scratched without having an into_box
                    if res_is_scratched and not res_into_box:
                        continue

                    if available_vacancies:
                        target_box = available_vacancies.pop(0)
                        cursor.execute("""
                            UPDATE race_runners
                            SET into_box = ?, is_scratched = 0
                            WHERE id = ?
                        """, (target_box, r_id))
                        total_reallocated += 1
                    else:
                        # No vacant/scratched box available!
                        # Reserve does NOT get a start. Mark as non-starter.
                        cursor.execute("""
                            UPDATE race_runners
                            SET into_box = NULL, is_scratched = 1
                            WHERE id = ?
                        """, (r_id,))
                        total_reserves_purged += 1

        conn.commit()
        conn.close()

        return {
            "reserves_reallocated": total_reallocated,
            "unallocated_reserves_purged": total_reserves_purged
        }

    def sync_daily_scratchings(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Pulls official scratchings across all Australian tracks and updates the database,
        then automatically executes official reserve reallocations.
        """
        now_aedt = datetime.now(AEDT_TZ)
        if target_date is None:
            target_dates = [
                (now_aedt - timedelta(days=1)).strftime("%Y-%m-%d"),
                now_aedt.strftime("%Y-%m-%d"),
                (now_aedt + timedelta(days=1)).strftime("%Y-%m-%d")
            ]
        else:
            target_dates = [target_date]

        try:
            resp = self.client.get(self.SCRATCHINGS_URL)
            if resp.status_code != 200:
                print(f"[ScratchingsManager] Failed to fetch scratchings page: HTTP {resp.status_code}")
                return {"status": "http_error", "code": resp.status_code, "scratched_marked": 0, "reserves_reallocated": 0}
        except Exception as e:
            print(f"[ScratchingsManager] Request error: {e}")
            return {"status": "error", "error": str(e), "scratched_marked": 0, "reserves_reallocated": 0}

        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")
        conn = get_connection()
        cursor = conn.cursor()

        # Query all active tracks across the target dates
        placeholders = ",".join(["?"] * len(target_dates))
        cursor.execute(f"SELECT DISTINCT track_name, meeting_date FROM meetings WHERE meeting_date IN ({placeholders})", target_dates)
        db_meetings = cursor.fetchall()
        db_tracks = list({row[0] for row in db_meetings})

        tables = soup.find_all("table")
        total_marked = 0

        for t in tables:
            for tr in t.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
                if len(tds) >= 12 and tds[0] != "VENUE":
                    raw_venue = tds[0].strip()
                    matched_track = self.match_track_name(raw_venue, db_tracks)
                    for r_num in range(1, 13):
                        if r_num < len(tds):
                            scratch_val = tds[r_num]
                            if scratch_val:
                                rugs = re.findall(r"\d+", scratch_val)
                                for rug in rugs:
                                    rug_int = int(rug)
                                    cursor.execute(f"""
                                    UPDATE race_runners
                                    SET is_scratched = 1
                                    WHERE race_id IN (
                                        SELECT r.id FROM races r
                                        JOIN meetings m ON r.meeting_id = m.id
                                        WHERE (m.track_name = ? OR m.track_name LIKE ?) 
                                          AND r.race_number = ? 
                                          AND m.meeting_date IN ({placeholders})
                                    ) AND (box_number = ? OR rug_number = ?)
                                    """, (matched_track, f"%{matched_track}%", r_num, *target_dates, rug_int, rug_int))
                                    total_marked += cursor.rowcount

        conn.commit()
        conn.close()

        # Now execute reserve reallocation
        realloc_stats = self.reallocate_reserves(target_dates)

        return {
            "status": "success",
            "scratched_marked": total_marked,
            "reserves_reallocated": realloc_stats["reserves_reallocated"],
            "unallocated_reserves_purged": realloc_stats["unallocated_reserves_purged"],
            "target_dates": target_dates
        }


if __name__ == "__main__":
    sm = ScratchingsManager()
    stats = sm.sync_daily_scratchings()
    print(f"Scratchings synchronized: {stats}")
