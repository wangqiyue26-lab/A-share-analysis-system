from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare_bias(
    strict_summary: dict[str, object],
    current_summary: dict[str, object],
) -> dict[str, object]:
    if strict_summary.get("status") != "complete":
        raise ValueError("Strict PIT summary is not complete")
    if not bool(strict_summary.get("strict_point_in_time_universe")):
        raise ValueError("Strict summary does not declare point-in-time universe evidence")
    if strict_summary.get("start") != current_summary.get("start"):
        raise ValueError("Comparator start dates differ")
    if strict_summary.get("end") != current_summary.get("end"):
        raise ValueError("Comparator end dates differ")

    strict_rolling = dict(strict_summary["rolling"])
    current_rolling = dict(current_summary["rolling"])
    strict_return = float(strict_summary["total_return"])
    current_return = float(current_summary["total_return"])

    return {
        "strategy_version": strict_summary.get("strategy_version"),
        "start": strict_summary.get("start"),
        "end": strict_summary.get("end"),
        "strict_point_in_time_universe": True,
        "current_universe_comparator_is_biased": True,
        "strict_total_return": strict_return,
        "current_universe_total_return": current_return,
        "strict_minus_current_return": strict_return - current_return,
        "current_minus_strict_return": current_return - strict_return,
        "strict_max_drawdown": float(strict_summary["max_drawdown"]),
        "current_universe_max_drawdown": float(current_summary["max_drawdown"]),
        "strict_median_20d_return": float(strict_rolling["median_return"]),
        "current_universe_median_20d_return": float(current_rolling["median_return"]),
        "strict_median_20d_excess": strict_rolling.get("median_excess_return"),
        "current_universe_median_20d_excess": current_rolling.get("median_excess_return"),
        "strict_benchmark_outperformance_rate": strict_rolling.get(
            "benchmark_outperformance_rate"
        ),
        "current_universe_benchmark_outperformance_rate": current_rolling.get(
            "benchmark_outperformance_rate"
        ),
        "strict_return_excluding_best_block": strict_rolling.get(
            "return_excluding_best_block"
        ),
        "current_universe_return_excluding_best_block": current_rolling.get(
            "return_excluding_best_block"
        ),
        "interpretation": (
            "The current-universe comparator intentionally replays today's candidate universe "
            "backward. The return gap is a diagnostic of universe-selection sensitivity, not "
            "a guaranteed causal estimate of survivorship bias."
        ),
        "real_money_ready": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.pit_bias_report")
    parser.add_argument("--strict-summary", required=True)
    parser.add_argument("--current-summary", required=True)
    parser.add_argument("--output", default="reports/strict-pit-v2/bias_comparison.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    strict_summary = json.loads(Path(args.strict_summary).read_text(encoding="utf-8"))
    current_summary = json.loads(Path(args.current_summary).read_text(encoding="utf-8"))
    payload = compare_bias(strict_summary, current_summary)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
