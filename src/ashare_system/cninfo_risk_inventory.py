from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

RISK_CATEGORIES = ("特别处理和退市", "风险提示")

APPLY_PATTERNS = (
    r"实施退市风险警示",
    r"实施其他风险警示",
    r"实施风险警示",
    r"被实施退市风险警示",
    r"被实施其他风险警示",
    r"股票交易被实施.*风险警示",
)
REMOVE_PATTERNS = (
    r"撤销退市风险警示",
    r"撤销其他风险警示",
    r"撤销风险警示",
    r"撤销.*\*?ST",
)
DELIST_PATTERNS = (
    r"终止上市",
    r"退市整理期",
)


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lookup = {str(column).strip(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


def _classify_title(title: str) -> str:
    text = str(title)
    for pattern in REMOVE_PATTERNS:
        if re.search(pattern, text):
            return "risk_warning_remove_candidate"
    for pattern in APPLY_PATTERNS:
        if re.search(pattern, text):
            return "risk_warning_apply_candidate"
    for pattern in DELIST_PATTERNS:
        if re.search(pattern, text):
            return "delisting_related"
    return "other_risk_disclosure"


def _month_windows(start: str, end: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if end_ts < start_ts:
        raise ValueError("end must be on or after start")
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start_ts
    while cursor <= end_ts:
        month_end = min(cursor + pd.offsets.MonthEnd(0), end_ts)
        windows.append((cursor, pd.Timestamp(month_end).normalize()))
        cursor = pd.Timestamp(month_end).normalize() + pd.Timedelta(days=1)
    return windows


@dataclass(frozen=True)
class CNInfoFetchResult:
    frame: pd.DataFrame
    calls: int
    warnings: tuple[str, ...]


class CNInfoRiskAnnouncementProvider:
    """Archive risk-warning-related disclosure metadata from CNINFO.

    The metadata is reconstructed after the fact. It is useful evidence for locating
    historical status transitions, but an announcement date is not automatically
    interpreted as the effective ST/*ST date.
    """

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_window(
        self,
        *,
        category: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_zh_a_disclosure_report_cninfo(
            symbol="",
            market="沪深京",
            keyword="",
            category=category,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )

    def fetch(self, *, start: str, end: str) -> CNInfoFetchResult:
        frames: list[pd.DataFrame] = []
        warnings: list[str] = []
        calls = 0
        for window_start, window_end in _month_windows(start, end):
            for category in RISK_CATEGORIES:
                calls += 1
                try:
                    raw = self._fetch_window(
                        category=category,
                        start=window_start,
                        end=window_end,
                    )
                except Exception as exc:  # noqa: BLE001 - evidence source isolation
                    warnings.append(
                        f"{category} {window_start.date()}..{window_end.date()}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                if raw is None or raw.empty:
                    continue
                normalized = normalize_cninfo_announcements(raw, category=category)
                if not normalized.empty:
                    frames.append(normalized)

        if not frames:
            empty = pd.DataFrame(
                columns=[
                    "symbol",
                    "name",
                    "announcement_title",
                    "announcement_time",
                    "announcement_link",
                    "category",
                    "event_candidate",
                    "source",
                ]
            )
            return CNInfoFetchResult(empty, calls, tuple(warnings))

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["symbol", "announcement_title", "announcement_time", "announcement_link"]
        )
        return CNInfoFetchResult(
            combined.sort_values(["announcement_time", "symbol"]).reset_index(drop=True),
            calls,
            tuple(warnings),
        )


def normalize_cninfo_announcements(frame: pd.DataFrame, *, category: str) -> pd.DataFrame:
    code_col = _first_column(frame, ("代码", "secCode", "证券代码"))
    name_col = _first_column(frame, ("简称", "secName", "证券简称"))
    title_col = _first_column(frame, ("公告标题", "announcementTitle", "标题"))
    time_col = _first_column(frame, ("公告时间", "announcementTime", "公告日期"))
    link_col = _first_column(frame, ("公告链接", "adjunctUrl", "announcementUrl", "链接"))

    required = {
        "code": code_col,
        "name": name_col,
        "title": title_col,
        "time": time_col,
    }
    missing = [name for name, column in required.items() if not column]
    if missing:
        raise ValueError(
            f"CNINFO payload missing fields {missing}; columns={list(frame.columns)}"
        )

    result = pd.DataFrame(
        {
            "symbol": frame[code_col].astype(str).str.extract(r"(\d{6})", expand=False),
            "name": frame[name_col].astype(str),
            "announcement_title": frame[title_col].astype(str),
            "announcement_time": pd.to_datetime(frame[time_col], errors="coerce"),
            "announcement_link": (
                frame[link_col].astype(str) if link_col else pd.Series("", index=frame.index)
            ),
        }
    )
    result = result.dropna(subset=["symbol", "announcement_time"])
    result["symbol"] = result["symbol"].str.zfill(6)
    result = result[result["symbol"].str.match(r"^[036]\d{5}$", na=False)].copy()
    result["category"] = category
    result["event_candidate"] = result["announcement_title"].map(_classify_title)
    result["source"] = "cninfo_disclosure_metadata"
    return result.reset_index(drop=True)


def run_inventory(
    *,
    output_dir: str | Path,
    start: str,
    end: str,
) -> dict[str, object]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fetched = CNInfoRiskAnnouncementProvider().fetch(start=start, end=end)
    frame = fetched.frame
    frame.to_csv(output / "risk_announcements.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")

    counts = (
        frame["event_candidate"].value_counts().sort_index().to_dict()
        if not frame.empty
        else {}
    )
    exchange_guess = {
        "SSE": int(frame["symbol"].str.startswith(("6",)).sum()) if not frame.empty else 0,
        "SZSE": int(frame["symbol"].str.startswith(("0", "3")).sum()) if not frame.empty else 0,
    }
    payload = {
        "status": "inventory_complete" if not frame.empty else "inventory_empty",
        "start": pd.Timestamp(start).date().isoformat(),
        "end": pd.Timestamp(end).date().isoformat(),
        "source": "CNINFO information-disclosure search via AKShare",
        "source_url": (
            "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?"
            "url=disclosure/list/search"
        ),
        "calls_attempted": fetched.calls,
        "announcement_rows": int(len(frame)),
        "event_candidate_counts": {str(k): int(v) for k, v in counts.items()},
        "exchange_guess_counts": exchange_guess,
        "warnings": list(fetched.warnings),
        "effective_date_status": "not_parsed",
        "strict_point_in_time_evidence": False,
        "note": (
            "Announcement metadata is reconstructed after the fact. Titles identify candidate "
            "risk-warning events only. No ST/*ST effective date is inferred from announcement "
            "time; strict upgrade requires the effective date from announcement body or "
            "exchange event records."
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.cninfo_risk_inventory")
    parser.add_argument("--output-dir", default="reports/historical-pit/cninfo")
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument(
        "--end",
        default=pd.Timestamp.now(tz="Asia/Shanghai").date().isoformat(),
    )
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
