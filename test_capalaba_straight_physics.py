import pytest
from analytics import GreyhoundAnalytics

def test_capalaba_aliases_segregation():
    ga = GreyhoundAnalytics()
    # Ensure Q1 Lakeside 390m time is not matched as Capalaba PB
    prof = ga.get_dog_profile(11687, track_name="Capalaba", distance=366)
    # Next Black Jack only had Q1 Lakeside in DB, should NOT match Capalaba track_pb
    assert prof.get("track_pb") is None or prof.get("track_pb") < 20.5, "Capalaba must not match circular 21.81s Q1 Lakeside time"

def test_capalaba_outside_lure_box_physics():
    ga = GreyhoundAnalytics()
    # Test on race 1632 (Capalaba 366m)
    preds = ga.predict_race_probabilities(1632)
    assert len(preds) > 0
    
    # Ushuaia (Box 8) should be top
    assert preds[0]["box"] == 8
    
    # Hara's Usain (Box 1) should not be #1 roughie or top 4
    top4_boxes = [p["box"] for p in preds[:4]]
    assert 1 not in top4_boxes
    
    # Roughie should not be Box 1 with fake inside rail advantage
    roughie = ga.select_value_roughie(preds, track_name="Capalaba", distance=366)
    if roughie:
        assert roughie["box"] != 1
