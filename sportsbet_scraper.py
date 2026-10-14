"""
Sportsbet Greyhound Racing Scraper & Live Odds Service.
Queries Sportsbet's official racing gateway endpoints to extract:
  - Today's Greyhound meetings and scheduled races
  - Imminent "Next to Jump" domestic events
  - Live fixed win & place prices, market fluctuations, and scratchings
"""

import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import httpx

from db import get_connection

AEDT_TZ = timezone(timedelta(hours=11))


def normalize_string(s: str) -> str:
    """Normalize names for fuzzy/clean matching."""
    if not s:
        return ""
    # Normalize unicode
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


class SportsbetScraper:
    BASE_URL = "https://www.sportsbet.com.au"
    API_BASE = "https://www.sportsbet.com.au/apigw"

    def __init__(self, timeout: float = 15.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-AU,en-US;q=0.9,en;q=0.8",
            "Referer": "https://www.sportsbet.com.au/greyhound-racing"
        }
        self.client = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=True)

    def fetch_next_events(self) -> List[Dict[str, Any]]:
        """Fetch the next 40 domestic greyhound races to jump."""
        url = f"{self.API_BASE}/sportsbook-racing/Sportsbook/Racing/NextEvents?racingFilters=GH_DOMESTIC&groupByFilters=true"
        try:
            resp = self.client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"[Sportsbet] Failed fetching NextEvents: {e}")
        return []

    def fetch_all_racing_today(self, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch all meetings and races for a date (format YYYY-MM-DD).
        Filters specifically for 'Greyhound Racing'.
        """
        if not date_str:
            date_str = datetime.now(AEDT_TZ).strftime("%Y-%m-%d")

        url = f"{self.API_BASE}/sportsbook-racing/Sportsbook/Racing/AllRacing/{date_str}"
        events_list: List[Dict[str, Any]] = []

        try:
            resp = self.client.get(url)
            if resp.status_code != 200:
                return []

            data = resp.json()
            dates = data.get("dates", [])
            if not dates:
                return []

            sections = dates[0].get("sections", [])
            for section in sections:
                for meeting in section.get("meetings", []):
                    cls_name = meeting.get("className", "")
                    if "greyhound" not in cls_name.lower():
                        continue

                    track_name = meeting.get("name", "")
                    meeting_id = meeting.get("id")

                    for ev in meeting.get("events", []):
                        ev_copy = dict(ev)
                        ev_copy["competitionName"] = track_name
                        ev_copy["meetingId"] = meeting_id
                        events_list.append(ev_copy)

        except Exception as e:
            print(f"[Sportsbet] Failed fetching AllRacing: {e}")

        return events_list

    def fetch_racecards_batch(self, event_ids: List[int]) -> Dict[str, Any]:
        """
        Batch query racecards for up to 15 event IDs using MultipleRacecards endpoint.
        Returns a dict of event_id_str -> racecard_dict.
        """
        if not event_ids:
            return {}

        id_str = ",".join(str(eid) for eid in event_ids)
        url = f"{self.API_BASE}/sportsbook-racing/Sportsbook/Racing/Events/MultipleRacecards?eventIds={id_str}"
        try:
            resp = self.client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                events = data.get("events", [])
                if isinstance(events, list):
                    return {str(item.get("id")): item for item in events if item.get("id")}
                elif isinstance(events, dict):
                    return events
        except Exception as e:
            print(f"[Sportsbet] Error fetching racecards batch: {e}")
        return {}

    def parse_racecard_runners(self, racecard: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract runner details (prices, box, scratching status) from a single racecard.
        """
        runners = []
        markets = racecard.get("markets", [])
        if not markets:
            return []

        # Find 'Win or Place' or primary win market
        win_market = None
        for m in markets:
            m_name = (m.get("name") or "").lower()
            if "win or place" in m_name or "win" in m_name:
                win_market = m
                break
        if not win_market:
            win_market = markets[0]

        selections = win_market.get("selections", [])
        for sel in selections:
            name = sel.get("name", "")
            if not name or "vacant" in name.lower():
                continue

            # Determine box/barrier
            draw_num = sel.get("drawNumber") or sel.get("runnerNumber")
            is_scratched = 1 if (sel.get("isOut") or sel.get("result") == "V") else 0

            # Find fixed win & place prices
            win_price = None
            place_price = None

            prices = sel.get("prices", [])
            for p in prices:
                # "L" code is Live Fixed price
                if p.get("priceCode") == "L":
                    win_price = p.get("winPrice")
                    place_price = p.get("placePrice")
                    break

            # Fallback to any valid winPrice if "L" not explicitly found
            if win_price is None and prices:
                for p in prices:
                    if p.get("winPrice"):
                        win_price = p.get("winPrice")
                        place_price = p.get("placePrice")
                        break

            # Flucs
            flucs = sel.get("recentOddsFluctuations") or []

            runners.append({
                "selection_id": sel.get("id"),
                "dog_name": name.strip(),
                "box_number": draw_num,
                "win_price": float(win_price) if win_price is not None else None,
                "place_price": float(place_price) if place_price is not None else None,
                "is_scratched": is_scratched,
                "flucs": flucs
            })

        return runners

    def sync_sportsbet_odds(self, dates: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Fetches events from Sportsbet across specified dates (or yesterday/today/tomorrow to bridge AEDT midnight),
        matches them with local database meetings/races, and updates race_runners with Sportsbet win and place odds.
        """
        now_aedt = datetime.now(AEDT_TZ)
        d_today = now_aedt.strftime("%Y-%m-%d")
        if not dates:
            d_prev = (now_aedt - timedelta(days=1)).strftime("%Y-%m-%d")
            d_next = (now_aedt + timedelta(days=1)).strftime("%Y-%m-%d")
            dates = [d_prev, d_today, d_next]

        # 1. Fetch events across dates
        sb_events: List[Dict[str, Any]] = []
        seen_ids = set()

        for d in dates:
            for ev in self.fetch_all_racing_today(d):
                eid = ev.get("id")
                if eid and eid not in seen_ids:
                    ev["eventDate"] = d
                    sb_events.append(ev)
                    seen_ids.add(eid)

        # Supplement with NextEvents
        for ne in self.fetch_next_events():
            ne_id = ne.get("id")
            if ne_id and ne_id not in seen_ids:
                ne["eventDate"] = d_today
                sb_events.append(ne)
                seen_ids.add(ne_id)

        if not sb_events:
            return {"status": "no_events", "events_found": 0, "runners_updated": 0}

        conn = get_connection()
        c = conn.cursor()

        # Load active database meetings for the candidate dates
        placeholders = ",".join("?" for _ in dates)
        c.execute(f"""
        SELECT m.id as meeting_id, m.track_name, m.meeting_date, r.id as race_id, r.race_number
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.meeting_date IN ({placeholders})
        """, dates)
        db_races = c.fetchall()

        # Build lookup: (norm_track, meeting_date, race_number) -> race_id
        db_lookup: Dict[str, int] = {}
        for row in db_races:
            t_norm = normalize_string(row["track_name"])
            m_date = row["meeting_date"]
            r_num = row["race_number"]
            db_lookup[f"{t_norm}_{m_date}_{r_num}"] = row["race_id"]
            # First word alias (e.g., 'murray' for 'murray bridge')
            t_first = t_norm.split()[0]
            db_lookup.setdefault(f"{t_first}_{m_date}_{r_num}", row["race_id"])

        # Match Sportsbet events to database races
        matched_events: List[Dict[str, Any]] = []
        for ev in sb_events:
            ev_track = ev.get("competitionName") or ""
            r_num = ev.get("raceNumber")
            ev_id = ev.get("id")
            ev_date = ev.get("eventDate") or d_today
            if not ev_id or not r_num:
                continue

            track_norm = normalize_string(ev_track)
            key = f"{track_norm}_{ev_date}_{r_num}"
            race_id = db_lookup.get(key)
            if not race_id and " " in track_norm:
                race_id = db_lookup.get(f"{track_norm.split()[0]}_{ev_date}_{r_num}")

            if race_id:
                matched_events.append({
                    "event_id": ev_id,
                    "race_id": race_id,
                    "track": ev_track,
                    "race_number": r_num
                })

        # 2. Batch fetch racecards in chunks of 15
        total_runners_updated = 0
        total_scratched = 0
        chunk_size = 15
        event_id_list = [m["event_id"] for m in matched_events]

        for i in range(0, len(event_id_list), chunk_size):
            chunk = event_id_list[i:i + chunk_size]
            racecards_dict = self.fetch_racecards_batch(chunk)

            for m in matched_events[i:i + chunk_size]:
                ev_id_str = str(m["event_id"])
                card = racecards_dict.get(ev_id_str)
                if not card:
                    continue

                race_id = m["race_id"]
                c.execute("UPDATE races SET sportsbet_event_id = ? WHERE id = ?", (ev_id_str, race_id))

                runners = self.parse_racecard_runners(card)
                for r in runners:
                    d_norm = normalize_string(r["dog_name"])
                    box_num = r["box_number"]
                    win_p = r["win_price"]
                    place_p = r["place_price"]
                    scratched = r["is_scratched"]

                    # Find runner in DB by exact or close name match (NEVER match by box alone!)
                    c.execute("""
                    SELECT rr.id, g.dog_name, rr.box_number
                    FROM race_runners rr
                    LEFT JOIN greyhounds g ON rr.dog_id = g.id
                    WHERE rr.race_id = ?
                    """, (race_id,))
                    db_runners = c.fetchall()

                    matched_rr_id = None
                    for dbr in db_runners:
                        db_dog_norm = normalize_string(dbr["dog_name"])
                        if db_dog_norm == d_norm:
                            matched_rr_id = dbr["id"]
                            break
                        if len(d_norm) > 4 and (db_dog_norm in d_norm or d_norm in db_dog_norm):
                            matched_rr_id = dbr["id"]
                            break

                    if matched_rr_id:
                        if win_p is not None:
                            c.execute("""
                            UPDATE race_runners
                            SET sportsbet_odds = ?,
                                sportsbet_place_odds = ?,
                                is_scratched = ?
                            WHERE id = ?
                            """, (win_p, place_p, scratched, matched_rr_id))
                            try:
                                c.execute("""
                                INSERT INTO odds_snapshots (race_id, runner_id, box_number, bookmaker, win_odds, place_odds)
                                VALUES (?, ?, ?, 'sportsbet', ?, ?)
                                """, (target_race_id, matched_rr_id, box_num, win_p, place_p))
                            except Exception:
                                pass
                            total_runners_updated += 1
                        elif scratched:
                            c.execute("UPDATE race_runners SET is_scratched = 1, sportsbet_odds = NULL, sportsbet_place_odds = NULL WHERE id = ?", (matched_rr_id,))
                            total_scratched += 1

        conn.commit()
        conn.close()

        return {
            "status": "success",
            "events_scanned": len(sb_events),
            "races_matched": len(matched_events),
            "runners_updated": total_runners_updated,
            "scratched_updated": total_scratched
        }


if __name__ == "__main__":
    scraper = SportsbetScraper()
    print("Testing Sportsbet live odds synchronization across active dates...")
    res = scraper.sync_sportsbet_odds()
    print("Sync Result:", res)
