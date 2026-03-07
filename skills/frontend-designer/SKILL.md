---
name: frontend-designer
description: Design and implement distinctive, production-ready frontend interfaces with strong visual direction, responsive behavior, and clean component structure. Use when Codex needs to create or redesign web UI, improve weak/generic styling, build landing pages or dashboards, define design tokens, or translate product goals into polished HTML/CSS/JS or React frontend code.
---

# Frontend Designer

## Core Workflow

Follow this sequence to avoid generic UI and reduce rework.

1. Define visual direction before writing components.
2. Build a token system (color, type, spacing, radius, shadows, motion).
3. Compose layout and component hierarchy around content priorities.
4. Add motion and interaction states with intent, not decoration.
5. Verify responsiveness, accessibility, and implementation quality.

## 1) Define Visual Direction

Commit to one clear concept early (editorial, brutalist, retro-future, premium finance, etc.).
State it in 2 to 4 bullets:
- tone
- core contrast pattern
- typography mood
- interaction mood

Reject safe defaults unless constrained by an existing design system.

## 2) Create Design Tokens First

Define CSS variables before composing detailed sections.
Include:
- color roles: `--bg`, `--surface`, `--text`, `--muted`, `--accent`, `--accent-2`
- typography: display, heading, body, mono stacks
- spacing scale: at least 6 steps
- radius and shadow scale
- animation timing and easing tokens

Select expressive fonts and avoid generic stacks unless user constraints require them.

## 3) Build Structure and Components

Start from semantic structure and information hierarchy, then style.
Prefer:
- clear section rhythm
- asymmetry or focal offsets where appropriate
- deliberate headline treatment
- restrained but meaningful ornament (gradients, patterns, shapes)

For components, always include all states:
- default
- hover/focus
- active/pressed
- disabled
- loading/skeleton (if relevant)

## 4) Add Motion With Purpose

Use a small motion system:
- page-intro reveal
- section stagger for dense blocks
- interactive transitions tied to user intent

Avoid constant animation loops and novelty effects that reduce legibility.

## 5) Ship-Ready Checks

Run this checklist before finalizing:
- responsive behavior at mobile, tablet, desktop breakpoints
- keyboard focus visibility and sensible tab order
- text contrast and readable line length
- no layout shifts from late-loading assets
- consistent spacing and token usage
- code organization that matches project conventions

## Output Standard

When generating or editing frontend code:
1. Explain the chosen visual direction in 3 to 5 bullets.
2. Implement tokens first, then sections/components.
3. Preserve existing design systems when present.
4. Ensure desktop and mobile both render correctly.
5. Avoid "average template" aesthetics unless explicitly requested.
