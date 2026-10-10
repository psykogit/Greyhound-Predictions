import sqlite3
conn = sqlite3.connect('greyhound_racing.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

races = [2, 3, 5, 8, 10]
for rn in races:
    c.execute('''
        SELECT r.id, r.distance 
        FROM races r JOIN meetings m ON r.meeting_id = m.id 
        WHERE m.track_name LIKE '%Shepparton%' AND r.race_number = ?
    ''', (rn,))
    race = c.fetchone()
    if not race: continue
    print(f'\n--- Shepparton R{rn} ({race["distance"]}m) ---')
    for r in c.execute('SELECT box_number, finish_position, running_style, split_1, best_time, sportsbet_odds, sp_odds FROM race_runners WHERE race_id = ? ORDER BY box_number', (race['id'],)):
        odds = r['sportsbet_odds'] or r['sp_odds']
        print(f"Box {r['box_number']}: Fin={r['finish_position']} | Style={r['running_style']} | Split={r['split_1']} | PB={r['best_time']} | Odds={odds}")
