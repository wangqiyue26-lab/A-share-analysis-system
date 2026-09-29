from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CHINA_TZ = ZoneInfo("Asia/Shanghai")

_FACTOR_LABELS = {
    "rank": "排名",
    "symbol": "代码",
    "name": "名称",
    "composite_score": "综合分",
    "momentum_20": "20日动量",
    "momentum_60": "60日动量",
    "trend_ma20_ma60": "均线趋势",
    "volatility_20": "20日波动",
    "max_drawdown_60": "60日回撤",
    "factor_coverage": "因子覆盖",
}


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _fmt_number(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _fmt_pct(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _card(label: str, value: str, detail: str = "") -> str:
    detail_html = f'<div class="metric-detail">{html.escape(detail)}</div>' if detail else ""
    return (
        '<div class="metric-card">'
        f'<div class="metric-label">{html.escape(label)}</div>'
        f'<div class="metric-value">{value}</div>{detail_html}</div>'
    )


def _selection_table(frame: pd.DataFrame, limit: int = 20) -> str:
    if frame.empty:
        return '<div class="empty">暂无选股结果。</div>'
    preferred = [
        "rank",
        "symbol",
        "name",
        "composite_score",
        "momentum_20",
        "momentum_60",
        "trend_ma20_ma60",
        "volatility_20",
        "max_drawdown_60",
        "factor_coverage",
    ]
    columns = [column for column in preferred if column in frame.columns]
    if not columns:
        columns = list(frame.columns[:8])

    header = "".join(f"<th>{html.escape(_FACTOR_LABELS.get(col, col))}</th>" for col in columns)
    rows: list[str] = []
    percentage_columns = {
        "momentum_20",
        "momentum_60",
        "trend_ma20_ma60",
        "volatility_20",
        "max_drawdown_60",
    }
    for _, row in frame.head(limit).iterrows():
        cells: list[str] = []
        for column in columns:
            value = row[column]
            if column == "rank":
                text = str(int(float(value))) if not pd.isna(value) else "—"
            elif column == "symbol":
                text = html.escape(str(value).zfill(6))
            elif column == "name":
                text = html.escape(str(value)) if not pd.isna(value) else "—"
            elif column in percentage_columns:
                text = _fmt_pct(value)
            else:
                text = _fmt_number(value, 3)
            cells.append(f"<td>{text}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + header
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _line_svg(frame: pd.DataFrame) -> str:
    required = {"portfolio_index", "benchmark_index"}
    if frame.empty or not required.issubset(frame.columns) or len(frame) < 2:
        return '<div class="empty">暂无基准对比曲线。</div>'

    portfolio = pd.to_numeric(frame["portfolio_index"], errors="coerce")
    benchmark = pd.to_numeric(frame["benchmark_index"], errors="coerce")
    valid = portfolio.notna() & benchmark.notna()
    portfolio = portfolio[valid].reset_index(drop=True)
    benchmark = benchmark[valid].reset_index(drop=True)
    if len(portfolio) < 2:
        return '<div class="empty">暂无基准对比曲线。</div>'

    width, height, pad_x, pad_y = 860, 260, 42, 28
    low = float(min(portfolio.min(), benchmark.min()))
    high = float(max(portfolio.max(), benchmark.max()))
    if high <= low:
        high = low + 1.0

    def points(values: pd.Series) -> str:
        result: list[str] = []
        for index, value in enumerate(values):
            x = pad_x + index / (len(values) - 1) * (width - 2 * pad_x)
            y = pad_y + (high - float(value)) / (high - low) * (height - 2 * pad_y)
            result.append(f"{x:.1f},{y:.1f}")
        return " ".join(result)

    return f"""
    <div class="chart-wrap"><svg viewBox="0 0 {width} {height}" role="img" aria-label="组合与基准净值曲线">
      <line x1="{pad_x}" y1="{height-pad_y}" x2="{width-pad_x}" y2="{height-pad_y}" class="axis"/>
      <line x1="{pad_x}" y1="{pad_y}" x2="{pad_x}" y2="{height-pad_y}" class="axis"/>
      <polyline points="{points(portfolio)}" class="portfolio-line"/>
      <polyline points="{points(benchmark)}" class="benchmark-line"/>
      <text x="{pad_x}" y="18" class="chart-label">组合</text>
      <text x="{pad_x+58}" y="18" class="chart-label benchmark-label">沪深300</text>
      <text x="4" y="{pad_y+4}" class="tick">{high:.3f}</text>
      <text x="4" y="{height-pad_y}" class="tick">{low:.3f}</text>
    </svg></div>
    """


def build_dashboard(
    output_dir: str | Path,
    *,
    selection_dir: str | Path | None = None,
    backtest_dir: str | Path | None = None,
    title: str = "A股量化研究系统",
    mode_label: str = "研究预览",
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selection = Path(selection_dir) if selection_dir else None
    backtest = Path(backtest_dir) if backtest_dir else None

    selected = pd.DataFrame()
    selection_summary: dict[str, object] = {}
    if selection:
        selected_path = selection / "selected.csv"
        if selected_path.exists():
            selected = pd.read_csv(selected_path, dtype={"symbol": str})
        selection_summary = _read_json(selection / "summary.json")

    benchmark = pd.DataFrame()
    backtest_summary: dict[str, object] = {}
    if backtest:
        backtest_summary = _read_json(backtest / "summary.json")
        benchmark_path = backtest / "benchmark.csv"
        if benchmark_path.exists():
            benchmark = pd.read_csv(benchmark_path)

    generated_at = datetime.now(CHINA_TZ)
    data_as_of = selection_summary.get("data_as_of_max")
    if not data_as_of and not selected.empty and "as_of" in selected.columns:
        parsed = pd.to_datetime(selected["as_of"], errors="coerce").dropna()
        if not parsed.empty:
            data_as_of = parsed.max().date().isoformat()
    data_as_of = str(data_as_of or "—")

    cards = []
    if selection_summary.get("market_snapshot_rows") is not None:
        cards.append(_card("全市场覆盖", f"{int(selection_summary['market_snapshot_rows']):,}"))
    if selection_summary.get("prefilter_count") is not None:
        cards.append(_card("流动性预筛", str(int(selection_summary["prefilter_count"]))))
    cards.extend(
        [
            _card("有效排名", str(int(selection_summary.get("ranked_count", len(selected))))),
            _card("入选数量", str(int(selection_summary.get("selected_count", len(selected))))),
            _card("因子数据日期", html.escape(data_as_of)),
        ]
    )
    if selection_summary.get("market_snapshot_provider"):
        cards.append(_card("全市场数据源", html.escape(str(selection_summary["market_snapshot_provider"]))))

    if backtest_summary:
        cards.extend(
            [
                _card("区间收益", _fmt_pct(backtest_summary.get("portfolio_return"))),
                _card("沪深300", _fmt_pct(backtest_summary.get("benchmark_return"))),
                _card("超额收益", _fmt_pct(backtest_summary.get("excess_return"))),
                _card("最大回撤", _fmt_pct(backtest_summary.get("max_drawdown"))),
                _card("换手", _fmt_number(backtest_summary.get("turnover"), 2) + "×"),
            ]
        )

    if selection_summary.get("pipeline") == "full_market_liquidity_prefilter":
        subtitle = (
            "先扫描全 A 股市场并按当日流动性预筛，再对候选股读取约 280 个日历日历史，"
            "经 20 日平均成交额二次过滤后计算技术因子并排序。"
        )
    else:
        subtitle = "当前综合分由动量、趋势、波动、回撤和成交额等市场因子组成。"

    status_note = (
        "当前页面是量化研究输出，不构成个股推荐、买卖指令或收益承诺。"
        "基本面 Point-in-Time 数据层已经具备，但尚未混入当前综合分。"
    )

    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#f4f6f8;--panel:#fff;--text:#17212b;--muted:#65717d;--line:#dce2e8;--accent:#1f5f8b;--accent2:#9a5b22}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text)}}
.container{{width:min(1220px,calc(100% - 32px));margin:0 auto;padding:28px 0 60px}}.hero{{background:linear-gradient(135deg,#17212b,#29485e);color:#fff;border-radius:18px;padding:28px 30px;box-shadow:0 10px 30px rgba(20,35,50,.12)}}
.hero-top{{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}}h1{{margin:0;font-size:clamp(26px,4vw,40px)}}.badge{{padding:6px 11px;border:1px solid rgba(255,255,255,.35);border-radius:999px;font-size:13px}}.hero p{{max-width:900px;margin:13px 0 0;color:#dce8f0;line-height:1.65}}.meta{{margin-top:14px;font-size:12px;color:#aebfca}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}}.metric-card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:0 2px 10px rgba(20,35,50,.04)}}.metric-card{{padding:16px}}.metric-label{{color:var(--muted);font-size:12px}}.metric-value{{font-size:21px;font-weight:700;margin-top:6px}}.metric-detail{{color:var(--muted);font-size:11px;margin-top:3px}}
.panel{{padding:20px;margin-top:16px}}.panel h2{{margin:0 0 6px;font-size:18px}}.subtitle{{color:var(--muted);font-size:13px;margin-bottom:15px;line-height:1.55}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:right}}th{{background:#f7f9fb;color:#4c5965;font-size:12px;position:sticky;top:0}}th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3){{text-align:left}}tbody tr:hover{{background:#f8fbfd}}
.chart-wrap{{width:100%;overflow:hidden}}svg{{width:100%;height:auto;background:#fbfcfd;border:1px solid var(--line);border-radius:10px}}.axis{{stroke:#c8d1d9}}.portfolio-line{{fill:none;stroke:var(--accent);stroke-width:2.4}}.benchmark-line{{fill:none;stroke:var(--accent2);stroke-width:2;stroke-dasharray:5 4}}.chart-label,.tick{{fill:#52606c;font-size:12px}}.benchmark-label{{fill:var(--accent2)}}.notice{{border-left:4px solid #d59a42;padding:12px 14px;background:#fffaf1;color:#64523b;border-radius:8px;line-height:1.55;font-size:13px}}.empty{{color:var(--muted);padding:20px;text-align:center}}.footer{{color:var(--muted);font-size:12px;margin-top:20px;line-height:1.6}}
@media(max-width:650px){{.container{{width:min(100% - 18px,1220px);padding-top:10px}}.hero{{border-radius:12px;padding:22px 18px}}.panel{{padding:14px}}}}
</style></head><body><main class="container">
<section class="hero"><div class="hero-top"><h1>{html.escape(title)}</h1><span class="badge">{html.escape(mode_label)}</span></div><p>{html.escape(status_note)}</p><div class="meta">生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai · 因子数据：{html.escape(data_as_of)}</div></section>
<section class="metrics">{''.join(cards)}</section>
<section class="panel"><h2>最新量化排名</h2><div class="subtitle">{html.escape(subtitle)}</div>{_selection_table(selected)}</section>
<section class="panel"><h2>回测与基准</h2><div class="subtitle">有完整回测报告时显示组合与沪深300对比；当前生产日更先以选股结果为主。</div>{_line_svg(benchmark)}</section>
<section class="panel"><div class="notice">{html.escape(status_note)}</div></section>
<div class="footer">A-share-analysis-system · GitHub Actions 云端计算 · GitHub Pages 展示 · 多数据源自动降级。</div>
</main></body></html>"""
    index = output / "index.html"
    index.write_text(page, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    metadata = {
        "generated_at": generated_at.isoformat(),
        "mode": mode_label,
        "selection_rows": len(selected),
        "data_as_of": data_as_of,
        "market_snapshot_rows": selection_summary.get("market_snapshot_rows"),
        "prefilter_count": selection_summary.get("prefilter_count"),
        "has_backtest": bool(backtest_summary),
    }
    (output / "site-meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index
