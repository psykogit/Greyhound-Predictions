from analytics import GreyhoundAnalytics
import sqlite3

a = GreyhoundAnalytics()
conn = sqlite3.connect('greyhound_racing.db')
c = conn.cursor()
c.execute('''
    SELECT r.id, m.meeting_date 
    FROM races r JOIN meetings m ON r.meeting_id = m.id 
    WHERE m.track_name LIKE '%Shepparton%' AND r.race_number = 8
    ORDER BY m.meeting_date DESC
    LIMIT 1
''')
race = c.fetchone()
print(f"Testing Shepparton R8 on {race[1]}")
preds = a.predict_race_probabilities(race[0])
for p in preds:
    print(f"Box {p['box']}: Win Prob = {p['model_win_prob']:.3f} | Score = {p.get('power_score')}")
