# Phase 3 rule-source notes

The backtest rule layer is date-aware. Historical rules must be traceable to exchange or government notices before they are used in research.

Phase 3A encodes the following effective-date boundaries:

- Sell-side securities transaction stamp duty: seller-only regime supported from 2008-09-19; rate reduced from 0.10% to 0.05% effective 2023-08-28.
- ChiNext daily price limit: 20% from 2020-08-24; earlier supported dates use 10%.
- STAR Market daily price limit: 20%.
- Beijing Stock Exchange daily price limit: 30%.
- Main-board risk-warning shares: 10% from 2026-07-06; earlier supported dates use 5%.

The implementation intentionally separates these date rules from strategy code. When a regime is not modelled, the engine should fail or the research should exclude the affected observations instead of assuming today's rule.

This file records policy boundaries, not legal advice. Before expanding historical coverage, add the relevant official exchange/government notice and a regression test for its effective date.
