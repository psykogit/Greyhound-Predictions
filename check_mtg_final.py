from analytics import GreyhoundAnalytics
a = GreyhoundAnalytics()
for p in a.predict_race_probabilities(1136):
    print(f"Box {p['box']}: Win Prob = {p['model_win_prob']:.3f} | Dog = {p['dog_name']}")
