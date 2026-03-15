#!/usr/bin/env python3
"""
Update _build_model_v2_components calls to include new parameters
"""

import re

with open('main.py', 'r') as f:
    content = f.read()

# Find the section in generate_picks_for_sport where we calculate variables before model_v2
# We need to add calculations for:
# - public_bet_pct
# - line_velocity  
# - hours_to_game

# Insert calculations before the model_v2 call
old_section = r'''        if confidence < MODEL_MIN_CONFIDENCE:
            continue
        model_v2 = _build_model_v2_components\(
            edge_ev=adjusted_edge,
            conservative_edge_ev=conservative_edge,
            books_count=books_count,
            disagreement_pct=disagreement_pct,
            line_gap_pct=line_gap_pct,
            ci_width_pct=ci_width_pct,
        \)'''

new_section = '''        if confidence < MODEL_MIN_CONFIDENCE:
            continue
        
        # IMPROVEMENT: Calculate new model parameters
        # Public betting percentage (default 50% if not available)
        public_bet_pct = float(game.get("public_pct", 50.0) or 50.0)
        
        # Line movement velocity (cents per minute)
        line_velocity = 0.0
        if isinstance(cached_model, dict):
            cache_age = now_ts - float(cached_model.get("ts", 0.0) or 0.0)
            if cache_age > 0:
                line_velocity = _line_move_velocity(
                    cached_model.get("home_ml"),
                    cached_model.get("away_ml"),
                    home_ml,
                    away_ml,
                    cache_age
                )
        
        # Hours until game starts
        hours_to_game = 24.0
        try:
            game_time_str = game.get("commence_time", "")
            if game_time_str:
                from datetime import datetime
                game_dt = datetime.fromisoformat(game_time_str.replace('Z', '+00:00'))
                time_diff = (game_dt.timestamp() - now_ts) / 3600.0
                hours_to_game = max(0.0, time_diff)
        except Exception:
            pass
        
        model_v2 = _build_model_v2_components(
            edge_ev=adjusted_edge,
            conservative_edge_ev=conservative_edge,
            books_count=books_count,
            disagreement_pct=disagreement_pct,
            line_gap_pct=line_gap_pct,
            ci_width_pct=ci_width_pct,
            public_bet_pct=public_bet_pct,
            line_velocity=line_velocity,
            hours_to_game=hours_to_game,
        )'''

content = re.sub(old_section, new_section, content, flags=re.DOTALL)
print("✅ Updated model_v2 call in generate_picks_for_sport")

# Also update the other model_v2 call around line 2354
old_section2 = r'''            model_v2 = _build_model_v2_components\(
                edge_ev=adjusted_edge,
                conservative_edge_ev=conservative_ev,
                books_count=books_count,
                disagreement_pct=disagreement_pct,
                line_gap_pct=line_gap_pct,
                ci_width_pct=ci_width_pct,
            \)'''

new_section2 = '''            model_v2 = _build_model_v2_components(
                edge_ev=adjusted_edge,
                conservative_edge_ev=conservative_ev,
                books_count=books_count,
                disagreement_pct=disagreement_pct,
                line_gap_pct=line_gap_pct,
                ci_width_pct=ci_width_pct,
                public_bet_pct=float(g.get("public_pct", 50.0) or 50.0),
                line_velocity=0.0,  # Not available in fallback picks
                hours_to_game=24.0,  # Default for fallback
            )'''

content = re.sub(old_section2, new_section2, content, flags=re.DOTALL)
print("✅ Updated model_v2 call in _fallback_picks_from_games")

with open('main.py', 'w') as f:
    f.write(content)

print("\n🎉 All model_v2 calls updated with new parameters!")

