import sqlite3
from analytics import GreyhoundAnalytics
a = GreyhoundAnalytics()
c = sqlite3.connect('greyhound_racing.db').cursor()
c.execute('''
    SELECT r.id 
    FROM races r JOIN meetings m ON r.meeting_id = m.id 
    WHERE m.track_name LIKE '%Warragul%' AND r.race_number = 5
    ORDER BY m.meeting_date DESC LIMIT 1
''')
race = c.fetchone()
preds = a.predict_race_probabilities(race[0])
for p in preds:
    print(f"Box {p['box']}: Prob={p['model_win_prob']:.3f} | SpeedRank={p.get('speed_rank')} | PB={p.get('best_time')}")
