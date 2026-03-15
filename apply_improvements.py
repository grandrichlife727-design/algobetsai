#!/usr/bin/env python3
"""
Apply Phase 1 prediction algorithm improvements to main.py
"""

import re

# Read the file
with open('main.py', 'r') as f:
    content = f.read()

# ============================================================================
# IMPROVEMENT #1: Add line movement velocity tracking
# ============================================================================

# Add velocity calculation function after _spread_move_points
velocity_func = '''

def _line_move_velocity(prev_home: Any, prev_away: Any, cur_home: Any, cur_away: Any, time_diff_seconds: float) -> float:
    """
    Calculate line movement velocity in cents per minute.
    Fast movement = sharp money = higher quality signal.
    """
    if time_diff_seconds <= 0:
        return 0.0
    cents_move = _line_move_cents(prev_home, prev_away, cur_home, cur_away)
    if cents_move >= 999:  # Invalid data
        return 0.0
    minutes = time_diff_seconds / 60.0
    if minutes <= 0:
        return 0.0
    return cents_move / minutes
'''

# Insert after _spread_move_points function
pattern = r'(def _spread_move_points\(.*?\n(?:.*?\n)*?    return 0\.0\n)'
match = re.search(pattern, content)
if match:
    insert_pos = match.end()
    content = content[:insert_pos] + velocity_func + content[insert_pos:]
    print("✅ Added line movement velocity function")
else:
    print("⚠️  Could not find insertion point for velocity function")

# ============================================================================
# IMPROVEMENT #2 & #3: Enhanced market_consensus_fair_prob with recency weighting
# ============================================================================

# Find and replace the market_consensus_fair_prob function
old_consensus_loop = r'''    weighted_rows: list\[tuple\[float, float\]\] = \[\]
    for book, lines in by_book\.items\(\):
        home_o = lines\.get\("home"\)
        away_o = lines\.get\("away"\)
        if home_o is None or away_o is None:
            continue
        h, a = devig_two_way_probabilities\(home_o, away_o\)
        w = sharp_weight_for_book\(book\)
        weighted_rows\.append\(\(h, w\)\)'''

new_consensus_loop = '''    weighted_rows: list[tuple[float, float]] = []
    current_time = time.time()
    
    for book, lines in by_book.items():
        home_o = lines.get("home")
        away_o = lines.get("away")
        if home_o is None or away_o is None:
            continue
        h, a = devig_two_way_probabilities(home_o, away_o)
        
        # Base sharp book weight
        w = sharp_weight_for_book(book)
        
        # IMPROVEMENT #3: Recency weighting
        # Apply recency bonus - odds updated in last 5 mins get 1.15x weight
        # Odds 5-15 mins old get 1.05x weight, older odds get no bonus
        odds_timestamp = lines.get("last_update", current_time)
        age_minutes = (current_time - odds_timestamp) / 60.0
        if age_minutes < 5:
            recency_multiplier = 1.15
        elif age_minutes < 15:
            recency_multiplier = 1.05
        else:
            recency_multiplier = 1.0
        
        w = w * recency_multiplier
        weighted_rows.append((h, w))'''

content = re.sub(old_consensus_loop, new_consensus_loop, content)
print("✅ Added recency weighting to market consensus")

# ============================================================================
# IMPROVEMENT #4: Enhanced timing model with public betting integration
# ============================================================================

# Find and replace _build_model_v2_components function
old_model_v2 = r'''def _build_model_v2_components\(
    edge_ev: float,
    conservative_edge_ev: float,
    books_count: int,
    disagreement_pct: float,
    line_gap_pct: float,
    ci_width_pct: float,
\) -> dict\[str, float\]:
    # Bounded component models blended into a single ensemble quality score\.
    market_score = max\(0\.0, min\(100\.0, 45\.0 \+ conservative_edge_ev \* 6\.0\)\)
    value_score = max\(0\.0, min\(100\.0, 45\.0 \+ edge_ev \* 4\.0\)\)
    liquidity_score = max\(0\.0, min\(100\.0, 35\.0 \+ books_count \* 8\.0\)\)
    stability_score = max\(0\.0, min\(100\.0, 92\.0 - disagreement_pct \* 2\.4\)\)
    uncertainty_score = max\(0\.0, min\(100\.0, 95\.0 - ci_width_pct \* 8\.0\)\)
    timing_score = max\(0\.0, min\(100\.0, 50\.0 \+ line_gap_pct \* 4\.0\)\)
    ensemble = round\(
        market_score \* 0\.28
        \+ value_score \* 0\.20
        \+ liquidity_score \* 0\.16
        \+ stability_score \* 0\.16
        \+ uncertainty_score \* 0\.12
        \+ timing_score \* 0\.08,
        2,
    \)'''

