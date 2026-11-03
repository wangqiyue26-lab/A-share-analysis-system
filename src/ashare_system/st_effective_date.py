from __future__ import annotations

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from .cninfo_risk_inventory import CNInfoRiskAnnouncementProvider

_QUOTED_NAME_CHANGE = re.compile(
    r"(?:股票|证券)?简称(?:将)?由[“\"](?P<before>[^”\"]{1,20})[”\"]"
    r"(?:变更为|变为)[“\"](?P<after>[^”\"]{1,20})[”\"]"
)
_PLAIN_NAME_CHANGE = re.compile(
    r"(?:股票|证券)?简称(?:将)?由"
    r"(?P<before>[*A-Za-z0-9\u4e00-\u9fff]{2,16})"
    r"(?:变更为|变为)"
    r"(?P<after>[*A-Za-z0-9\u4e00-\u9fff]{2,16})"
)
_EFFECTIVE_DATE = re.compile(
    r"(?:(?P<year>20\d{2})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
    r"(?:开市|开盘|交易)?起"
)
_RISK_WORD = re.compile(r"风险警示")
_APPLY_WORD = re.compile(r"(?:实施|被实施)")
_REMOVE_WORD = re.compile(r"撤销")


def _normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", "", text)


def _is_st_name(name: str) -> bool:
    return "ST" in str(name).upper()


def _resolve_effective_date(
    *,
    year_text: str | None,
    month: int,
    day: int,
    announcement_time: pd.Timestamp,
) -> pd.Timestamp | None:
    if year_text:
        try:
            value = pd.Timestamp(year=int(year_text), month=month, day=day).normalize()
        except ValueError:
            return None
        delta = (value - announcement_time.normalize()).days
        if -60 <= delta <= 30:
            return value
        return None

    candidates: list[pd.Timestamp] = []
    for year in (announcement_time.year - 1, announcement_time.year, announcement_time.year + 1):
        try:
            value = pd.Timestamp(year=year, month=month, day=day).normalize()
        except ValueError:
            continue
        delta = (value - announcement_time.normalize()).days
        if -730 <= delta <= 30:
            candidates.append(value)
    if not candidates:
        return None
    return min(candidates, key=lambda value: abs((value - announcement_time.normalize()).days))


@dataclass(frozen=True)
class VerifiedSTEvent:
    symbol: str | None
    effective_date: str
    before_name: str
    after_name: str
    is_st_before: bool
    is_st_after: bool
    action: str
    evidence_snippet: str


