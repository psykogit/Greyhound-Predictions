import sqlite3
from analytics import GreyhoundAnalytics

conn = sqlite3.connect('greyhound_racing.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute('''
    SELECT r.id, r.distance 
    FROM races r 
    JOIN meetings m ON r.meeting_id = m.id 
    WHERE m.track_name LIKE '%Mount Gambier%' AND r.race_number = 8
''')
race = c.fetchone()
if not race:
    print('Mount Gambier R8 not found')
else:
    race_id = race['id']
    distance = race['distance']
    print(f'Mount Gambier R8 ID: {race_id}, Distance: {distance}m')
    
    a = GreyhoundAnalytics()
    preds = a.predict_race_probabilities(race_id)
    
    print("\nModel Output:")
    for p in preds:
        print(f"Box {p['box']}: Win Prob = {p['model_win_prob']}")
    
    print("\nRunner Profiles:")
    for p in preds:
        box = p['box']
        r = c.execute('SELECT running_style, split_1, best_time, sportsbet_odds, market_odds FROM race_runners WHERE race_id = ? AND box_number = ?', (race_id, box)).fetchone()
        print(f"Box {box}: PB={r['best_time']} | Split={r['split_1']} | Style={r['running_style']} | Odds={r['sportsbet_odds']} | Market Odds={r['market_odds']}")
