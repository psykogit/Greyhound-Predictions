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


def test_first4_combinatorial_selective_boxing():
    opt = ExoticBetOptimizer()
    race_id = 1632

    # 1. Boxed (120 combos)
    f4_box = opt.estimate_boxed_first4_odds(race_id, ticket_structure="boxed")
    assert f4_box["combinations_count"] == 120
    assert abs(f4_box["flexi_percent"] - 5.0) < 0.05
    assert f4_box["flexi_multiplier"] == 1.0

    # 2. Standout Anchor (Key 1st -> 24 combos, 25% flexi = 5x payout boost)
    f4_std = opt.estimate_boxed_first4_odds(race_id, ticket_structure="standout")
    assert f4_std["combinations_count"] == 24
    assert abs(f4_std["flexi_percent"] - 25.0) < 0.05
    assert f4_std["flexi_multiplier"] == 5.0
    assert "Standout Anchor" in f4_std["structure_label"]
    assert f4_std["anchor_box"] is not None

    # 3. Dual Banker (1st & 2nd -> 12 combos, 50% flexi = 10x payout boost)
    f4_dual = opt.estimate_boxed_first4_odds(race_id, ticket_structure="dual_banker")
    assert f4_dual["combinations_count"] == 12
    assert abs(f4_dual["flexi_percent"] - 50.0) < 0.05
    assert f4_dual["flexi_multiplier"] == 10.0
    assert "Dual Banker" in f4_dual["structure_label"]

    # 4. Roving Banker (96 combos, 6.25% flexi)
    f4_rov = opt.estimate_boxed_first4_odds(race_id, ticket_structure="roving_banker")
    assert f4_rov["combinations_count"] == 96
    assert abs(f4_rov["flexi_percent"] - 6.25) < 0.05

    # Check available_structures metadata
    assert len(f4_std["available_structures"]) == 4
    struct_names = [s["structure"] for s in f4_std["available_structures"]]
    assert "boxed" in struct_names
    assert "standout" in struct_names
    assert "dual_banker" in struct_names
    assert "roving_banker" in struct_names


def test_trifecta_combinatorial_selective_boxing():
    opt = ExoticBetOptimizer()
    race_id = 1632

    # 1. Boxed (24 combos, 5% flexi)
    tri_box = opt.estimate_boxed_trifecta_odds(race_id, ticket_structure="boxed")
    assert tri_box["combinations_count"] == 24
    assert abs(tri_box["flexi_percent"] - 5.0) < 0.05
    assert tri_box["flexi_multiplier"] == 1.0

    # 2. Standout Anchor (6 combos, 20% flexi = 4x payout boost)
    tri_std = opt.estimate_boxed_trifecta_odds(race_id, ticket_structure="standout")
    assert tri_std["combinations_count"] == 6
    assert abs(tri_std["flexi_percent"] - 20.0) < 0.05
    assert tri_std["flexi_multiplier"] == 4.0
    assert "Standout Anchor" in tri_std["structure_label"]

    # 3. Dual Banker (4 combos, 30% flexi = 6x payout boost)
    tri_dual = opt.estimate_boxed_trifecta_odds(race_id, ticket_structure="dual_banker")
    assert tri_dual["combinations_count"] == 4
    assert abs(tri_dual["flexi_percent"] - 30.0) < 0.05
    assert tri_dual["flexi_multiplier"] == 6.0

    # 4. Roving Banker (18 combos, 6.67% flexi)
    tri_rov = opt.estimate_boxed_trifecta_odds(race_id, ticket_structure="roving_banker")
    assert tri_rov["combinations_count"] == 18
    assert abs(tri_rov["flexi_percent"] - 6.67) < 0.05


def test_track_calibration_engine():
    from track_calibration_engine import TrackCalibrationEngine
    engine = TrackCalibrationEngine()

    # Verify Grafton sprint calibration from historical finishes
    calib = engine.get_track_calibration("Grafton", 350)
    assert calib is not None
    assert calib["track_name"] == "Grafton"
    assert calib["distance_band"] == "sprint"
    assert calib["sample_starts"] > 0
    assert 0.20 <= calib["rail_bias_index"] <= 4.0
    assert 0.88 <= calib["speed_weight_factor"] <= 1.18
    assert len(calib["box_multipliers"]) == 8
    # Inside boxes should have realistic Bayesian multipliers
    for b in range(1, 9):
        assert 0.75 <= calib["box_multipliers"][b] <= 1.40

    # Verify calibration summary queries
    summary = engine.get_calibration_summary()
    assert len(summary) > 0
    assert summary[0]["sample_starts"] >= summary[-1]["sample_starts"]


def test_api_selective_boxing_and_track_calibration():
    client = TestClient(app)

    # 1. First 4 with Standout
    f4_res = client.get("/api/races/1632/estimate-first4?structure=standout")
    assert f4_res.status_code == 200
    f4_data = f4_res.json()
    assert f4_data["ticket_structure"] == "standout"
    assert f4_data["combinations_count"] == 24
    assert f4_data["flexi_multiplier"] == 5.0

    # 2. Trifecta with Dual Banker
    tri_res = client.get("/api/races/1632/estimate-trifecta?structure=dual_banker")
    assert tri_res.status_code == 200
    tri_data = tri_res.json()
    assert tri_data["ticket_structure"] == "dual_banker"
    assert tri_data["combinations_count"] == 4
    assert tri_data["flexi_multiplier"] == 6.0

    # 3. Track Calibration Report Endpoint
    t_res = client.get("/api/tracks/calibration-report")
    assert t_res.status_code == 200
    t_data = t_res.json()
    assert "calibrations" in t_data
    assert len(t_data["calibrations"]) > 0

    # 4. Specific Track Calibration Endpoint
    tg_res = client.get("/api/tracks/calibration-report?track=Grafton&distance=350")
    assert tg_res.status_code == 200
    tg_data = tg_res.json()
    assert tg_data["track_name"] == "Grafton"
    assert "rail_bias_index" in tg_data

