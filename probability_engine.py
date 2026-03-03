import math


def normal_cdf(x, mean, std):
    if std <= 0:
        return 0.5
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def over_probability(line, projection, std_dev):
    under_prob = normal_cdf(line, projection, std_dev)
    return 1 - under_prob


def expected_value(win_prob, odds):
    if odds > 0:
        profit = odds / 100
    else:
        profit = 100 / abs(odds)
    return (win_prob * profit) - (1 - win_prob)


def kelly_fraction(win_prob, odds, kelly_mult=0.5):
    if odds > 0:
        b = odds / 100
    else:
        b = 100 / abs(odds)

    q = 1 - win_prob
    kelly = (b * win_prob - q) / b
    return max(0, kelly * kelly_mult)