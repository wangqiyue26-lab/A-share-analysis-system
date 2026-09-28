from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .market import MIN_MARKET_FACTOR_ROWS, FactorDataError, compute_market_factors
from .scoring import FactorScoringConfig, score_factor_table
from .universe import check_market_eligibility


@dataclass(frozen=True)
class FactorRunResult:
    ranking: pd.DataFrame
    exclusions: pd.DataFrame


class FactorEngine:
    """Build a latest-date cross-sectional ranking from canonical daily bars."""

    def __init__(
        self,
        scoring_config: FactorScoringConfig,
        *,
        min_history: int = MIN_MARKET_FACTOR_ROWS,
        min_average_amount_20: float = 20_000_000.0,
    ) -> None:
        self.scoring_config = scoring_config
        self.min_history = min_history
        self.min_average_amount_20 = min_average_amount_20

    def run(self, bars_by_symbol: Mapping[str, pd.DataFrame]) -> FactorRunResult:
        rows: list[dict[str, object]] = []
        excluded: list[dict[str, object]] = []

        for symbol in sorted(bars_by_symbol):
            frame = bars_by_symbol[symbol]
            eligibility = check_market_eligibility(
                frame,
                min_history=self.min_history,
                min_average_amount_20=self.min_average_amount_20,
            )
            if not eligibility.eligible:
                excluded.append(
                    {
                        "symbol": str(symbol).zfill(6),
                        "reason": eligibility.reason,
                        "average_amount_20": eligibility.average_amount_20,
                    }
                )
                continue

            try:
                snapshot = compute_market_factors(frame)
            except FactorDataError as exc:
                excluded.append(
                    {
                        "symbol": str(symbol).zfill(6),
                        "reason": f"factor_error:{exc}",
                        "average_amount_20": eligibility.average_amount_20,
                    }
                )
                continue
            rows.append(snapshot)

        raw = pd.DataFrame(rows)
        if raw.empty:
            ranking = pd.DataFrame(
                columns=["symbol", "as_of", "factor_coverage", "composite_score", "rank"]
            )
        else:
            ranking = score_factor_table(raw, self.scoring_config)

        exclusions = pd.DataFrame(
            excluded,
            columns=["symbol", "reason", "average_amount_20"],
        )
        return FactorRunResult(ranking=ranking, exclusions=exclusions)
