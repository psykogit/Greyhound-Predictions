import pytest
from fastapi.testclient import TestClient
from app import app
from first4_audit_report import generate_first4_audit


def test_first4_audit_report_current_only():
    # Test function directly
    data_all = generate_first4_audit(date_str="2026-10-14", current_only=False)
    assert "breakdown_by_track" in data_all
    assert "current_tracks_count" in data_all
    assert "total_tracks_count" in data_all
    assert data_all["total_tracks_count"] >= data_all["current_tracks_count"]

    data_curr = generate_first4_audit(date_str="2026-10-14", current_only=True)
    assert len(data_curr["breakdown_by_track"]) == data_curr["current_tracks_count"]
    for t in data_curr["breakdown_by_track"]:
        assert t["is_current_location"] is True
        assert t["remaining_races"] > 0


def test_api_first4_audit_endpoint_current_only():
    client = TestClient(app)
    res = client.get("/api/reports/first4-audit?date=2026-10-14&current_only=true")
    assert res.status_code == 200
    payload = res.json()
    assert payload["current_only"] is True
    for t in payload["breakdown_by_track"]:
        assert t["is_current_location"] is True
        assert t["remaining_races"] > 0

