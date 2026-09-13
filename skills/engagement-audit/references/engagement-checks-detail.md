# Engagement Checks — Executable Reference

This document describes the behavior implemented by `scripts/engagement_extractor.py`
and `scripts/engagement_validator.py`. Tunable lists and thresholds live in
`references/engagement-config.json`.

## Content gate

A page is eligible only when all of these are true:

1. HTTP status is 2xx.
2. Content type is HTML/XHTML when supplied.
3. The response body is non-empty.
4. The response is not a probable access-denied, CAPTCHA, or bot-challenge interstitial.
5. Extraction completed and set `html_success: true`.

Fetch errors, blocked responses, empty bodies, and non-HTML resources remain in raw
coverage with `fetch_status`, but no engagement issue is inferred from them. If every
page is ineligible, `EngagementValidator.run_all()` returns `[]`.

## EG-01 — Navigation

- Reports a missing primary navigation structure on standard pages only when neither
  a semantic/inferred menu nor header navigation links were extracted.
- Separately reports menus inferred from classes/header links that lack a `<nav>` or
  `role="navigation"` landmark.
- With at least three menus containing two labels, reports substantial template
  inconsistency only when at least two pages have under 35% label-set overlap with the
  sampled baseline.
- On pages with explicit commercial profile evidence, checks for any relevant
  product/service **or** contact/support route. It never requires pricing, about,
  products, or contact as a fixed universal menu.
- News, documentation, and general sites are not judged against commercial navigation.

## EG-02 — Orientation

- Reports zero non-empty H1 elements (`medium`). More than two H1s is a low-severity
  hierarchy review, not an invalidity claim.
- On the homepage and commercial-profile landing pages, an H1 with no paragraph/H2 of
  at least 20 characters near the start of `<main>` is measurable missing intro copy.
- Breadcrumbs are expected only at path depth ≥2 for documentation/commercial pages,
  or depth ≥3 for other hierarchical pages. Editorial/date permalinks and shallow
  pages are suppressed.
- Visible breadcrumb selectors, microdata, and JSON-LD `BreadcrumbList` count as evidence.

## EG-03 — Structural performance risks

These are static architecture observations, not measured FCP/LCP/CLS scores:

- More than 3 external `<head>` scripts without `async`, `defer`, or `type="module"`.
- More than 4 external stylesheets in `<head>`.
- More than 10 referenced third-party resource hosts.
- At least two images and at least 50% of images missing both intrinsic dimensions.
- At least three images after the first two document images lacking `loading="lazy"`.
  Document order is explicitly described as a static proxy, so the recommendation asks
  the reviewer to verify actual viewport placement.

## EG-04 — Viewport and zoom

- Missing/non-empty viewport metadata: `critical`.
- Viewport present without exact `width=device-width`: `high`.
- `user-scalable=no`, `user-scalable=0`, or numeric `maximum-scale < 2`: `high`.
- `maximum-scale=10` is not mistaken for `maximum-scale=1`.

## EG-05 — CTA clarity

CTA controls are measured per DOM element; repeated generic labels therefore remain
measurable evidence. Action-density uses distinct normalized high-intent labels. Evidence
includes button controls, role buttons, styled action links, and recognized action/generic text.

- Zero controls is reported only for commercial-profile pages (`high`).
- Generic labels are reported only on non-editorial pages when at least three generic
  labels represent at least 60% of measured controls (`low`). This suppresses normal
  “Read more” repetition on news/blog indexes.
- More than the configured number (default 3) of distinct recognized high-intent actions
  is a low-severity competing-action observation.

## EG-06 — Site search

Detection covers search inputs, search forms, search landmarks, labelled triggers, and
search-related icon classes. Icon-only triggers with no text, `aria-label`, or `title`
are reported separately.

Search absence is eligible only when:

- at least five standard successful pages were sampled; or
- at least three successful pages were sampled and at least three have documentation
  or editorial profile evidence.

Utility paths and small samples are suppressed.

## EG-07 — Context retention

Evidence includes recently viewed/history/continue-reading controls, saved/favorite/
wishlist controls, account/profile links, and explicit client storage hooks. Absence is
reported only when at least three standard pages were sampled and at least two have
commercial profile evidence. General, news, and documentation sites have no rigid
continuity requirement.

## Shared finding contract

`EngagementValidator(raw).run_all()` returns `list[dict]`, sorted by severity. Each
finding is created by `lib/report.py::make_finding` and contains the deterministic
shared `id`; the engagement skill does not add a sequential `finding_id`.

```python
session = requests.Session()  # may be the orchestrator's cached SafeSession
raw = engagement_extractor.build_raw(base_url, paths, session, max_pages)
findings = EngagementValidator(raw).run_all()
```
