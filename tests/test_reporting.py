from pathlib import Path

import pandas as pd

from ashare_system.factors import FactorRunResult
from ashare_system.reporting import write_selection_outputs


def test_write_selection_outputs(tmp_path: Path):
    ranking = pd.DataFrame(
        {
            "symbol": ["000001", "600000"],
            "as_of": pd.to_datetime(["2024-06-28", "2024-06-28"]),
            "composite_score": [1.2, 0.3],
            "rank": pd.Series([1, 2], dtype="Int64"),
        }
    )
    exclusions = pd.DataFrame(columns=["symbol", "reason", "average_amount_20"])
    paths = write_selection_outputs(
        FactorRunResult(ranking=ranking, exclusions=exclusions),
        tmp_path,
        top_n=1,
    )
    assert paths["selected_csv"].exists()
    selected = pd.read_csv(paths["selected_csv"], dtype={"symbol": str})
    assert selected["symbol"].tolist() == ["000001"]
