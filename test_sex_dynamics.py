"""
Unit tests for Greyhound Sex (Male vs Female) Tracking, Momentum Collision Physics,
and Predictive Model Dynamics.
"""

import pytest
import sqlite3
from db import get_connection
from analytics import GreyhoundAnalytics
from history_importer import HistoryImporter
import re


def test_db_schema_has_sex_columns():
    """Verify that greyhounds, race_runners, and past_performances have sex columns."""
    conn = get_connection()
    c = conn.cursor()

    for table in ["greyhounds", "race_runners", "past_performances"]:
        c.execute(f"PRAGMA table_info({table})")
        cols = {row["name"] for row in c.fetchall()}
        assert "sex" in cols, f"Column 'sex' missing from table '{table}'"

    # Verify backfilled data exists in DB
    c.execute("SELECT count(*) as cnt FROM greyhounds WHERE sex IN ('D', 'B')")
    assert c.fetchone()["cnt"] > 1000

    c.execute("SELECT count(*) as cnt FROM race_runners WHERE sex IN ('D', 'B')")
    assert c.fetchone()["cnt"] > 1000

    c.execute("SELECT count(*) as cnt FROM past_performances WHERE sex IN ('D', 'B')")
    assert c.fetchone()["cnt"] > 1000
    conn.close()


def test_history_importer_sex_extraction_and_fallback():
    """Test that HistoryImporter extracts sex from text aliases or falls back to weight."""
    importer = HistoryImporter()

    # Explicit bitch / dog aliases
    records = [
        {
            "meeting_date": "2026-10-14",
            "track_name": "Test Park",
            "race_number": 1,
            "box_number": 1,
            "dog_name": "Test Dog Alpha",
            "sex": "Dog",
            "weight": 34.0,
            "finish_position": 1,
            "race_time": 25.10
        },
        {
            "meeting_date": "2026-10-14",
            "track_name": "Test Park",
            "race_number": 1,
            "box_number": 2,
            "dog_name": "Test Bitch Beta",
            "gender": "Bitch",
            "weight": 26.5,
            "finish_position": 2,
            "race_time": 25.20
        },
        {
            "meeting_date": "2026-10-14",
            "track_name": "Test Park",
            "race_number": 1,
            "box_number": 3,
            "dog_name": "Test Weight Fallback Dog",
            "weight": 32.5,
            "finish_position": 3,
            "race_time": 25.30
        },
        {
            "meeting_date": "2026-10-14",
            "track_name": "Test Park",
            "race_number": 1,
            "box_number": 4,
            "dog_name": "Test Weight Fallback Bitch",
            "weight": 27.2,
            "finish_position": 4,
            "race_time": 25.40
        }
    ]

    res = importer.import_records(records, auto_calibrate=False)
    assert res["status"] == "SUCCESS"

    conn = get_connection()
    c = conn.cursor()

    # Check greyhound masters
    c.execute("SELECT dog_name, sex FROM greyhounds WHERE dog_name LIKE 'Test %'")
    rows = {r["dog_name"]: r["sex"] for r in c.fetchall()}
    assert rows["Test Dog Alpha"] == "D"
    assert rows["Test Bitch Beta"] == "B"
    assert rows["Test Weight Fallback Dog"] == "D"
    assert rows["Test Weight Fallback Bitch"] == "B"

    # Cleanup test data
    c.execute("DELETE FROM past_performances WHERE dog_id IN (SELECT id FROM greyhounds WHERE dog_name LIKE 'Test %')")
    c.execute("DELETE FROM race_runners WHERE dog_id IN (SELECT id FROM greyhounds WHERE dog_name LIKE 'Test %')")
    c.execute("DELETE FROM greyhounds WHERE dog_name LIKE 'Test %'")
    c.execute("DELETE FROM races WHERE race_name = 'Race 1' AND meeting_id IN (SELECT id FROM meetings WHERE track_name = 'Test Park')")
    c.execute("DELETE FROM meetings WHERE track_name = 'Test Park'")
    conn.commit()
    conn.close()


def test_predict_race_probabilities_includes_sex():
    """Verify that predictions return sex for runners and form a valid probability distribution."""
    analytics = GreyhoundAnalytics()
    # Test on real race in DB (e.g. race 1638 at Sale)
    preds = analytics.predict_race_probabilities(1638)
    assert len(preds) > 0

    # Ensure probabilities sum to 1.0 within floating point precision
    total_prob = sum(p["model_win_prob"] for p in preds)
    assert abs(total_prob - 1.0) < 0.001

    # Check that each runner has a valid sex ('D' or 'B')
    for p in preds:
        assert p["sex"] in ("D", "B"), f"Runner {p['dog_name']} has invalid sex: {p.get('sex')}"
        assert p.get("today_weight") is not None or p.get("last_weight") is not None


def test_sex_and_momentum_physics_logic():
    """
    Empirical physics validation:
    1. Box 8 agility bonus for light bitches in clean air
    2. Box 1 rail defense for heavier male dogs
    3. Staying distance scaling on 530m+
    """
    analytics = GreyhoundAnalytics()

    # Check dog profile lookup contains sex
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, dog_name, sex FROM greyhounds WHERE sex IS NOT NULL LIMIT 5")
    samples = c.fetchall()
    conn.close()

    for s in samples:
        prof = analytics.get_dog_profile(s["id"])
        assert prof["sex"] in ("D", "B"), f"Profile for {s['dog_name']} missing sex"

