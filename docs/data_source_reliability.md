# Data-source reliability policy

The daily research job runs after the A-share close on GitHub-hosted cloud runners. Public market-wide quote endpoints can reject or stall requests from shared cloud IPs, so production must not depend on one anonymous all-market endpoint.

## Observed failure mode

The Eastmoney all-A-share endpoint has repeatedly returned `RemoteDisconnected` from GitHub-hosted runners. Sina's all-A-share endpoint is independent and often succeeds when Eastmoney fails, but it can also time out on a different runner. Individual daily-bar endpoints may remain healthy even when both all-market snapshots are unavailable.

This means all-market spot availability is a health signal, not a sufficient definition of production health.

## Universe source hierarchy

The production universe router uses the following layers in order:

1. **Sina all-A live snapshot** — first-class live source.
2. **Eastmoney all-A live snapshot** — independent first-class live source.
3. **Recent verified candidate cache** — at most three days old and revalidated against the currently observable security master for exchange, listing status, ST status, minimum listing age, and minimum traded amount.
4. **Security master + recent per-symbol traded amount** — rebuild the bounded candidate list from canonical membership and recent daily bars when both market-wide live sources are unavailable.
5. **Legacy disaster bootstrap** — retained only as a final workflow-level availability path and always labeled degraded.

Using Sina does not by itself mean degraded mode. Cache/history layers do.

## Live snapshot quality gate

A live all-market payload is accepted only when it covers at least 80% of currently listed SSE/SZSE symbols in the observable security master. This prevents partial responses from silently becoming a valid candidate universe.

The candidate screen still applies:

- allowed exchange filtering;
- listed status;
- ST exclusion;
- minimum listing age;
- minimum traded amount;
- deterministic ranking by traded amount.

## Cache policy

The candidate cache is not trusted solely because it is recent. On every fallback use, cached symbols are revalidated against the current observable security master. If fewer than the requested candidate count remain eligible, the system rejects the cache and rebuilds from recent per-symbol history instead.

A successful live or history-liquidity build refreshes the cache. A cache fallback does not refresh its own age.

## CI versus production health

CI records whether the anonymous Sina/Eastmoney all-market endpoints are currently reachable, but a simultaneous outage is treated as provider-health telemetry rather than a code failure. Unit tests validate deterministic failover behavior. The scheduled production workflow is the end-to-end test of the full live/cache/history chain.

## Higher-reliability authenticated source

Because the scheduled job is post-close, the system does not fundamentally require an intraday snapshot. An authenticated batch end-of-day provider is a better long-term primary source than anonymous web quote endpoints.

Tushare Pro is a suitable optional candidate: its A-share `daily` interface supports querying by trading date and returns up to 6,000 rows per request, which is sufficient for a full-market post-close cross-section. It requires a user token/permissions, so it should be integrated only as an optional source backed by a GitHub Actions secret and validated independently before becoming authoritative.

The intended future hierarchy is therefore:

`authenticated post-close batch source -> Sina live -> Eastmoney live -> verified cache -> recent per-symbol history`

No provider should be promoted solely because it returned data once; coverage, schema, date, and failure behavior must be validated first.
