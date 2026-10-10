"""
test_track_calibration_improvements.py
Simulates refinements for Wentworth Park, Meadows, and Dubbo.
"""

import sqlite3
import re
from analytics import GreyhoundAnalytics

def test_simulation():
    conn = sqlite3.connect('greyhound_racing.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Temporarily update temperatures in memory or DB
    c.execute("UPDATE track_calibrations SET temperature = 2.40 WHERE track_name = 'Wentworth Park'")
    c.execute("UPDATE track_calibrations SET temperature = 2.30 WHERE track_name = 'Dubbo'")
    conn.commit()

    analytics = GreyhoundAnalytics()

    for track_name in ['Meadows', 'Wentworth Park', 'Dubbo']:
        c.execute("""
            SELECT r.id, r.race_number, r.distance
            FROM races r JOIN meetings m ON r.meeting_id = m.id
            WHERE m.meeting_date = '2026-10-10' AND m.track_name LIKE ?
            ORDER BY r.race_number
        """, (f"%{track_name}%",))
        races = c.fetchall()

        total = 0
        f4_hits = 0
        srm_hits = 0
        tri_hits = 0

        for r in races:
            c.execute("""
                SELECT rr.box_number, rr.finish_position, g.dog_name
                FROM race_runners rr LEFT JOIN greyhounds g ON rr.dog_id = g.id
                WHERE rr.race_id = ? AND rr.finish_position IS NOT NULL
                ORDER BY rr.finish_position ASC
            """, (r['id'],))
            acts = c.fetchall()
            if len(acts) < 4: continue
            total += 1

            preds = analytics.predict_race_probabilities(r['id'])
            p_boxes = [p['box'] for p in preds[:4]]
            a_boxes = [a['box_number'] for a in acts[:4]]

            overlap = len(set(p_boxes).intersection(set(a_boxes)))
            tri = set(a_boxes[:3]).issubset(set(p_boxes))

            if overlap == 4: f4_hits += 1
            if overlap >= 3: srm_hits += 1
            if tri: tri_hits += 1

        print(f"[TEST] {track_name}: Completed: {total} | Boxed F4: {f4_hits} | 3+ in 4 (SRM): {srm_hits} | Tri in 4: {tri_hits}")

    # Revert DB for now
    c.execute("UPDATE track_calibrations SET temperature = 1.40 WHERE track_name = 'Wentworth Park'")
    c.execute("UPDATE track_calibrations SET temperature = 1.40 WHERE track_name = 'Dubbo'")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    test_simulation()
