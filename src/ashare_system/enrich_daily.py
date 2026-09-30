from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .factors.fundamental import FUNDAMENTAL_FACTOR_COLUMNS
from .fundamentals_pipeline import build_fundamental_snapshot

_START = "<!-- PIT-FUNDAMENTALS-START -->"
_END = "<!-- PIT-FUNDAMENTALS-END -->"


@dataclass(frozen=True)
class DailyFundamentalEnrichmentResult:
    requested_count: int
    success_count: int
    failure_count: int
    status: str
    fundamentals_file: str
    summary_file: str
    page_file: str

    def as_dict(self) -> dict[str, object]:
        return {
            "requested_count": self.requested_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "status": self.status,
            "fundamentals_file": self.fundamentals_file,
            "summary_file": self.summary_file,
            "page_file": self.page_file,
        }


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fmt_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _fmt_number(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _selected_as_of(selected: pd.DataFrame) -> pd.Timestamp:
    if "as_of" not in selected.columns:
        raise ValueError("selected.csv must contain an as_of column")
    dates = pd.to_datetime(selected["as_of"], errors="coerce").dropna()
    if dates.empty:
        raise ValueError("selected.csv contains no valid as_of date")
    return pd.Timestamp(dates.max()).normalize()


def _display_frame(selected: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    keep = [column for column in ["rank", "symbol", "name", "composite_score"] if column in selected]
    base = selected.loc[:, keep].copy()
    base["symbol"] = base["symbol"].astype(str).str.zfill(6)
    if fundamentals.empty:
        return base
    facts = fundamentals.copy()
    facts["symbol"] = facts["symbol"].astype(str).str.zfill(6)
    return base.merge(facts, on="symbol", how="left")


def _render_fundamental_page(
    selected: pd.DataFrame,
    fundamentals: pd.DataFrame,
    summary: dict[str, object],
    output: Path,
) -> None:
    frame = _display_frame(selected, fundamentals)
    columns = [
        ("rank", "排名"),
        ("symbol", "代码"),
        ("name", "名称"),
        ("fundamental_period_end", "财报期"),
        ("revenue_yoy", "营收同比"),
        ("net_profit_yoy", "净利润同比"),
        ("net_margin", "净利率"),
        ("ocf_to_net_profit", "经营现金流/净利润"),
        ("debt_to_assets", "资产负债率"),
        ("fundamental_coverage", "覆盖率"),
        ("fundamental_source", "来源"),
    ]
    active = [(key, label) for key, label in columns if key in frame]
    pct = {
        "revenue_yoy",
        "net_profit_yoy",
        "net_margin",
        "debt_to_assets",
        "fundamental_coverage",
    }
    rows: list[str] = []
    for _, row in frame.iterrows():
        cells: list[str] = []
        for key, _ in active:
            value = row[key]
            if key == "rank":
                text = "—" if pd.isna(value) else str(int(float(value)))
            elif key == "symbol":
                text = html.escape(str(value).zfill(6))
            elif key in {"name", "fundamental_source"}:
                text = "—" if pd.isna(value) else html.escape(str(value))
            elif key == "fundamental_period_end":
                parsed = pd.to_datetime(value, errors="coerce")
                text = "—" if pd.isna(parsed) else parsed.date().isoformat()
            elif key in pct:
                text = _fmt_pct(value)
            else:
                text = _fmt_number(value)
            cells.append(f"<td>{text}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    requested = int(summary.get("requested_count", len(selected)))
    success = int(summary.get("success_count", len(fundamentals)))
    failure = int(summary.get("failure_count", max(requested - success, 0)))
    pipeline_error = summary.get("pipeline_error")
    status = "正常" if success > 0 and not pipeline_error else "降级"
    header = "".join(f"<th>{html.escape(label)}</th>" for _, label in active)
    body = "".join(rows) if rows else '<tr><td colspan="11">暂无可展示的基本面数据。</td></tr>'
    error_note = ""
    if pipeline_error:
        error_note = (
            '<div class="notice danger">基本面增强本次失败：'
            + html.escape(str(pipeline_error))
            + "。市场因子排名未受影响。</div>"
        )

    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PIT 基本面补充</title>
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--text:#17212b;--muted:#65717d;--line:#dce2e8;--accent:#1f5f8b}}
*{{box-sizing:border-box}} body{{margin:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text)}}
.container{{width:min(1240px,calc(100% - 24px));margin:0 auto;padding:24px 0 54px}} a{{color:var(--accent);text-decoration:none}}
.hero,.panel,.metric{{background:var(--panel);border:1px solid var(--line);border-radius:14px}} .hero{{padding:24px}} h1{{margin:8px 0}} .sub{{color:var(--muted);line-height:1.65}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}} .metric{{padding:14px}} .metric span{{display:block;color:var(--muted);font-size:12px}} .metric strong{{display:block;font-size:20px;margin-top:5px}}
.panel{{padding:18px;margin-top:14px}} .table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}} table{{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}} th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:right}} th{{background:#f7f9fb;color:#4c5965;font-size:12px}} th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3){{text-align:left}}
.notice{{border-left:4px solid #d59a42;background:#fffaf1;padding:12px 14px;border-radius:8px;line-height:1.6;font-size:13px}} .danger{{border-left-color:#b24b4b;background:#fff5f5}} .footer{{color:var(--muted);font-size:12px;margin-top:16px;line-height:1.6}}
</style></head><body><main class="container">
<section class="hero"><a href="index.html">← 返回主排名</a><h1>PIT 基本面补充</h1><div class="sub">只使用在数据截止时点已经公告/可见的财务事实。当前页面用于补充解释，不参与市场因子综合分和 Top10 排名。</div></section>
<section class="metrics"><div class="metric"><span>状态</span><strong>{status}</strong></div><div class="metric"><span>请求股票</span><strong>{requested}</strong></div><div class="metric"><span>成功</span><strong>{success}</strong></div><div class="metric"><span>失败</span><strong>{failure}</strong></div><div class="metric"><span>平均覆盖</span><strong>{_fmt_pct(summary.get('average_coverage'))}</strong></div><div class="metric"><span>截止时间</span><strong style="font-size:13px">{html.escape(str(summary.get('as_of') or '—'))}</strong></div></section>
{error_note}<section class="panel"><div class="notice">营收/净利润同比、净利率、经营现金流与净利润匹配度、资产负债率均按 Point-in-Time 规则计算。财务数据缺失会显示为“—”，不会用未来公告回填历史时点。</div></section>
<section class="panel"><div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div></section>
<div class="footer">研究用途，不构成个股推荐或收益承诺。基本面增强失败时，主页面仍保留已验证的市场因子排名。</div>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


def _patch_main_index(index_path: Path, summary: dict[str, object]) -> None:
    if not index_path.exists():
        return
    requested = int(summary.get("requested_count", 0))
    success = int(summary.get("success_count", 0))
    block = f"""{_START}
  <section class="panel" id="pit-fundamentals">
    <h2>PIT 基本面补充</h2>
    <div class="subtitle">已对 Top{requested} 入选股票尝试补充公告时间约束的财务因子；成功 {success} 只，平均因子覆盖 {_fmt_pct(summary.get('average_coverage'))}。该数据当前不参与综合分。</div>
    <div class="notice"><a href="fundamentals.html">查看营收/利润增长、净利率、经营现金流质量和资产负债率 →</a></div>
  </section>
{_END}"""
    text = index_path.read_text(encoding="utf-8")
    if _START in text and _END in text:
        prefix, tail = text.split(_START, 1)
        _, suffix = tail.split(_END, 1)
        text = prefix + block + suffix
    else:
        needle = '  <section class="panel">\n    <h2>回测与基准</h2>'
        text = text.replace(needle, block + "\n" + needle, 1) if needle in text else text.replace("</main>", block + "\n</main>", 1)
    old = "当前综合分由动量、趋势、波动、回撤和成交额等市场因子组成；后续阶段再接入经过 Point-in-Time 校验的基本面因子。"
    new = "当前综合分仍由动量、趋势、波动、回撤和成交额等市场因子组成；Point-in-Time 基本面已作为补充页面上线，暂不参与综合分。"
    index_path.write_text(text.replace(old, new), encoding="utf-8")


def _write_failure_outputs(
    selected: pd.DataFrame,
    cutoff: pd.Timestamp,
    fundamentals_file: Path,
    summary_file: Path,
    error: str,
) -> None:
    columns = [
        "symbol",
        "fundamental_as_of",
        "fundamental_period_end",
        *FUNDAMENTAL_FACTOR_COLUMNS,
        "fundamental_coverage",
        "fundamental_source",
        "fundamental_warning",
    ]
    pd.DataFrame(columns=columns).to_csv(fundamentals_file, index=False)
    _write_json(
        summary_file,
        {
            "as_of": cutoff.tz_localize("UTC").isoformat(),
            "requested_count": len(selected),
            "success_count": 0,
            "failure_count": len(selected),
            "average_coverage": None,
            "source_counts": {},
            "warning_count": 0,
            "warnings": [],
            "failures": [],
            "scoring_integration": False,
            "pipeline_error": error,
            "note": "Supplemental PIT fundamentals failed; market-factor ranking remains authoritative.",
        },
    )


def enrich_daily_outputs(
    *,
    selection_dir: str | Path = "reports/production/selection",
    site_dir: str | Path = "site",
    cache_root: str | Path = "data/cache/financials",
    manifest_path: str | Path = "reports/production/manifest.json",
    workers: int = 2,
    refresh_days: int = 7,
    stale_days: int = 30,
    fail_on_error: bool = False,
) -> DailyFundamentalEnrichmentResult:
    selection = Path(selection_dir)
    site = Path(site_dir)
    manifest_file = Path(manifest_path)
    selected_file = selection / "selected.csv"
    if not selected_file.exists():
        raise FileNotFoundError(f"Selection output not found: {selected_file}")

    selected = pd.read_csv(selected_file, dtype={"symbol": str})
    if "symbol" not in selected.columns:
        raise ValueError("selected.csv must contain a symbol column")
    selected["symbol"] = selected["symbol"].astype(str).str.zfill(6)
    cutoff = _selected_as_of(selected)
    fundamentals_file = selection / "fundamentals.csv"
    summary_file = selection / "fundamental_summary.json"

    try:
        build_fundamental_snapshot(
            selected["symbol"],
            as_of=cutoff,
            cache_root=cache_root,
            output_dir=selection,
            workers=workers,
            refresh_days=refresh_days,
            stale_days=stale_days,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _write_failure_outputs(selected, cutoff, fundamentals_file, summary_file, error)
        if fail_on_error:
            raise

    fundamentals = pd.read_csv(fundamentals_file, dtype={"symbol": str})
    summary = _read_json(summary_file)
    page_file = site / "fundamentals.html"
    _render_fundamental_page(selected, fundamentals, summary, page_file)
    _patch_main_index(site / "index.html", summary)

    selection_summary_path = selection / "summary.json"
    selection_summary = _read_json(selection_summary_path)
    selection_summary.update(
        {
            "fundamental_requested_count": int(summary.get("requested_count", len(selected))),
            "fundamental_success_count": int(summary.get("success_count", len(fundamentals))),
            "fundamental_failure_count": int(summary.get("failure_count", 0)),
            "fundamental_average_coverage": summary.get("average_coverage"),
            "fundamental_scoring_integration": False,
            "fundamental_pipeline_error": summary.get("pipeline_error"),
        }
    )
    _write_json(selection_summary_path, selection_summary)

    manifest = _read_json(manifest_file)
    if manifest:
        manifest.update(
            {
                "fundamental_snapshot": str(fundamentals_file),
                "fundamental_summary": str(summary_file),
                "fundamental_page": str(page_file),
                "fundamental_success_count": int(summary.get("success_count", len(fundamentals))),
                "fundamental_scoring_integration": False,
                "fundamental_pipeline_error": summary.get("pipeline_error"),
            }
        )
        _write_json(manifest_file, manifest)

    requested = int(summary.get("requested_count", len(selected)))
    success = int(summary.get("success_count", len(fundamentals)))
    failure = int(summary.get("failure_count", max(requested - success, 0)))
    status = "success" if success > 0 and not summary.get("pipeline_error") else "degraded"
    return DailyFundamentalEnrichmentResult(
        requested_count=requested,
        success_count=success,
        failure_count=failure,
        status=status,
        fundamentals_file=str(fundamentals_file),
        summary_file=str(summary_file),
        page_file=str(page_file),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.enrich_daily")
    parser.add_argument("--selection-dir", default="reports/production/selection")
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--cache-root", default="data/cache/financials")
    parser.add_argument("--manifest", default="reports/production/manifest.json")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--refresh-days", type=int, default=7)
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument("--fail-on-error", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = enrich_daily_outputs(
        selection_dir=args.selection_dir,
        site_dir=args.site_dir,
        cache_root=args.cache_root,
        manifest_path=args.manifest,
        workers=args.workers,
        refresh_days=args.refresh_days,
        stale_days=args.stale_days,
        fail_on_error=args.fail_on_error,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
