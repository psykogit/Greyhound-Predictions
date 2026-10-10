with open('srm_optimizer.py', 'r') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'active_odds' not in line:
        lines[i] = line.replace('["market_odds"]', '["active_odds"]')
        lines[i] = lines[i].replace("['market_odds']", '["active_odds"]')

with open('srm_optimizer.py', 'w') as f:
    f.writelines(lines)
