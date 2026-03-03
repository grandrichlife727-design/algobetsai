def american_to_decimal(odds):
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)


def compute_bet_profit(stake, odds, result):
    if result == "push":
        return 0
    if result == "loss":
        return -stake
    dec = american_to_decimal(odds)
    return stake * (dec - 1)


def compute_bankroll(start_bankroll, bets):
    bankroll = start_bankroll
    total_staked = 0
    wins = losses = pushes = 0

    for b in bets:
        if b.get("result") is None:
            continue

        stake = float(b["stake"])
        odds = float(b["odds"])
        result = b["result"]

        bankroll += compute_bet_profit(stake, odds, result)
        total_staked += stake

        if result == "win":
            wins += 1
        elif result == "loss":
            losses += 1
        else:
            pushes += 1

    roi = (bankroll - start_bankroll) / total_staked if total_staked else 0

    return {
        "bankroll": bankroll,
        "roi": roi,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
    }