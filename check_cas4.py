import sqlite3
from analytics import GreyhoundAnalytics

conn = sqlite3.connect('greyhound_racing.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute('''
    SELECT r.id, r.distance 
    FROM races r 
    JOIN meetings m ON r.meeting_id = m.id 
    WHERE m.track_name LIKE '%Casino%' AND r.race_number = 4
''')
race = c.fetchone()
if race:
    race_id = race['id']
    distance = race['distance']
    print(f'Casino R4 ID: {race_id}, Distance: {distance}m')
    
    print("\nRunner Profiles:")
    for r in c.execute('SELECT box_number, dog_id, running_style, split_1, best_time, sp_odds, sportsbet_odds FROM race_runners WHERE race_id = ? ORDER BY box_number', (race_id,)):
        print(f"Box {r['box_number']}: PB={r['best_time']} | Split={r['split_1']} | Style={r['running_style']} | Odds={r['sportsbet_odds'] or r['sp_odds']}")

    print("\nModel Output (with latest logic):")
    a = GreyhoundAnalytics()
    preds = a.predict_race_probabilities(race_id)
    for p in preds:
        print(f"Box {p['box']}: Win Prob = {p['model_win_prob']:.3f} | Dog = {p['dog_name']}")
