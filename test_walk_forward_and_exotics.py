import pytest
import sqlite3
from exotic_bet_optimizer import ExoticBetOptimizer
from walk_forward_evaluation import WalkForwardEvaluator
from analytics import GreyhoundAnalytics
from db import get_connection


def test_discount_harville_math_properties():
    probs = {1: 0.40, 2: 0.25, 3: 0.15, 4: 0.10, 5: 0.05, 6: 0.05}
    
    # 1. Pure Harville (gamma = 1.0)
    pure_dist = ExoticBetOptimizer.calculate_harville_permutations(
        probs, length=4, discount_factors=(1.0, 1.0, 1.0, 1.0)
    )
    assert len(pure_dist) == 360  # 6 * 5 * 4 * 3
    assert abs(sum(pure_dist.values()) - 1.0) < 1e-6

    # 2. Empirical Discount Harville (gamma = (1.0, 0.77, 0.50, 0.38))
    disc_dist = ExoticBetOptimizer.calculate_harville_permutations(
        probs, length=4, discount_factors=(1.0, 0.77, 0.50, 0.38)
    )
    assert len(disc_dist) == 360
    assert abs(sum(disc_dist.values()) - 1.0) < 1e-6

    # In empirical discounted Harville, favorite-heavy combinations like (1, 2, 3, 4)
    # should have lower probability than pure Harville because 2nd/3rd/4th disperse to outsiders:
    assert disc_dist[(1, 2, 3, 4)] < pure_dist[(1, 2, 3, 4)]
    # Meanwhile, an outsider finishing 4th like (1, 2, 3, 6) gains probability mass:
    ratio_top = disc_dist[(1, 2, 3, 4)] / disc_dist[(1, 2, 3, 6)]
    ratio_pure = pure_dist[(1, 2, 3, 4)] / pure_dist[(1, 2, 3, 6)]
    assert ratio_top < ratio_pure


def test_calibrate_discount_factors():
    opt = ExoticBetOptimizer()
    gammas = opt.calibrate_discount_factors(min_races=10)
    assert len(gammas) == 4
    assert gammas[0] == 1.0
    # gamma_2, gamma_3, gamma_4 should be monotonically non-increasing and < 1.0
    assert 0.4 <= gammas[1] <= 1.0
    assert 0.2 <= gammas[2] <= gammas[1]
    assert 0.1 <= gammas[3] <= gammas[2]


def test_point_in_time_reference_date_isolation():
    analytics = GreyhoundAnalytics()
    # Find a dog with past starts
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT dog_id, min(coalesce(run_date_iso, run_date)), max(coalesce(run_date_iso, run_date)), count(*)
        FROM past_performances
        GROUP BY dog_id
        HAVING count(*) >= 3 AND min(coalesce(run_date_iso, run_date)) < max(coalesce(run_date_iso, run_date))
        LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        pytest.skip("No multi-start dog found")

    dog_id, min_date, max_date, total_count = row

    # Profile without date filter (sees all starts)
    p_full = analytics.get_dog_profile(dog_id)
    # Profile with reference_date set to min_date (sees 0 starts before min_date)
    p_isolated = analytics.get_dog_profile(dog_id, reference_date=min_date)

    assert p_isolated["starts_count"] < p_full["starts_count"]


def test_walk_forward_evaluator_structure():
    evaluator = WalkForwardEvaluator()
    dates = evaluator.get_completed_meeting_dates()
    assert len(dates) >= 2
    # Verify strict ascending order
    assert dates == sorted(dates)
    # Verify no 'Guides' corrupt strings
    assert not any("Guides" in d for d in dates)
