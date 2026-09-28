import pandas as pd

from ashare_system.factors.scoring import (
    FactorScoringConfig,
    FactorSpec,
    load_factor_config,
    score_factor_table,
)


def test_load_factor_config():
    config = load_factor_config("config/factors.yml")
    assert len(config.specs) == 6
    assert sum(spec.weight for spec in config.specs) == 1.0
    assert config.min_factor_coverage == 1.0


def test_score_factor_table_respects_direction_and_keeps_components():
    config = FactorScoringConfig(
        specs=(
            FactorSpec("return_factor", 0.5, "higher"),
            FactorSpec("risk_factor", 0.5, "lower"),
        )
    )
    raw = pd.DataFrame(
        {
            "symbol": ["000001", "000002", "000003"],
            "return_factor": [0.30, 0.10, -0.10],
            "risk_factor": [0.10, 0.20, 0.40],
        }
    )
    ranked = score_factor_table(raw, config)
    assert ranked.iloc[0]["symbol"] == "000001"
    assert ranked.iloc[0]["rank"] == 1
    assert "score_return_factor" in ranked.columns
    assert "score_risk_factor" in ranked.columns
    assert ranked.iloc[0]["score_percentile"] == 100.0
