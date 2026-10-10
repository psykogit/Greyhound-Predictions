import sqlite3
import argparse
from analytics import GreyhoundAnalytics

def initialize_db():
    conn = sqlite3.connect('greyhound_racing.db')
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS historical_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id INTEGER NOT NULL UNIQUE,
        predicted_top4 TEXT NOT NULL,
        actual_top4 TEXT,
        snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(race_id) REFERENCES races(id)
    );
    """)
    conn.commit()
    return conn

def extract_placing(finish_pos_str):
    if not finish_pos_str: return 99
    try:
        if "=" in finish_pos_str:
            finish_pos_str = finish_pos_str.replace("=", "")
        return int(finish_pos_str)
    except ValueError:
        return 99

def get_actual_top4(conn, race_id):
    c = conn.cursor()
    c.execute('''
        SELECT box_number, finish_position 
        FROM race_runners 
        WHERE race_id = ? AND finish_position IS NOT NULL
    ''', (race_id,))
    runners = c.fetchall()
    if not runners:
        return None
    
    # Sort by numeric finish position
    valid = []
    for r in runners:
        pos = extract_placing(str(r[1]))
        if pos <= 8:  # 1st to 8th
            valid.append((r[0], pos))
    
    if not valid:
        return None
        
    valid.sort(key=lambda x: x[1])
    # Extract top 4 boxes
    top4 = [str(v[0]) for v in valid[:4]]
    return ",".join(top4)

def export_predictions(backfill=False):
    conn = initialize_db()
    c = conn.cursor()
    
    if backfill:
        print("Backfilling historical predictions...")
        analytics = GreyhoundAnalytics()
        
        # Get all completed races
        c.execute('''
            SELECT DISTINCT rr.race_id 
            FROM race_runners rr
            WHERE rr.finish_position IS NOT NULL
        ''')
        completed_races = [r[0] for r in c.fetchall()]
        
        for r_id in completed_races:
            # Check if already snapshotted
            c.execute('SELECT id FROM historical_predictions WHERE race_id = ?', (r_id,))
            if not c.fetchone():
                preds = analytics.predict_race_probabilities(r_id)
                if preds:
                    predicted_boxes = [str(p["box"]) for p in preds[:4]]
                    pred_str = ",".join(predicted_boxes)
                    
                    actual_str = get_actual_top4(conn, r_id) or ""
                    
                    c.execute('''
                        INSERT INTO historical_predictions (race_id, predicted_top4, actual_top4)
                        VALUES (?, ?, ?)
                    ''', (r_id, pred_str, actual_str))
        conn.commit()
        print("Backfill complete.")

    # Export
    print("\n--- HISTORICAL PREDICTIONS EXPORT ---")
    c.execute('''
        SELECT m.track_name, r.race_number, hp.predicted_top4, hp.actual_top4
        FROM historical_predictions hp
        JOIN races r ON hp.race_id = r.id
        JOIN meetings m ON r.meeting_id = m.id
        ORDER BY m.meeting_date DESC, m.track_name, r.race_number
    ''')
    
    records = c.fetchall()
    if not records:
        print("No historical predictions found.")
    
    for row in records:
        track = row[0]
        race_num = row[1]
        pred = row[2]
        act = row[3] or "Pending"
        
        print(f"{track} R{race_num} pred: {pred} act: {act}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="Run the model over past races to generate missing snapshots")
    args = parser.parse_args()
    export_predictions(backfill=args.backfill)
