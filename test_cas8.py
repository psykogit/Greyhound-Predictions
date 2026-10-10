import sqlite3
c = sqlite3.connect('greyhound_racing.db')
c.row_factory = sqlite3.Row
cur = c.cursor()
cur.execute('''
    SELECT r.id, r.distance 
    FROM races r JOIN meetings m ON r.meeting_id = m.id 
    WHERE m.track_name LIKE '%Casino%' AND r.race_number = 8
    ORDER BY m.meeting_date DESC LIMIT 1
''')
race = cur.fetchone()
if race:
    print(f'Casino R8 ({race["distance"]}m)')
    for r in cur.execute('SELECT box_number, split_1, best_time, sp_odds, sportsbet_odds, running_style FROM race_runners WHERE race_id = ? ORDER BY box_number', (race['id'],)):
        odds = r['sportsbet_odds'] or r['sp_odds']
        print(f'Box {r["box_number"]}: Style={r["running_style"]} | Split={r["split_1"]} | PB={r["best_time"]} | Odds={odds}')