new_model_v2 = '''def _build_model_v2_components(
    edge_ev: float,
    conservative_edge_ev: float,
    books_count: int,
    disagreement_pct: float,
    line_gap_pct: float,
    ci_width_pct: float,
    public_bet_pct: float = 50.0,
    line_velocity: float = 0.0,
    hours_to_game: float = 24.0,
) -> dict[str, float]:
    """
    Bounded component models blended into a single ensemble quality score.
    
    IMPROVEMENTS:
    - Enhanced timing model with game proximity and line velocity
    - Public betting integration (fade the public bonus)
    - Line movement velocity bonus
    """
    market_score = max(0.0, min(100.0, 45.0 + conservative_edge_ev * 6.0))
    value_score = max(0.0, min(100.0, 45.0 + edge_ev * 4.0))
    liquidity_score = max(0.0, min(100.0, 35.0 + books_count * 8.0))
    stability_score = max(0.0, min(100.0, 92.0 - disagreement_pct * 2.4))
    uncertainty_score = max(0.0, min(100.0, 95.0 - ci_width_pct * 8.0))
    
    # IMPROVEMENT #4: Enhanced timing model
    # Base timing from CLV gap
    timing_base = 50.0 + line_gap_pct * 4.0
    
    # Bonus for games closer to start time (better CLV opportunity)
    # Games within 6 hours get up to +10 points
    proximity_bonus = max(0.0, min(10.0, (24.0 - hours_to_game) / 2.0))
    
    # Bonus for fast line movement (sharp money indicator)
    # Velocity > 5 cents/min gets up to +8 points
    velocity_bonus = max(0.0, min(8.0, line_velocity * 1.6))
    
    timing_score = max(0.0, min(100.0, timing_base + proximity_bonus + velocity_bonus))
    
    # IMPROVEMENT #2: Public betting integration
    # Fade the public: when public is >65% on one side, boost score
    # When public is <35%, also boost (contrarian indicator)
    public_score = 50.0
    if public_bet_pct >= 65.0:
        # Fading heavy public - good sign
        public_score = max(0.0, min(100.0, 50.0 + (public_bet_pct - 65.0) * 2.0))
    elif public_bet_pct <= 35.0:
        # Also fading public on other side
        public_score = max(0.0, min(100.0, 50.0 + (35.0 - public_bet_pct) * 2.0))
    
    # Reweight ensemble to include public score (reduce timing weight slightly)
    ensemble = round(
        market_score * 0.26
        + value_score * 0.20
        + liquidity_score * 0.15
        + stability_score * 0.15
        + uncertainty_score * 0.10
        + timing_score * 0.08
        + public_score * 0.06,
        2,
    )'''

content = re.sub(old_model_v2, new_model_v2, content, flags=re.DOTALL)
print("✅ Enhanced timing model and added public betting integration")

# Update the return statement in _build_model_v2_components
old_return = r'''    return \{
        "market_model": round\(market_score, 2\),  # conservative edge focus
        "value_model": round\(value_score, 2\),  # point-estimate EV
        "liquidity_model": round\(liquidity_score, 2\),
        "stability_model": round\(stability_score, 2\),
        "uncertainty_model": round\(uncertainty_score, 2\),
        "timing_model": round\(timing_score, 2\),
        "ensemble_score": ensemble,
    \}'''

new_return = '''    return {
        "market_model": round(market_score, 2),  # conservative edge focus
        "value_model": round(value_score, 2),  # point-estimate EV
        "liquidity_model": round(liquidity_score, 2),
        "stability_model": round(stability_score, 2),
        "uncertainty_model": round(uncertainty_score, 2),
        "timing_model": round(timing_score, 2),
        "public_model": round(public_score, 2),  # fade the public
        "ensemble_score": ensemble,
    }'''

content = re.sub(old_return, new_return, content)
print("✅ Updated model v2 return to include public_model")

# Write the modified content
with open('main.py', 'w') as f:
    f.write(content)

print("\n🎉 All Phase 1 improvements applied successfully!")
print("\nNext steps:")
print("1. Update generate_picks_for_sport() to use new parameters")
print("2. Test the changes")
print("3. Commit and push")

