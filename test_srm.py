import sqlite3
from srm_optimizer import SameRaceMultiOptimizer

conn = sqlite3.connect('greyhound_racing.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("SELECT r.id, r.race_number, m.track_name FROM races r JOIN meetings m ON r.meeting_id = m.id WHERE m.track_name LIKE '%Richmond%' ORDER BY r.id DESC LIMIT 5")
races = c.fetchall()

opt = SameRaceMultiOptimizer()

for race in races:
    print(f'Testing Race: {race["track_name"]} R{race["race_number"]} (ID {race["id"]})')
    try:
        combos = opt.generate_srm_combinations(race["id"], min_total_odds=1.25)
        print(f'Combos returned: {len(combos)}')
        preds = opt.analytics.predict_race_probabilities(race["id"])
        valid = [p for p in preds if p.get('market_odds') is not None and p.get('market_odds') > 1.0]
        print(f'Total preds: {len(preds)}, Valid runners: {len(valid)}')
    except Exception as e:
        import traceback
        traceback.print_exc()
