---
name: sports-betting-predictor
description: Build and operate a disciplined sports betting prediction workflow using probabilistic modeling, market pricing, and bankroll risk controls. Use when Codex needs to estimate win probabilities, compare model odds vs sportsbook odds, size wagers with risk limits, backtest strategy performance, or explain why a bet should be skipped due to weak edge or uncertainty.
---

# Sports Betting Predictor

## Core Principles

- Do not claim guaranteed wins.
- Treat every pick as a probabilistic decision under uncertainty.
- Prefer no-bet over forced action when edge is unclear.
- Protect bankroll first; performance compounds through survival and discipline.

## Core Workflow

1. Define target market and constraints.
2. Gather and sanity-check input data.
3. Build implied odds and model probabilities.
4. Estimate edge after vig and fees.
5. Size stake with strict risk caps.
6. Log outcomes and recalibrate.

## 1) Define Market and Constraints

Specify:
- league and bet type (moneyline, spread, totals, props)
- sportsbook source and timestamp
- minimum edge threshold
- max risk per bet and per day
- no-bet triggers (missing data, line movement, low confidence)

## 2) Gather and Validate Data

Collect:
- recent team/player performance splits
- injuries, rest, travel, and schedule density
- historical matchup and pace/context features
- opening line vs current line movement

Reject or down-weight stale, sparse, or conflicting inputs.

## 3) Convert Prices and Predict Probabilities

For each candidate bet:
- convert book odds to implied probability
- remove vig where possible to estimate fair market baseline
- compute model probability from selected features
- produce confidence interval, not only point estimate

## 4) Estimate Edge and Decision Quality

Compute:
- edge = model probability - fair implied probability
- expected value using offered payout terms
- sensitivity under reasonable assumption shifts

Decision policy:
- place bet only when edge exceeds threshold and confidence is acceptable
- skip if edge disappears under mild sensitivity changes

## 5) Position Sizing and Risk Limits

Use fractional Kelly or fixed-unit sizing with hard caps:
- cap stake by bankroll percentage
- cap cumulative exposure by day/slate
- avoid highly correlated positions unless risk-adjusted

If risk controls and edge conflict, enforce risk controls.

## 6) Track, Backtest, and Recalibrate

Maintain a log with:
- prediction timestamp and closing line
- model probability, implied probability, and edge
- stake size and result
- post-mortem tags (data miss, variance, model misspecification)

Review on schedule:
- calibration (predicted vs realized)
- CLV (closing line value)
- ROI by market type and confidence band

Update thresholds and feature weights only after enough sample size.

## Output Standard

When responding:
1. State market, odds source, and timestamp.
2. Show implied probability, model probability, and edge.
3. Recommend `bet` or `no-bet` with rationale.
4. Provide stake size with explicit bankroll rule.
5. List top uncertainty factors and invalidation triggers.
