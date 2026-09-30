from __future__ import annotations

import argparse
import html
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .backtest import BacktestEngine
from .production import _fetch_one_history

STRATEGY_VERSION = "market-factors-v1"
_START = "<!-- SHADOW-TRADING-START -->"
_END = "<!-- SHADOW-TRADING-END -->"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _selected_signal(selected: pd.DataFrame, observed_at: pd.Timestamp) -> pd.DataFrame:
    required = {"symbol", "as_of", "board", "is_st", "rank", "composite_score"}
    missing = sorted(required - set(selected.columns))
    if missing:
        raise ValueError(f"selected.csv missing columns required for shadow trading: {missing}")

    frame = selected.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["signal_date"] = pd.to_datetime(frame["as_of"], errors="raise").dt.normalize()
    dates = frame["signal_date"].drop_duplicates()
    if len(dates) != 1:
        raise ValueError("selected.csv must contain exactly one signal date")
    if frame.empty:
        raise ValueError("selected.csv is empty")

    weight = 1.0 / len(frame)
    frame["target_weight"] = weight
    frame["observed_at"] = observed_at.isoformat()
    frame["strategy_version"] = STRATEGY_VERSION
    if "name" not in frame:
        frame["name"] = ""
    if "screen_source" not in frame:
        frame["screen_source"] = "unknown"

    columns = [
        "signal_date",
        "symbol",
        "name",
        "board",
        "is_st",
        "rank",
        "composite_score",
        "target_weight",
        "screen_source",
        "strategy_version",
        "observed_at",
    ]
    return frame.loc[:, columns].sort_values(["rank", "symbol"]).reset_index(drop=True)


def capture_signal(
    selected_file: str | Path,
    ledger_root: str | Path,
    *,
    observed_at: pd.Timestamp | None = None,
) -> Path:
    """Persist one immutable daily selection snapshot.

    A rerun may reuse an identical snapshot, but it may not rewrite a historical
    signal with different constituents, scores or weights.
    """
    selected = pd.read_csv(selected_file, dtype={"symbol": str})
    observed = observed_at or pd.Timestamp.now(tz="UTC")
    if observed.tzinfo is None:
        observed = observed.tz_localize("UTC")
    else:
        observed = observed.tz_convert("UTC")
    signal = _selected_signal(selected, observed)
    signal_date = pd.Timestamp(signal["signal_date"].iloc[0]).date().isoformat()

    output = Path(ledger_root) / "signals" / f"{signal_date}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        existing = pd.read_csv(output, dtype={"symbol": str})
        compare_columns = [column for column in signal.columns if column != "observed_at"]
        existing_compare = existing.loc[:, compare_columns].copy()
        candidate_compare = signal.loc[:, compare_columns].copy()
        existing_compare["signal_date"] = pd.to_datetime(existing_compare["signal_date"]).dt.strftime("%Y-%m-%d")
        candidate_compare["signal_date"] = pd.to_datetime(candidate_compare["signal_date"]).dt.strftime("%Y-%m-%d")
        if not existing_compare.reset_index(drop=True).equals(candidate_compare.reset_index(drop=True)):
            raise RuntimeError(
                f"Refusing to rewrite immutable shadow signal for {signal_date}; "
                "the newly generated selection differs from the first observation"
            )
        return output

    signal.to_csv(output, index=False, date_format="%Y-%m-%d")
    return output


def load_signal_history(ledger_root: str | Path) -> pd.DataFrame:
    files = sorted((Path(ledger_root) / "signals").glob("*.csv"))
    if not files:
        raise FileNotFoundError("No shadow signal snapshots found")
    frames = [pd.read_csv(path, dtype={"symbol": str}) for path in files]
    history = pd.concat(frames, ignore_index=True)
    history["symbol"] = history["symbol"].astype(str).str.zfill(6)
    history["signal_date"] = pd.to_datetime(history["signal_date"]).dt.normalize()
    history["target_weight"] = pd.to_numeric(history["target_weight"], errors="raise")
    return history.sort_values(["signal_date", "rank", "symbol"]).reset_index(drop=True)


