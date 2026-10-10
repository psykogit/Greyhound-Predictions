import sqlite3

conn = sqlite3.connect('greyhound_racing.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

for r in c.execute('SELECT box_number, running_style, split_1, best_time, sp_odds, sportsbet_odds FROM race_runners WHERE race_id = 1136 ORDER BY box_number'):
    print(f"Box {r['box_number']}: PB={r['best_time']} | Split={r['split_1']} | Style={r['running_style']} | Odds={r['sportsbet_odds'] or r['sp_odds']}")