def parse_effective_st_event(
    text: str,
    *,
    announcement_time: str | pd.Timestamp,
    symbol: str | None = None,
) -> tuple[str, VerifiedSTEvent | None]:
    """Conservatively verify a boolean ST transition from announcement body text.

    A verified event requires:
    - an explicit security-short-name transition;
    - that transition must cross the boolean ST boundary;
    - an explicit effective-date phrase ending in '起';
    - nearby risk-warning wording consistent with the direction of the transition.
    """
    normalized = _normalize_text(text)
    if not normalized:
        return "no_text", None

    timestamp = pd.Timestamp(announcement_time)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Shanghai").tz_localize(None)

    transitions: list[tuple[int, int, str, str, bool, bool]] = []
    seen_spans: set[tuple[int, int]] = set()
    for pattern in (_QUOTED_NAME_CHANGE, _PLAIN_NAME_CHANGE):
        for match in pattern.finditer(normalized):
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            before = match.group("before").strip("“”\"' ")
            after = match.group("after").strip("“”\"' ")
            before_st = _is_st_name(before)
            after_st = _is_st_name(after)
            if before_st == after_st:
                continue
            transitions.append((span[0], span[1], before, after, before_st, after_st))

    if not transitions:
        return "no_boolean_name_transition", None

    dates: list[tuple[int, int, pd.Timestamp]] = []
    for match in _EFFECTIVE_DATE.finditer(normalized):
        value = _resolve_effective_date(
            year_text=match.group("year"),
            month=int(match.group("month")),
            day=int(match.group("day")),
            announcement_time=timestamp,
        )
        if value is not None:
            dates.append((match.start(), match.end(), value))
    if not dates:
        return "no_explicit_effective_date", None

    candidates: list[VerifiedSTEvent] = []
    for start, end, before, after, before_st, after_st in transitions:
        nearest = min(
            dates,
            key=lambda item: min(abs(item[0] - start), abs(item[1] - end)),
        )
        distance = min(abs(nearest[0] - start), abs(nearest[1] - end))
        if distance > 500:
            continue

        window_start = max(0, min(start, nearest[0]) - 220)
        window_end = min(len(normalized), max(end, nearest[1]) + 300)
        window = normalized[window_start:window_end]
        if not _RISK_WORD.search(window):
            continue

        if not before_st and after_st:
            if not _APPLY_WORD.search(window):
                continue
            action = "apply_st"
        elif before_st and not after_st:
            if not _REMOVE_WORD.search(window):
                continue
            if re.search(r"继续(?:被)?实施.{0,30}风险警示", window):
                continue
            action = "remove_st"
        else:
            continue

        snippet_start = max(0, min(start, nearest[0]) - 120)
        snippet_end = min(len(normalized), max(end, nearest[1]) + 180)
        candidates.append(
            VerifiedSTEvent(
                symbol=str(symbol).zfill(6) if symbol else None,
                effective_date=nearest[2].date().isoformat(),
                before_name=before,
                after_name=after,
                is_st_before=before_st,
                is_st_after=after_st,
                action=action,
                evidence_snippet=normalized[snippet_start:snippet_end],
            )
        )

    unique = {
        (
            item.effective_date,
            item.before_name,
            item.after_name,
            item.action,
        ): item
        for item in candidates
    }
    if not unique:
        return "no_consistent_action_date_pair", None
    if len(unique) > 1:
        return "ambiguous_multiple_verified_events", None
    return "verified", next(iter(unique.values()))


