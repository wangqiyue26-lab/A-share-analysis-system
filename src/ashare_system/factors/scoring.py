from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_system.config import load_yaml


@dataclass(frozen=True)
class FactorSpec:
    name: str
    weight: float
    direction: str

    @property
    def sign(self) -> float:
        if self.direction == "higher":
            return 1.0
        if self.direction == "lower":
            return -1.0
        raise ValueError(f"Unsupported factor direction for {self.name}: {self.direction}")


@dataclass(frozen=True)
class FactorScoringConfig:
    specs: tuple[FactorSpec, ...]
    winsorize_lower: float = 0.05
    winsorize_upper: float = 0.95
    min_factor_coverage: float = 1.0


def load_factor_config(path: str | Path = "config/factors.yml") -> FactorScoringConfig:
    payload = load_yaml(path)
    factor_payload = payload.get("factors")
    if not isinstance(factor_payload, dict) or not factor_payload:
        raise ValueError("factors.yml must define a non-empty 'factors' mapping")

    specs: list[FactorSpec] = []
    for name, settings in factor_payload.items():
        if not isinstance(settings, dict):
            raise TypeError(f"Factor settings must be a mapping: {name}")
        specs.append(
            FactorSpec(
                name=str(name),
                weight=float(settings["weight"]),
                direction=str(settings["direction"]),
            )
        )

    if any(spec.weight <= 0 for spec in specs):
        raise ValueError("Factor weights must be positive")
    if any(spec.direction not in {"higher", "lower"} for spec in specs):
        raise ValueError("Factor directions must be either 'higher' or 'lower'")

    scoring = payload.get("scoring", {})
    lower = float(scoring.get("winsorize_lower", 0.05))
    upper = float(scoring.get("winsorize_upper", 0.95))
    coverage = float(scoring.get("min_factor_coverage", 1.0))
    if not 0.0 <= lower < upper <= 1.0:
        raise ValueError("Winsorization bounds must satisfy 0 <= lower < upper <= 1")
    if not 0.0 < coverage <= 1.0:
        raise ValueError("min_factor_coverage must be in (0, 1]")

    return FactorScoringConfig(
        specs=tuple(specs),
        winsorize_lower=lower,
        winsorize_upper=upper,
        min_factor_coverage=coverage,
    )


def _winsorized_zscore(series: pd.Series, lower: float, upper: float) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    valid = numeric.dropna()
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    if valid.empty:
        return result

    low_value = valid.quantile(lower)
    high_value = valid.quantile(upper)
    clipped = numeric.clip(lower=low_value, upper=high_value)
    mean = clipped.mean(skipna=True)
    std = clipped.std(skipna=True, ddof=0)
    if not np.isfinite(std) or std == 0:
        result.loc[clipped.notna()] = 0.0
        return result
    return (clipped - mean) / std


def score_factor_table(raw: pd.DataFrame, config: FactorScoringConfig) -> pd.DataFrame:
    """Cross-sectionally standardize factor values and build an explainable composite score."""
    if "symbol" not in raw.columns:
        raise ValueError("Factor table must contain a symbol column")
    missing = [spec.name for spec in config.specs if spec.name not in raw.columns]
    if missing:
        raise ValueError(f"Factor table is missing configured factors: {missing}")
    if raw["symbol"].duplicated().any():
        raise ValueError("Factor table contains duplicate symbols")

    result = raw.copy()
    total_weight = sum(spec.weight for spec in config.specs)
    weighted_score = pd.Series(0.0, index=result.index, dtype=float)
    available_weight = pd.Series(0.0, index=result.index, dtype=float)

    for spec in config.specs:
        standardized = _winsorized_zscore(
            result[spec.name],
            config.winsorize_lower,
            config.winsorize_upper,
        )
        contribution = standardized * spec.sign
        result[f"score_{spec.name}"] = contribution
        valid = contribution.notna()
        weighted_score = weighted_score + contribution.fillna(0.0) * spec.weight
        available_weight = available_weight + valid.astype(float) * spec.weight

    result["factor_coverage"] = available_weight / total_weight
    result["composite_score"] = weighted_score / available_weight.replace(0.0, np.nan)
    result.loc[result["factor_coverage"] < config.min_factor_coverage, "composite_score"] = np.nan

    scorable = result["composite_score"].notna()
    result["rank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result.loc[scorable, "rank"] = (
        result.loc[scorable, "composite_score"].rank(method="min", ascending=False).astype("Int64")
    )

    count = int(scorable.sum())
    result["score_percentile"] = np.nan
    if count == 1:
        result.loc[scorable, "score_percentile"] = 100.0
    elif count > 1:
        ranks = result.loc[scorable, "rank"].astype(float)
        result.loc[scorable, "score_percentile"] = (count - ranks) / (count - 1) * 100.0

    return result.sort_values(
        ["composite_score", "symbol"],
        ascending=[False, True],
        na_position="last",
    ).reset_index(drop=True)
