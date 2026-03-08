---
name: sports-prediction-math-methods
description: Apply mathematical and statistical methods for sports prediction modeling with defensible uncertainty estimates and validation. Use when Codex needs to design, compare, or improve sports models using methods such as Poisson scoring models, logistic regression, Elo-style ratings, Bayesian updating, Monte Carlo simulation, calibration analysis, and backtesting.
---

# Sports Prediction Math Methods

## Core Principles

- Optimize expected value, not certainty.
- Quantify uncertainty for every prediction.
- Prefer simpler models unless complexity proves out-of-sample value.
- Prevent leakage and overfitting before tuning for performance.

## Core Workflow

1. Define prediction target and horizon.
2. Build mathematically consistent features.
3. Select baseline and candidate models.
4. Validate with time-aware evaluation.
5. Calibrate probabilities.
6. Convert predictions into decision metrics.

## 1) Define Target

Specify:
- task type (win/loss, score, margin, totals, player prop)
- prediction horizon (pre-game, in-game window, season-long)
- objective metric (log loss, Brier score, MAE, RMSE, ROI proxy)
- operational constraints (latency, data freshness, interpretability)

## 2) Build Feature Set

Create features from:
- team/player strength and recent form
- pace/tempo and matchup interactions
- home/away effects, rest, travel, injuries
- market-implied signals (line movement, closing numbers)

Guardrails:
- split data chronologically before feature engineering
- avoid post-outcome and target-leakage fields
- use rolling windows consistent with prediction timestamp

## 3) Model Families

Start with baselines, then compare stronger methods:
- logistic regression for classification probabilities
- Poisson or negative binomial models for scoring counts
- Elo/Glicko-style rating updates for dynamic strength
- Bayesian hierarchical models for partial pooling and shrinkage
- Monte Carlo simulation for distributional outcomes

Keep one transparent baseline in every comparison.

## 4) Validation and Backtesting

Use:
- walk-forward or expanding-window validation
- out-of-time holdout sets
- ablation tests for feature contribution
- robustness checks under regime changes (injury clusters, playoffs)

Report:
- central metric values
- confidence intervals via bootstrap or repeated folds
- failure cases by segment (team tier, market type, season phase)

## 5) Probability Calibration

Evaluate and correct calibration:
- reliability curves
- calibration intercept/slope
- isotonic regression or Platt scaling when needed

Require calibrated probabilities before expected-value decisions.

## 6) Decision Layer

Translate model output into actionable quantities:
- implied probability from market odds
- model edge = model probability - fair implied probability
- expected value under offered payouts
- stake sizing via fractional Kelly or fixed-unit policy with caps

Default to no-action when edge is unstable under sensitivity checks.

## Output Standard

When responding:
1. State model objective and target variable.
2. Show selected methods and why they fit the task.
3. Provide validation metrics and uncertainty bounds.
4. Show calibration quality and any correction applied.
5. Present decision recommendation with explicit risk constraints.
