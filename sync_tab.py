import sqlite3
import json
from db import get_connection
from tab_scraper import TABScraper

def sync_tab_tips():
    ts = TABScraper()
    tips_data = ts.fetch_race_tips()
    
    if not tips_data:
        print("No TAB tips found to sync.")
        return 0
        
    conn = get_connection()
    c = conn.cursor()
    updated = 0
    
    for tip in tips_data:
        track = tip['track']
        rnum = tip['race_number']
        tips_list = tip['tips']
        tips_str = ",".join(map(str, tips_list))
        
        # We use a permissive LIKE query on track name since TAB names might differ slightly
        # e.g., 'THE MEADOWS' vs 'The Meadows'
        track_query = f"%{track.replace(' ', '%')}%"
        
        c.execute("""
            UPDATE races 
            SET tab_tips = ?
            WHERE race_number = ? 
            AND meeting_id IN (
                SELECT id FROM meetings 
                WHERE meeting_date = date('now', 'localtime')
                AND track_name LIKE ?
            )
        """, (tips_str, rnum, track_query))
        
        if c.rowcount > 0:
            updated += c.rowcount
            print(f"Synced TAB Tips for {track} R{rnum}: {tips_str}")
            
    conn.commit()
    conn.close()
    return updated

if __name__ == "__main__":
    print("Syncing TAB tips...")
    u = sync_tab_tips()
    print(f"Updated {u} races with TAB Sky Racing tips.")
