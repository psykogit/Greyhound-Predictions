import sqlite3
conn = sqlite3.connect('greyhound_racing.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('''
    SELECT r.id 
    FROM races r JOIN meetings m ON r.meeting_id = m.id 
    WHERE m.track_name LIKE '%Casino%' AND r.race_number = 5
    ORDER BY m.meeting_date DESC LIMIT 1
''')
race = c.fetchone()
for r in c.execute('SELECT box_number, running_style, split_1, best_time, sp_odds, sportsbet_odds FROM race_runners WHERE race_id = ? ORDER BY box_number', (race['id'],)):
    odds = r['sportsbet_odds'] or r['sp_odds']
    print(f"Box {r['box_number']}: Style={r['running_style']} | Split={r['split_1']} | PB={r['best_time']} | Odds={odds}")
