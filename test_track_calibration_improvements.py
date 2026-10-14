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

def test_sale_calibration():
    from track_calibration_engine import TrackCalibrationEngine
    tce = TrackCalibrationEngine()
    calib = tce.get_track_calibration('Sale', 435)
    
    # Verify distance band and empirical sweep bias
    assert calib is not None
    assert calib.get("is_calibrated") is True
    assert calib.get("outside_sweep_index", 0.0) >= 1.40
    assert calib.get("rail_bias_index", 1.0) < 0.80

    analytics = GreyhoundAnalytics()
    # Race 1: Box 6 (Starts Monday) was the actual winner and model top pick with Bayesian shrinkage
    p1 = analytics.predict_race_probabilities(1638)
    assert p1[0]["box"] == 6
    assert p1[0]["model_win_prob"] > 0.30  # Clear top pick with Bayesian shrinkage applied

    # Race 2: Dr Freddy (Box 3) must be derisked due to rookie inside pinch on outside sweep track
    p2 = analytics.predict_race_probabilities(1639)
    freddy = next((p for p in p2 if p["box"] == 3), None)
    assert freddy is not None
    assert freddy["model_win_prob"] < 0.25  # Capped and derisked from old 40.7%

if __name__ == "__main__":
    test_simulation()
    test_sale_calibration()

