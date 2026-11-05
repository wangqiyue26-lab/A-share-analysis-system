"""Narrow, source-attributed delisting correction for reconstructed PIT research.

The canonical lifecycle archive is preserved unchanged. This module applies
exactly one reviewed exchange-notice correction to a COPY of that archive.
The resulting dates are retrospective evidence, never strict_live_observed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

OFFICIAL_SSE_NOTICE = (
    "https://www.sse.com.cn/disclosure/announcement/listing/stock/"
    "c/c_20250718_10785880.shtml"
)
NOTICE_ID = "上证公告（公司股票摘牌）〖2025〗064号"
NOTICE_PUBLISHED = "2025-07-18"
SYMBOL = "600190"
ORIGINAL_ARCHIVE_DELIST_DATE = "2025-07-28"
OFFICIAL_DELIST_DATE = "2025-07-25"


def apply_reviewed_delisting_correction(
    lifecycle: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Fail closed unless original archive exactly matches documented anomaly."""
    required = {"symbol", "exchange", "list_date", "delist_date"}
    if not required.issubset(lifecycle.columns):
        raise ValueError(f"Lifecycle lacks {sorted(required - set(lifecycle.columns))}")
    result = lifecycle.copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    if result["symbol"].duplicated().any():
        raise ValueError("Duplicate lifecycle symbols prohibit official correction")
    selected = result.index[result["symbol"].eq(SYMBOL)]
    if len(selected) != 1:
        raise ValueError("Expected exactly one lifecycle record for 600190")
    index = selected[0]
    row = result.loc[index]
    if row["exchange"] != "SSE":
        raise ValueError("Official SSE notice cannot override a non-SSE security")
    original = pd.Timestamp(row["delist_date"])
    if pd.isna(original) or original.date().isoformat() != ORIGINAL_ARCHIVE_DELIST_DATE:
        raise ValueError(
            f"600190 delisting mismatch: expected {ORIGINAL_ARCHIVE_DELIST_DATE}, "
            f"got {row['delist_date']!r}; manual review required"
        )
    listing = pd.Timestamp(row["list_date"])
    official = pd.Timestamp(OFFICIAL_DELIST_DATE)
    published = pd.Timestamp(NOTICE_PUBLISHED)
    if not listing < official or not published <= official:
        raise ValueError("Invalid official listing/delisting evidence chronology")
    result.loc[index, "delist_date"] = OFFICIAL_DELIST_DATE
    changes = result.astype(str).ne(lifecycle.astype(str))
    # 'symbol' was normalized to six characters. Verify precisely one changed cell,
    # in delist_date, with no unintended membership edits.
    changed_cells = [
        (int(i), str(col))
        for i, col in zip(*changes.to_numpy().nonzero(), strict=True)
        for col in [changes.columns[col]]
    ]
    if changed_cells != [(result.index.get_loc(index), "delist_date")]:
        raise AssertionError(f"Unexpected lifecycle changes: {changed_cells[:10]}")
    audit = {
        "correction_status": "source_reviewed_research_override",
        "symbol": SYMBOL,
        "exchange": "SSE",
        "original_archive_delist_date": ORIGINAL_ARCHIVE_DELIST_DATE,
        "official_delist_date": OFFICIAL_DELIST_DATE,
        "official_notice_id": NOTICE_ID,
        "official_notice_published": NOTICE_PUBLISHED,
        "official_source_url": OFFICIAL_SSE_NOTICE,
        "source_finding": "上交所明确于2025年7月25日对600190予以摘牌",
        "delist_boundary_semantics": "exclusive; exclude 600190 on 2025-07-25",
        "strict_live_observed": False,
        "real_money_ready": False,
        "original_archive_preserved": True,
        "changed_row_count": 1,
        "changed_column": "delist_date",
    }
    return result, audit


def main() -> int:
    parser = argparse.ArgumentParser(prog="historical-verified-lifecycle-correction")
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    raw = pd.read_csv(args.lifecycle_file, dtype={"symbol": str})
    corrected, audit = apply_reviewed_delisting_correction(raw)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    corrected.to_csv(out / "corrected_lifecycle.csv", index=False)
    (out / "evidence.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
