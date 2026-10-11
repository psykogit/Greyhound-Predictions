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
