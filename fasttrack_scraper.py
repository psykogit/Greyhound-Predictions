"""
FastTrack GRV Scraper & Data Collector.
Extracts meeting cards, races, dog entries, career records, box records, and detailed sectional past performances.
"""

import re
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from db import get_connection


class FastTrackScraper:
    BASE_URL = "https://fasttrack.grv.org.au"

    def __init__(self, timeout: float = 30.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        self.client = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=True)

    def fetch_meeting_links(self) -> List[str]:
        """Fetch active/upcoming meeting URLs from FastTrack homepage."""
        try:
            r = self.client.get(self.BASE_URL)
            r.raise_for_status()
            try:
                soup = BeautifulSoup(r.text, "lxml")
            except Exception:
                soup = BeautifulSoup(r.text, "html.parser")
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/RaceField/ViewRaces/" in href and href not in links:
                    full_url = self.BASE_URL + href if href.startswith("/") else href
                    links.append(full_url)
            return links
        except Exception as e:
            print(f"[FastTrack] Error fetching home meeting links: {e}")
            return []

    def parse_meeting(self, meeting_url: str) -> Optional[Dict[str, Any]]:
        """Download and parse an entire meeting field with all races and runner histories."""
        print(f"[FastTrack] Scraping meeting: {meeting_url}")
        try:
            resp = self.client.get(meeting_url)
            resp.raise_for_status()
        except Exception as e:
            print(f"[FastTrack] Failed to fetch {meeting_url}: {e}")
            return None

        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")
        # Robust track and date extraction: search headings for valid date format
        title_candidates = [el.get_text(" ", strip=True) for el in soup.find_all(["h1", "h2", "h3", "title"])]
        track_name = ""
        meeting_date = ""

        date_pattern = re.compile(r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b')
        for text in title_candidates:
            if "Form Guides" in text or text.strip().endswith("Guides:"):
                continue
            date_match = date_pattern.search(text)
            if date_match:
                raw_date = date_match.group(1)
                try:
                    parts = re.split(r'[/-]', raw_date)
                    if len(parts) == 3:
                        if len(parts[0]) == 4:  # YYYY-MM-DD
                            meeting_date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                        else:  # DD/MM/YYYY
                            meeting_date = f"{parts[2]}-{int(parts[1]):02d}-{int(parts[0]):02d}"
                except Exception:
                    meeting_date = raw_date
                
                track_part = text[:date_match.start()].strip()
                if track_part:
                    track_name = re.sub(r'^(FastTrack\s*-\s*|Meeting\s*:\s*)', '', track_part, flags=re.I).strip()
                break

        if not meeting_date or not track_name or "Guides" in meeting_date:
            header_el = soup.find("h2") or soup.find("h1")
            meeting_title = header_el.get_text(strip=True) if header_el else ""
            if "Guides" in meeting_title:
                print(f"[FastTrack] Skipping invalid meeting title '{meeting_title}' for {meeting_url}")
                return None
            parts = meeting_title.split(" ")
            if len(parts) > 1 and re.match(r'^\d{4}-\d{2}-\d{2}$', parts[-1]):
                track_name = " ".join(parts[:-1]).strip()
                meeting_date = parts[-1].strip()
            else:
                print(f"[FastTrack] Could not extract valid meeting date from '{meeting_title}' for {meeting_url}")
                return None

        # Extract meeting ID from URL
        match_id = re.search(r'/ViewRaces/([-\d]+)', meeting_url)
        source_id = match_id.group(1) if match_id else meeting_url

        meeting_data = {
            "source_id": source_id,
            "track_name": track_name.strip(),
            "meeting_date": meeting_date.strip(),
            "source": "fasttrack",
            "races": []
        }

        # Track races: Find tables with 'Box' and 'Greyhound' in headers
        race_idx = 1
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if "Box" in headers and "Greyhound" in headers:
                # Find previous heading for distance / grade if present
                prev_text = ""
                prev_el = table.find_previous(["h3", "h4", "div", "p"])
                if prev_el:
                    prev_text = prev_el.get_text(" ", strip=True)

                dist_match = re.search(r'(\d{3,4})m', prev_text)
                distance = int(dist_match.group(1)) if dist_match else None

                race = {
                    "race_number": race_idx,
                    "race_name": f"Race {race_idx}",
                    "distance": distance,
                    "runners": []
                }

                for tr in table.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 8 and tds[1].get_text(strip=True).isdigit():
                        box_num = int(tds[1].get_text(strip=True))
                        dog_raw = tds[2].get_text(strip=True)
                        m_dog = re.match(r'^(.*?)(?:\s*\((.*?)\))?(?:\[(.*?)\])?$', dog_raw)
                        dog_name = m_dog.group(1).strip() if m_dog else dog_raw

                        trainer_name = tds[6].get_text(strip=True)
                        form_str = tds[3].get_text(strip=True)
                        career_str = tds[4].get_text(strip=True)

                        # Parse detailed past runs inside next sibling tr
                        past_runs = []
                        nxt_tr = tr.find_next_sibling("tr")
                        if nxt_tr:
                            for sub_tr in nxt_tr.find_all("tr"):
                                sub_tds = [td.get_text(strip=True) for td in sub_tr.find_all("td")]
                                if len(sub_tds) >= 16 and any(sub_tds[1].endswith(suf) for suf in ['st', 'nd', 'rd', 'th']):
                                    # Convert clean numerical values where applicable
                                    def to_float(val: str) -> Optional[float]:
                                        v = re.sub(r'[^\d.]', '', val)
                                        return float(v) if v else None

                                    past_runs.append({
                                        "finish_pos": sub_tds[1],
                                        "box": int(sub_tds[2]) if sub_tds[2].isdigit() else None,
                                        "weight": to_float(sub_tds[3]),
                                        "distance": int(sub_tds[4]) if sub_tds[4].isdigit() else None,
                                        "track_code": sub_tds[5],
                                        "run_date": sub_tds[6],
                                        "race_time": to_float(sub_tds[7]),
                                        "winner_time": to_float(sub_tds[8]),
                                        "margin": to_float(sub_tds[9]),
                                        "pir": sub_tds[11] if len(sub_tds) > 11 else "",
                                        "split_1": to_float(sub_tds[15]) if len(sub_tds) > 15 else None,
                                        "sp_odds": to_float(sub_tds[16]) if len(sub_tds) > 16 else None,
                                        "grade": sub_tds[17] if len(sub_tds) > 17 else ""
                                    })

                        # Deduplicate past runs by (run_date, track_code, race_time)
                        dedup = []
                        seen_keys = set()
                        for pr in past_runs:
                            k = (pr["run_date"], pr["track_code"], pr["race_time"])
                            if k not in seen_keys:
                                seen_keys.add(k)
                                dedup.append(pr)

                        race["runners"].append({
                            "box_number": box_num,
                            "dog_name": dog_name,
                            "trainer_name": trainer_name,
                            "form_string": form_str,
                            "career_string": career_str,
                            "past_performances": dedup
                        })

                if race["runners"]:
                    meeting_data["races"].append(race)
                    race_idx += 1

        return meeting_data

    def save_to_db(self, meeting_data: Dict[str, Any]) -> int:
        """Persist structured meeting, races, dogs, and past performances to SQLite."""
        conn = get_connection()
        cursor = conn.cursor()

        # 1. Upsert meeting
        cursor.execute("""
        INSERT INTO meetings (source_id, track_name, meeting_date, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET track_name=excluded.track_name, meeting_date=excluded.meeting_date
        """, (
            meeting_data["source_id"],
            meeting_data["track_name"],
            meeting_data["meeting_date"],
            meeting_data["source"]
        ))
        cursor.execute("SELECT id FROM meetings WHERE source_id = ?", (meeting_data["source_id"],))
        meeting_id = cursor.fetchone()["id"]

        for race in meeting_data["races"]:
            source_race_id = f"{meeting_id}_R{race['race_number']}"
            cursor.execute("""
            INSERT INTO races (source_race_id, meeting_id, race_number, race_name, distance)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_race_id) DO UPDATE SET distance=excluded.distance
            """, (
                source_race_id,
                meeting_id,
                race["race_number"],
                race["race_name"],
                race["distance"]
            ))
            cursor.execute("SELECT id FROM races WHERE source_race_id = ?", (source_race_id,))
            race_id = cursor.fetchone()["id"]

            for runner in race["runners"]:
                # Upsert Greyhound
                cursor.execute("""
                INSERT OR IGNORE INTO greyhounds (dog_name) VALUES (?)
                """, (runner["dog_name"],))
                cursor.execute("SELECT id FROM greyhounds WHERE dog_name = ?", (runner["dog_name"],))
                dog_id = cursor.fetchone()["id"]

                # Upsert Trainer
                trainer_id = None
                if runner["trainer_name"]:
                    cursor.execute("""
                    INSERT OR IGNORE INTO trainers (trainer_name) VALUES (?)
                    """, (runner["trainer_name"],))
                    cursor.execute("SELECT id FROM trainers WHERE trainer_name = ?", (runner["trainer_name"],))
                    trainer_id = cursor.fetchone()["id"]

                # Upsert Race Runner
                cursor.execute("""
                INSERT INTO race_runners (race_id, dog_id, trainer_id, box_number, form_string)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(race_id, box_number) DO UPDATE SET
                    dog_id=excluded.dog_id,
                    trainer_id=excluded.trainer_id,
                    form_string=excluded.form_string
                """, (
                    race_id,
                    dog_id,
                    trainer_id,
                    runner["box_number"],
                    runner["form_string"]
                ))

                # Insert Past Performances
                for pp in runner["past_performances"]:
                    cursor.execute("""
                    INSERT OR IGNORE INTO past_performances (
                        dog_id, run_date, track_code, distance, box, weight, finish_pos,
                        race_time, winner_time, margin, split_1, pir, sp_odds, grade
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        dog_id,
                        pp["run_date"],
                        pp["track_code"],
                        pp["distance"],
                        pp["box"],
                        pp["weight"],
                        pp["finish_pos"],
                        pp["race_time"],
                        pp["winner_time"],
                        pp["margin"],
                        pp["split_1"],
                        pp["pir"],
                        pp["sp_odds"],
                        pp["grade"]
                    ))

        conn.commit()
        conn.close()
        return meeting_id


if __name__ == "__main__":
    scraper = FastTrackScraper()
    links = scraper.fetch_meeting_links()
    print(f"Found {len(links)} FastTrack meetings.")
    if links:
        sample_url = links[0]
        data = scraper.parse_meeting(sample_url)
        if data:
            print(f"Parsed {data['track_name']} with {len(data['races'])} races.")
            m_id = scraper.save_to_db(data)
            print(f"Successfully saved meeting to database! (Meeting ID: {m_id})")
