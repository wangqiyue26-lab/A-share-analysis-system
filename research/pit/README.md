# Strict PIT evidence ledger

This directory is an append-only research ledger.

Daily candidate sets and their source security-master states are persisted only after the A-share close and only when the production market date matches the current China date.

Do not manually edit historical evidence. The Phase 4K tooling verifies checksums and refuses same-date rewrites.

- `days/YYYY-MM-DD.json` — provenance and hashes for one observed trading date.
- `candidates/YYYY-MM-DD.csv` — exact 60-name production candidate set.
- `security_master/states/<hash>.csv` — deduplicated observable security-master state.

Missing dates must remain missing unless genuine contemporaneous evidence is recovered. Never fill a historical gap with a newer/current universe.
