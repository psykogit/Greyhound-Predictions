import sqlite3
from analytics import GreyhoundAnalytics

def check_casino_today():
    conn = sqlite3.connect('greyhound_racing.db')
    c = conn.cursor()
    
    # Get the latest Casino meeting with completed races
    c.execute('''
        SELECT m.id, m.meeting_date 
        FROM meetings m
        JOIN races r ON r.meeting_id = m.id
        JOIN race_runners rr ON rr.race_id = r.id
        WHERE m.track_name LIKE '%Casino%' AND rr.finish_position IS NOT NULL
        ORDER BY m.meeting_date DESC
        LIMIT 1
    ''')
    meeting = c.fetchone()
    if not meeting:
        print("No completed Casino meetings found.")
        return
        
    meeting_id = meeting[0]
    date = meeting[1]
    print(f"--- Casino Meeting: {date} ---")
    
    c.execute('''
        SELECT id, race_number, distance 
        FROM races 
        WHERE meeting_id = ?
        ORDER BY race_number
    ''', (meeting_id,))
    races = c.fetchall()
    
    analytics = GreyhoundAnalytics()
    
    for r in races:
        race_id, race_num, dist = r
        
        # Get actual top 4
        c.execute('''
            SELECT box_number, finish_position 
            FROM race_runners 
            WHERE race_id = ? AND finish_position IS NOT NULL
            ORDER BY CAST(finish_position AS INTEGER)
            LIMIT 4
        ''', (race_id,))
        actual = [str(x[0]) for x in c.fetchall()]
        if not actual: continue
        
        # Get predictions
        preds = analytics.predict_race_probabilities(race_id)
        predicted = [str(p["box"]) for p in preds[:4]]
        
        print(f"R{race_num} ({dist}m) | Pred: {','.join(predicted)} | Act: {','.join(actual)}")
        
        # Let's also print out details of the top 2 predicted vs top 2 actual for deep dive
        for p in preds:
            if str(p["box"]) in actual[:2] or p["box"] == preds[0]["box"] or p["box"] == preds[1]["box"]:
                print(f"  Box {p['box']}: Prob={p['model_win_prob']:.3f} | Style={p.get('running_style')} | PB={p.get('best_time')} | Split={p.get('split_1')} | MktOdds={p.get('market_odds')}")

if __name__ == "__main__":
    check_casino_today()
