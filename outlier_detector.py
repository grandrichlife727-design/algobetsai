from statistics import mean


def american_to_prob(odds):
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def detect_outliers(book_odds):
    probs = [american_to_prob(o) for o in book_odds.values()]
    consensus = mean(probs)

    results = {}
    for book, odds in book_odds.items():
        prob = american_to_prob(odds)
        diff = consensus - prob
        cents = abs(diff * 1000)

        strength = None
        is_outlier = False

        if cents >= 12:
            strength = "strong"
            is_outlier = True
        elif cents >= 6:
            strength = "moderate"
            is_outlier = True

        results[book] = {
            "is_outlier": is_outlier,
            "outlier_strength": strength,
            "edge_vs_market": diff,
        }

    return results