def _fetch_shadow_bars(
    signals: pd.DataFrame,
    *,
    market_as_of: pd.Timestamp,
    cache_root: Path,
    workers: int,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, str]]]:
    start = pd.Timestamp(signals["signal_date"].min()).normalize()
    symbols = sorted(set(signals["symbol"].astype(str).str.zfill(6)))
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    warnings: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
        futures = {
            pool.submit(
                _fetch_one_history,
                symbol,
                start=start,
                end=market_as_of,
                cache_root=cache_root,
                adjust="",
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            returned, frame, provider, warning = future.result()
            if frame is None:
                raise RuntimeError(f"Missing shadow history for {returned}: {warning}")
            trimmed = frame[
                (pd.to_datetime(frame["trade_date"]) >= start)
                & (pd.to_datetime(frame["trade_date"]) <= market_as_of)
            ].copy()
            if trimmed.empty:
                raise RuntimeError(f"No shadow bars in evaluation window for {returned}")
            bars_by_symbol[returned] = trimmed
            if warning:
                warnings.append({"symbol": returned, "provider": provider or "unknown", "warning": warning})

    missing = sorted(set(symbols) - set(bars_by_symbol))
    if missing:
        raise RuntimeError(f"Shadow histories missing symbols: {missing}")
    return bars_by_symbol, warnings


def evaluate_signals(
    signals: pd.DataFrame,
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    initial_cash: float = 1_000_000.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    engine_signals = signals.rename(columns={"signal_date": "trade_date"}).loc[
        :, ["trade_date", "symbol", "target_weight", "board", "is_st"]
    ]
    result = BacktestEngine(initial_cash=initial_cash).run(bars_by_symbol, engine_signals)
    equity = result.equity_curve.copy()
    trades = result.trades.copy()
    daily_returns = equity["equity"].pct_change().dropna() if len(equity) > 1 else pd.Series(dtype=float)
    metrics: dict[str, object] = dict(result.metrics)
    metrics.update(
        {
            "initial_cash": initial_cash,
            "latest_equity": float(equity["equity"].iloc[-1]),
            "signal_dates": int(signals["signal_date"].nunique()),
            "signal_rows": len(signals),
            "distinct_symbols": int(signals["symbol"].nunique()),
            "trade_count": len(trades),
            "observed_return_days": len(daily_returns),
            "daily_win_rate": float((daily_returns > 0).mean()) if len(daily_returns) else None,
            "latest_trade_date": pd.Timestamp(equity["trade_date"].iloc[-1]).date().isoformat(),
        }
    )
    days = int(metrics["observed_return_days"])
    if days < 20:
        phase = "collecting"
    elif days < 60:
        phase = "first_review"
    else:
        phase = "rolling_validation"
    metrics["validation_phase"] = phase
    metrics["real_money_ready"] = False
    return equity, trades, metrics


def _fmt_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:.2f}%"


def _render_page(
    signals: pd.DataFrame,
    metrics: dict[str, object],
    output: Path,
    *,
    evaluation_error: str | None = None,
) -> None:
    latest_date = pd.Timestamp(signals["signal_date"].max()).date().isoformat()
    latest = signals[signals["signal_date"] == signals["signal_date"].max()].copy()
    rows = "".join(
        "<tr>"
        f"<td>{int(float(row['rank']))}</td>"
        f"<td>{html.escape(str(row['symbol']).zfill(6))}</td>"
        f"<td>{html.escape(str(row.get('name', '')))}</td>"
        f"<td>{float(row['target_weight']) * 100:.1f}%</td>"
        "</tr>"
        for _, row in latest.iterrows()
    )
    error_note = ""
    if evaluation_error:
        error_note = (
            '<div class="notice danger">本次绩效重算暂时失败：'
            + html.escape(evaluation_error)
            + "。当天原始信号仍已保存，不会因此改写历史。</div>"
        )

    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>影子实盘</title><style>
:root{{--bg:#f4f6f8;--panel:#fff;--text:#17212b;--muted:#65717d;--line:#dce2e8;--accent:#1f5f8b}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text)}}
.container{{width:min(1100px,calc(100% - 24px));margin:0 auto;padding:24px 0 54px}}a{{color:var(--accent);text-decoration:none}}.hero,.panel,.metric{{background:var(--panel);border:1px solid var(--line);border-radius:14px}}.hero{{padding:24px}}h1{{margin:8px 0}}.sub{{color:var(--muted);line-height:1.65}}.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}}.metric{{padding:14px}}.metric span{{display:block;color:var(--muted);font-size:12px}}.metric strong{{display:block;font-size:20px;margin-top:5px}}.panel{{padding:18px;margin-top:14px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left}}.notice{{border-left:4px solid #d59a42;background:#fffaf1;padding:12px 14px;border-radius:8px;line-height:1.6}}.danger{{border-left-color:#b24b4b;background:#fff5f5}}
</style></head><body><main class="container">
<section class="hero"><a href="index.html">← 返回主排名</a><h1>影子实盘 · 前瞻验证</h1><div class="sub">每天收盘后的 Top10 信号首次观察后即锁定；目标组合等权，信号最早在下一交易日开盘执行，并计入 A 股 T+1、涨跌停、停牌、佣金、印花税与滑点规则。该页面用于验证系统，不代表真实资金建议。</div></section>
<section class="metrics"><div class="metric"><span>验证阶段</span><strong>{html.escape(str(metrics.get('validation_phase', 'collecting')))}</strong></div><div class="metric"><span>累计收益</span><strong>{_fmt_pct(metrics.get('total_return'))}</strong></div><div class="metric"><span>最大回撤</span><strong>{_fmt_pct(metrics.get('max_drawdown'))}</strong></div><div class="metric"><span>日胜率</span><strong>{_fmt_pct(metrics.get('daily_win_rate'))}</strong></div><div class="metric"><span>已观察收益日</span><strong>{int(metrics.get('observed_return_days', 0) or 0)}</strong></div><div class="metric"><span>交易笔数</span><strong>{int(metrics.get('trade_count', 0) or 0)}</strong></div></section>
{error_note}<section class="panel"><div class="notice">前 20 个有效交易日只收集证据，不根据短期盈亏调参；20 日进行第一次检查，60 日以后再进入更严格的滚动验证。系统不会因为回测/模拟结果不好而删除历史信号。</div></section>
<section class="panel"><h2>{latest_date} 锁定信号</h2><table><thead><tr><th>排名</th><th>代码</th><th>名称</th><th>目标权重</th></tr></thead><tbody>{rows}</tbody></table></section>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


def _patch_main_index(index_path: Path, metrics: dict[str, object]) -> None:
    if not index_path.exists():
        return
    block = f"""{_START}
  <section class="panel" id="shadow-trading">
    <h2>影子实盘验证</h2>
    <div class="subtitle">累计锁定 {int(metrics.get('signal_dates', 0) or 0)} 个交易日信号，已观察 {int(metrics.get('observed_return_days', 0) or 0)} 个收益日；当前阶段 {html.escape(str(metrics.get('validation_phase', 'collecting')))}。真实资金条件尚未判定通过。</div>
    <div class="notice"><a href="shadow.html">查看模拟组合收益、回撤和每日锁定信号 →</a></div>
  </section>
{_END}"""
    text = index_path.read_text(encoding="utf-8")
    if _START in text and _END in text:
        prefix, tail = text.split(_START, 1)
        _, suffix = tail.split(_END, 1)
        text = prefix + block + suffix
    else:
        text = text.replace("</main>", block + "\n</main>", 1)
    index_path.write_text(text, encoding="utf-8")


def run_shadow_pipeline(
    *,
    selected_file: str | Path = "reports/production/selection/selected.csv",
    ledger_root: str | Path = "research/shadow",
    output_dir: str | Path = "reports/production/shadow",
    site_dir: str | Path = "site",
    cache_root: str | Path = "data/cache",
    initial_cash: float = 1_000_000.0,
    workers: int = 2,
) -> dict[str, object]:
    signal_file = capture_signal(selected_file, ledger_root)
    signals = load_signal_history(ledger_root)
    market_as_of = pd.Timestamp(signals["signal_date"].max()).normalize()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    signals.to_csv(output / "signal_history.csv", index=False, date_format="%Y-%m-%d")

    evaluation_error: str | None = None
    metrics: dict[str, object]
    try:
        bars, warnings = _fetch_shadow_bars(
            signals,
            market_as_of=market_as_of,
            cache_root=Path(cache_root),
            workers=workers,
        )
        equity, trades, metrics = evaluate_signals(
            signals,
            bars,
            initial_cash=initial_cash,
        )
        equity.to_csv(output / "equity_curve.csv", index=False, date_format="%Y-%m-%d")
        trades.to_csv(output / "trades.csv", index=False, date_format="%Y-%m-%d")
        metrics["history_warnings"] = warnings
        metrics["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 - preserve today's immutable signal even if evaluation is degraded
        evaluation_error = f"{type(exc).__name__}: {exc}"
        metrics = {
            "status": "degraded",
            "validation_phase": "collecting",
            "real_money_ready": False,
            "signal_dates": int(signals["signal_date"].nunique()),
            "signal_rows": len(signals),
            "distinct_symbols": int(signals["symbol"].nunique()),
            "observed_return_days": 0,
            "trade_count": 0,
            "total_return": None,
            "max_drawdown": None,
            "daily_win_rate": None,
            "evaluation_error": evaluation_error,
        }

    metrics.update(
        {
            "strategy_version": STRATEGY_VERSION,
            "signal_file": str(signal_file),
            "latest_signal_date": market_as_of.date().isoformat(),
            "execution_rule": "signal at close, earliest execution next trading-day open",
            "weighting": "equal_weight_top10",
            "optimization_policy": "no parameter changes before fixed review windows",
        }
    )
    _write_json(output / "metrics.json", metrics)
    _render_page(signals, metrics, Path(site_dir) / "shadow.html", evaluation_error=evaluation_error)
    _patch_main_index(Path(site_dir) / "index.html", metrics)
    return metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.shadow_trading")
    parser.add_argument("--selected-file", default="reports/production/selection/selected.csv")
    parser.add_argument("--ledger-root", default="research/shadow")
    parser.add_argument("--output-dir", default="reports/production/shadow")
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--workers", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    metrics = run_shadow_pipeline(
        selected_file=args.selected_file,
        ledger_root=args.ledger_root,
        output_dir=args.output_dir,
        site_dir=args.site_dir,
        cache_root=args.cache_root,
        initial_cash=args.initial_cash,
        workers=args.workers,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
