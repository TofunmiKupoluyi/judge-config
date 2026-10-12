#!/usr/bin/env python3
"""Standalone backtest HTML for ONE hypothesis traded as a strategy — same structure as the Obsidian master backtest report
(per calendar year → monthly returns → metric table on the $10M reference book → key-metric panels → time-series charts), with
no note box at the top. Judge runs this for every tested hypothesis; the file is the "how would this single alpha have done"
exhibit next to the markdown report.

Input: a DAILY frame (one row per UTC day) with
  ret            daily return as a fraction of notional, net of funding and costs            (required)
  gross          average gross exposure that day, × notional                                  (optional; default NaN)
  delta, beta    average net delta / BTC-beta$ that day, × notional                           (optional)
  turnover       $ traded that day ÷ notional; req_turnover = requested before participation   (optional)
  costs          costs that day as a fraction of notional                                      (optional)
  funding        funding P&L that day as a fraction of notional                                (optional)
and optionally an HOURLY nav series (for the hourly max-drawdown column); without it the daily close drawdown is used.

    python3 tools/backtest_report.py --daily results/auto/<ID>_daily.csv --title "<ID> — <title> — standalone sleeve 2022-01 → 2026-09" \
                                     --out results/auto/<ID>_backtest.html [--hourly-nav results/auto/<ID>_nav_hourly.csv] [--label "<ID>"]
Python: build_backtest_html(daily_df, title, out_path, hourly_nav=None, label=None)
Chart.js is loaded from the CDN (open the file in a browser with network access). Only numpy/pandas required.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

ANN = 365
AUM = 10_000_000       # $ figures in the metric table / charts are on the $10M reference book
NOTIONAL = 100_000     # the "= $ on $100k" column of the per-year table
COLOR = "#2563eb"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _col(d, name):
    return d[name].astype(float) if name in d.columns else pd.Series(np.nan, index=d.index)


def _year_html(d, hourly_nav, label):
    rows = []
    for y in sorted(set(d.index.year)):
        r = d.loc[d.index.year == y, "ret"].dropna()
        if r.empty:
            continue
        if hourly_nav is not None and (hourly_nav.index.year == y).any():
            hn = hourly_nav[hourly_nav.index.year == y]; mdd = float((hn / hn.cummax() - 1).min()); ddlab = "Max DD (hourly)"
        else:
            nav = (1 + r).cumprod(); mdd = float((nav / nav.cummax() - 1).min()); ddlab = "Max DD (daily)"
        tot = float((1 + r).prod() - 1); sh = float(r.mean() / r.std() * np.sqrt(ANN)) if r.std() > 0 else float("nan")
        rows.append(f"<tr><td>{y}</td><td>{len(r)}</td><td>{tot:+.1%}</td><td>{tot * NOTIONAL:+,.0f}</td><td>{sh:.2f}</td><td>{mdd:+.1%}</td><td>{float(r.min()):+.1%}</td></tr>")
    return (f'<table><thead><tr><th>Year</th><th>Days</th><th>{label} return</th><th>= $ on $100k</th><th>Sharpe</th><th>{ddlab}</th><th>Worst day</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table>")


def _month_html(d):
    r = d["ret"].dropna(); m = (1 + r).groupby([r.index.year, r.index.month]).prod() - 1
    rows = []
    for y in sorted(set(r.index.year)):
        cells = []
        for mo in range(1, 13):
            v = m.get((y, mo))
            cells.append(f'<td class="{"pos" if v >= 0 else "neg"}">{v:+.1%}</td>' if v is not None and pd.notna(v) else "<td></td>")
        yt = float((1 + r[r.index.year == y]).prod() - 1)
        rows.append(f"<tr><td>{y}</td>{''.join(cells)}<td><b>{yt:+.1%}</b></td></tr>")
    return ('<table><thead><tr><th>Year</th>' + "".join(f"<th>{x}</th>" for x in MONTHS) + '<th>Year</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def _metrics(d):
    r = d["ret"].dropna(); nav = (1 + r).cumprod(); dd = nav / nav.cummax() - 1
    down = r[r < 0].std() * np.sqrt(ANN)
    to, rto = _col(d, "turnover"), _col(d, "req_turnover")
    out = {
        "Daily avg PNL ($)": r.mean() * AUM, "Total net PNL ($)": (nav.iloc[-1] - 1) * AUM, "Min daily PNL ($)": r.min() * AUM, "Max daily PNL ($)": r.max() * AUM,
        "Annualised Return ($)": r.mean() * ANN * AUM, "Annualised Volatility ($)": r.std() * np.sqrt(ANN) * AUM,
        "Sharpe": r.mean() / r.std() * np.sqrt(ANN) if r.std() > 0 else np.nan, "Sortino": r.mean() * ANN / down if down > 0 else np.nan,
        "Max Drawdown ($)": dd.min() * AUM,
        "Volume sent ($/day)": rto.mean() * AUM, "Volume traded ($/day)": to.mean() * AUM,
        "Fill ratio": (to.sum() / rto.sum()) if rto.sum() > 0 else np.nan,
        "Avg |beta| ($)": _col(d, "beta").abs().mean() * AUM, "Avg |delta| ($)": _col(d, "delta").abs().mean() * AUM,
        "Avg gross exposure ($)": _col(d, "gross").mean() * AUM, "Costs ($/day)": _col(d, "costs").mean() * AUM,
        "% up days": (r > 0).mean() * 100, "Maker ratio": 0.0,
    }
    return out


def _frames(d):
    r = d["ret"].fillna(0.0); nav = (1 + r).cumprod()
    return pd.DataFrame({
        "daily_net_pnl_$": r * AUM, "cumulative_pnl_$": (nav - 1) * AUM, "drawdown_$": (nav / nav.cummax() - 1) * AUM,
        "gross_exposure_$": _col(d, "gross") * AUM, "net_delta_$": _col(d, "delta") * AUM, "beta_$": _col(d, "beta") * AUM,
        "volume_traded_$": _col(d, "turnover") * AUM, "volume_sent_$": _col(d, "req_turnover") * AUM, "costs_$": _col(d, "costs") * AUM,
        "funding_pnl_$": _col(d, "funding") * AUM,
        "rolling_sharpe_90d": r.rolling(90).mean() / r.rolling(90).std() * np.sqrt(ANN), "rolling_ann_return_90d_$": r.rolling(90).mean() * ANN * AUM,
    })


def build_backtest_html(daily: pd.DataFrame, title: str, out_path: str, hourly_nav: pd.Series | None = None, label: str | None = None) -> str:
    d = daily.copy(); d.index = pd.to_datetime(d.index, utc=True); d = d.sort_index()
    if "ret" not in d.columns:
        raise ValueError("daily frame needs a 'ret' column (fraction of notional per day)")
    label = label or title.split(" — ")[0]
    M = _metrics(d); F = _frames(d)
    tbl = ('<table><thead><tr><th>Metric</th><th>' + label + '</th></tr></thead><tbody>'
           + "".join(f"<tr><td>{k}</td><td>{'—' if pd.isna(v) else f'{v:,.2f}'}</td></tr>" for k, v in M.items()) + "</tbody></table>")
    key = ["Daily avg PNL ($)", "Annualised Return ($)", "Sharpe", "Sortino", "Max Drawdown ($)", "Volume traded ($/day)", "Costs ($/day)", "Annualised Volatility ($)"]
    kb_html = "".join(f'<div class="panel"><canvas id="kb{i}"></canvas></div>' for i in range(len(key)))
    kb_js = "".join(f"""new Chart(document.getElementById('kb{i}'),{{type:'bar',data:{{labels:[{json.dumps(m)}],datasets:[{{label:{json.dumps(label)},data:[{0 if pd.isna(M[m]) else round(float(M[m]), 3)}],backgroundColor:'{COLOR}'}}]}},
options:{{responsive:true,plugins:{{title:{{display:true,text:{json.dumps(m)}}},legend:{{display:{str(i == 0).lower()}}}}},scales:{{y:{{beginAtZero:false,ticks:{{maxTicksLimit:6}}}},x:{{display:false}}}}}}}});""" for i, m in enumerate(key))
    labels = [t.strftime("%Y-%m-%d") for t in F.index]
    ts_html = "".join(f'<div class="panel wide"><canvas id="c{i}"></canvas></div>' for i in range(len(F.columns)))
    ts_js = "".join(f"""new Chart(document.getElementById('c{i}'),{{type:'line',data:{{labels:{json.dumps(labels)},datasets:[{{label:{json.dumps(label)},data:{json.dumps([None if pd.isna(v) else round(float(v), 4) for v in F[m]])},borderColor:'{COLOR}',borderWidth:1.2,pointRadius:0,spanGaps:false}}]}},
options:{{responsive:true,animation:false,plugins:{{title:{{display:true,text:'{m}'}},legend:{{display:false}}}},scales:{{x:{{ticks:{{maxTicksLimit:8}}}}}}}}}});""" for i, m in enumerate(F.columns))
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
body{{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;background:#f8fafc;color:#0f172a}} h1{{font-size:20px}}
table{{border-collapse:collapse;background:#fff;font-size:13px;margin-bottom:16px}} th,td{{border:1px solid #e2e8f0;padding:5px 10px;text-align:right}}
td:first-child,th:first-child{{text-align:left;font-weight:600}} th{{background:#f1f5f9}} .pos{{color:#059669}} .neg{{color:#dc2626}}
.panel{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px;margin-bottom:12px}}
.grid4{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}} .wide{{max-width:1100px}}
</style></head><body>
<h1>{title}</h1>
<h2 style="font-size:15px">Per calendar year</h2>{_year_html(d, hourly_nav, label)}
<h2 style="font-size:15px">Monthly returns</h2>{_month_html(d)}
<h2 style="font-size:15px">Metric table and charts ($10M reference book)</h2>{tbl}
<h2 style="font-size:15px">Key metrics — per-metric scale</h2><div class="grid4">{kb_html}</div>
{ts_html}
<script>{kb_js}{ts_js}</script></body></html>"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--daily", required=True, help="CSV/parquet with a date index and a 'ret' column (+ optional gross/delta/beta/turnover/req_turnover/costs/funding)")
    ap.add_argument("--title", required=True); ap.add_argument("--out", required=True); ap.add_argument("--hourly-nav", help="CSV/parquet with a datetime index and a 'nav' column"); ap.add_argument("--label")
    a = ap.parse_args()
    rd = (lambda p: pd.read_parquet(p)) if a.daily.endswith(".parquet") else (lambda p: pd.read_csv(p, index_col=0, parse_dates=True))
    d = rd(a.daily); hn = None
    if a.hourly_nav:
        h = pd.read_parquet(a.hourly_nav) if a.hourly_nav.endswith(".parquet") else pd.read_csv(a.hourly_nav, index_col=0, parse_dates=True)
        hn = h["nav"] if "nav" in h.columns else h.iloc[:, 0]; hn.index = pd.to_datetime(hn.index, utc=True)
    print("wrote", build_backtest_html(d, a.title, a.out, hn, a.label))


if __name__ == "__main__":
    main()
