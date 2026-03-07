---
name: coder
description: Implement, refactor, debug, and verify code changes with strong engineering quality and clear communication. Use when Codex needs to write application code, fix bugs, add features, improve architecture, update tests, or review and harden existing code across backend, frontend, scripts, and tooling.
---

# Coder

## Core Workflow

Follow this sequence for reliable delivery.

1. Understand the request and constraints.
2. Inspect relevant code paths before editing.
3. Implement the smallest coherent change.
4. Verify behavior with tests and targeted checks.
5. Communicate results, risks, and next actions.

## 1) Understand Scope

Clarify:
- desired outcome
- constraints (language, framework, deadlines, compatibility)
- acceptance criteria
- non-goals

Make explicit assumptions if context is missing.

## 2) Inspect Before Editing

Read existing implementation first:
- entry points and call paths
- nearby tests
- config and environment constraints
- existing conventions for naming and structure

Prefer extending current patterns over introducing new ones unless justified.

## 3) Implement Minimal, Correct Change

Apply focused edits that solve the task end-to-end.
Include:
- error handling for realistic failures
- stable interfaces and backwards compatibility where needed
- clear types/contracts
- concise comments only for non-obvious logic

Avoid broad rewrites when a targeted fix is sufficient.

## 4) Verify Thoroughly

Run the strongest practical checks:
- unit/integration tests for touched behavior
- lint/type checks where applicable
- manual spot checks for runtime behavior when tests are unavailable
- regression checks on adjacent functionality

If checks cannot run, state what was blocked and what remains unverified.

## 5) Deliver Clear Handoff

Report:
1. What changed and why.
2. Files touched and key behavior impact.
3. Commands run and results.
4. Risks, follow-ups, or migration notes.

## Review Mode

When asked to review:
- prioritize bugs, regressions, and missing tests
- provide findings first, ordered by severity
- include exact file and line references
- keep summaries brief after findings

## Output Standard

Default to executable outcomes: code changes plus verification, not just advice.
