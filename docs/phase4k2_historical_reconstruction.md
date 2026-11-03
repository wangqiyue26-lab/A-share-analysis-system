# Phase 4K-2 — Historical PIT reconstruction

Phase 4K creates indisputable point-in-time evidence going forward. Phase 4K-2 tries to extend the research horizon backward without weakening that evidence standard.

The central rule is:

> Historical reconstruction is allowed; fabricated contemporaneous evidence is not.

A fact retrieved today from an official historical record is useful reconstructed evidence, but it is not identical to a file that was actually observed and locked on the historical date.

## Evidence grades

Phase 4K-2 uses explicit evidence grades.

### 1. strict_live_observed

Evidence was actually observed by the production system on that trading date and locked into `research/pit/`.

This is the highest grade and is eligible for strict PIT validation.

### 2. official_reconstructed_membership

Listing and delisting membership is reconstructed later from official SSE/SZSE historical records.

This can establish whether a security was plausibly listed on a date, but it does not by itself establish complete historical ST status, liquidity ranking or the exact daily candidate universe.

This grade is **not strict PIT**.

### 3. disclosure_metadata_candidate_event

Historical disclosure metadata identifies a likely risk-warning event such as:

- implementation of ST / *ST;
- removal of a risk warning;
- delisting-related disclosure.

The announcement timestamp is preserved, but it is **not automatically converted into the effective status date**.

This is not strict PIT evidence.

### 4. verified_effective_status_event

Future Phase 4K-2 work may upgrade a candidate event only after an official announcement body or exchange event record proves the effective date of the status change.

The record must preserve:

- symbol;
- old status;
- new status;
- effective trading date;
- source URL;
- source publication time;
- retrieval time;
- source hash or immutable identifier.

### 5. reconstructed_candidate_day

A historical daily candidate set may be reconstructed only when all required ingredients are available:

- historical listed membership;
- historical ST/*ST state;
- historical daily transaction amount for the full eligible universe;
- v2 universe eligibility rules;
- historical trading calendar.

Even then it remains reconstructed evidence unless it can be cross-checked against contemporaneous retained observations.

## Current official sources

The first Phase 4K-2 inventory uses free sources exposed through AKShare but whose upstream data is exchange / disclosure oriented:

- SSE current A-share list;
- SSE delisted-company list;
- SZSE current A-share list;
- SZSE terminated-listing list;
- SZSE abbreviation-change history;
- CNINFO information-disclosure search for risk-warning / special-treatment announcements.

Raw source tables are saved in workflow artifacts together with checksums.

## Lifecycle reconstruction

`historical_pit_reconstruction.py` builds:

```text
reports/historical-pit/lifecycle/
├── lifecycle.csv
├── membership_calendar.csv
├── szse_name_changes.csv
├── raw/
│   ├── current_sse_szse.csv
│   ├── sse_delisted.csv
│   ├── szse_delisted.csv
│   └── szse_name_changes_raw.csv
└── summary.json
```

A security is considered part of the reconstructed listed membership when:

```text
list_date <= date < delist_date
```

For currently listed securities, `delist_date` is null.

The delisting boundary is deliberately exclusive. If an exchange source says the security's termination date is day D, Phase 4K-2 does not treat the security as a normal listed member on D without stronger evidence.

## Historical ST evidence

SZSE publishes abbreviation-change history. Phase 4K-2 extracts transitions where the old/new abbreviation crosses an ST or *ST boundary.

The parser stores:

- change date;
- old abbreviation;
- new abbreviation;
- ST state before;
- ST state after.

No transition is invented when the source does not explicitly provide old/new abbreviations.

SSE historical ST coverage is currently a known blocker. Current risk-warning lists are insufficient to prove historical state. Phase 4K-2 therefore does not claim strict candidate reconstruction yet.

## CNINFO risk-warning announcement inventory

`cninfo_risk_inventory.py` scans historical disclosure metadata by month and category.

It classifies announcement titles into conservative candidate classes:

- `risk_warning_apply_candidate`;
- `risk_warning_remove_candidate`;
- `delisting_related`;
- `other_risk_disclosure`.

The word **candidate** is intentional.

For example, an announcement published on day T does not necessarily mean the ST status became effective on day T. Exchange rules can involve suspension and an effective date on a later trading day.

Therefore:

```text
announcement_time != effective_status_date
```

unless the announcement body or exchange event record proves otherwise.

## Why the scan is sharded by year

A full-market disclosure scan is network-sensitive. The workflow splits 2023, 2024, 2025 and 2026 into separate jobs and monthly request windows.

This reduces:

- one-job timeout risk;
- source-rate-limit blast radius;
- the chance that one bad month destroys the full inventory.

## Historical amount reconstruction

This is the second large blocker.

The v2 production universe is liquidity screened. Reconstructing the historical Top60 requires historical daily transaction amount for every security that was eligible on that date, not only for securities that survive today.

Phase 4K-2 deliberately does **not** start a strict backtest until this cross-sectional amount layer exists.

Preferred order of work:

1. locate a bulk historical cross-sectional source;
2. if unavailable, build a sharded per-symbol history archive;
3. cache raw histories permanently enough to avoid repeated network load;
4. reconstruct daily liquidity ranking only after membership/ST coverage is adequate.

## Cross-check against live PIT evidence

As the Phase 4K forward ledger grows, reconstructed historical logic will be tested against dates for which we possess true contemporaneous evidence.

For an overlapping date:

- reconstructed listed membership should contain the observed candidate symbols;
- reconstructed ST state should match observed `is_st`;
- reconstructed transaction-amount ranking should reproduce the observed Top60 within defined source tolerances.

A reconstruction method that cannot reproduce retained live evidence cannot be promoted to a stricter evidence grade.

## Automation

`.github/workflows/historical-pit-reconstruction.yml` runs:

- official lifecycle inventory;
- annual sharded CNINFO risk-announcement inventory;
- evidence-grade assertions;
- 90-day research artifacts.

The workflow does not mutate `research/pit/`.

Only the live Phase 4K production process may create `strict_live_observed` evidence.

## Promotion gate to Phase 4K-2B

Historical candidate reconstruction should not start strict backtesting until all of the following are true:

- lifecycle listing-date coverage is complete;
- delisting-date coverage is complete for known delisted securities;
- SSE and SZSE historical ST effective-date coverage is sufficient;
- historical daily amount is available across the eligible universe;
- reconstructed dates pass consistency checks;
- overlapping reconstructed dates reproduce retained live PIT evidence.

Until then:

`strict_candidate_reconstruction_ready = false`.
