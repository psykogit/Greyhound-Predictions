"""
Live Odds Poller & Market Feed Service.
Extracts live pre-race fixed win odds and market fluctuations across upcoming Australian greyhound races.
"""

import re
import httpx
from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional
from db import get_connection


class LiveOddsPoller:
    BASE_URL = "https://www.thedogs.com.au"

    def __init__(self, timeout: float = 20.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.thedogs.com.au/racing"
        }
        self.client = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=True)

    def fetch_race_live_odds(self, race_url: str) -> Dict[int, float]:
        """
        Fetches live pre-race fixed odds for a given race URL by querying the /odds endpoint.
        Returns a dictionary mapping box_number -> live_fixed_odds.
        """
        # Ensure url ends with /odds
        base_clean = race_url.split("?")[0].rstrip("/")
        if not base_clean.endswith("/odds"):
            odds_url = f"{base_clean}/odds"
        else:
            odds_url = base_clean

        try:
            resp = self.client.get(odds_url)
            if resp.status_code != 200:
                return {}
        except Exception as e:
            print(f"[LiveOddsPoller] Request failed for {odds_url}: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "lxml")
        odds_map: Dict[int, float] = {}

        rows = soup.find_all("tr", class_=lambda c: c and "race-runner" in c)
        for row in rows:
            box_sprite = row.find("sprite-svg")
            if not box_sprite:
                continue

            box_str = box_sprite.get("name", "")
            m_box = re.search(r"\d+", box_str)
            if not m_box:
                continue
            box_num = int(m_box.group(0))

            # Look for decimal odds in the row cells (e.g., '2.35', '12.00', '$4.60')
            tds = row.find_all("td")
            found_odds = None
            for td in tds:
                txt = td.get_text(strip=True).replace("$", "")
                if re.match(r"^\d{1,3}\.\d{2}$", txt):
                    val = float(txt)
                    if 1.01 <= val <= 501.0:
                        found_odds = val
                        break

            if found_odds:
                odds_map[box_num] = found_odds

        return odds_map

    def update_all_upcoming_odds(self) -> int:
        """
        Scans all upcoming races in the database and updates their runners with live market odds.
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Find upcoming races (where finish_position is still NULL)
        cursor.execute("""
        SELECT DISTINCT r.id, m.track_name, r.race_number, m.meeting_date
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        JOIN race_runners rr ON rr.race_id = r.id
        WHERE rr.finish_position IS NULL
        """)
        upcoming_races = cursor.fetchall()

        updated_runners = 0
        for r in upcoming_races:
            race_id = r["id"]
            track_slug = r["track_name"].lower().replace(" ", "-")
            date_str = r["meeting_date"]
            race_num = r["race_number"]

            # Construct /odds search path pattern
            # Fetch matching race URL from TheDogs if known, or construct standard route
            odds_url = f"{self.BASE_URL}/racing/{track_slug}/{date_str}/{race_num}/odds"
            odds_map = self.fetch_race_live_odds(odds_url)

            if not odds_map:
                # Try finding exact race URL from meeting page
                continue

            for box_num, odds_val in odds_map.items():
                cursor.execute("""
                UPDATE race_runners
                SET sp_odds = ?
                WHERE race_id = ? AND box_number = ?
                """, (odds_val, race_id, box_num))
                updated_runners += cursor.rowcount

        conn.commit()
        conn.close()
        return updated_runners


if __name__ == "__main__":
    poller = LiveOddsPoller()
    # Test on Sandown R5
    sample_url = "https://www.thedogs.com.au/racing/sandown/2026-10-05/5/sportsbet-ht1-1-6-wins/odds"
    odds = poller.fetch_race_live_odds(sample_url)
    print(f"Live odds fetched for Sandown R5: {odds}")
