#!/usr/bin/env python3
"""
POTD Generator — AlgoBets Ai
Generates a single Pick of the Day using a 5-agent weighted model
and writes the result to potd.json for static frontend consumption.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_BASE = "https://api.the-odds-api.com/v4/sports"

SPORTS = [
    "basketball_nba",
    "icehockey_nhl",
    "basketball_ncaab",
    "baseball_mlb",
    "soccer_epl",
]

BOOKMAKERS = ["draftkings", "fanduel", "betmgm", "pinnacle", "caesars", "betonlineag"]


def american_to_implied(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def format_american(odds: int) -> str:
    return f"+{odds}" if odds > 0 else str(odds)


def format_book_name(key: str) -> str:
    return {
        "draftkings": "DraftKings",
        "fanduel": "FanDuel",
        "betmgm": "BetMGM",
        "pinnacle": "Pinnacle",
        "caesars": "Caesars",
        "betonlineag": "BetOnline",
    }.get(key, key.title())


def fetch_odds(sport_key: str) -> list[dict[str, Any]]:
    url = f"{ODDS_BASE}/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "american",
        "bookmakers": ",".join(BOOKMAKERS),
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"[{sport_key}] HTTP {resp.status_code}")
            return []
        remaining = resp.headers.get("x-requests-remaining", "?")
        data = resp.json()
        print(f"[{sport_key}] games={len(data)} remaining={remaining}")
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[{sport_key}] fetch error: {exc}")
        return []


def parse_odds_by_book(game: dict[str, Any]) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, dict[str, int | float]]]]:
    odds_by_book: dict[str, dict[str, int]] = {}
    spreads_by_book: dict[str, dict[str, dict[str, int | float]]] = {}
    home = game.get("home_team", "")
    away = game.get("away_team", "")
    for bm in game.get("bookmakers", []):
        book_key = bm.get("key")
        if not book_key:
            continue
        for market in bm.get("markets", []):
            mkey = market.get("key")
            if mkey == "h2h":
                outcomes: dict[str, int] = {}
                for o in market.get("outcomes", []):
                    if o.get("name") == home:
                        outcomes["home"] = int(o.get("price"))
                    elif o.get("name") == away:
                        outcomes["away"] = int(o.get("price"))
                if outcomes:
                    odds_by_book[book_key] = outcomes
            elif mkey == "spreads":
                outcomes_spread: dict[str, dict[str, int | float]] = {}
                for o in market.get("outcomes", []):
                    row = {"odds": int(o.get("price")), "point": float(o.get("point", 0))}
                    if o.get("name") == home:
                        outcomes_spread["home"] = row
                    elif o.get("name") == away:
                        outcomes_spread["away"] = row
                if outcomes_spread:
                    spreads_by_book[book_key] = outcomes_spread
    return odds_by_book, spreads_by_book


def agent_best_line_ev(odds_by_book: dict[str, dict[str, int]]) -> Optional[dict[str, Any]]:
    pinnacle = odds_by_book.get("pinnacle", {})
    if not pinnacle:
        return None
    results: list[dict[str, Any]] = []
    for outcome_key in ("home", "away"):
        pin_odds = pinnacle.get(outcome_key)
        if pin_odds is None:
            continue
        pin_implied = american_to_implied(pin_odds)
        best_odds = None
        best_book = None
        for book, outcomes in odds_by_book.items():
            book_odds = outcomes.get(outcome_key)
            if book_odds is None:
                continue
            if best_odds is None or book_odds > best_odds:
                best_odds = book_odds
                best_book = book
        if best_odds is None:
            continue
        best_implied = american_to_implied(best_odds)
        ev_edge = pin_implied - best_implied
        if ev_edge > 0:
            results.append(
                {
                    "outcome": outcome_key,
                    "ev_edge": round(ev_edge * 100, 2),
                    "best_odds": int(best_odds),
                    "best_book": str(best_book),
                    "fair_prob": round(pin_implied * 100, 1),
                    "market_prob": round(best_implied * 100, 1),
                }
            )
    return max(results, key=lambda r: r["ev_edge"]) if results else None


def agent_devig_consensus(odds_by_book: dict[str, dict[str, int]]) -> Optional[dict[str, Any]]:
    home_probs: list[float] = []
    away_probs: list[float] = []
    for outcomes in odds_by_book.values():
        home_odds = outcomes.get("home")
        away_odds = outcomes.get("away")
        if home_odds is None or away_odds is None:
            continue
        home_imp = american_to_implied(home_odds)
        away_imp = american_to_implied(away_odds)
        total_imp = home_imp + away_imp
        if total_imp > 0:
            home_probs.append(home_imp / total_imp)
            away_probs.append(away_imp / total_imp)
    if len(home_probs) < 3:
        return None
    return {
        "home_fair_prob": round(sum(home_probs) / len(home_probs) * 100, 1),
        "away_fair_prob": round(sum(away_probs) / len(away_probs) * 100, 1),
        "books_used": len(home_probs),
    }


def agent_steam(odds_by_book: dict[str, dict[str, int]]) -> dict[str, Any]:
    pinnacle = odds_by_book.get("pinnacle", {})
    if not pinnacle:
        return {"steam_score": 0.0, "direction": None, "steam_flag": False}
    soft_books = ["draftkings", "fanduel", "betmgm", "caesars"]
    divergences: list[dict[str, Any]] = []
    for outcome in ("home", "away"):
        pin_odds = pinnacle.get(outcome)
        if pin_odds is None:
            continue
        for book in soft_books:
            book_odds = odds_by_book.get(book, {}).get(outcome)
            if book_odds is None:
                continue
            pin_imp = american_to_implied(pin_odds)
            book_imp = american_to_implied(book_odds)
            divergences.append(
                {
                    "outcome": outcome,
                    "book": book,
                    "divergence": abs(pin_imp - book_imp),
                    "direction": f"sharp_on_{outcome}" if pin_imp > book_imp else f"soft_on_{outcome}",
                }
            )
    if not divergences:
        return {"steam_score": 0.0, "direction": None, "steam_flag": False}
    max_div = max(divergences, key=lambda d: d["divergence"])
    steam_score = round(max_div["divergence"] * 100, 1)
    return {
        "steam_score": steam_score,
        "direction": max_div["direction"],
        "book": max_div["book"],
        "steam_flag": steam_score > 3.0,
    }


def agent_public_fade(odds_by_book: dict[str, dict[str, int]]) -> dict[str, Any]:
    retail_books = ["draftkings", "fanduel", "betmgm", "caesars"]
    pinnacle = odds_by_book.get("pinnacle", {})
    if not pinnacle:
        return {"fade_signal": False}
    retail_home_implied: list[float] = []
    for book in retail_books:
        home_odds = odds_by_book.get(book, {}).get("home")
        if home_odds is not None:
            retail_home_implied.append(american_to_implied(home_odds))
    if len(retail_home_implied) < 2:
        return {"fade_signal": False}
    avg_retail_home = sum(retail_home_implied) / len(retail_home_implied)
    pin_home = american_to_implied(pinnacle.get("home", -110))
    skew = (avg_retail_home - pin_home) * 100
    return {
        "fade_signal": abs(skew) > 2.0,
        "skew_pct": round(skew, 1),
        "public_side": "home" if skew > 0 else "away",
        "contrarian_side": "away" if skew > 0 else "home",
    }


def agent_timing_clv(ev_result: Optional[dict[str, Any]], steam_result: dict[str, Any]) -> dict[str, Any]:
    if not ev_result:
        return {"clv_signal": False, "urgency": "low"}
    ev_edge = float(ev_result.get("ev_edge", 0.0) or 0.0)
    steam_flag = bool(steam_result.get("steam_flag", False))
    if ev_edge >= 5.0 and steam_flag:
        return {"clv_signal": True, "urgency": "high", "window": "closing"}
    if ev_edge >= 3.0:
        return {"clv_signal": True, "urgency": "medium", "window": "open"}
    return {"clv_signal": False, "urgency": "low", "window": "stable"}


def score_game(game: dict[str, Any], odds_by_book: dict[str, dict[str, int]], spreads_by_book: dict[str, dict[str, dict[str, int | float]]]) -> Optional[dict[str, Any]]:
    ev = agent_best_line_ev(odds_by_book)
    devig = agent_devig_consensus(odds_by_book)
    steam = agent_steam(odds_by_book)
    fade = agent_public_fade(odds_by_book)
    timing = agent_timing_clv(ev, steam)
    if not ev or float(ev["ev_edge"]) < 2.0:
        return None

    confidence = 0.0
    agents_fired = 1

    confidence += min(float(ev["ev_edge"]) / 10.0, 1.0) * 35.0
    if devig:
        agents_fired += 1
        fair = float(devig.get("home_fair_prob", 50.0) if ev["outcome"] == "home" else devig.get("away_fair_prob", 50.0))
        if fair > 50:
            confidence += min((fair - 50.0) / 30.0, 1.0) * 25.0
    if steam.get("steam_flag"):
        agents_fired += 1
        steam_bonus = min(float(steam.get("steam_score", 0.0)) / 8.0, 1.0) * 20.0
        if ev["outcome"] in str(steam.get("direction") or ""):
            confidence += steam_bonus
    if fade.get("fade_signal"):
        agents_fired += 1
        if fade.get("contrarian_side") == ev["outcome"]:
            confidence += 10.0
    if timing.get("clv_signal"):
        agents_fired += 1
        confidence += 10.0 if timing.get("urgency") == "high" else 6.0

    confidence = min(round(confidence), 100)
    grade = "A" if confidence >= 75 else ("B" if confidence >= 60 else "C")
    outcome = str(ev["outcome"])
    team = game["home_team"] if outcome == "home" else game["away_team"]
    matchup = f"{game['away_team']} @ {game['home_team']}"

    is_underdog = int(ev["best_odds"]) > 0
    bet_type = "ML"
    bet_odds = int(ev["best_odds"])
    bet_book = str(ev["best_book"])
    spread_point: Optional[float] = None

    if is_underdog and spreads_by_book:
        best_spread_odds = None
        best_spread_book = None
        best_spread_point = None
        for book, spread_data in spreads_by_book.items():
            side = spread_data.get(outcome)
            if not side:
                continue
            s_odds = int(side["odds"])
            s_point = float(side["point"])
            if best_spread_odds is None or s_odds > best_spread_odds:
                best_spread_odds = s_odds
                best_spread_book = book
                best_spread_point = s_point
        if best_spread_odds is not None and best_spread_point is not None:
            bet_type = "spread"
            bet_odds = best_spread_odds
            bet_book = str(best_spread_book)
            spread_point = best_spread_point

    if bet_type == "spread":
        point_str = f"+{spread_point}" if spread_point and spread_point > 0 else str(spread_point)
        bet_label = f"{team} {point_str}"
    else:
        bet_label = f"{team} ML"

    return {
        "game": matchup,
        "league": game.get("sport_title", ""),
        "sport_key": game.get("sport_key", ""),
        "bet": bet_label,
        "bet_type": bet_type,
        "team": team,
        "odds": format_american(bet_odds),
        "odds_raw": bet_odds,
        "spread_point": spread_point,
        "book": format_book_name(bet_book),
        "book_key": bet_book,
        "confidence": int(confidence),
        "grade": grade,
        "edge": float(ev["ev_edge"]),
        "fair_prob": float(ev["fair_prob"]),
        "market_prob": float(ev["market_prob"]),
        "agents_fired": int(agents_fired),
        "is_underdog": bool(is_underdog),
        "steam_score": float(steam.get("steam_score", 0.0)),
        "fade_signal": bool(fade.get("fade_signal", False)),
        "clv_urgency": str(timing.get("urgency", "low")),
        "game_time": game.get("commence_time", ""),
    }


def write_potd(pick: Optional[dict[str, Any]]) -> None:
    payload = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pick": pick,
    }
    with open("potd.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"potd.json written ({'with pick' if pick else 'empty'})")


def main() -> None:
    print("=== AlgoBets Ai POTD Generator ===")
    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY not set")
        write_potd(None)
        return

    all_scored: list[dict[str, Any]] = []
    now_utc = datetime.now(timezone.utc)
    for sport in SPORTS:
        games = fetch_odds(sport)
        for game in games:
            commence_raw = str(game.get("commence_time") or "").strip()
            if commence_raw:
                try:
                    commence_at = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                    if commence_at < now_utc:
                        continue
                except Exception:
                    pass
            odds_by_book, spreads_by_book = parse_odds_by_book(game)
            if len(odds_by_book) < 3 or "pinnacle" not in odds_by_book:
                continue
            scored = score_game(game, odds_by_book, spreads_by_book)
            if scored:
                all_scored.append(scored)

    qualified: list[dict[str, Any]] = []
    for p in all_scored:
        if p["bet_type"] == "spread" and not p["is_underdog"] and p.get("spread_point") is not None and abs(float(p["spread_point"])) > 3.5:
            continue
        if int(p["agents_fired"]) < 2:
            continue
        if float(p["edge"]) < 5.0:
            continue
        if int(p["agents_fired"]) < 3:
            continue
        qualified.append(p)

    if qualified:
        potd = max(qualified, key=lambda x: (int(x["confidence"]), float(x["edge"])))
        write_potd(potd)
        return

    fallback = None
    if all_scored:
        candidate = max(all_scored, key=lambda x: (int(x["confidence"]), float(x["edge"])))
        if float(candidate["edge"]) >= 3.0 and int(candidate["agents_fired"]) >= 2:
            candidate["grade"] = "B"
            fallback = candidate
    write_potd(fallback)


if __name__ == "__main__":
    main()
