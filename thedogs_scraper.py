"""
TheDogs.com.au Scraper & Real-time Odds/Results Collector.
Scrapes upcoming and past race cards across Australia with average first sectionals (speedmap),
form strings, track/distance records, running styles, and live starting prices.
"""

import re
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from db import get_connection


class TheDogsScraper:
    BASE_URL = "https://www.thedogs.com.au"

    def __init__(self, timeout: float = 25.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.thedogs.com.au/racing"
        }
        self.client = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=True)
        # Initialize cookies/session
        try:
            self.client.get(f"{self.BASE_URL}/racing")
        except Exception as e:
            print(f"[TheDogs] Warning initializing session: {e}")

    def fetch_today_race_links(self) -> List[str]:
        """Fetch all individual race URLs scheduled or run today."""
        try:
            resp = self.client.get(f"{self.BASE_URL}/racing")
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Match /racing/{track}/{date}/{race_number}/{slug}
                if re.search(r"^/racing/[a-z0-9\-]+/\d{4}-\d{2}-\d{2}/\d+/[a-z0-9\-]+", href):
                    full_url = self.BASE_URL + href.split("?")[0]
                    if full_url not in links:
                        links.append(full_url)
            return links
        except Exception as e:
            print(f"[TheDogs] Error fetching race links: {e}")
            return []

    def parse_race(self, race_url: str) -> Optional[Dict[str, Any]]:
        """Extract track, distance, grade, runners, split speedmaps, form, and starting prices."""
        try:
            resp = self.client.get(race_url)
            resp.raise_for_status()
        except Exception as e:
            print(f"[TheDogs] Failed to fetch race at {race_url}: {e}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        
        match = re.search(r"/racing/([a-z0-9\-]+)/(\d{4}-\d{2}-\d{2})/(\d+)", race_url)
        if not match:
            return None
        
        track_slug, meeting_date, race_number = match.groups()
        track_name = track_slug.replace("-", " ").title()
        # Clean commercial prefixes (e.g. 'Ladbrokes Q Straight' -> 'Q Straight', 'Sportsbet Ballarat' -> 'Ballarat')
        for sponsor in ["Ladbrokes ", "Sportsbet ", "Tab "]:
            if track_name.startswith(sponsor):
                track_name = track_name[len(sponsor):].strip()

        # Parse Race Name & Grade & Distance from header
        race_name = ""
        distance = None
        grade = ""
        start_time = None
        header_info = soup.find("div", class_="race-header__info")
        if header_info:
            name_el = header_info.find("div", class_="race-header__info__name")
            if name_el:
                race_name = name_el.get_text(strip=True)
            grade_el = header_info.find("div", class_="race-header__info__grade")
            if grade_el:
                grade_txt = grade_el.get_text(" ", strip=True)
                dist_match = re.search(r"(\d{3,4})m", grade_txt)
                if dist_match:
                    distance = int(dist_match.group(1))
                grade = grade_txt
            # Extract start time if present (e.g. '21:01 05 Oct')
            time_match = re.search(r"(\d{2}:\d{2})\s+\d{2}\s+[A-Za-z]{3}", header_info.get_text())
            if time_match:
                start_time = time_match.group(1)

        # Extract Expert Tips if present (e.g. [1, 2, 4, 8])
        expert_tips = []
        tips_div = soup.find(class_=re.compile(r"race-runners__tips|tabs-content__tips"))
        if tips_div:
            for m in re.finditer(r"rug_(\d+)", str(tips_div)):
                b = int(m.group(1))
                if b not in expert_tips:
                    expert_tips.append(b)

        runners = []
        runner_rows = soup.find_all("tr", class_=lambda c: c and "race-runner" in c)
        for row in runner_rows:
            # Box number from rug sprite
            box_sprite = row.find("sprite-svg")
            box_str = box_sprite.get("name", "") if box_sprite else ""
            m_box = re.search(r"\d+", box_str)
            box_num = int(m_box.group(0)) if m_box else None

            # Dog name, profile URL, best track time, and reserve reallocation
            dog_el = row.find("div", class_="race-runners__name__dog")
            dog_text = dog_el.get_text(" ", strip=True) if dog_el else ""
            if "vacant" in dog_text.lower():
                continue

            dog_a = dog_el.find("a") if dog_el else None
            profile_url = dog_a["href"] if dog_a and dog_a.has_attr("href") else None

            # Extract reserve slot into box (e.g. '(into box 6)')
            into_box_match = re.search(r"\(into box (\d+)\)", dog_text, re.IGNORECASE)
            into_box = int(into_box_match.group(1)) if into_box_match else None

            # Extract best track time (e.g. 16.84, 29.87)
            time_match = re.search(r"\b(\d{2}\.\d{2})\b", dog_text)
            extracted_best_time = float(time_match.group(1)) if time_match else None

            # Clean dog name (strip best time tag and into box tag)
            clean_name = re.sub(r"\(into box \d+\)", "", dog_text, flags=re.IGNORECASE)
            clean_name = re.sub(r"\b(?:NBT|\d{2}\.\d{2})\b", "", clean_name)
            dog_name = re.sub(r"\s+", " ", clean_name).strip()

            # Trainer
            trainer_el = row.find("div", class_="race-runners__name__trainer")
            trainer_text = trainer_el.get_text(" ", strip=True) if trainer_el else ""
            trainer_match = re.search(r"T:\s*([^R]+)", trainer_text)
            trainer_name = trainer_match.group(1).strip() if trainer_match else trainer_text

            # Running style trait (e.g. RR, FM, Wide)
            trait_el = row.find("td", class_="race-runners__track-sa-trait")
            running_style = trait_el.get_text(strip=True) if trait_el else ""

            # Last 4 Form String (e.g. '5164', '7211')
            last_four_el = row.find("td", class_="race-runners__last-four")
            form_string = last_four_el.get_text(strip=True) if last_four_el else ""

            # Average 1st Sectional (Speedmap) e.g. 5.16s
            speedmap_el = row.find("td", class_="race-runners__speedmap")
            speedmap_txt = speedmap_el.get_text(strip=True) if speedmap_el else ""
            avg_sec1 = float(speedmap_txt) if re.match(r"^\d+\.\d+$", speedmap_txt) else None

            # Finish pos (if completed race)
            pos_el = row.find("td", class_="race-runners__finish-position")
            pos_text = pos_el.get_text(strip=True) if pos_el else ""
            pos_num = int(re.sub(r"\D", "", pos_text)) if re.search(r"\d+", pos_text) else None

            # Weight
            wgt_el = row.find("td", class_="race-runners__weight")
            wgt = float(wgt_el.get_text(strip=True)) if wgt_el and wgt_el.get_text(strip=True) else None

            # Sectionals (if completed race)
            secs = [s.get_text(strip=True) for s in row.find_all("td", class_="race-runners__sectional")]
            split_1 = float(secs[0]) if len(secs) > 0 and secs[0] else avg_sec1
            split_2 = float(secs[1]) if len(secs) > 1 and secs[1] else None

            # Race time
            time_el = row.find("td", class_="race-runners__time")
            race_time = float(time_el.get_text(strip=True)) if time_el and time_el.get_text(strip=True) else None

            # Starting price / Odds
            sp_el = row.find("td", class_="race-runners__starting-price")
            sp_val = None
            sp_text = sp_el.get_text(strip=True).upper() if sp_el else ""
            if sp_el and sp_el.get_text(strip=True):
                raw_sp = sp_el.get_text(strip=True).replace("$", "")
                try:
                    sp_val = float(raw_sp)
                except ValueError:
                    sp_val = None

            # Check if runner is scratched ('SCR' in finish pos or price cell, or row class)
            row_classes = row.get("class", [])
            is_scratched = 1 if (
                any("scratched" in c.lower() for c in row_classes)
                or pos_text == "SCR"
                or sp_text == "SCR"
                or bool(re.search(r"\bSCR(?:ATCHED)?\b", pos_text))
                or bool(re.search(r"\bSCR(?:ATCHED)?\b", sp_text))
            ) else 0

            runners.append({
                "box_number": box_num,
                "dog_name": dog_name,
                "trainer_name": trainer_name,
                "running_style": running_style,
                "form_string": form_string,
                "finish_position": pos_num,
                "weight": wgt,
                "split_1": split_1,
                "split_2": split_2,
                "race_time": race_time,
                "best_time": extracted_best_time,
                "into_box": into_box,
                "profile_url": profile_url,
                "sp_odds": sp_val,
                "is_scratched": is_scratched
            })

        return {
            "track_name": track_name,
            "meeting_date": meeting_date,
            "race_number": int(race_number),
            "race_name": race_name,
            "distance": distance,
            "grade": grade,
            "start_time": start_time,
            "race_url": race_url,
            "expert_tips": expert_tips,
            "runners": runners
        }

    def save_race_to_db(self, race_data: Dict[str, Any]) -> int:
        """Save parsed race and runner results/odds to SQLite."""
        conn = get_connection()
        cursor = conn.cursor()

        # 1. Upsert meeting
        source_id = f"thedogs_{race_data['track_name']}_{race_data['meeting_date']}"
        cursor.execute("""
        INSERT INTO meetings (source_id, track_name, meeting_date, source)
        VALUES (?, ?, ?, 'thedogs')
        ON CONFLICT(source_id) DO UPDATE SET track_name=excluded.track_name
        """, (source_id, race_data["track_name"], race_data["meeting_date"]))
        
        cursor.execute("SELECT id FROM meetings WHERE source_id = ?", (source_id,))
        meeting_id = cursor.fetchone()["id"]

        # 2. Upsert race
        source_race_id = f"{meeting_id}_R{race_data['race_number']}"
        race_url_val = race_data.get("race_url")
        expert_tips_val = ",".join(map(str, race_data.get("expert_tips", []))) if race_data.get("expert_tips") else None
        cursor.execute("""
        INSERT INTO races (source_race_id, meeting_id, race_number, race_name, distance, grade, start_time, race_url, expert_tips)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_race_id) DO UPDATE SET
            distance=excluded.distance,
            grade=excluded.grade,
            race_name=excluded.race_name,
            start_time=excluded.start_time,
            race_url=coalesce(excluded.race_url, races.race_url),
            expert_tips=coalesce(excluded.expert_tips, races.expert_tips)
        """, (
            source_race_id,
            meeting_id,
            race_data["race_number"],
            race_data["race_name"],
            race_data["distance"],
            race_data["grade"],
            race_data["start_time"],
            race_url_val,
            expert_tips_val
        ))
        cursor.execute("SELECT id FROM races WHERE source_race_id = ?", (source_race_id,))
        race_id = cursor.fetchone()["id"]

        # 3. Upsert runners
        for r in race_data["runners"]:
            if not r["dog_name"]:
                continue

            # Greyhound
            cursor.execute("INSERT OR IGNORE INTO greyhounds (dog_name) VALUES (?)", (r["dog_name"],))
            cursor.execute("SELECT id FROM greyhounds WHERE dog_name = ?", (r["dog_name"],))
            dog_id = cursor.fetchone()["id"]
            if r.get("profile_url"):
                cursor.execute("UPDATE greyhounds SET profile_url = ? WHERE id = ?", (r["profile_url"], dog_id))

            # Trainer
            trainer_id = None
            if r["trainer_name"]:
                cursor.execute("INSERT OR IGNORE INTO trainers (trainer_name) VALUES (?)", (r["trainer_name"],))
                cursor.execute("SELECT id FROM trainers WHERE trainer_name = ?", (r["trainer_name"],))
                trainer_id = cursor.fetchone()["id"]

            # Race Runner
            cursor.execute("""
            INSERT INTO race_runners (
                race_id, dog_id, trainer_id, box_number, weight, running_style,
                form_string, finish_position, race_time, split_1, split_2, sp_odds, is_scratched, best_time, into_box
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_id, box_number) DO UPDATE SET
                dog_id=excluded.dog_id,
                trainer_id=excluded.trainer_id,
                weight=COALESCE(excluded.weight, race_runners.weight),
                running_style=COALESCE(NULLIF(excluded.running_style, ''), race_runners.running_style),
                form_string=COALESCE(NULLIF(excluded.form_string, ''), race_runners.form_string),
                finish_position=COALESCE(excluded.finish_position, race_runners.finish_position),
                race_time=COALESCE(excluded.race_time, race_runners.race_time),
                split_1=COALESCE(excluded.split_1, race_runners.split_1),
                split_2=COALESCE(excluded.split_2, race_runners.split_2),
                sp_odds=COALESCE(excluded.sp_odds, race_runners.sp_odds),
                is_scratched=COALESCE(excluded.is_scratched, race_runners.is_scratched),
                best_time=COALESCE(excluded.best_time, race_runners.best_time),
                into_box=COALESCE(excluded.into_box, race_runners.into_box)
            """, (
                race_id,
                dog_id,
                trainer_id,
                r["box_number"],
                r["weight"],
                r["running_style"],
                r["form_string"],
                r["finish_position"],
                r["race_time"],
                r["split_1"],
                r["split_2"],
                r["sp_odds"],
                r.get("is_scratched", 0),
                r.get("best_time"),
                r.get("into_box")
            ))

        conn.commit()
        conn.close()
        return race_id


if __name__ == "__main__":
    scraper = TheDogsScraper()
    links = scraper.fetch_today_race_links()
    print(f"Discovered {len(links)} today's race links on TheDogs.com.au")
    if links:
        sample = links[0]
        data = scraper.parse_race(sample)
        if data:
            print(f"Parsed {data['track_name']} R{data['race_number']} ({data['distance']}m) with {len(data['runners'])} runners.")
            r_id = scraper.save_race_to_db(data)
            print(f"Saved race to DB (ID: {r_id})")
