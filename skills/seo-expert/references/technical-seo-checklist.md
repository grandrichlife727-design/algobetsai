# Technical SEO Checklist

Use this list during technical audits. Focus on blockers first.

## Crawl and Indexation

- Verify `robots.txt` allows important sections.
- Verify XML sitemaps exist, are clean, and list canonical URLs only.
- Confirm `noindex` is not applied to ranking-critical pages.
- Check canonical tags for self-reference and consistency.
- Identify orphan pages and broken internal links.

## HTTP and Rendering

- Resolve 5xx and recurring 4xx errors.
- Remove redirect chains and loops.
- Enforce one canonical protocol/host variant.
- Validate server-side rendering or hydration for critical content.
- Confirm Googlebot can access JS/CSS resources.

## Duplication and Cannibalization

- Detect duplicate title/H1/body combinations.
- Consolidate near-duplicate pages with canonicals or redirects.
- Identify query cannibalization across similar URLs.

## Performance and UX Signals

- Check Core Web Vitals on mobile first.
- Reduce LCP bottlenecks (image size, critical CSS, TTFB).
- Reduce CLS (fixed dimensions, stable layout containers).
- Improve INP through JS budget and event handling hygiene.

## Structured Data

- Validate schema syntax and required fields.
- Match schema type to actual page intent.
- Remove misleading or spammy markup.

## International and Local (if relevant)

- Validate `hreflang` return links and locale consistency.
- Ensure local landing pages contain unique local signals.
- Keep NAP consistency across site and listings.
