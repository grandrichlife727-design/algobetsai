---
name: seo-expert
description: Plan, audit, and execute SEO programs to grow qualified organic traffic and conversions. Use when Codex needs keyword research, topical clustering, technical SEO audits, on-page optimization, internal linking plans, local SEO, schema recommendations, content briefs, SERP analysis, or SEO measurement and reporting.
---

# SEO Expert

## Core Workflow

1. Define business goal, conversion event, geography, and constraints.
2. Map search intent, keyword clusters, and target pages.
3. Audit technical SEO blockers and indexing quality.
4. Prioritize on-page and internal-link opportunities.
5. Deliver an execution backlog with impact and effort scores.
6. Define measurement, reporting cadence, and iteration loop.

## 1) Scope and Baseline

Capture:
- primary business objective (lead, sale, signup, download)
- ICP or audience segment
- target market and language
- current baseline metrics (organic sessions, ranking share, conversion rate)

State assumptions explicitly when analytics access is missing.

## 2) Keyword and Intent Mapping

Build clusters by intent first, then volume:
- informational
- commercial investigation
- transactional
- navigational / branded

For each cluster, specify:
- primary keyword
- secondary/supporting keywords
- target URL (existing or net-new)
- search intent match
- content type (landing page, guide, comparison, category, FAQ)

Reject keyword lists that ignore business relevance, conversion potential, or SERP fit.

## 3) Technical SEO Audit

Run a concise blocker-first audit:
- indexation and crawlability (`robots.txt`, meta robots, canonicals, XML sitemaps)
- duplicate or thin pages
- status code issues (3xx chains, 4xx, 5xx)
- Core Web Vitals and mobile usability
- JavaScript rendering/indexing risk
- structured data validity

Use [technical-seo-checklist.md](references/technical-seo-checklist.md) for full checks.

## 4) On-Page and Information Architecture

For priority pages, optimize:
- title tags and meta descriptions for intent + CTR
- H1/H2 hierarchy and semantic coverage
- entity and topical completeness
- internal links (hub-spoke, contextual anchors)
- schema markup opportunities

Recommend rewrites only when intent mismatch, cannibalization, or weak topical depth is clear.

## 5) Content Production System

When net-new content is needed:
- produce a brief with intent, outline, entities, FAQs, and linking targets
- define authoring constraints (voice, evidence standards, conversion CTA)
- define refresh cadence for decaying pages

Use [content-brief-template.md](references/content-brief-template.md) as the default format.

## 6) Prioritization and Backlog

Score each recommendation with:
- expected impact (`High`, `Medium`, `Low`)
- effort (`S`, `M`, `L`)
- confidence (`0.0-1.0`)
- dependency risk

Prioritize quick wins only if they do not block foundational fixes.

## 7) Measurement and Reporting

Track:
- organic sessions by landing page group
- non-brand vs brand query share
- ranking distribution (Top 3, Top 10, Top 20)
- CTR by query/page pair
- conversions and revenue/leads from organic
- technical health trend (index coverage, CWV pass rate)

Use 2-4 week review cycles; avoid declaring success from single-week variance.

## Output Standard

When responding, return:
1. Assumptions and current-state summary.
2. Keyword-intent map tied to URLs.
3. Technical findings ordered by severity.
4. Prioritized execution backlog table.
5. KPI plan with next review date.
