# Phase 4K — Strict point-in-time universe validation

Phase 4K addresses the largest remaining credibility gap in the v2 research process: historical universe bias.

The strong Phase 4I/4J development results replay a current 60-name liquid universe backward through older market data. Even when prices and factor calculations are date-safe, that construction leaks future information about which securities survived and remained liquid.

Phase 4K does not tune the v2 strategy. It changes the evidence standard.

## Core rule

A historical date is eligible for strict research only when the repository contains evidence that was actually observed on or before that date.

The system must never:

- substitute today's security master for an older date;
- rebuild a missing candidate universe using future membership;
- silently skip a missing trading day and keep the same rebalance cadence;
- rewrite a previously locked daily candidate set.

If evidence is missing, the strict runner fails closed or remains in a collecting state.

## Permanent PIT evidence ledger

Daily production now stages two forms of evidence:

1. the exact 60-name candidate set actually used by production;
2. the observable security-master state behind that selection.

After-close evidence is persisted only when:

- the workflow is a scheduled or manual main-branch run;
- China time is at least 15:10;
- the production selection `as_of` date equals the current China calendar date.

This prevents intraday manual runs, holidays, stale market dates and delayed data from being recorded as valid close-of-day evidence.

The ledger structure is:

```text
research/pit/
├── days/
│   └── YYYY-MM-DD.json
├── candidates/
│   └── YYYY-MM-DD.csv
└── security_master/
    └── states/
        └── <content-hash>.csv
```

Candidate files are written every eligible trading day and are immutable.

Security-master states are content-addressed. The full 5k+ row state is written only when the relevant membership/status metadata changes. Daily manifests reference the correct state hash. This keeps the Git history auditable without duplicating the full master every day.

## Provenance checks

Before a staged bundle can enter the permanent ledger, Phase 4K verifies:

- the security-master `observed_at` exactly matches the master timestamp recorded by the universe run;
- every candidate symbol exists in that master;
- exchange, board, listing date, ST status and listed status agree;
- candidate and master checksums match the staged manifest;
- an existing day cannot be replaced with different bytes.

The evidence ledger therefore records what the system actually knew, not a reconstruction created later.

## Strict walk-forward runner

`python -m ashare_system.strict_pit_v2` reads only the immutable PIT ledger.

For each trading date it:

1. requires an exact daily candidate file;
2. restricts factor ranking to those observed candidates;
3. uses only price history at or before that date;
4. applies the frozen capital-aware-v2 portfolio rules;
5. executes no earlier than the next trading day;
6. uses the user's actual stock and ETF cost schedules;
7. evaluates 20-trading-day blocks against CSI 300.

The benchmark calendar is also used as an integrity check. If one trading date inside the observed ledger span is missing candidate evidence, the strict run returns `evidence_gap` instead of shifting the rebalance calendar.

## Collection threshold

A 20-day return block requires at least 21 daily equity observations.

Before 21 continuous eligible trading dates exist, the strict workflow reports:

```json
{
  "status": "collecting",
  "strict_point_in_time_universe": true,
  "remaining_dates": "..."
}
```

This is intentional. Historical development results are not substituted while strict evidence accumulates.

## Bias comparison

Once a complete strict block exists, the workflow runs two strategies over exactly the same dates:

- strict PIT: daily candidate sets that were actually observed;
- current-universe comparator: today's 60-name universe replayed backward.

`pit_bias_report.py` then compares:

- cumulative return;
- maximum drawdown;
- median 20-day return;
- median 20-day excess return;
- CSI 300 outperformance frequency;
- return excluding the best block.

The return difference is reported as universe-selection sensitivity. It is not claimed to be a perfect causal estimate of survivorship bias.

## Evidence quality versus PIT correctness

A date can be point-in-time correct while using a degraded production source, such as a validated recent candidate cache.

Such dates remain visible in `degraded_evidence_dates`. They are not silently treated as pristine live-source observations.

## Historical boundary

Phase 4K intentionally does not fabricate strict evidence for 2023–2026 dates that predate retained observations.

Historical exchange/event archives may later be used to reconstruct additional dates only when the reconstruction can be supported by genuine contemporaneous evidence. Any date that cannot be proven remains outside the strict sample.

This means the first indisputable strict sample grows forward from the permanent ledger.

## Research hierarchy

The project now uses four evidence levels:

1. current-universe development backtest — fast hypothesis rejection;
2. robustness / temporal tests — fragility checks;
3. strict PIT walk-forward — no future universe substitution;
4. CNY 20k forward shadow portfolio — independent live-time validation.

No single layer can set `real_money_ready=true`.

## Strategy freeze

Phase 4K keeps `capital-aware-v2` frozen.

Factor weights, Top5 entry, Top10 keep buffer, 5-day rebalance, minimum hold, ETF sleeve and transaction-cost rules are not modified in response to the strict results. A later strategy change must receive a new version and restart the relevant validation chain.
