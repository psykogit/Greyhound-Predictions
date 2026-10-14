"""
Dog History Collector & Sectional Ingestion Module.
Fetches full career race logs, box-by-box statistics, and track/distance personal bests from TheDogs.com.au.
Persists structured sectional records into SQLite (past_performances, greyhounds).
"""

import re
import json
import urllib.parse
import httpx
from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional
from datetime import datetime
import sqlite3
from db import get_connection


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



class DogHistoryCollector:
    BASE_URL = "https://www.thedogs.com.au"

    def __init__(self, timeout: float = 20.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        self.client = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=True)

    def fetch_dog_profile(self, profile_path: str) -> Optional[Dict[str, Any]]:
        """
        Scrapes a dog profile page on TheDogs (e.g., '/dogs/438134/cheam').
        Extracts:
        - Box-by-box breakdown (Boxes 1 to 8: Starts, Wins, Places, Win%, Place%)
        - Distance breakdown (<400, 400+, 500+, 600+, 700+)
        - Track Personal Bests (Fastest winning/place times by track & distance)
        - Full Lifetime Race Log (Date, Track, Distance, Box, Finish Pos, Race Time, Split 1, Margin, SP Odds)
        """
        url = self.BASE_URL + profile_path if profile_path.startswith("/") else profile_path
        try:
            resp = self.client.get(url)
            if resp.status_code != 200:
                print(f"[DogCollector] Failed fetching {url}: Status {resp.status_code}")
                return None
            try:
                soup = BeautifulSoup(resp.text, "lxml")
            except Exception:
                soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[DogCollector] HTTP Error on {url}: {e}")
            return None

        # Dog Name
        dog_heading = soup.find("h1")
        dog_name = dog_heading.get_text(strip=True) if dog_heading else "Unknown"

        tables = soup.find_all("table")

        # 1. Box-by-Box Breakdown (Table with Starts, Wins, Places across boxes 1-8)
        box_stats = {}
        for t in tables:
            rows = t.find_all("tr")
            row_headers = [r.find("th") or (r.find_all("td")[0] if r.find_all("td") else None) for r in rows]
            row_labels = [rh.get_text(strip=True).upper() if rh else "" for rh in row_headers]
            
            if "STARTS" in row_labels and "WINS" in row_labels:
                starts_row = [td.get_text(strip=True) for td in rows[row_labels.index("STARTS")].find_all("td")[1:]]
                wins_row = [td.get_text(strip=True) for td in rows[row_labels.index("WINS")].find_all("td")[1:]]
                places_row = [td.get_text(strip=True) for td in rows[row_labels.index("PLACES")].find_all("td")[1:]] if "PLACES" in row_labels else []
                win_pct_row = [td.get_text(strip=True) for td in rows[row_labels.index("WIN %")].find_all("td")[1:]] if "WIN %" in row_labels else []

                for box_idx in range(1, 9):
                    s_val = int(starts_row[box_idx - 1]) if len(starts_row) >= box_idx and starts_row[box_idx - 1].isdigit() else 0
                    w_val = int(wins_row[box_idx - 1]) if len(wins_row) >= box_idx and wins_row[box_idx - 1].isdigit() else 0
                    p_val = int(places_row[box_idx - 1]) if len(places_row) >= box_idx and places_row[box_idx - 1].isdigit() else 0
                    pct_val = win_pct_row[box_idx - 1] if len(win_pct_row) >= box_idx else "0%"
                    box_stats[box_idx] = {
                        "starts": s_val,
                        "wins": w_val,
                        "places": p_val,
                        "win_pct": pct_val
                    }
                break

        # 2. Track & Distance Personal Bests (Fastest Times)
        personal_bests = []
        for t in tables:
            headers = [th.get_text(strip=True).upper() for th in t.find_all("th")]
            if "TRACK" in headers and "TIME" in headers and "DIST" in headers and "DATE" in headers:
                for row in t.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(tds) >= 4 and tds[1] and re.match(r"^\d+\.\d+$", tds[1]):
                        personal_bests.append({
                            "track": tds[0],
                            "best_time": float(tds[1]),
                            "date": tds[3],
                            "distance": tds[4]
                        })
                break

        # 3. Lifetime Race Log
        career_runs = []
        for t in tables:
            headers = [th.get_text(strip=True).upper() for th in t.find_all("th")]
            if "PLC" in headers and "DIST" in headers and "TRACK" in headers and "TIME" in headers:
                for row in t.find_all("tr")[1:]:
                    tds = row.find_all("td")
                    if len(tds) >= 12:
                        plc_text = tds[0].get_text(strip=True)
                        if not plc_text or "SHOW MORE" in plc_text.upper():
                            continue

                        # Box number from sprite
                        box_sprite = tds[1].find("sprite-svg")
                        box_str = box_sprite.get("name", "") if box_sprite else tds[1].get_text(strip=True)
                        m_box = re.search(r"\d+", box_str)
                        box_num = int(m_box.group(0)) if m_box else None

                        def parse_float(txt: str) -> Optional[float]:
                            clean = re.sub(r"[^\d.]", "", txt)
                            try:
                                return float(clean) if clean else None
                            except ValueError:
                                return None

                        wgt_val = parse_float(tds[2].get_text(strip=True))
                        dist_val = int(re.sub(r"\D", "", tds[3].get_text(strip=True))) if re.search(r"\d+", tds[3].get_text(strip=True)) else None
                        date_str = tds[4].get_text(strip=True)
                        track_code = tds[5].get_text(strip=True)
                        grade_val = tds[6].get_text(strip=True)
                        time_val = parse_float(tds[7].get_text(strip=True))
                        win_time_val = parse_float(tds[8].get_text(strip=True)) if len(tds) > 8 else None
                        split_1 = parse_float(tds[10].get_text(strip=True)) if len(tds) > 10 else None
                        margin = parse_float(tds[11].get_text(strip=True)) if len(tds) > 11 else None
                        second_dog = tds[12].get_text(strip=True) if len(tds) > 12 else ""
                        pir_val = tds[13].get_text(strip=True) if len(tds) > 13 else ""
                        sp_odds = parse_float(tds[14].get_text(strip=True)) if len(tds) > 14 else None

                        career_runs.append({
                            "finish_pos": plc_text,
                            "box": box_num,
                            "weight": wgt_val,
                            "distance": dist_val,
                            "run_date": date_str,
                            "track_code": track_code,
                            "grade": grade_val,
                            "race_time": time_val,
                            "winner_time": win_time_val,
                            "split_1": split_1,
                            "margin": margin,
                            "pir": pir_val,
                            "second_dog": second_dog,
                            "sp_odds": sp_odds
                        })
                break

        # Career totals calculation
        total_starts = len(career_runs)
        total_wins = sum(1 for r in career_runs if "1st" in str(r["finish_pos"]))
        total_seconds = sum(1 for r in career_runs if "2nd" in str(r["finish_pos"]))
        total_thirds = sum(1 for r in career_runs if "3rd" in str(r["finish_pos"]))

        return {
            "dog_name": dog_name,
            "profile_url": profile_path,
            "total_starts": total_starts,
            "total_wins": total_wins,
            "total_seconds": total_seconds,
            "total_thirds": total_thirds,
            "win_rate": round(total_wins / total_starts, 3) if total_starts > 0 else 0.0,
            "place_rate": round((total_wins + total_seconds + total_thirds) / total_starts, 3) if total_starts > 0 else 0.0,
            "box_stats": box_stats,
            "personal_bests": personal_bests,
            "career_runs": career_runs
        }

    def sync_dog_history_to_db(self, dog_id: int, profile_url: str) -> Optional[Dict[str, Any]]:
        """Scrapes dog history and persists records into SQLite."""
        data = self.fetch_dog_profile(profile_url)
        if not data:
            return None

        conn = get_connection()
        c = conn.cursor()

        # 1. Update greyhounds master record
        c.execute("""
        UPDATE greyhounds
        SET career_starts = ?,
            career_wins = ?,
            career_seconds = ?,
            career_thirds = ?
        WHERE id = ?
        """, (
            data["total_starts"],
            data["total_wins"],
            data["total_seconds"],
            data["total_thirds"],
            dog_id
        ))

        # Check if pb_times_json / box_record_json columns exist in greyhounds; if not, add them
        c.execute("PRAGMA table_info(greyhounds)")
        existing_cols = {col["name"] for col in c.fetchall()}
        if "profile_url" not in existing_cols:
            c.execute("ALTER TABLE greyhounds ADD COLUMN profile_url TEXT")
        if "pb_times_json" not in existing_cols:
            c.execute("ALTER TABLE greyhounds ADD COLUMN pb_times_json TEXT")
        if "box_record_json" not in existing_cols:
            c.execute("ALTER TABLE greyhounds ADD COLUMN box_record_json TEXT")

        c.execute("""
        UPDATE greyhounds
        SET profile_url = ?,
            pb_times_json = ?,
            box_record_json = ?
        WHERE id = ?
        """, (
            profile_url,
            json.dumps(data["personal_bests"]),
            json.dumps(data["box_stats"]),
            dog_id
        ))

        # 2. Upsert historical runs into past_performances
        for r in data["career_runs"]:
            if not r["race_time"] or not r["run_date"]:
                continue
            dt = parse_flexible_date(r["run_date"])
            iso_date = dt.strftime("%Y-%m-%d") if dt else None
            c.execute("""
            INSERT INTO past_performances (
                dog_id, run_date, run_date_iso, track_code, distance, box, weight, finish_pos,
                race_time, winner_time, margin, split_1, pir, sp_odds, grade
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dog_id, run_date, track_code, race_time) DO UPDATE SET
                run_date_iso=COALESCE(excluded.run_date_iso, past_performances.run_date_iso),
                box=excluded.box,
                weight=excluded.weight,
                finish_pos=excluded.finish_pos,
                winner_time=excluded.winner_time,
                margin=excluded.margin,
                split_1=excluded.split_1,
                pir=excluded.pir,
                sp_odds=excluded.sp_odds,
                grade=excluded.grade
            """, (
                dog_id,
                r["run_date"],
                iso_date,
                r["track_code"],
                r["distance"],
                r["box"],
                r["weight"],
                r["finish_pos"],
                r["race_time"],
                r["winner_time"],
                r["margin"],
                r["split_1"],
                r["pir"],
                r["sp_odds"],
                r["grade"]
            ))

        conn.commit()
        conn.close()
        return data

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            resp = self.client.get(url)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        import subprocess
        try:
            cmd = [
                "curl.exe", "-s", "-L",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                url
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except Exception:
            pass
        return None

    def find_profile_url_by_name(self, dog_name: str) -> Optional[str]:
        """Search TheDogs for dog profile URL if not already stored."""
        clean_name = re.sub(r"[^\w\s]", "", dog_name).strip()
        encoded = urllib.parse.quote_plus(clean_name)
        url = f"https://www.thedogs.com.au/search?query={encoded}"
        html = self._fetch_html(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=lambda h: h and "/dogs/" in h)
        for a in links:
            txt = a.get_text(strip=True).upper()
            if txt == clean_name.upper():
                return a["href"]
        if links:
            return links[0]["href"]
        return None

    def get_dog_dossier_from_db(self, dog_id: int) -> Dict[str, Any]:
        """Loads complete dossier from DB; fallback to live sync if past_performances is empty."""
        conn = get_connection()
        c = conn.cursor()

        c.execute("""
        SELECT id, dog_name, career_starts, career_wins, career_seconds, career_thirds,
               prizemoney, profile_url, pb_times_json, box_record_json
        FROM greyhounds WHERE id = ?
        """, (dog_id,))
        g = c.fetchone()
        if not g:
            conn.close()
            return {}

        c.execute("""
        SELECT run_date, track_code, distance, box, weight, finish_pos,
               race_time, winner_time, margin, split_1, pir, sp_odds, grade
        FROM past_performances
        WHERE dog_id = ?
        ORDER BY coalesce(run_date_iso, run_date) DESC, id ASC
        """, (dog_id,))
        runs = [dict(row) for row in c.fetchall()]

        # If no runs in DB, attempt live sync via profile_url or search by name
        if not runs:
            target_url = g["profile_url"]
            if not target_url:
                target_url = self.find_profile_url_by_name(g["dog_name"])
            if target_url:
                conn.close()
                synced = self.sync_dog_history_to_db(dog_id, target_url)
                if synced:
                    return synced
                conn = get_connection()
                c = conn.cursor()


        pbs = json.loads(g["pb_times_json"]) if g["pb_times_json"] else []
        box_stats = json.loads(g["box_record_json"]) if g["box_record_json"] else {}

        conn.close()
        return {
            "dog_id": g["id"],
            "dog_name": g["dog_name"],
            "total_starts": g["career_starts"] or len(runs),
            "total_wins": g["career_wins"] or sum(1 for r in runs if "1st" in str(r.get("finish_pos", ""))),
            "total_seconds": g["career_seconds"] or sum(1 for r in runs if "2nd" in str(r.get("finish_pos", ""))),
            "total_thirds": g["career_thirds"] or sum(1 for r in runs if "3rd" in str(r.get("finish_pos", ""))),
            "personal_bests": pbs,
            "box_stats": box_stats,
            "career_runs": runs
        }


if __name__ == "__main__":
    collector = DogHistoryCollector()
    print("Testing live sync on Cheam (Dog ID 360)...")
    res = collector.sync_dog_history_to_db(360, "/dogs/438134/cheam")
    print("Synced Successfully!")
    print(f"Dog: {res['dog_name']} | Starts: {res['total_starts']} | Wins: {res['total_wins']}")
    print(f"Personal Bests: {res['personal_bests']}")
    print(f"Box Stats: {res['box_stats']}")
