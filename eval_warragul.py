import sqlite3
from analytics import GreyhoundAnalytics

def check_warragul():
    conn = sqlite3.connect('greyhound_racing.db')
    c = conn.cursor()
    
    # Get the latest Warragul meeting
    c.execute('''
        SELECT id, meeting_date 
        FROM meetings 
        WHERE track_name LIKE '%Warragul%' 
        ORDER BY meeting_date DESC 
        LIMIT 1
    ''')
    meeting = c.fetchone()
    if not meeting:
        print("No Warragul meetings found.")
        return
        
    meeting_id = meeting[0]
    print(f"--- Warragul Meeting: {meeting[1]} ---")
    
    c.execute('''
        SELECT id, race_number, distance 
        FROM races 
        WHERE meeting_id = ? AND race_number <= 4
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
        
        # Get predictions
        preds = analytics.predict_race_probabilities(race_id)
        if not preds: continue
        predicted = [str(p["box"]) for p in preds[:4]]
        
        actual_str = ",".join(actual) if actual else "Pending"
        print(f"\n--- R{race_num} ({dist}m) ---")
        print(f"Pred: {','.join(predicted)} | Act: {actual_str}")
        
        # Print top 4 predicted details
        for p in preds[:4]:
            print(f"  [PRED {preds.index(p)+1}] Box {p['box']}: Prob={p['model_win_prob']:.3f} | PB={p.get('best_time')} | Split={p.get('split_1')} | MktOdds={p.get('market_odds')} | SpdRank={p.get('speed_rank')}")
            
        # Print winner details if missed
        if actual and actual[0] not in predicted:
            winner_box = int(actual[0])
            for p in preds:
                if p["box"] == winner_box:
                    print(f"  [MISSED WINNER] Box {p['box']}: Prob={p['model_win_prob']:.3f} | PB={p.get('best_time')} | Split={p.get('split_1')} | MktOdds={p.get('market_odds')} | SpdRank={p.get('speed_rank')}")

if __name__ == "__main__":
    check_warragul()
