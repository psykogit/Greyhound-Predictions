import itertools

def calculate_harville_probabilities(win_probs):
    """
    Given a dict of {box: win_prob}, calculates the probability of all valid Top-4 permutations.
    Returns a dict mapping (1st, 2nd, 3rd, 4th) -> probability
    """
    boxes = list(win_probs.keys())
    permutations = list(itertools.permutations(boxes, 4))
    
    top4_probs = {}
    total_prob = 0.0
    
    for perm in permutations:
        b1, b2, b3, b4 = perm
        p1 = win_probs[b1]
        p2 = win_probs[b2]
        p3 = win_probs[b3]
        p4 = win_probs[b4]
        
        # Harville Formula
        denom1 = 1.0 - p1
        denom2 = 1.0 - p1 - p2
        denom3 = 1.0 - p1 - p2 - p3
        
        if denom1 <= 0 or denom2 <= 0 or denom3 <= 0:
            continue
            
        prob = p1 * (p2 / denom1) * (p3 / denom2) * (p4 / denom3)
        top4_probs[perm] = prob
        total_prob += prob
        
    # Normalize to ensure sum is exactly 1.0 (accounting for very slight float rounding or Harville anomalies)
    for perm in top4_probs:
        top4_probs[perm] /= total_prob
        
    return top4_probs

def evaluate_srm_condition(top4_probs, conditions):
    """
    conditions: dict of {box: condition_string}
    condition_string in ["win", "top_2", "top_3", "top_4"]
    Returns the true probability of this exact SRM occurring.
    """
    total_prob = 0.0
    for perm, prob in top4_probs.items():
        # Check if this permutation satisfies ALL conditions
        satisfies_all = True
        for box, cond in conditions.items():
            if box not in perm:
                satisfies_all = False
                break
            
            idx = perm.index(box) # 0 for 1st, 1 for 2nd, 2 for 3rd, 3 for 4th
            if cond == "win" and idx > 0:
                satisfies_all = False
                break
            elif cond == "top_2" and idx > 1:
                satisfies_all = False
                break
            elif cond == "top_3" and idx > 2:
                satisfies_all = False
                break
            elif cond == "top_4" and idx > 3:
                satisfies_all = False
                break
                
        if satisfies_all:
            total_prob += prob
            
    return total_prob

if __name__ == "__main__":
    # Richmond Straight Race 6 Data from User Image
    # 5 is scratched
    market_win_odds = {
        1: 16.00,
        2: 1.80,
        3: 71.00,
        4: 3.00,
        6: 35.00,
        7: 3.60,
        8: 27.00
    }
    
    # 1. Calculate Implied True Win Probabilities (removing overround)
    market_sum = sum(1.0 / odds for odds in market_win_odds.values())
    true_win_probs = {box: (1.0 / odds) / market_sum for box, odds in market_win_odds.items()}
    
    # 2. Generate all 840 permutations of the Top 4 finishes
    top4_distribution = calculate_harville_probabilities(true_win_probs)
    
    # 3. Define the 4 dogs we want to build an SRM around
    # Likely the top 4 in the market: 2 (Ruination), 4 (Despicable Leo), 7 (Tarawi Pringle), 1 (Bring On Bling)
    dogs = [2, 4, 7, 1]
    
    print(f"--- 4-Leg SRM Evaluator for Richmond Straight Race 6 ---")
    print(f"Market Overround: {market_sum*100:.1f}%")
    print(f"Selected Dogs: Box {dogs}")
    
    # 4. Evaluate various combinations
    scenarios = [
        {"desc": "All 4 dogs to finish Top 4 (Boxed First 4)", "conds": {2: "top_4", 4: "top_4", 7: "top_4", 1: "top_4"}},
        
        {"desc": "Dog 2 to Win, Dogs 4, 7, 1 to finish Top 4", "conds": {2: "win", 4: "top_4", 7: "top_4", 1: "top_4"}},
        {"desc": "Dog 4 to Win, Dogs 2, 7, 1 to finish Top 4", "conds": {4: "win", 2: "top_4", 7: "top_4", 1: "top_4"}},
        
        {"desc": "Dog 2 Top 2, Dog 4 Top 2, Dogs 7, 1 Top 4", "conds": {2: "top_2", 4: "top_2", 7: "top_4", 1: "top_4"}},
        {"desc": "Dog 2 Top 2, Dog 7 Top 2, Dogs 4, 1 Top 4", "conds": {2: "top_2", 7: "top_2", 4: "top_4", 1: "top_4"}},
        
        {"desc": "Dog 2 Top 2, Dog 4 Top 3, Dog 7 Top 4, Dog 1 Top 4", "conds": {2: "top_2", 4: "top_3", 7: "top_4", 1: "top_4"}},
        
        {"desc": "Dog 2 Win, Dog 4 Top 2, Dog 7 Top 3, Dog 1 Top 4", "conds": {2: "win", 4: "top_2", 7: "top_3", 1: "top_4"}},
        
        # Adding a scenario without dog 1 (using the 4th fav, dog 8)
        {"desc": "Top 4 Favorites (2, 4, 7, 8) to all finish Top 4", "conds": {2: "top_4", 4: "top_4", 7: "top_4", 8: "top_4"}}
    ]
    
    print(f"\n--- Probabilities for different SRM combinations ---")
    for s in scenarios:
        prob = evaluate_srm_condition(top4_distribution, s["conds"])
        fair_odds = 1.0 / prob if prob > 0 else 0
        print(f"\nScenario: {s['desc']}")
        print(f"True Probability:  {prob*100:.2f}%")
        print(f"Fair Target Odds:  ${fair_odds:.2f}")
