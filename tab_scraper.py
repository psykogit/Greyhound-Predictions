"""
TAB (Sky Racing) Greyhound Scraper.
Connects to the official TAB API to extract scheduled races and 'Sky Racing Tips'.
"""

import httpx
from datetime import datetime
from typing import Dict, Any, List, Optional
import time

class TABScraper:
    def __init__(self, timeout: float = 15.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"
        }
        self.client = httpx.Client(headers=self.headers, timeout=timeout)

    def fetch_today_meetings(self, jurisdiction: str = "VIC") -> List[Dict[str, Any]]:
        """Fetch all meetings for today."""
        url = f"https://api.beta.tab.com.au/v1/tab-info-service/racing/dates/today/meetings?jurisdiction={jurisdiction}"
        try:
            res = self.client.get(url)
            res.raise_for_status()
            return res.json().get("meetings", [])
        except Exception as e:
            print(f"[TAB] Error fetching meetings: {e}")
            return []

    def fetch_race_tips(self) -> List[Dict[str, Any]]:
        """
        Scans all greyhound races for today and extracts Sky Racing tips.
        Returns a list of dicts with track, race_number, and tips array.
        """
        all_tips = []
        meetings = []
        for state in ['VIC', 'NSW', 'QLD', 'SA', 'WA', 'TAS']:
            meetings.extend(self.fetch_today_meetings(jurisdiction=state))
        
        # Filter for Greyhound meetings
        g_meetings = [m for m in meetings if m.get("raceType") == "G"]
        
        for m in g_meetings:
            track = m.get("meetingName")
            races_url = m.get("_links", {}).get("races")
            if not races_url:
                continue
                
            try:
                races_res = self.client.get(races_url)
                if races_res.status_code != 200:
                    continue
                races_data = races_res.json().get("races", [])
                
                for r in races_data:
                    race_num = r.get("raceNumber")
                    detail_url = r.get("_links", {}).get("self")
                    if not detail_url:
                        continue
                        
                    detail_res = self.client.get(detail_url)
                    if detail_res.status_code == 200:
                        detail = detail_res.json()
                        tips_obj = detail.get("tips", {})
                        if tips_obj and "tipRunnerNumbers" in tips_obj:
                            all_tips.append({
                                "track": track,
                                "race_number": race_num,
                                "tipster": tips_obj.get("tipster", "Sky Racing"),
                                "tips": tips_obj["tipRunnerNumbers"]
                            })
                            print(f"[TAB] Scraped Tips for {track} R{race_num}: {tips_obj['tipRunnerNumbers']}")
                            
                    time.sleep(0.1) # Be nice to the API
            except Exception as e:
                print(f"[TAB] Error fetching tips for {track}: {e}")
                
        return all_tips

if __name__ == "__main__":
    t = TABScraper()
    tips = t.fetch_race_tips()
    print(f"Total races with tips found: {len(tips)}")
