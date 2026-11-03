from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _to_bs_code(symbol: str) -> str:
    code = str(symbol).zfill(6)
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    raise ValueError(f"Unsupported SSE/SZSE A-share symbol: {code}")


def _read_query_result(result) -> pd.DataFrame:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock query failed: {result.error_code} {result.error_msg}")
    return pd.DataFrame(rows, columns=result.fields)


def crosscheck_events(
    verified_events: pd.DataFrame,
    *,
    output_dir: str | Path,
) -> dict[str, object]:
    import baostock as bs

    required = {
        "symbol",
        "effective_date",
        "is_st_before",
        "is_st_after",
        "action",
        "pdf_sha256",
    }
    missing = sorted(required - set(verified_events.columns))
    if missing:
        raise ValueError(f"verified event file missing columns: {missing}")

    events = verified_events.copy()
    events["symbol"] = events["symbol"].astype(str).str.zfill(6)
    events["effective_date"] = pd.to_datetime(
        events["effective_date"], errors="coerce"
    ).dt.normalize()
    events["is_st_before"] = events["is_st_before"].astype(str).str.lower().isin(
        {"true", "1"}
    )
    events["is_st_after"] = events["is_st_after"].astype(str).str.lower().isin(
        {"true", "1"}
    )
    if events["effective_date"].isna().any():
        raise ValueError("verified events contain invalid effective dates")

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")

    rows: list[dict[str, object]] = []
    try:
        for event in events.itertuples(index=False):
            target = pd.Timestamp(event.effective_date).normalize()
            start = target - pd.Timedelta(days=15)
            end = target + pd.Timedelta(days=5)
            base = {
                "symbol": event.symbol,
                "effective_date": target.date().isoformat(),
                "pdf_is_st_before": bool(event.is_st_before),
                "pdf_is_st_after": bool(event.is_st_after),
                "action": event.action,
                "pdf_sha256": event.pdf_sha256,
            }
            try:
                result = bs.query_history_k_data_plus(
                    _to_bs_code(event.symbol),
                    "date,code,tradestatus,isST",
                    start_date=start.date().isoformat(),
                    end_date=end.date().isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                history = _read_query_result(result)
                if history.empty:
                    raise RuntimeError("BaoStock returned no event-window history")
                history["trade_date"] = pd.to_datetime(
                    history["date"], errors="coerce"
                ).dt.normalize()
                history["is_st"] = history["isST"].astype(str).str.strip().eq("1")
                history["trade_status"] = (
                    history["tradestatus"].astype(str).str.strip().eq("1")
                )
                history = history.sort_values("trade_date").reset_index(drop=True)

                on_date = history[history["trade_date"].eq(target)]
                before = history[history["trade_date"].lt(target)]
                if on_date.empty:
                    base.update(
                        {
                            "status": "missing_effective_date_row",
                            "baostock_is_st_before": None,
                            "baostock_is_st_after": None,
                            "match": False,
                        }
                    )
                elif before.empty:
                    base.update(
                        {
                            "status": "missing_prior_state",
                            "baostock_is_st_before": None,
                            "baostock_is_st_after": bool(on_date.iloc[-1]["is_st"]),
                            "match": False,
                        }
                    )
                else:
                    before_state = bool(before.iloc[-1]["is_st"])
                    after_state = bool(on_date.iloc[-1]["is_st"])
                    match = (
                        before_state == bool(event.is_st_before)
                        and after_state == bool(event.is_st_after)
                    )
                    base.update(
                        {
                            "status": "matched" if match else "state_mismatch",
                            "baostock_prior_date": before.iloc[-1][
                                "trade_date"
                            ].date().isoformat(),
                            "baostock_is_st_before": before_state,
                            "baostock_is_st_after": after_state,
                            "baostock_effective_trade_status": bool(
                                on_date.iloc[-1]["trade_status"]
                            ),
                            "match": match,
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - audit isolates one event
                base.update(
                    {
                        "status": f"error:{type(exc).__name__}",
                        "error": str(exc),
                        "match": False,
                    }
                )
            rows.append(base)
    finally:
        bs.logout()

    detail = pd.DataFrame(rows).sort_values(
        ["effective_date", "symbol"]
    ).reset_index(drop=True)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    detail.to_csv(output / "detail.csv", index=False)

    matched = detail["match"].astype(bool)
    comparable = ~detail["status"].str.startswith("error:", na=False)
    payload = {
        "status": "complete",
        "purpose": "cninfo_pdf_vs_baostock_st_event_crosscheck",
        "strict_point_in_time_evidence": False,
        "event_count": len(detail),
        "matched_count": int(matched.sum()),
        "match_rate": float(matched.mean()) if len(detail) else 0.0,
        "comparable_count": int(comparable.sum()),
        "status_counts": {
            str(key): int(value) for key, value in detail["status"].value_counts().items()
        },
        "cross_source_gate": (
            "pass" if len(detail) >= 10 and float(matched.mean()) >= 0.90 else "fail"
        ),
        "note": (
            "CNINFO PDF evidence and BaoStock historical state are independent reconstructed "
            "sources. A pass supports use of BaoStock as the daily state layer, but neither "
            "source becomes contemporaneous strict PIT evidence."
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.st_event_crosscheck")
    parser.add_argument("--verified-events", required=True)
    parser.add_argument("--output-dir", default="reports/historical-pit/st-event-crosscheck")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    frame = pd.read_csv(args.verified_events, dtype={"symbol": str})
    payload = crosscheck_events(frame, output_dir=args.output_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
