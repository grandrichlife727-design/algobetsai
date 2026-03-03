import json
import os
from datetime import datetime

BET_FILE = "bets_history.json"


def _load():
    if not os.path.exists(BET_FILE):
        return []
    with open(BET_FILE, "r") as f:
        return json.load(f)


def _save(data):
    with open(BET_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_user_bet(pick_id, stake, odds):
    data = _load()
    data.append(
        {
            "pick_id": pick_id,
            "stake": stake,
            "odds": odds,
            "result": None,
            "placed_at": datetime.utcnow().isoformat(),
        }
    )
    _save(data)