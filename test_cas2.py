import sqlite3
from analytics import GreyhoundAnalytics

a = GreyhoundAnalytics()
conn = sqlite3.connect('greyhound_racing.db')
c = conn.cursor()
c.execute('''
    SELECT r.id, r.race_number 
    FROM races r JOIN meetings m ON r.meeting_id = m.id 
    WHERE m.track_name LIKE '%Casino%' 
    ORDER BY r.race_number
''')
races = c.fetchall()[:5]
for r in races:
    preds = a.predict_race_probabilities(r[0])
    print(f"Casino R{r[1]}: {','.join([str(p['box']) for p in preds[:4]])}")
