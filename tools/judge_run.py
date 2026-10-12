#!/usr/bin/env python3
"""Judge's one command per framework round: validate → sweep (calibration only) → best → verification battery → pass bar → outputs.

    python3 tools/judge_run.py --config nos14_r1.json --ticket NOS-14 --round 1 --out out/NOS-14/r1 \
                               [--data s3://…/v1/binance/um | /local/mirror] [--eval-end 2026-09-30] [--max-grid 48] [--quick]

Outputs in --out:  <ticket>_r<n>_result.md   (the comment Judge posts: headline table first, bar checklist, sweep summary, next-step line)
                   <ticket>_r<n>_result.json (every number), <ticket>_r<n>_daily.csv (best config, full sample, risk framework)
                   <ticket>_r<n>_backtest.html (the exhibit), <ticket>_r<n>_best_config.json (the selected config, params resolved)
Honesty rules baked in: the sweep grid is built ONLY from the ranges and alternatives Scout declared; the best is chosen on the
calibration window (2022-01 → 2023-12) by net Sharpe under the risk framework; the evaluation window (2024-01 → EVAL_END) is
run once, for the chosen config, and reported as is. The grid size is printed on the result. The verification battery runs on the
chosen config only. The pass bar is fixed in BAR below (reference/PASS_BAR.md).
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import engine as E   # noqa: E402
from data import Data  # noqa: E402
from validate_config import validate  # noqa: E402

ANN = 365
CALIB = ("2022-01-01", "2023-12-31")
BAR = {"sharpe_min_both": 1.0, "eval_return_min": 0.0, "max_dd_max": 0.10, "sharpe_2x_min": 0.5, "placebo_t_max": 1.5, "plateau_sharpe_min": 0.7, "plateau_band": 0.5, "top5_share_max": 0.5}
REFERENCE = ["ref_funding_carry.json", "ref_illiquidity.json", "ref_taker_flow.json"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def stats(r):
    r = r.dropna()
    if len(r) < 20:
        return {"days": int(len(r)), "return": np.nan, "sharpe": np.nan, "sortino": np.nan, "max_dd": np.nan, "worst_day": np.nan, "hit": np.nan, "ann_vol": np.nan}
    nav = (1 + r).cumprod(); dd = nav / nav.cummax() - 1; dn = r[r < 0].std() * np.sqrt(ANN)
    return {"days": int(len(r)), "return": float(nav.iloc[-1] - 1), "ann_return": float(nav.iloc[-1] ** (ANN / len(r)) - 1), "sharpe": float(r.mean() / r.std() * np.sqrt(ANN)) if r.std() > 0 else np.nan,
            "sortino": float(r.mean() * ANN / dn) if dn > 0 else np.nan, "max_dd": float(dd.min()), "worst_day": float(r.min()), "hit": float((r > 0).mean()), "ann_vol": float(r.std() * np.sqrt(ANN))}


def win(daily, a, b):
    return daily[(daily.index > pd.Timestamp(a, tz="UTC")) & (daily.index <= pd.Timestamp(b, tz="UTC") + pd.Timedelta(days=1))]


def grid_from(cfg, max_grid, seed=11):
    """Cartesian grid over declared parameter ranges (5 points each incl. the value) × declared alternatives; capped by random sample."""
    axes = {}
    for name, p in (cfg.get("params") or {}).items():
        if "range" in p:
            lo, hi = p["range"]; pts = np.linspace(lo, hi, 5).tolist() + [p["value"]]
            if p.get("integer") or p.get("unit") in ("days", "hours", "count"):
                pts = sorted({int(round(x)) for x in pts})
            else:
                pts = sorted({round(float(x), 6) for x in pts})
            axes["params." + name] = pts
    for k, v in (cfg.get("alternatives") or {}).items():
        base = cfg
        for part in k.split("."):
            base = base[part]
        axes[k] = sorted(set(list(v) + [base]), key=str)
    keys = list(axes); combos = list(itertools.product(*[axes[k] for k in keys])) if keys else [()]
    if len(combos) > max_grid:
        rng = np.random.default_rng(seed); idx = rng.choice(len(combos), size=max_grid - 1, replace=False)
        base_combo = tuple(cfg["params"][k.split(".", 1)[1]]["value"] if k.startswith("params.") else axes[k][0] for k in keys)
        combos = [combos[i] for i in idx] + [base_combo]
    return keys, combos, len(list(itertools.product(*[axes[k] for k in keys])) if keys else [()])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True); ap.add_argument("--ticket", required=True); ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--out", required=True); ap.add_argument("--data", default=os.environ.get("BINANCE_DATA_ROOT", "s3://binance-data-546016014952-eu-west-2-an/v1/binance/um"))
    ap.add_argument("--eval-end", default=None, help="last completed month end by default"); ap.add_argument("--max-grid", type=int, default=48); ap.add_argument("--quick", action="store_true", help="skip reference-sleeve correlations")
    ap.add_argument("--start", default="2021-10-01")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8")); errs, warns = validate(cfg)
    if errs:
        print("\n".join("error: " + e for e in errs)); sys.exit(2)
    eval_end = a.eval_end or (pd.Timestamp.utcnow().normalize().replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    os.makedirs(a.out, exist_ok=True); tag = f"{a.ticket}_r{a.round}"
    data = Data(a.data, start=a.start, end=eval_end)
    # ---------------- sweep on calibration only
    keys, combos, full_size = grid_from(cfg, a.max_grid)
    log(f"sweep: {len(combos)} of {full_size} combinations over {keys or ['(no tunables)']}, calibration window only")
    rows = []
    for c in combos:
        ov = dict(zip(keys, c)); cc = E.with_params(cfg, ov)
        d, _, _ = E.run(cc, data, risk=True, window=CALIB); s = stats(d["ret"])
        rows.append({**{k: v for k, v in ov.items()}, "sharpe_calib": s["sharpe"], "return_calib": s["return"], "max_dd_calib": s["max_dd"], "turnover": float(d["turnover"].mean()) if len(d) else np.nan})
    sw = pd.DataFrame(rows); sw = sw.sort_values("sharpe_calib", ascending=False).reset_index(drop=True)
    best_ov = {k: sw.iloc[0][k] for k in keys}; best_ov = {k: (int(v) if isinstance(v, (np.integer,)) or (isinstance(v, float) and float(v).is_integer() and k.startswith("params.") and (cfg["params"][k.split(".", 1)[1]].get("integer") or cfg["params"][k.split(".", 1)[1]].get("unit") in ("days", "hours", "count"))) else (v.item() if hasattr(v, "item") else v)) for k, v in best_ov.items()}
    best = E.with_params(cfg, best_ov); best["experiment"] = a.ticket; best["round"] = a.round
    # plateau: neighbours whose every swept param is within ±band of the best's value
    plateau = np.nan
    if keys:
        def near(row):
            for k in keys:
                if k.startswith("params."):
                    b = float(best_ov[k]); v = float(row[k])
                    if b != 0 and abs(v - b) / abs(b) > BAR["plateau_band"]:
                        return False
                elif row[k] != best_ov[k]:
                    return False
            return True
        nb = sw[sw.apply(near, axis=1)]; plateau = float(nb["sharpe_calib"].median()) if len(nb) > 1 else float(sw.iloc[0]["sharpe_calib"])
    # ---------------- the chosen config on the full sample (calib + eval), risk framework
    log("best config: full sample under the risk framework")
    daily, attr, n_rebal = E.run(best, data, risk=True); daily = daily[daily.index >= pd.Timestamp(CALIB[0], tz="UTC")]
    H = {"calibration 2022-01 → 2023-12": stats(win(daily, *CALIB)["ret"]), f"evaluation 2024-01 → {eval_end}": stats(win(daily, "2024-01-01", eval_end)["ret"]), "full": stats(daily["ret"])}
    for y in sorted(set(daily.index.year)):
        H[str(y)] = stats(daily[daily.index.year == y]["ret"])
    turn = float(daily["turnover"].mean()); cost_yr = float(daily["costs"].mean() * ANN); fund_yr = float(daily["funding"].mean() * ANN)
    # ---------------- verification battery (chosen config only)
    log("verification: raw sleeve, 2x costs, placebo, concentration, reference sleeves")
    raw, _, _ = E.run(best, data, risk=False); s_raw = stats(raw["ret"])
    d2x, _, _ = E.run(best, data, risk=True, cost_mult=2.0); s_2x = stats(d2x["ret"])
    pl = []
    for sd in (1, 2, 3):
        dp, _, _ = E.run(best, data, risk=True, shuffle_seed=sd); r = dp["ret"].dropna(); pl.append(float(r.mean() / r.std() * np.sqrt(len(r))) if r.std() > 0 else np.nan)
    placebo_t = float(np.nanmean(pl))
    pos_attr = attr[attr > 0]; top5 = float(pos_attr.sort_values(ascending=False).head(5).sum() / pos_attr.sum()) if pos_attr.sum() > 0 else np.nan
    top5_names = attr.sort_values(ascending=False).head(5).round(4).to_dict()
    nav = (1 + daily["ret"]).cumprod(); worst = {f"worst_{k}d": float((nav / nav.shift(k) - 1).min()) for k in (1, 7, 30)}
    ref_corr = {}
    if not a.quick:
        for rf in REFERENCE:
            p = os.path.join(HERE, "..", "examples", rf)
            if os.path.exists(p):
                try:
                    rc = json.load(open(p)); dr, _, _ = E.run(rc, data, risk=True); ref_corr[rf.replace("ref_", "").replace(".json", "")] = float(daily["ret"].corr(dr["ret"].reindex(daily.index)))
                except Exception as e:  # noqa: BLE001
                    ref_corr[rf] = f"error: {e}"
    # ---------------- bar
    c_, e_, f_ = H["calibration 2022-01 → 2023-12"], H[f"evaluation 2024-01 → {eval_end}"], H["full"]
    checks = {
        f"Sharpe ≥ {BAR['sharpe_min_both']} in both windows": (c_["sharpe"] >= BAR["sharpe_min_both"] and e_["sharpe"] >= BAR["sharpe_min_both"], f"calib {c_['sharpe']:+.2f} / eval {e_['sharpe']:+.2f}"),
        "evaluation return > 0": (e_["return"] > BAR["eval_return_min"], f"{e_['return']:+.1%}"),
        f"max drawdown ≤ {BAR['max_dd_max']:.0%} (full)": (f_["max_dd"] >= -BAR["max_dd_max"], f"{f_['max_dd']:.1%}"),
        f"Sharpe > {BAR['sharpe_2x_min']} at 2× costs (full)": (s_2x["sharpe"] > BAR["sharpe_2x_min"], f"{s_2x['sharpe']:+.2f}"),
        f"placebo (shuffled names) |t| < {BAR['placebo_t_max']}": (abs(placebo_t) < BAR["placebo_t_max"], f"{placebo_t:+.2f} (3 seeds)"),
        f"parameter plateau: neighbours within ±{BAR['plateau_band']:.0%} keep calib Sharpe ≥ {BAR['plateau_sharpe_min']}": (not np.isfinite(plateau) or plateau >= BAR["plateau_sharpe_min"], f"median {plateau:+.2f}" if np.isfinite(plateau) else "no tunables"),
        f"top-5 names ≤ {BAR['top5_share_max']:.0%} of positive P&L": (np.isfinite(top5) and top5 <= BAR["top5_share_max"], f"{top5:.0%}" if np.isfinite(top5) else "n/a"),
    }
    passed = all(v[0] for v in checks.values()); verdict = "PASS" if passed else "FAIL"
    failed = [k for k, v in checks.items() if not v[0]]
    # ---------------- "For Scout": plain-words diagnosis in the fixed vocabulary of TICKET_FORMAT.md (code-computed, no engine numbers)
    ret_yr = float(daily["ret"].mean() * ANN); fs = []
    if (not np.isfinite(c_["sharpe"]) or c_["sharpe"] < 0.5) and (not np.isfinite(e_["sharpe"]) or e_["sharpe"] < 0.5):
        fs.append("no signal in either window — the ranking does not separate winners from losers at this horizon")
    elif c_["sharpe"] >= 1.0 and (e_["sharpe"] < 0.5 or e_["return"] <= 0):
        fs.append("signal only early — it worked in the calibration years and faded afterwards; the participants who created it may be gone")
    elif e_["sharpe"] >= 1.0 and c_["sharpe"] < 0.5:
        fs.append("signal only late — absent in the calibration years; one episode or a new participant, not yet a durable mechanism")
    if s_2x["sharpe"] <= BAR["sharpe_2x_min"] or (ret_yr > 0 and cost_yr > 0.5 * ret_yr):
        fs.append("costs eat it — the rebalancing bill is a large share of what the spread earns; the construction trades too often for the size of the effect")
    if fund_yr < 0 and ret_yr > 0 and abs(fund_yr) > 0.5 * ret_yr:
        fs.append("funding eats it — the legs pay more funding than the spread earns; the ranking selects names on the wrong side of the funding sort")
    if np.isfinite(top5) and top5 > BAR["top5_share_max"]:
        fs.append("concentrated — a handful of names make the money; the effect is not broad across the universe")
    for k, v in ref_corr.items():
        if isinstance(v, float) and abs(v) >= 0.6:
            fs.append(f"tracks a known sleeve ({k}) — the ranking largely reproduces an existing construction")
    if np.isfinite(plateau) and plateau < BAR["plateau_sharpe_min"]:
        fs.append("not robust to its parameters — neighbouring settings lose most of the result; the chosen point is a knife edge")
    if abs(placebo_t) >= BAR["placebo_t_max"]:
        fs.append("fails the placebo — randomly relabelled names do about as well; the observable reacts to returns rather than predicting them")
    if f_["max_dd"] < -BAR["max_dd_max"]:
        fs.append("drawdown too deep — the construction concentrates risk in time; a smoother selection or a longer horizon class")
    if passed:
        watch = [f"{y} was negative" for y, v in H.items() if y.isdigit() and np.isfinite(v["return"]) and v["return"] < 0]
        watch += [f"correlation {v:+.2f} with the {k} sleeve" for k, v in ref_corr.items() if isinstance(v, float) and abs(v) >= 0.4]
        for_scout = "passed the bar" + ("; watch: " + ", ".join(watch) if watch else "")
    else:
        for_scout = "; ".join(fs) if fs else "failed the bar without a single dominant cause — see the bar lines above"
    # ---------------- outputs
    def row(k, v):
        if not np.isfinite(v.get("sharpe", np.nan)):
            return f"| {k} | — | — | — | — | — | — | {v['days']} |"
        return f"| {k} | {v['return']:+.1%} | {v['sharpe']:+.2f} | {v['sortino']:+.2f} | {v['max_dd']:.1%} | {v['worst_day']:+.2%} | {v['hit']:.0%} | {v['days']} |"
    md = [f"[JUDGE RESULT {a.round}/5 — {verdict}]", f"**{a.ticket} — {cfg['hypothesis']}**", f"Framework {a.round}: {cfg['framework'][:220]}{'…' if len(cfg['framework']) > 220 else ''}", "",
          "**Headline** (best config of the sweep, selected on calibration only; full risk framework; net of costs and funding)", "",
          "| window / year | return | Sharpe | Sortino | max DD | worst day | hit rate | days |", "|---|---|---|---|---|---|---|---|"] + [row(k, v) for k, v in H.items()] + [
          f"\nTurnover {turn:.3f} × N/day · cost drag {cost_yr:.2%}/yr · funding P&L {fund_yr:+.2%}/yr · rebalances {n_rebal} · raw sleeve (no caps/hedge/governor) Sharpe {s_raw['sharpe']:+.2f} · worst 1/7/30 d {worst['worst_1d']:+.1%} / {worst['worst_7d']:+.1%} / {worst['worst_30d']:+.1%}",
          "", "**Bar**", ""] + [f"- {'✓' if v[0] else '✗'} {k}: {v[1]}" for k, v in checks.items()] + [
          "", f"**Sweep** {len(combos)} of {full_size} combinations over {', '.join(keys) if keys else 'no tunables'}; best = " + (", ".join(f"{k.split('.')[-1]}={v}" for k, v in best_ov.items()) if best_ov else "the submitted config") +
          f"; calib Sharpe of the grid: best {sw['sharpe_calib'].max():+.2f}, median {sw['sharpe_calib'].median():+.2f}, worst {sw['sharpe_calib'].min():+.2f}",
          f"**Concentration** top-5 names {top5:.0%} of positive P&L: " + ", ".join(f"{k} {v:+.2%}" for k, v in top5_names.items()) if np.isfinite(top5) else "**Concentration** n/a",
          ("**Redundancy** daily-return correlation with reference sleeves: " + ", ".join(f"{k} {v:+.2f}" if isinstance(v, float) else f"{k} {v}" for k, v in ref_corr.items())) if ref_corr else "",
          "", f"**For Scout** {for_scout}",
          "", (f"**Next** → Human Review (bar passed)." if passed else f"**Next** → Critiqued: failed {len(failed)} bar line(s) — " + "; ".join(failed) + (". Scout: a different trade framework for the same hypothesis (round " + str(a.round + 1) + "/5)." if a.round < 5 else ". This was round 5/5 → Discarded.")),
          f"Exhibit: {tag}_backtest.html · numbers: {tag}_result.json · config: {tag}_best_config.json"]
    md = "\n".join(x for x in md if x is not None)
    base = os.path.join(a.out, tag)
    open(base + "_result.md", "w", encoding="utf-8").write(md + "\n"); daily.to_csv(base + "_daily.csv")
    json.dump({"ticket": a.ticket, "round": a.round, "verdict": verdict, "failed": failed, "eval_end": eval_end, "headline": H, "bar": {k: {"pass": bool(v[0]), "value": v[1]} for k, v in checks.items()},
               "sweep": {"keys": keys, "n_run": len(combos), "n_full": full_size, "best": best_ov, "plateau_median_sharpe": plateau, "top10": sw.head(10).to_dict("records")},
               "raw_sleeve": s_raw, "two_x_costs": s_2x, "placebo_t": placebo_t, "placebo_seeds": pl, "top5_share": top5, "top5_names": top5_names, "worst": worst, "reference_corr": ref_corr,
               "turnover_per_day": turn, "cost_drag_per_yr": cost_yr, "funding_per_yr": fund_yr, "n_rebalances": n_rebal, "warnings": warns, "for_scout": for_scout},
              open(base + "_result.json", "w"), indent=1, default=lambda x: float(x) if isinstance(x, (np.floating, np.integer)) else str(x))
    json.dump(best, open(base + "_best_config.json", "w"), indent=1, ensure_ascii=False)
    try:
        import backtest_report as BR
        BR.build_backtest_html(daily[["ret", "gross", "delta", "beta", "turnover", "req_turnover", "costs", "funding"]], f"{a.ticket} round {a.round} — {cfg['hypothesis'][:70]} — {daily.index[0].date()} → {daily.index[-1].date()}", base + "_backtest.html", label=a.ticket)
    except Exception as e:  # noqa: BLE001
        log(f"exhibit skipped: {e}")
    print(md); log(f"wrote {base}_result.md / .json / _daily.csv / _backtest.html / _best_config.json")


if __name__ == "__main__":
    main()
