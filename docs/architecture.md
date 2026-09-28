# Architecture notes

## Design principles

1. **Provider isolation** — strategies never call AKShare or another vendor directly.
2. **Canonical schema** — provider-specific column names are normalized immediately.
3. **Failover-ready** — `DataRouter` accepts multiple providers in priority order.
4. **Rebuildable cache** — Parquet cache improves speed but is never treated as the sole source of truth.
5. **No external network in unit tests** — provider availability must not make code correctness tests flaky.
6. **Point-in-Time later, not retrofitted** — Phase 2/3 financial-factor tables will carry availability timestamps to prevent look-ahead bias.
7. **A-share execution rules are explicit** — T+1, limits, suspensions, costs and historical rule changes belong in a dedicated Phase 3 layer.

## Phase boundaries

Phase 1 deliberately stops before factor ranking and investment signals. It proves cloud execution, data normalization, cache IO and testability first.