def fetch_pdf_text(pdf_url: str, *, max_bytes: int = 25 * 1024 * 1024) -> dict[str, object]:
    from akshare.utils import http

    response = http.get(pdf_url)
    response.raise_for_status()
    payload = response.content
    if len(payload) > max_bytes:
        raise RuntimeError(f"PDF exceeds max_bytes: {len(payload)}")
    if not payload.startswith(b"%PDF"):
        raise RuntimeError("CNINFO attachment is not a PDF")

    reader = PdfReader(BytesIO(payload))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages)
    return {
        "text": text,
        "page_count": len(reader.pages),
        "pdf_bytes": len(payload),
        "pdf_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _sample_key(row: pd.Series) -> str:
    raw = str(row.get("announcement_id") or "")
    if not raw:
        raw = (
            f"{row['symbol']}|{row['announcement_time']}|"
            f"{row['announcement_title']}"
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def select_pilot_candidates(
    frame: pd.DataFrame,
    *,
    per_cohort: int = 10,
) -> pd.DataFrame:
    candidates = frame[
        frame["event_candidate"].isin(
            ["risk_warning_apply_candidate", "risk_warning_remove_candidate"]
        )
        & frame["pdf_url"].astype(str).str.startswith("http")
    ].copy()
    if candidates.empty:
        raise RuntimeError("No PDF-backed risk-warning candidates available")

    candidates["exchange_guess"] = candidates["symbol"].map(
        lambda value: "SSE" if str(value).startswith("6") else "SZSE"
    )
    candidates["direction"] = candidates["event_candidate"].map(
        {
            "risk_warning_apply_candidate": "apply",
            "risk_warning_remove_candidate": "remove",
        }
    )
    candidates["cohort"] = candidates["exchange_guess"] + "_" + candidates["direction"]
    candidates["_sample_key"] = candidates.apply(_sample_key, axis=1)

    sampled = (
        candidates.sort_values("_sample_key")
        .groupby("cohort", group_keys=False)
        .head(per_cohort)
        .drop(columns="_sample_key")
        .sort_values(["cohort", "symbol", "announcement_time"])
        .reset_index(drop=True)
    )
    return sampled


def run_pilot(
    *,
    output_dir: str | Path,
    start: str = "2023-01-01",
    end: str = "2024-12-31",
    per_cohort: int = 10,
    workers: int = 2,
) -> dict[str, object]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    fetched = CNInfoRiskAnnouncementProvider().fetch(start=start, end=end)
    sample = select_pilot_candidates(fetched.frame, per_cohort=per_cohort)
    sample.to_csv(output / "sample.csv", index=False)

    rows: list[dict[str, object]] = []

    def parse_row(row: pd.Series) -> dict[str, object]:
        base = {
            "symbol": str(row["symbol"]).zfill(6),
            "name": row["name"],
            "cohort": row["cohort"],
            "announcement_id": row.get("announcement_id", ""),
            "announcement_time": row["announcement_time"],
            "announcement_title": row["announcement_title"],
            "pdf_url": row["pdf_url"],
        }
        try:
            pdf = fetch_pdf_text(str(row["pdf_url"]))
            status, event = parse_effective_st_event(
                str(pdf["text"]),
                announcement_time=row["announcement_time"],
                symbol=str(row["symbol"]),
            )
            base.update(
                {
                    "fetch_success": True,
                    "parse_status": status,
                    "page_count": pdf["page_count"],
                    "pdf_bytes": pdf["pdf_bytes"],
                    "pdf_sha256": pdf["pdf_sha256"],
                }
            )
            if event is not None:
                base.update(asdict(event))
            return base
        except Exception as exc:  # noqa: BLE001 - pilot isolates individual PDFs
            base.update(
                {
                    "fetch_success": False,
                    "parse_status": f"error:{type(exc).__name__}",
                    "error": str(exc),
                }
            )
            return base

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(sample)))) as pool:
        futures = [
            pool.submit(parse_row, pd.Series(row._asdict()))
            for row in sample.itertuples(index=False)
        ]
        for future in as_completed(futures):
            rows.append(future.result())

    detail = pd.DataFrame(rows).sort_values(
        ["cohort", "symbol", "announcement_time"]
    ).reset_index(drop=True)
    detail.to_csv(output / "detail.csv", index=False)
    verified = detail[detail["parse_status"] == "verified"].copy()
    verified.to_csv(output / "verified_events.csv", index=False)

    cohorts: dict[str, dict[str, object]] = {}
    for cohort, group in detail.groupby("cohort"):
        cohorts[str(cohort)] = {
            "sample_count": len(group),
            "fetch_success_rate": float(group["fetch_success"].astype(bool).mean()),
            "verified_count": int((group["parse_status"] == "verified").sum()),
            "verified_rate": float((group["parse_status"] == "verified").mean()),
            "parse_status_counts": {
                str(key): int(value)
                for key, value in group["parse_status"].value_counts().items()
            },
        }

    overall_verified_rate = (
        float((detail["parse_status"] == "verified").mean()) if len(detail) else 0.0
    )
    payload = {
        "status": "complete",
        "purpose": "st_effective_date_pdf_parser_pilot",
        "strict_point_in_time_evidence": False,
        "start": pd.Timestamp(start).date().isoformat(),
        "end": pd.Timestamp(end).date().isoformat(),
        "sample_count": len(detail),
        "fetch_success_rate": (
            float(detail["fetch_success"].astype(bool).mean()) if len(detail) else 0.0
        ),
        "verified_count": len(verified),
        "verified_rate": overall_verified_rate,
        "cohorts": cohorts,
        "scale_recommendation": (
            "parser_promising_expand_carefully"
            if overall_verified_rate >= 0.40
            else "parser_needs_revision_before_scale"
        ),
        "note": (
            "Verified events require explicit PDF text evidence for an effective date and a "
            "short-name transition that changes the boolean ST state. Announcement title or "
            "announcement date alone never creates an effective event."
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.st_effective_date")
    parser.add_argument("--output-dir", default="reports/historical-pit/st-effective-pilot")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--per-cohort", type=int, default=10)
    parser.add_argument("--workers", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_pilot(
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        per_cohort=args.per_cohort,
        workers=args.workers,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
