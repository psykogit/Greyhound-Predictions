import pytest
from fastapi.testclient import TestClient
from app import app
from exotic_bet_optimizer import ExoticBetOptimizer

def test_estimate_boxed_trifecta_odds_defaults_and_math():
    opt = ExoticBetOptimizer()
    # Test on race 1632
    tri = opt.estimate_boxed_trifecta_odds(1632)
    assert tri is not None
    assert tri["combinations_count"] == 24
    assert len(tri["selected_boxes"]) == 4
    # Default stake must be $1.20 for 5% flexi
    assert tri["stake"] == 1.20
    assert abs(tri["flexi_percent"] - 5.0) < 0.01
    # Break-even dividend on 24 combos must be $24.00
    assert tri["break_even_dividend"] == 24.00
    assert tri["est_avg_dividend"] > 0
    assert tri["projected_avg_payout"] > 0
    assert "verdict" in tri
    assert len(tri["top_combinations_preview"]) <= 5
    for combo in tri["top_combinations_preview"]:
        assert len(combo["order"].split("-")) == 3

def test_estimate_boxed_first4_odds_defaults_and_math():
    opt = ExoticBetOptimizer()
    f4 = opt.estimate_boxed_first4_odds(1632)
    assert f4 is not None
    assert f4["combinations_count"] == 120
    assert len(f4["selected_boxes"]) == 5
    # Default stake must be $6.00 for 5% flexi
    assert f4["stake"] == 6.00
    assert abs(f4["flexi_percent"] - 5.0) < 0.01
    # Break-even dividend on 120 combos must be $120.00
    assert f4["break_even_dividend"] == 120.00
    assert f4["est_avg_dividend"] > 0
    assert f4["projected_avg_payout"] > 0
    assert len(f4["top_combinations_preview"]) <= 5
    for combo in f4["top_combinations_preview"]:
        assert len(combo["order"].split("-")) == 4

def test_api_endpoints_trifecta_and_first4():
    client = TestClient(app)
    # Check race analysis payload contains both estimates
    res = client.get("/api/races/1632")
    assert res.status_code == 200
    data = res.json()
    assert "boxed_first4_estimate" in data
    assert "boxed_trifecta_estimate" in data
    assert data["boxed_first4_estimate"]["stake"] == 6.00
    assert data["boxed_first4_estimate"]["flexi_percent"] == 5.0
    assert data["boxed_trifecta_estimate"]["stake"] == 1.20
    assert data["boxed_trifecta_estimate"]["flexi_percent"] == 5.0

    # Check dedicated trifecta endpoint
    tri_res = client.get("/api/races/1632/estimate-trifecta")
    assert tri_res.status_code == 200
    tri_data = tri_res.json()
    assert tri_data["stake"] == 1.20
    assert tri_data["combinations_count"] == 24

    # Check dedicated first 4 endpoint
    f4_res = client.get("/api/races/1632/estimate-first4")
    assert f4_res.status_code == 200
    f4_data = f4_res.json()
    assert f4_data["stake"] == 6.00
    assert f4_data["combinations_count"] == 120

def test_two_places_paid_and_no_third_place_dividend():
    opt = ExoticBetOptimizer()
    # Race 2 has 5 active runners: Paying 2 places, No 3rd place dividend
    f4 = opt.estimate_boxed_first4_odds(2)
    assert f4["is_available"] is True
    assert f4["active_runners_count"] == 5
    assert f4["places_paid"] == 2
    assert f4["no_third_place_dividend"] is True
    assert f4["is_full_field_coverage"] is True
    assert f4["field_coverage_pct"] == 100.0
    assert f4["verdict"] == "NEGATIVE EXPECTATION (100% COVERAGE)"
    assert "22% takeout" in f4["verdict_note"]

    # Race 4 has 6 active runners: Paying 2 places, No 3rd place dividend
    f4_6 = opt.estimate_boxed_first4_odds(4)
    assert f4_6["is_available"] is True
    assert f4_6["active_runners_count"] == 6
    assert f4_6["places_paid"] == 2
    assert f4_6["no_third_place_dividend"] is True
    assert f4_6["total_race_permutations"] == 360  # P(6, 4) = 360
    assert f4_6["is_full_field_coverage"] is False
    assert round(f4_6["field_coverage_pct"], 1) == 33.3

def test_unavailable_exotics_when_field_below_minimum():
    opt = ExoticBetOptimizer()
    # Race 11 has 4 active runners:
    # First 4 requires >= 5 runners -> should be unavailable
    f4 = opt.estimate_boxed_first4_odds(11)
    assert f4["is_available"] is False
    assert f4["active_runners_count"] == 4
    assert "fewer than 5 runners" in f4["unavailable_reason"]
    assert f4["verdict"] == "NOT OFFERED (FIELD < 5)"

    # Trifecta requires >= 4 runners -> should be available with 100% full field coverage
    tri = opt.estimate_boxed_trifecta_odds(11)
    assert tri["is_available"] is True
    assert tri["active_runners_count"] == 4
    assert tri["total_race_permutations"] == 24  # P(4, 3) = 24
    assert tri["is_full_field_coverage"] is True
    assert tri["field_coverage_pct"] == 100.0
    assert tri["verdict"] == "NEGATIVE EXPECTATION (100% COVERAGE)"
    assert "21% takeout" in tri["verdict_note"]

def test_api_race_analysis_includes_place_terms_metadata():
    client = TestClient(app)
    # Race 2 (5 runners): Paying 2 places
    res2 = client.get("/api/races/2")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["no_third_place_dividend"] is True
    assert data2["places_paid"] == 2
    assert "Paying 2 Places" in data2["place_terms_label"]
    assert data2["boxed_first4_estimate"]["is_full_field_coverage"] is True

    # Race 11 (4 runners): First 4 unavailable, Trifecta available
    res11 = client.get("/api/races/11")
    assert res11.status_code == 200
    data11 = res11.json()
    assert data11["no_third_place_dividend"] is True
    assert data11["places_paid"] == 1
    assert data11["boxed_first4_estimate"]["is_available"] is False
    assert data11["boxed_trifecta_estimate"]["is_available"] is True
