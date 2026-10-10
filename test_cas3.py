import sqlite3
from analytics import GreyhoundAnalytics
a = GreyhoundAnalytics()
c = sqlite3.connect('greyhound_racing.db').cursor()
c.execute('SELECT id FROM races WHERE race_number = 4 AND meeting_id = (SELECT id FROM meetings WHERE track_name LIKE "%Casino%" ORDER BY meeting_date DESC LIMIT 1)')
r_id = c.fetchone()[0]
preds = a.predict_race_probabilities(r_id)
for p in preds:
    if p['box'] == 6:
        print(f"Box 6: SpeedRank={p.get('speed_rank')}, Prob={p['model_win_prob']:.3f}")
