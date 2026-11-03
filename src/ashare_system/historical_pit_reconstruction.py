from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from .data.benchmark import AkshareBenchmarkProvider

OFFICIAL_SOURCES = {
    "sse_current": "https://www.sse.com.cn/assortment/stock/home/",
    "sse_delisted": "https://www.sse.com.cn/assortment/stock/list/delisting/",
    "szse_current": "https://www.szse.cn/market/product/stock/list/index.html",
    "szse_delisted": "https://www.szse.cn/market/stock/suspend/index.html",
    "szse_name_changes": "https://www.szse.cn/www/market/stock/changename/index.html",
    "cninfo_disclosure": (
        "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
    ),
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lookup = {str(column).strip(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


def _column_containing(frame: pd.DataFrame, needles: tuple[str, ...]) -> str | None:
    for column in frame.columns:
        text = str(column)
        if all(needle in text for needle in needles):
            return str(column)
    return None


def _normalize_symbol(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(r"(\d{6})", expand=False).str.zfill(6)


def _infer_board(symbol: str, exchange: str) -> str:
    code = str(symbol).zfill(6)
    if exchange == "SSE":
        return "科创板" if code.startswith("688") else "上证主板"
    if exchange == "SZSE":
        return "创业板" if code.startswith(("300", "301")) else "深证主板"
    raise ValueError(f"Unsupported exchange: {exchange}")


def _contains_st(name: object) -> bool:
    if pd.isna(name):
        return False
    return "ST" in str(name).upper()


def _safe_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.normalize()


@dataclass(frozen=True)
class HistoricalSourceBundle:
    observed_at: str
    current: pd.DataFrame
    sse_delisted: pd.DataFrame
    szse_delisted: pd.DataFrame
    szse_name_changes: pd.DataFrame
    warnings: tuple[str, ...]


class AkshareHistoricalPITSources:
    """Free exchange-oriented historical evidence for lifecycle reconstruction.

    These calls retrieve historical facts today. They are useful for reconstructing
    listing membership and known name/ST transitions, but they are not contemporaneous
    observations and therefore cannot by themselves create strict PIT evidence.
    """

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _sse_current(self) -> pd.DataFrame:
        import akshare as ak

        frames = [
            ak.stock_info_sh_name_code(symbol="主板A股"),
            ak.stock_info_sh_name_code(symbol="科创板"),
        ]
        return pd.concat(frames, ignore_index=True)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _szse_current(self) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_info_sz_name_code(symbol="A股列表")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _sse_delisted(self) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_info_sh_delist(symbol="全部")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _szse_delisted(self) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_info_sz_delist(symbol="终止上市公司")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _szse_name_changes(self) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_info_sz_change_name(symbol="简称变更")

    def fetch(self) -> HistoricalSourceBundle:
        observed_at = pd.Timestamp.now(tz="UTC").isoformat()
        warnings: list[str] = []
        required: dict[str, Callable[[], pd.DataFrame]] = {
            "sse_current": self._sse_current,
            "szse_current": self._szse_current,
            "sse_delisted": self._sse_delisted,
            "szse_delisted": self._szse_delisted,
        }
        results: dict[str, pd.DataFrame] = {}
        for name, fetcher in required.items():
            frame = fetcher()
            if frame is None or frame.empty:
                raise RuntimeError(f"Required official historical source returned no data: {name}")
            results[name] = frame.copy()

        try:
            changes = self._szse_name_changes()
            if changes is None or changes.empty:
                warnings.append("SZSE abbreviation-change source returned no rows")
                changes = pd.DataFrame()
        except Exception as exc:  # noqa: BLE001 - optional evidence layer
            warnings.append(
                f"SZSE abbreviation-change source unavailable: {type(exc).__name__}: {exc}"
            )
            changes = pd.DataFrame()

        return HistoricalSourceBundle(
            observed_at=observed_at,
            current=pd.concat(
                [
                    _normalize_sse_current(results["sse_current"]),
                    _normalize_szse_current(results["szse_current"]),
                ],
                ignore_index=True,
            ),
            sse_delisted=results["sse_delisted"],
            szse_delisted=results["szse_delisted"],
            szse_name_changes=changes,
            warnings=tuple(warnings),
        )


def _normalize_sse_current(frame: pd.DataFrame) -> pd.DataFrame:
    code_col = _first_column(frame, ("证券代码", "A股代码"))
    name_col = _first_column(frame, ("证券简称", "A股简称"))
    date_col = _first_column(frame, ("上市日期", "A股上市日期"))
    if not code_col or not name_col or not date_col:
        raise ValueError(f"Unexpected SSE current-list columns: {list(frame.columns)}")
    result = pd.DataFrame(
        {
            "symbol": _normalize_symbol(frame[code_col]),
            "name": frame[name_col].astype(str),
            "exchange": "SSE",
            "list_date": _safe_date(frame[date_col]),
        }
    )
    result["board"] = result["symbol"].map(lambda value: _infer_board(value, "SSE"))
    result["currently_listed"] = True
    return result.dropna(subset=["symbol", "list_date"]).drop_duplicates("symbol")


def _normalize_szse_current(frame: pd.DataFrame) -> pd.DataFrame:
    code_col = _first_column(frame, ("A股代码", "证券代码"))
    name_col = _first_column(frame, ("A股简称", "证券简称"))
    date_col = _first_column(frame, ("A股上市日期", "上市日期"))
    board_col = _first_column(frame, ("板块",))
    if not code_col or not name_col or not date_col:
        raise ValueError(f"Unexpected SZSE current-list columns: {list(frame.columns)}")
    result = pd.DataFrame(
        {
            "symbol": _normalize_symbol(frame[code_col]),
            "name": frame[name_col].astype(str),
            "exchange": "SZSE",
            "list_date": _safe_date(frame[date_col]),
        }
    )
    if board_col:
        result["board"] = frame[board_col].astype(str)
    else:
        result["board"] = result["symbol"].map(lambda value: _infer_board(value, "SZSE"))
    result["currently_listed"] = True
    return result.dropna(subset=["symbol", "list_date"]).drop_duplicates("symbol")


def _normalize_delisted(frame: pd.DataFrame, exchange: str) -> pd.DataFrame:
    code_col = _first_column(frame, ("公司代码", "证券代码", "A股代码"))
    name_col = _first_column(frame, ("公司简称", "证券简称", "A股简称"))
    list_col = _first_column(frame, ("上市日期", "A股上市日期"))
    delist_col = _first_column(
        frame,
        ("终止上市日期", "暂停上市日期", "摘牌日期", "退市日期"),
    )
    if not code_col or not name_col or not list_col or not delist_col:
        raise ValueError(
            f"Unexpected {exchange} delisted columns: {list(frame.columns)}"
        )
    result = pd.DataFrame(
        {
            "symbol": _normalize_symbol(frame[code_col]),
            "name": frame[name_col].astype(str),
            "exchange": exchange,
            "list_date": _safe_date(frame[list_col]),
            "delist_date": _safe_date(frame[delist_col]),
        }
    )
    result["board"] = result["symbol"].map(lambda value: _infer_board(value, exchange))
    result["currently_listed"] = False
    return (
        result.dropna(subset=["symbol", "list_date", "delist_date"])
        .drop_duplicates("symbol", keep="last")
        .reset_index(drop=True)
    )


def build_lifecycle(bundle: HistoricalSourceBundle) -> pd.DataFrame:
    current = bundle.current.copy()
    current["delist_date"] = pd.NaT

    historical = pd.concat(
        [
            _normalize_delisted(bundle.sse_delisted, "SSE"),
            _normalize_delisted(bundle.szse_delisted, "SZSE"),
        ],
        ignore_index=True,
    )

    overlaps = sorted(set(current["symbol"]) & set(historical["symbol"]))
    if overlaps:
        raise RuntimeError(
            "Official current and delisted lists overlap; lifecycle is ambiguous: "
            f"{overlaps[:10]}"
        )

    result = pd.concat(
        [
            current.loc[
                :,
                [
                    "symbol",
                    "name",
                    "exchange",
                    "board",
                    "list_date",
                    "delist_date",
                    "currently_listed",
                ],
            ],
            historical.loc[
                :,
                [
                    "symbol",
                    "name",
                    "exchange",
                    "board",
                    "list_date",
                    "delist_date",
                    "currently_listed",
                ],
            ],
        ],
        ignore_index=True,
    )
    result["membership_source"] = result["exchange"].map(
        {
            "SSE": "official_exchange_current_plus_delisted",
            "SZSE": "official_exchange_current_plus_delisted",
        }
    )
    result["reconstruction_grade"] = "official_reconstructed_membership"
    if result["symbol"].duplicated().any():
        duplicates = sorted(result.loc[result["symbol"].duplicated(False), "symbol"].unique())
        raise RuntimeError(f"Duplicate lifecycle symbols: {duplicates[:10]}")
    return result.sort_values(["exchange", "symbol"]).reset_index(drop=True)


def normalize_szse_name_changes(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    columns = [
        "symbol",
        "change_date",
        "old_name",
        "new_name",
        "is_st_before",
        "is_st_after",
        "st_transition",
        "source",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns), ["No SZSE name-change evidence available"]

    code_col = _first_column(frame, ("证券代码", "A股代码"))
    date_col = _first_column(frame, ("变更日期",))
    old_col = (
        _first_column(frame, ("变更前简称", "原证券简称", "原简称"))
        or _column_containing(frame, ("变更前", "简称"))
    )
    new_col = (
        _first_column(frame, ("变更后简称", "新证券简称", "新简称", "证券简称"))
        or _column_containing(frame, ("变更后", "简称"))
    )
    if not code_col or not date_col:
        return pd.DataFrame(columns=columns), [
            f"Cannot parse SZSE name-change keys from columns={list(frame.columns)}"
        ]
    if not old_col or not new_col:
        return pd.DataFrame(columns=columns), [
            (
                "SZSE name-change table lacks explicit old/new abbreviation columns; "
                f"columns={list(frame.columns)}"
            )
        ]

    result = pd.DataFrame(
        {
            "symbol": _normalize_symbol(frame[code_col]),
            "change_date": _safe_date(frame[date_col]),
            "old_name": frame[old_col].astype(str),
            "new_name": frame[new_col].astype(str),
        }
    ).dropna(subset=["symbol", "change_date"])
    result["is_st_before"] = result["old_name"].map(_contains_st)
    result["is_st_after"] = result["new_name"].map(_contains_st)
    result["st_transition"] = result["is_st_before"] != result["is_st_after"]
    result["source"] = "szse_official_abbreviation_change"
    return result.sort_values(["change_date", "symbol"]).reset_index(drop=True), warnings


def membership_as_of(lifecycle: pd.DataFrame, as_of: str | pd.Timestamp) -> pd.DataFrame:
    date = pd.Timestamp(as_of).normalize()
    listed = lifecycle["list_date"].le(date)
    not_delisted = lifecycle["delist_date"].isna() | lifecycle["delist_date"].gt(date)
    return lifecycle[listed & not_delisted].copy().reset_index(drop=True)


def build_membership_calendar(
    lifecycle: pd.DataFrame,
    *,
    start: str,
    end: str,
) -> pd.DataFrame:
    benchmark = AkshareBenchmarkProvider().get_daily(
        "000300",
        pd.Timestamp(start).strftime("%Y%m%d"),
        pd.Timestamp(end).strftime("%Y%m%d"),
    )
    rows: list[dict[str, object]] = []
    for value in benchmark["trade_date"]:
        date = pd.Timestamp(value).normalize()
        frame = membership_as_of(lifecycle, date)
        counts = frame.groupby("exchange").size().to_dict()
        rows.append(
            {
                "trade_date": date,
                "listed_total": len(frame),
                "sse_listed": int(counts.get("SSE", 0)),
                "szse_listed": int(counts.get("SZSE", 0)),
            }
        )
    return pd.DataFrame(rows)


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, date_format="%Y-%m-%d")


def run_inventory(
    *,
    output_dir: str | Path,
    start: str = "2023-01-03",
    end: str | None = None,
) -> dict[str, object]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    end_value = end or pd.Timestamp.now(tz="Asia/Shanghai").date().isoformat()

    bundle = AkshareHistoricalPITSources().fetch()
    lifecycle = build_lifecycle(bundle)
    sz_changes, change_warnings = normalize_szse_name_changes(bundle.szse_name_changes)
    calendar = build_membership_calendar(lifecycle, start=start, end=end_value)

    _write_frame(output / "lifecycle.csv", lifecycle)
    _write_frame(output / "szse_name_changes.csv", sz_changes)
    _write_frame(output / "membership_calendar.csv", calendar)

    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _write_frame(raw_dir / "sse_delisted.csv", bundle.sse_delisted)
    _write_frame(raw_dir / "szse_delisted.csv", bundle.szse_delisted)
    _write_frame(raw_dir / "szse_name_changes_raw.csv", bundle.szse_name_changes)

    current = bundle.current.copy()
    _write_frame(raw_dir / "current_sse_szse.csv", current)

    current_counts = current.groupby("exchange").size().to_dict()
    lifecycle_counts = lifecycle.groupby("exchange").size().to_dict()
    delisted = lifecycle[~lifecycle["currently_listed"]]
    delisted_counts = delisted.groupby("exchange").size().to_dict()
    st_transitions = sz_changes[sz_changes["st_transition"]].copy()

    source_files = {
        path.relative_to(output).as_posix(): _sha256_file(path)
        for path in sorted(raw_dir.glob("*.csv"))
    }
    warnings = list(bundle.warnings) + change_warnings
    summary = {
        "status": "inventory_complete",
        "observed_at": bundle.observed_at,
        "period_start": pd.Timestamp(start).date().isoformat(),
        "period_end": pd.Timestamp(end_value).date().isoformat(),
        "strict_point_in_time_universe": False,
        "reconstruction_grade": "official_reconstructed_membership_not_strict",
        "official_sources": OFFICIAL_SOURCES,
        "current_counts": {
            "SSE": int(current_counts.get("SSE", 0)),
            "SZSE": int(current_counts.get("SZSE", 0)),
        },
        "lifecycle_symbol_counts": {
            "SSE": int(lifecycle_counts.get("SSE", 0)),
            "SZSE": int(lifecycle_counts.get("SZSE", 0)),
            "total": len(lifecycle),
        },
        "delisted_counts": {
            "SSE": int(delisted_counts.get("SSE", 0)),
            "SZSE": int(delisted_counts.get("SZSE", 0)),
            "total": len(delisted),
        },
        "listing_date_coverage": float(lifecycle["list_date"].notna().mean()),
        "delist_date_coverage_for_delisted": (
            float(delisted["delist_date"].notna().mean()) if len(delisted) else 1.0
        ),
        "trading_dates_inventory": len(calendar),
        "szse_name_change_rows": len(sz_changes),
        "szse_st_transition_rows": len(st_transitions),
        "st_history": {
            "SZSE": (
                "official_abbreviation_change_transitions"
                if len(sz_changes)
                else "unavailable"
            ),
            "SSE": "historical_transition_source_not_yet_ingested",
        },
        "historical_amount_cross_section": "not_yet_reconstructed",
        "strict_candidate_reconstruction_ready": False,
        "blocking_gaps": [
            "SSE historical ST/*ST effective-date transitions",
            "historical daily amount cross-section for all eligible SSE/SZSE securities",
            "cross-check reconstructed membership against retained contemporaneous PIT days",
        ],
        "warnings": warnings,
        "raw_source_sha256": source_files,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.historical_pit_reconstruction")
    parser.add_argument("--output-dir", default="reports/historical-pit")
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_inventory(
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
