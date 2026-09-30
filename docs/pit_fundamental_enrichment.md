# Point-in-time fundamental enrichment

Phase 4D adds a supplemental accounting layer to the validated daily market-factor pipeline.

## Why it is supplemental first

The production Top10 ranking remains driven by the existing market factors. Financial-statement data is fetched only after the ranking has been produced, then displayed on a separate dashboard page. This prevents a newly introduced accounting-data source or incomplete metric coverage from silently changing the production ranking before broader cross-sectional and historical validation is complete.

A failure in the fundamental layer therefore degrades only the supplemental page. The market ranking and main GitHub Pages deployment remain publishable.

## Point-in-time rule

Every financial fact carries both:

- `period_end`: the accounting period the number describes;
- `available_at`: the first defensible timestamp at which that version of the fact was observable.

The factor snapshot exposes only facts whose `available_at` is no later than the requested research cutoff. Revised values keep their later availability timestamp, so a currently visible restatement cannot leak backward into a historical research date.

## Current factors

The supplemental page currently displays:

- revenue year-over-year growth;
- net-profit year-over-year growth;
- net margin;
- operating cash flow / net profit;
- liabilities / assets;
- per-stock fundamental factor coverage.

Percentage growth is intentionally left missing when the comparison base is non-positive rather than inventing an unstable percentage across a loss/profit sign change.

## Daily production behavior

After the market-factor Top10 has been written, `python -m ashare_system.enrich_daily`:

1. reads `reports/production/selection/selected.csv`;
2. refreshes or reuses per-symbol PIT financial caches;
3. writes `fundamentals.csv` and `fundamental_summary.json` next to the selection outputs;
4. generates `site/fundamentals.html`;
5. adds a link from the main dashboard;
6. records fundamental success/failure state in the selection summary and production manifest.

GitHub Actions caches `data/cache/financials`. A cache is considered fresh for seven days. If refresh fails, a cache up to 30 days old may be used and is explicitly marked as a fallback. Older cached financial data is rejected.

## Failure isolation

The fundamental enrichment step is best-effort by default. Provider or parsing failures are recorded in `fundamental_summary.json` and the generated page is marked degraded. The market-factor ranking is not recomputed and is not changed.

For diagnostic runs, `--fail-on-error` converts a systemic fundamental enrichment exception into a failing process.

## Next validation gate before scoring integration

Fundamental factors should not enter the composite rank until the system has enough observations to evaluate:

- cross-sectional coverage and missingness by reporting season;
- stability of metric aliases across providers and issuers;
- restatement behavior;
- sector bias and scale effects;
- rolling, point-in-time backtest behavior with the historical security-master snapshots actually available to the system.

Only after those checks should a separate, versioned factor-weight configuration be introduced for live ranking experiments.
