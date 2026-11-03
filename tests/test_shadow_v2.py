import pandas as pd
import pytest

from ashare_system.shadow_v2 import capture_ranking, load_rankings


def _selected(symbols=("000001", "600000")):
    count = len(symbols)
    return pd.DataFrame(
        {
            "symbol": list(symbols),
            "name": [f"股票{i}" for i in range(count)],
            "as_of": ["2026-11-03"] * count,
            "board": ["主板"] * count,
            "is_st": [False] * count,
            "rank": list(range(1, count + 1)),
            "composite_score": [1.0 - i * 0.1 for i in range(count)],
            "close": [10.0 + i for i in range(count)],
            "screen_source": ["sina_spot_amount"] * count,
        }
    )


def test_v2_ranking_ledger_is_immutable(tmp_path):
    selected = tmp_path / "selected.csv"
    _selected().to_csv(selected, index=False)
    ledger = tmp_path / "ledger"

    first = capture_ranking(
        selected,
        ledger,
        observed_at=pd.Timestamp("2026-11-03T09:00:00Z"),
    )
    second = capture_ranking(
        selected,
        ledger,
        observed_at=pd.Timestamp("2026-11-03T10:00:00Z"),
    )
    assert first == second
    rankings = load_rankings(ledger)
    assert len(rankings) == 1
    assert len(next(iter(rankings.values()))) == 2

    changed = _selected(("000001", "300001"))
    changed.to_csv(selected, index=False)
    with pytest.raises(RuntimeError, match="Refusing to rewrite immutable v2 ranking"):
        capture_ranking(selected, ledger)
