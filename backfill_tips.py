import re
from thedogs_scraper import TheDogsScraper
from db import get_connection

def run_backfill(limit=50):
    scraper = TheDogsScraper()
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT r.id, m.track_name, r.race_number, r.race_url
    FROM races r
    JOIN meetings m ON r.meeting_id = m.id
    WHERE r.id BETWEEN 1392 AND 1401
    ORDER BY r.id ASC
    """)
    races = c.fetchall()
    print(f"Found {len(races)} races to backfill tips for...")

    updated = 0
    from bs4 import BeautifulSoup
    for r in races:
        url = r["race_url"]
        try:
            resp = scraper.client.get(url)
            soup = BeautifulSoup(resp.text, "lxml")
            tips_div = soup.find(class_=re.compile(r"race-runners__tips|tabs-content__tips"))
            tips = []
            if tips_div:
                for m in re.finditer(r"rug_(\d+)", str(tips_div)):
                    b = int(m.group(1))
                    if b not in tips:
                        tips.append(b)
            if tips:
                tips_str = ",".join(map(str, tips))
                conn.execute("UPDATE races SET expert_tips = ? WHERE id = ?", (tips_str, r["id"]))
                conn.commit()
                updated += 1
                print(f"  [+] {r['track_name']} R{r['race_number']} (ID {r['id']}): Tips {tips_str}")
        except Exception as e:
            print(f"  [-] Failed on ID {r['id']}: {e}")

    print(f"Backfill complete! Updated {updated}/{len(races)} races.")

if __name__ == "__main__":
    run_backfill(30)
