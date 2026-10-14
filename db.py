"""
Database models and initialization using SQLite.
Stores meetings, races, runners, historical performances, and weather observations.
"""

import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List

DB_PATH = Path(__file__).parent / "greyhound_racing.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Tracks Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        state TEXT,
        track_type TEXT, -- e.g. One-turn, Two-turn, Straight
        latitude REAL,
        longitude REAL
    );
    """)

    # 2. Meetings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT UNIQUE,
        track_id INTEGER REFERENCES tracks(id),
        track_name TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        source TEXT NOT NULL, -- e.g. 'fasttrack', 'thedogs'
        status TEXT DEFAULT 'scheduled',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Weather Conditions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS weather_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id INTEGER REFERENCES meetings(id),
        track_name TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        temperature REAL,
        apparent_temperature REAL,
        humidity REAL,
        precipitation REAL,
        wind_speed REAL,
        condition_desc TEXT
    );
    """)

    # 4. Races Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS races (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_race_id TEXT UNIQUE,
        meeting_id INTEGER REFERENCES meetings(id),
        race_number INTEGER NOT NULL,
        race_name TEXT,
        distance INTEGER,
        grade TEXT,
        track_condition TEXT, -- Fast, Good, Slow, Wet
        start_time TEXT,
        track_record REAL,
        race_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Safe migrations
    try:
        cursor.execute("ALTER TABLE races ADD COLUMN race_url TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE races ADD COLUMN sportsbet_event_id TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE race_runners ADD COLUMN sportsbet_place_odds REAL;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE races ADD COLUMN expert_tips TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE races ADD COLUMN tab_tips TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE past_performances ADD COLUMN run_date_iso TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pp_dog_date ON past_performances(dog_id, run_date_iso DESC);")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE weather_records ADD COLUMN apparent_temperature REAL;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE race_runners ADD COLUMN card_split_1 REAL;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE greyhounds ADD COLUMN sex TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE race_runners ADD COLUMN sex TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE past_performances ADD COLUMN sex TEXT;")
    except Exception:
        pass
    try:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id INTEGER NOT NULL,
            runner_id INTEGER,
            box_number INTEGER NOT NULL,
            bookmaker TEXT NOT NULL,
            win_odds REAL,
            place_odds REAL,
            snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_snapshots_race_box ON odds_snapshots(race_id, box_number, snapshot_time);")
    except Exception:
        pass

    # 5. Greyhounds Master Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS greyhounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dog_id TEXT UNIQUE,
        dog_name TEXT NOT NULL,
        sex TEXT, -- 'D' (Dog/Male) or 'B' (Bitch/Female)
        sire TEXT,
        dam TEXT,
        date_of_birth TEXT,
        career_starts INTEGER DEFAULT 0,
        career_wins INTEGER DEFAULT 0,
        career_seconds INTEGER DEFAULT 0,
        career_thirds INTEGER DEFAULT 0,
        prizemoney REAL DEFAULT 0.0
    );
    """)

    # 6. Trainers Master Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trainers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trainer_id TEXT UNIQUE,
        trainer_name TEXT NOT NULL,
        location TEXT
    );
    """)

    # 7. Race Entries / Runners Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS race_runners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id INTEGER REFERENCES races(id),
        dog_id INTEGER REFERENCES greyhounds(id),
        trainer_id INTEGER REFERENCES trainers(id),
        box_number INTEGER,
        rug_number INTEGER,
        weight REAL,
        sex TEXT, -- 'D' (Dog/Male) or 'B' (Bitch/Female)
        running_style TEXT, -- Railer, Mid, Wide
        form_string TEXT,
        best_time REAL,
        finish_position INTEGER,
        race_time REAL,
        split_1 REAL,
        split_2 REAL,
        sp_odds REAL,
        sportsbet_odds REAL,
        is_scratched INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(race_id, box_number)
    );
    """)

    # 8. Past Performances Table (Detailed sectional history)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS past_performances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dog_id INTEGER REFERENCES greyhounds(id),
        run_date TEXT NOT NULL,
        track_code TEXT NOT NULL,
        distance INTEGER,
        box INTEGER,
        weight REAL,
        sex TEXT, -- 'D' (Dog/Male) or 'B' (Bitch/Female)
        finish_pos TEXT,
        race_time REAL,
        winner_time REAL,
        margin REAL,
        split_1 REAL,
        pir TEXT,
        sp_odds REAL,
        grade TEXT,
        UNIQUE(dog_id, run_date, track_code, race_time)
    );
    """)

    # 9. Track Calibrations Table (Continuous Brier score, Log-Loss, Temperature)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS track_calibrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_name TEXT UNIQUE NOT NULL,
        sample_races INTEGER DEFAULT 0,
        brier_score REAL,
        log_loss REAL,
        ece REAL,
        temperature REAL DEFAULT 2.4,
        last_calibrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Pre-populate common Australian Greyhound Tracks with coordinates for weather lookup
    tracks_seed = [
        ("Sandown Park", "VIC", "Two-turn", -37.9566, 145.1596),
        ("The Meadows", "VIC", "Two-turn", -37.7126, 144.9080),
        ("Wentworth Park", "NSW", "Two-turn", -33.8767, 151.1895),
        ("Albion Park", "QLD", "Two-turn", -27.4326, 153.0450),
        ("Angle Park", "SA", "Two-turn", -34.8575, 138.5628),
        ("Cannington", "WA", "Two-turn", -32.0163, 115.9387),
        ("Hobart", "TAS", "Two-turn", -42.8272, 147.2885),
        ("Healesville", "VIC", "Straight", -37.6703, 145.5418),
        ("Richmond", "NSW", "Straight/Turn", -33.6850, 150.7450),
        ("Geelong", "VIC", "One-turn", -38.1722, 144.3541),
        ("Ballarat", "VIC", "One-turn", -37.5800, 143.8300),
        ("Bendigo", "VIC", "One-turn", -36.7570, 144.2787),
        ("Sale", "VIC", "Two-turn", -38.0915, 147.0630),
        ("Warragul", "VIC", "One-turn", -38.1643, 145.9341),
        ("Shepparton", "VIC", "One-turn", -36.4470, 145.4140),
        ("Maitland", "NSW", "One-turn", -32.7447, 151.5628),
        ("Grafton", "NSW", "One-turn", -29.6841, 152.9332),
        ("Casino", "NSW", "One-turn", -28.8661, 153.0550),
        ("Mount Gambier", "SA", "One-turn", -37.8250, 140.7820),
        ("Mandurah", "WA", "One-turn", -32.5268, 115.7225),
        ("Capalaba", "QLD", "Straight", -27.5184, 153.1913),
        ("Horsham", "VIC", "One-turn", -36.7212, 142.2078),
        ("Warrnambool", "VIC", "One-turn", -38.3842, 142.4842),
        ("Dubbo", "NSW", "One-turn", -32.2569, 148.6011),
        ("Darwin", "NT", "One-turn", -12.4634, 130.8456),
        ("Gosford", "NSW", "One-turn", -33.4267, 151.3417),
        ("Bulli", "NSW", "One-turn", -34.3411, 150.9167),
        ("Nowra", "NSW", "One-turn", -34.8980, 150.5910),
        ("Wagga", "NSW", "One-turn", -35.1239, 147.3689),
        ("Gunnedah", "NSW", "One-turn", -30.9744, 150.2628),
        ("Temora", "NSW", "One-turn", -34.4525, 147.5461),
        ("Launceston", "TAS", "One-turn", -41.4045, 147.1415),
        ("Gawler", "SA", "One-turn", -34.5993, 138.7402),
        ("Murray Bridge", "SA", "One-turn", -35.1192, 139.2736),
        ("Murray Bridge Straight", "SA", "Straight", -35.1192, 139.2736),
        ("Richmond Straight", "NSW", "Straight", -33.6850, 150.7450),
        ("Q Straight", "QLD", "Straight", -27.7120, 152.74070),
        ("Q1 Lakeside", "QLD", "Two-turn", -27.4326, 153.0450),
        ("Q2 Parklands", "QLD", "Two-turn", -27.7120, 152.7400),
        ("Rockhampton", "QLD", "One-turn", -23.3788, 150.5222),
        ("Townsville", "QLD", "One-turn", -19.2590, 146.8169),
        ("Northam", "WA", "One-turn", -31.6400, 116.6718),
        ("Taree", "NSW", "One-turn", -31.9103, 152.4631),
        ("Sandown", "VIC", "Two-turn", -37.9566, 145.1596),
        ("Meadows", "VIC", "Two-turn", -37.7126, 144.9080),
        ("The Gardens", "NSW", "One-turn", -32.8935171, 151.6832801),
        ("Ladbrokes Gardens", "NSW", "One-turn", -32.8935171, 151.6832801)
    ]

    for name, state, t_type, lat, lon in tracks_seed:
        cursor.execute("UPDATE tracks SET latitude = ?, longitude = ? WHERE name = ?", (lat, lon, name))
        cursor.execute("""
        INSERT OR IGNORE INTO tracks (name, state, track_type, latitude, longitude)
        VALUES (?, ?, ?, ?, ?)
        """, (name, state, t_type, lat, lon))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
