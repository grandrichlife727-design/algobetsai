def detect_rlm(public_pct, line_move):
    if public_pct is None:
        return False
    if public_pct >= 65 and line_move < 0:
        return True
    if public_pct <= 35 and line_move > 0:
        return True
    return False