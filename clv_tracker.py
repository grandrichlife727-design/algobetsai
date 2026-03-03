import json
import os
from datetime import datetime

CLV_FILE = "clv_history.json"


def _load_history():
    if not os.path.exists(CLV_FILE):
        return []
    with open(CLV_FILE, "r") as f:
        return json.load(f)


def _save_history(data):
    with open(CLV_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_pick_snapshot(pick):
    history = _load_history()
    history.append(
        {
            "id": pick.get("id"),
            "open_odds": pick.get("odds"),
            "closing_odds": None,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )
    _save_history(history)