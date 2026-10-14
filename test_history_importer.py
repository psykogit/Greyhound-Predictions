import pytest
import os
import tempfile
import json
import csv
import sqlite3
from fastapi.testclient import TestClient
from app import app
from history_importer import HistoryImporter, normalize_date_to_iso, clean_numeric


def test_date_and_numeric_normalizers():
    # Dates
    assert normalize_date_to_iso("2026-05-15") == "2026-05-15"
    assert normalize_date_to_iso("15/05/2026") == "2026-05-15"
    assert normalize_date_to_iso("15-05-2026") == "2026-05-15"
    assert normalize_date_to_iso("15/05/26") == "2026-05-15"
    assert normalize_date_to_iso("15 May 2026") == "2026-05-15"
    assert normalize_date_to_iso("15-May-2026 14:30:00") == "2026-05-15"

    # Numeric
    assert clean_numeric("$2.50") == 2.50
    assert clean_numeric("460m", is_int=True) == 460
    assert clean_numeric("32.4kg") == 32.4
    assert clean_numeric("SCR", is_int=True) is None
    assert clean_numeric("1st", is_int=True) == 1


def test_generate_template():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_p = os.path.join(tmpdir, "test.csv")
        json_p = os.path.join(tmpdir, "test.json")
        c_out, j_out = HistoryImporter.generate_template(csv_p, json_p)
        assert os.path.exists(c_out)
        assert os.path.exists(j_out)

        # Verify CSV content
        with open(c_out, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) >= 3
            assert "meeting_date" in rows[0]
            assert "dog_name" in rows[0]


def test_import_csv_and_auto_calibration():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = os.path.join(tmpdir, "warragul_history.csv")
        rows = [
            ["date", "venue", "race", "dist", "grade", "box", "runner", "trainer", "wgt", "pos", "marg", "time", "split", "sp"],
            ["2026-06-01", "Warragul", "1", "460m", "Grade 5", "1", "Turbo Sonic", "P Smith", "32.1", "1", "0.0", "25.75", "6.52", "$2.10"],
            ["2026-06-01", "Warragul", "1", "460m", "Grade 5", "2", "Blazing Comet", "P Smith", "30.5", "2", "1.5", "25.85", "6.60", "$4.50"],
            ["2026-06-01", "Warragul", "1", "460m", "Grade 5", "3", "Slow Jumper", "A Lee", "34.0", "3", "3.2", "25.97", "6.72", "$12.00"],
            ["2026-06-01", "Warragul", "1", "460m", "Grade 5", "4", "Scratched Dog", "A Lee", "31.0", "SCR", "", "", "", ""]
        ]
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)

        importer = HistoryImporter()
        res = importer.import_file(csv_file, auto_calibrate=True)
        assert res["status"] == "SUCCESS"
        assert res["runners_imported"] == 4
        assert res["meetings_count"] == 1
        assert res["races_count"] == 1
        assert "Warragul" in res["tracks"]

        # Check DB records
        conn = importer.conn
        cur = conn.cursor()
        cur.execute("SELECT id FROM greyhounds WHERE dog_name = 'Turbo Sonic'")
        d_row = cur.fetchone()
        assert d_row is not None
        dog_id = d_row[0]

        # Check past performance insertion
        cur.execute("SELECT finish_pos, split_1, race_time FROM past_performances WHERE dog_id = ?", (dog_id,))
        pp = cur.fetchone()
        assert pp is not None
        assert str(pp[0]) == "1"
        assert pp[1] == 6.52
        assert pp[2] == 25.75


def test_api_import_endpoints():
    client = TestClient(app)

    # Test template download
    res_t = client.get("/api/actions/import-template?format=csv")
    assert res_t.status_code == 200
    assert "text/csv" in res_t.headers["content-type"]

    # Test direct JSON payload import
    test_payload = {
        "records": [
            {
                "meeting_date": "2026-07-10",
                "track_name": "Darwin",
                "race_number": 1,
                "distance": 312,
                "box_number": 1,
                "dog_name": "NT Lightning",
                "finish_position": 1,
                "race_time": 18.05,
                "split_1": 7.08,
                "sp_odds": 2.20
            }
        ]
    }
    res_imp = client.post("/api/actions/import-history", json=test_payload)
    assert res_imp.status_code == 200
    data = res_imp.json()
    assert data["status"] == "SUCCESS"
    assert data["runners_imported"] == 1
    assert "Darwin" in data["tracks"]
