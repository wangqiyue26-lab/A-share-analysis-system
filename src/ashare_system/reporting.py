from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ashare_system.factors import FactorRunResult


def _records_json(frame: pd.DataFrame) -> str:
    return frame.to_json(orient="records", force_ascii=False, date_format="iso", indent=2)


def write_selection_outputs(
    result: FactorRunResult,
    output_dir: str | Path,
    *,
    top_n: int = 20,
    fetch_failures: list[dict[str, str]] | None = None,
) -> dict[str, Path]:
    """Persist a reproducible ranking bundle for Actions/Pages consumers."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    ranking = result.ranking.copy()
    selected = ranking[ranking["rank"].notna()].head(top_n).copy()
    failures = fetch_failures or []

    paths = {
        "ranking_csv": output / "ranking.csv",
        "ranking_json": output / "ranking.json",
        "selected_csv": output / "selected.csv",
        "selected_json": output / "selected.json",
        "exclusions_csv": output / "exclusions.csv",
        "summary_json": output / "summary.json",
    }
    ranking.to_csv(paths["ranking_csv"], index=False)
    paths["ranking_json"].write_text(_records_json(ranking) + "\n", encoding="utf-8")
    selected.to_csv(paths["selected_csv"], index=False)
    paths["selected_json"].write_text(_records_json(selected) + "\n", encoding="utf-8")
    result.exclusions.to_csv(paths["exclusions_csv"], index=False)

    summary: dict[str, Any] = {
        "ranked_count": int(ranking["rank"].notna().sum()) if "rank" in ranking else 0,
        "selected_count": len(selected),
        "excluded_count": len(result.exclusions),
        "fetch_failure_count": len(failures),
        "fetch_failures": failures,
    }
    paths["summary_json"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return paths
