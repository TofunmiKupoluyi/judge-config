#!/usr/bin/env python3
"""Judge's engine layer. Since 2026-10-12 the headline simulator is OBSIDIAN'S PRODUCTION REPLICA (tools/obsidian_engine.py, a verbatim
copy of backtest/engine_v2.py) run hourly on the panels of tools/panels.py — see `run_hourly` at the bottom. This file keeps:

  build_signal   the trade-config DSL evaluated on the point-in-time fields of the config's grid (tools/data.py: daily, or hourly when
                 schedule.resolution is "1h"); "$param" references resolved
  build_targets  the positions rule (top/bottom N, quantile, threshold, proportional; equal / signal / rank weighting; side_gross_pct or
                 per_name_bps; holding-period averaging; execution delay) → target weights on that grid, fraction of notional, pre-risk
  simulate/run   the original DAILY simulator with a re-implementation of the risk framework — kept ONLY for the raw-sleeve diagnostic
                 (risk=False: costs and funding, no caps / hedge) and for offline experiments; judge_run.py never uses it with risk=True.
                 An hourly config's raw sleeve is `run_raw_hourly`
  run_hourly     the headline: targets (daily held 24 h, or hourly as they are) → hourly `extra` panel → obsidian_engine.run with caps, max-loss tiers, GMV cap, no-trade
                 band, 4-hour resize gate, participation cap, BTC hedge (2% band), fee + spread tiers + impact, per-print funding;
                 drawdown governors OFF (decision 2026-10-12)
"""
from __future__ import annotations

import copy
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RISK = json.load(open(os.path.join(HERE, "..", "reference", "RISK_FRAMEWORK.json")))
BTC = "BTCUSDT"
REBAL_DAYS = {"1d": 1, "2d": 2, "3d": 3, "1w": 7, "2w": 14}
REBAL_HOURS = {"1h": 1, "2h": 2, "4h": 4, "6h": 6, "8h": 8, "12h": 12, "1d": 24}     # resolution "1h": entries at hours divisible by n (UTC)


# ----------------------------------------------------------------------------------------------------------- signal DSL
def _resolve(cfg, v):
    if isinstance(v, str) and v.startswith("$"):
        return float(cfg["params"][v[1:]]["value"])
    return v


def _int(cfg, v):
    return int(round(float(_resolve(cfg, v))))


def build_signal(cfg, data):
    """Evaluate signal.steps → dict of named daily frames; returns (output_frame, all_series)."""
    S = {}
    for st in cfg["signal"]["steps"]:
        op = st["op"]; out = st["out"]
        if op == "field":
            S[out] = data.field(st["name"]).copy()
        elif op == "const":
            S[out] = pd.DataFrame(float(_resolve(cfg, st["value"])), index=data.dates, columns=data.symbols)
        elif op in ("rolling_mean", "rolling_sum", "rolling_std", "rolling_max", "rolling_min", "rolling_median"):
            w = _int(cfg, st["window"]); mp = _int(cfg, st["min_periods"]) if "min_periods" in st else max(1, int(np.ceil(w * 0.7)))
            r = S[st["in"]].rolling(w, min_periods=mp); S[out] = getattr(r, op.split("_")[1])()
        elif op == "ewm_mean":
            S[out] = S[st["in"]].ewm(span=_int(cfg, st["window"]), min_periods=_int(cfg, st.get("min_periods", 1))).mean()
        elif op == "lag":
            S[out] = S[st["in"]].shift(_int(cfg, st["window"]))
        elif op == "diff":
            S[out] = S[st["in"]].diff(_int(cfg, st["window"]))
        elif op == "pct_change":
            S[out] = S[st["in"]].pct_change(_int(cfg, st["window"]), fill_method=None)
        elif op == "zscore_time":
            w = _int(cfg, st["window"]); x = S[st["in"]]; S[out] = (x - x.rolling(w, min_periods=max(5, w // 2)).mean()) / x.rolling(w, min_periods=max(5, w // 2)).std()
        elif op == "log":
            S[out] = np.log(S[st["in"]].where(S[st["in"]] > 0))
        elif op == "abs":
            S[out] = S[st["in"]].abs()
        elif op == "neg":
            S[out] = -S[st["in"]]
        elif op == "sign":
            S[out] = np.sign(S[st["in"]])
        elif op == "clip":
            S[out] = S[st["in"]].clip(lower=_resolve(cfg, st.get("lo")), upper=_resolve(cfg, st.get("hi")))
        elif op in ("add", "sub", "mul", "div", "pow"):
            a, b = S[st["a"]], S[st["b"]]
            S[out] = {"add": a + b, "sub": a - b, "mul": a * b, "div": a / b.replace(0, np.nan), "pow": a ** b}[op]
        elif op == "cross_rank":
            S[out] = S[st["in"]].rank(axis=1, pct=True)
        elif op == "cross_zscore":
            x = S[st["in"]]; S[out] = x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1).replace(0, np.nan), axis=0)
        elif op == "cross_demean":
            x = S[st["in"]]; S[out] = x.sub(x.mean(axis=1), axis=0)
        elif op in ("gt", "lt"):
            t = float(_resolve(cfg, st["threshold"])); x = S[st["in"]]
            S[out] = ((x > t) if op == "gt" else (x < t)).astype(float).where(x.notna())
        elif op in ("and", "or"):
            a, b = S[st["a"]].fillna(0) > 0, S[st["b"]].fillna(0) > 0; S[out] = ((a & b) if op == "and" else (a | b)).astype(float)
        elif op == "where":
            c = S[st["cond"]].fillna(0) > 0; S[out] = S[st["then"]].where(c, S[st["else"]])
        else:
            raise ValueError(f"unknown op {op}")
    return S[cfg["signal"]["output"]], S


# ----------------------------------------------------------------------------------------------------------- targets
def resolution(cfg):
    return cfg["schedule"].get("resolution", "1d")


def _schedule_rows(cfg, dates):
    """(entry row positions, holding rows, delay rows) on the config's grid."""
    sch = cfg["schedule"]
    if resolution(cfg) == "1h":
        if sch.get("rebalance_hours"):
            mask = dates.hour.isin([int(h) for h in sch["rebalance_hours"]])
        else:
            mask = dates.hour % REBAL_HOURS[sch["rebalance"]] == 0
        hold = _int(cfg, sch["holding_hours"]) if "holding_hours" in sch else REBAL_HOURS[sch["rebalance"]]
        return np.flatnonzero(mask), hold, int(sch.get("delay_hours", 0))
    n = REBAL_DAYS[sch["rebalance"]]
    if n >= 7 and sch.get("anchor", "monday") == "monday":
        rows = np.flatnonzero(dates.dayofweek == 0)[::n // 7]
    else:
        rows = np.arange(0, len(dates), n)
    hold = _int(cfg, sch["holding_days"]) if "holding_days" in sch else n
    return rows, hold, int(sch.get("delay_days", 0))


def _first_ranks(v, descending):
    """1-based ranks with ties broken by position (pandas method="first")."""
    order = np.argsort(-v if descending else v, kind="stable"); r = np.empty(len(v)); r[order] = np.arange(1, len(v) + 1); return r


def build_targets(cfg, data, signal, universe):
    """Target weight frame on the signal's grid (fraction of notional, pre-risk) from the positions rule, with holding-period averaging.
    Row i is the book intended from row i on; entries are averaged over their holding rows."""
    pos = cfg["positions"]; mode = pos["mode"]; weighting = pos.get("weighting", "equal")
    side = float(pos.get("side_gross_pct", 50)) / 100.0; per_name = pos.get("per_name_bps")
    do_long, do_short = pos.get("long", True), pos.get("short", True)
    dates = data.dates; rows, hold, delay = _schedule_rows(cfg, dates)
    sig = signal.reindex(index=dates, columns=data.symbols).where(universe.reindex(index=dates, columns=data.symbols).fillna(False)).to_numpy(dtype="float64")
    n_param = max(1, _int(cfg, pos["n"])) if mode == "top_bottom_n" else None
    q = float(_resolve(cfg, pos["quantile"])) if mode == "quantile" else None
    T = np.zeros(sig.shape); cnt = np.zeros(len(dates)); n_entries = 0
    for i in rows:
        row = sig[i]; idx = np.flatnonzero(~np.isnan(row)); s = row[idx]; m = len(s)
        if m < 10:
            continue
        if mode == "top_bottom_n":
            r = _first_ranks(s, descending=True); legs = (r <= n_param, r > m - n_param)
        elif mode == "quantile":
            r = _first_ranks(s, descending=False) / m; legs = (r > 1 - q, r <= q)
        elif mode == "threshold":
            legs = (s > float(_resolve(cfg, pos["long_above"])) if "long_above" in pos else np.zeros(m, bool),
                    s < float(_resolve(cfg, pos["short_below"])) if "short_below" in pos else np.zeros(m, bool))
        else:  # proportional: all names, weight ∝ demeaned signal
            s = s - s.mean(); legs = (s > 0, s < 0)
        w = np.zeros(sig.shape[1])
        for sign, on, sel in ((+1, do_long, legs[0]), (-1, do_short, legs[1])):
            if not on or not sel.any():
                continue
            x = s[sel]
            if mode == "proportional":
                base = np.abs(x)
            elif weighting == "equal":
                base = np.ones(len(x))
            elif weighting == "signal":                       # weight ∝ distance from the cross-sectional median (extremes get more)
                base = np.abs(x - np.median(s)) + 1e-12
            else:  # rank: the most extreme name of the leg gets the largest weight on either side
                base = pd.Series(x if sign > 0 else -x).rank().to_numpy()
            w[idx[sel]] = sign * (per_name / 1e4 * len(x) if per_name else side) * base / base.sum()
        n_entries += 1; i0 = i + delay
        T[i0:i0 + hold] += w; cnt[i0:i0 + hold] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        T = np.where(cnt[:, None] > 0, T / cnt[:, None], 0.0)
    return pd.DataFrame(T, index=dates, columns=data.symbols), n_entries


# ----------------------------------------------------------------------------------------------------------- simulation
def simulate(cfg, data, targets, risk=True, cost_mult=1.0, governor=True):
    R = RISK; caps = R["caps"]; fl = R["fund_limits"]; cm = R["cost_model"]; gov = R["governor"]["cppi"]
    dates = data.dates; syms = data.symbols
    ret = data.field("ret").fillna(0.0).to_numpy(); fund = data.field("funding").fillna(0.0).to_numpy()
    mdv = data.field("mdv21").to_numpy(); oi = data.field("oi_usd").to_numpy(); beta = data.beta.fillna(1.0).to_numpy()
    btc_i = list(syms).index(BTC); half = data.mdv_tier_bps().to_numpy()
    fee = cm["taker_fee_bps"] * cost_mult; imp = 15.0 * cost_mult
    N = R["sizing"]["aum_reference_usd"]            # $10M reference book for cap arithmetic
    tgt = targets.to_numpy()
    w = np.zeros(len(syms)); hedge = 0.0; nav = 1.0; hwm = 1.0; mult = 1.0; sig_u = gov["sigma_u"]["seed"]; lam = gov["sigma_u"]["lambda"]
    rows = []; attr = np.zeros(len(syms)); last_day_nav = 1.0
    # per-name caps (fraction of NAV): min(liq legs floored at 0.1% N, 5% N), then max-loss tiers
    tier_s = np.where(mdv >= 100e6, caps["max_loss"]["stress_short"]["large"], np.where(mdv >= 10e6, caps["max_loss"]["stress_short"]["medium"], caps["max_loss"]["stress_short"]["small"]))
    tier_l = np.where(mdv >= 100e6, caps["max_loss"]["stress_long"]["large"], np.where(mdv >= 10e6, caps["max_loss"]["stress_long"]["medium"], caps["max_loss"]["stress_long"]["small"]))
    cap_short = np.minimum(caps["max_loss"]["loss_budget_frac_aum"] / tier_s, caps["concentration_frac_nav"]); cap_long = np.minimum(caps["max_loss"]["loss_budget_frac_aum"] / tier_l, caps["concentration_frac_nav"])
    liq = np.fmin(caps["liquidity"]["frac_mdv"] * np.nan_to_num(mdv, nan=0.0), np.where(np.isfinite(oi), caps["liquidity"]["frac_oi"] * oi, np.inf)) / N
    liq = np.clip(liq, caps["liquidity"]["floor_frac_aum"], caps["concentration_frac_nav"])
    for i in range(1, len(dates)):
        nav_usd = nav * N
        # governor (daily mark)
        if risk and governor:
            dr = nav / last_day_nav - 1.0; dr_u = dr / max(mult, gov["floor"]); sig_u = float(np.sqrt((1 - lam) * sig_u ** 2 + lam * dr_u ** 2))
            hwm = max(hwm, nav); dd = 1 - nav / hwm
            mult = min(1.0, max(gov["floor"], (gov["cppi_stop"] - dd) / max(gov["k"] * sig_u, 1e-6)))
            for cl in gov["dal_clamp"]:
                if dd >= cl["dd_ge"]:
                    mult = min(mult, cl["mult_le"])
        last_day_nav = nav
        tw = tgt[i].copy()
        if risk:
            tw = np.where(tw > 0, np.minimum(tw, np.minimum(cap_long[i], liq[i])), np.maximum(tw, -np.minimum(cap_short[i], liq[i])))
            g = np.abs(tw).sum()
            if g > caps["gmv_cap_x_aum"]:
                tw *= caps["gmv_cap_x_aum"] / g
            tw = tw * mult
            B = float(np.dot(tw, beta[i])); D = float(tw.sum())
            if abs(B - D) > fl["beta_cap_frac_nav"] + fl["delta_cap_frac_nav"]:
                tw *= (fl["beta_cap_frac_nav"] + fl["delta_cap_frac_nav"]) / abs(B - D)
        delta = tw - w
        tr = np.abs(delta)
        slip = (half[i] + imp * np.sqrt(np.minimum(tr * nav_usd / np.maximum(np.nan_to_num(mdv[i], nan=1.0), 1.0), 1.0))) if risk else np.full(len(syms), 3.0 * cost_mult)
        cost = float(np.sum(tr * (fee + slip) * 1e-4))
        w = tw
        # hedge
        if risk and cfg["positions"].get("hedge_btc", True):
            B = float(np.dot(w, beta[i])); D = float(w.sum()); bc, dc = fl["beta_cap_frac_nav"], fl["delta_cap_frac_nav"]
            lo = max(-B - bc, -D - dc); hi = min(-B + bc, -D + dc); new_h = float(np.clip(-B, lo, hi))
            if abs(new_h - hedge) > fl["hedge"]["band_frac_nav"] or i == 1:
                cost += abs(new_h - hedge) * (fee + 1.0 * cost_mult) * 1e-4; hedge = new_h
        else:
            hedge = 0.0
        # P&L over day i (ret at i+1 is close_{i+1}/close_i − 1, known at i+1): use next row
        if i + 1 >= len(dates):
            break
        pa = float(np.dot(w, ret[i + 1])); pf = float(np.dot(w, -fund[i + 1])); ph = hedge * ret[i + 1][btc_i] - hedge * fund[i + 1][btc_i]
        attr += w * ret[i + 1] - w * fund[i + 1]
        r_day = pa + pf + ph - cost; nav *= (1 + r_day)
        rows.append({"date": dates[i + 1], "ret": r_day, "price": pa, "funding": pf, "hedge": ph, "costs": cost, "gross": float(np.abs(w).sum()) + abs(hedge),
                     "delta": float(w.sum()) + hedge, "beta": float(np.dot(w, beta[i])) + hedge, "turnover": float(tr.sum()), "req_turnover": float(np.abs(tgt[i] - w).sum()),
                     "mult": mult, "n_long": int((w > 0).sum()), "n_short": int((w < 0).sum()), "nav": nav})
    daily = pd.DataFrame(rows).set_index("date")
    return daily, pd.Series(attr, index=syms)


def run(cfg, data, risk=True, cost_mult=1.0, governor=True, shuffle_seed=None, window=None):
    """Full pipeline for one config. window=(start,end) restricts the sim (signals still use history before start).
    shuffle_seed: the placebo — relabel the ranking within each row's ranked eligible names (`relabel`) with a fixed seed. An hourly config only has the raw sleeve here
    (risk=False, `run_raw_hourly`); its risk-framework run is `run_hourly`."""
    if resolution(cfg) == "1h":
        if risk or window:
            raise ValueError("an hourly config runs under the risk framework through run_hourly; run() gives only its raw sleeve")
        return run_raw_hourly(cfg, data, cost_mult=cost_mult, shuffle_seed=shuffle_seed)
    universe = data.eligible(**{k: v for k, v in (cfg.get("universe") or {}).items() if k in ("min_mdv_usd", "min_age_days", "exclude", "include_only")})
    signal, _ = build_signal(cfg, data)
    if shuffle_seed is not None:
        signal = relabel(signal, universe, shuffle_seed)
    targets, n_rebal = build_targets(cfg, data, signal, universe)
    if window:
        a, b = pd.Timestamp(window[0], tz="UTC"), pd.Timestamp(window[1], tz="UTC")
        m = (targets.index >= a) & (targets.index <= b); targets = targets.copy(); targets.loc[~m, :] = 0.0
    daily, attr = simulate(cfg, data, targets, risk=risk, cost_mult=cost_mult, governor=governor)
    if window:
        daily = daily[(daily.index > a) & (daily.index <= b + pd.Timedelta(days=1))]
    return daily, attr, n_rebal


def run_raw_hourly(cfg, data, cost_mult=1.0, shuffle_seed=None):
    """Raw sleeve of an hourly config (the daily `simulate(risk=False)` on the hourly grid): the book of row h held over the next hour,
    price P&L and funding prints, cost = taker fee + 3 bp per unit traded, no caps / hedge. Returns (daily frame, attr, n_entries)."""
    V = data.view("1h"); signal, _ = build_signal(cfg, V)
    u = cfg.get("universe") or {}
    universe = V.eligible(**{k: v for k, v in u.items() if k in ("min_mdv_usd", "min_age_days", "exclude", "include_only")})
    if shuffle_seed is not None:
        signal = relabel(signal, universe, shuffle_seed)
    T, n = build_targets(cfg, V, signal, universe); w = T.to_numpy()
    ret = V.field("ret").fillna(0.0).to_numpy(); fund = V.field("funding").fillna(0.0).to_numpy()
    cost_bps = RISK["cost_model"]["taker_fee_bps"] * cost_mult + 3.0 * cost_mult
    traded = np.abs(np.diff(w, axis=0, prepend=np.zeros((1, w.shape[1])))).sum(axis=1)
    price = (w[:-1] * ret[1:]).sum(axis=1); funding = -(w[:-1] * fund[1:]).sum(axis=1); costs = traded[:-1] * cost_bps * 1e-4
    idx = V.dates[:-1] - pd.Timedelta(hours=1)                                       # open-time stamps, as run_hourly's NAV
    h = pd.DataFrame({"ret": price + funding - costs, "costs": costs, "funding": funding, "turnover": traded[:-1], "gross": np.abs(w[:-1]).sum(axis=1)}, index=idx)
    nav = (1 + h["ret"]).cumprod(); daily = pd.DataFrame({"ret": nav.resample("1D").last().pct_change(fill_method=None)})
    g = h.groupby(h.index.floor("D"))
    for c, how in (("costs", "sum"), ("funding", "sum"), ("turnover", "sum"), ("gross", "mean")):
        daily[c] = getattr(g[c], how)().reindex(daily.index)
    attr = pd.Series((w[:-1] * (ret[1:] - fund[1:])).sum(axis=0), index=T.columns)
    return daily.iloc[1:].fillna({"ret": 0.0}), attr, n


def with_params(cfg, overrides):
    """Return a copy of cfg with parameter values / dotted keys overridden (used by the sweep)."""
    c = copy.deepcopy(cfg)
    for k, v in overrides.items():
        if k.startswith("params."):
            c["params"][k.split(".", 1)[1]]["value"] = v
        else:
            node = c; parts = k.split(".")
            for p in parts[:-1]:
                node = node[p]
            node[parts[-1]] = v
    return c


# ----------------------------------------------------------------------------------------------------------- Obsidian hourly engine
# The headline engine since 2026-10-12: Obsidian's production replica (tools/obsidian_engine.py = backtest/engine_v2.py verbatim) run on
# the hourly panels of tools/panels.py, with the DSL's daily targets handed in as the externally sized `extra` panel
# (size_components = [] → nothing is reward-sized; caps, max-loss tiers, GMV cap, no-trade band, 4-hour resize gate, participation cap,
# BTC hedge with its 2% band, taker fee + spread tiers + impact, per-print funding all apply exactly as in production).
# Drawdown governors OFF (dd_ladder / dd_cppi / dd_glide = False; decision 2026-10-12). The daily `simulate` above is kept only for the
# raw-sleeve diagnostic (risk=False).
import obsidian_engine as OE  # noqa: E402

HARNESS_P = dict(OE.RISK2, dd_ladder=False, dd_cppi=False, dd_glide=False, vol_ceiling_ann=0.0, cooldown_loss_frac=0.0, size_components=[])
EDGE_BPS = 8.0      # constant |edge| handed to the entry hurdle (|edge| × expected_hold_d − 2(spread + fee) ≥ gamma): the production weekly
                    # sleeves carry ±8 bp, which clears the hurdle in every liquidity tier — so the hurdle never blocks a DSL target; the
                    # no-entry (delisting) mask still does


def relabel(signal, universe, seed):
    """The placebo (bar line 6): a random relabelling of the ranking that keeps the book's shape. On every row the ranked eligible
    names keep their count and their value multiset; the k-th smallest value goes to the name with the k-th smallest fixed random
    key, so the relabelling is persistent while the eligible set is. Replaces the whole-column permutation (2026-11-04b, NOS-44):
    that dropped most of the signal onto names outside the universe, built books a third the size at three times the weight, and
    the short-side caps plus the BTC hedge then gave every shuffled book the same ~10 %/yr bleed, whatever the ranking."""
    rng = np.random.default_rng(seed); K = rng.random(len(signal.columns))
    V = signal.where(universe.reindex(index=signal.index, columns=signal.columns).fillna(False)).to_numpy(dtype="float64", copy=True)
    for i in range(V.shape[0]):
        idx = np.flatnonzero(~np.isnan(V[i]))
        if len(idx) < 2:
            continue
        vals = np.sort(V[i, idx]); order = idx[np.argsort(K[idx], kind="stable")]
        V[i, order] = vals
    return pd.DataFrame(V, index=signal.index, columns=signal.columns)


def targets_hourly(T, hidx):
    """Daily target weights (row D = book intended from D 00:00, computed from data before D) → hourly panel stamped at OPEN time:
    row D−1 23:00 is the bar that closes at D 00:00, where the engine trades to the new book; held 24 rows until the next day's row."""
    X = T.copy(); X.index = X.index - pd.Timedelta(hours=1)
    return X.reindex(hidx).ffill(limit=23).fillna(0.0)


def run_hourly(cfg, data, panels, cost_mult=1.0, shuffle_seed=None, window=None, record=False):
    """Judge's production-replica run. Returns (daily frame, per-name price-P&L attribution, n_rebalance_days) in the same shape as `run`.
    window = (a, b) restricts the simulation to that hourly segment (the calibration sweep)."""
    hourly = resolution(cfg) == "1h"; V = data.view(resolution(cfg))
    signal, _ = build_signal(cfg, V)
    u = cfg.get("universe", {})
    universe = V.eligible(u.get("min_mdv_usd", 3e6), u.get("min_age_days", 30), tuple(u.get("exclude", [BTC])), u.get("include_only"))
    if shuffle_seed is not None:
        signal = relabel(signal, universe, shuffle_seed)
    T, n_rebal = build_targets(cfg, V, signal, universe)
    cols = panels["close"].columns; T = T.reindex(columns=cols).fillna(0.0)
    if hourly:                                                       # row h = book from h → the bar opening at h−1h closes at h, where it trades
        T.index = T.index - pd.Timedelta(hours=1); extra = T.reindex(panels["hidx"]).fillna(0.0).astype("float32")
    else:
        extra = targets_hourly(T, panels["hidx"]).astype("float32")  # on the FULL index: the window's
    P = panels                                                                                         # first day already carries its book
    if window is not None:
        P = __import__("panels").slice_panels(panels, *window); extra = extra.loc[P["hidx"][0]:P["hidx"][-1]]
    hidx = P["hidx"]
    edge = (np.sign(extra) * EDGE_BPS).astype("float32"); del T, signal; __import__("gc").collect()
    p = dict(HARNESS_P)
    if cost_mult != 1.0:
        for k in ("taker_fee_bps", "spread_bps_liquid", "spread_bps_mid", "spread_bps_illiquid", "impact_k_bps"):
            p[k] = p[k] * cost_mult
    if record:
        p["record_trades"] = True
    btc_ret = P["close"][BTC].pct_change(fill_method=None)
    res = OE.run(P["close"], P["qv"], P["fund"], edge, {"cfg": edge}, P["univ"], P["no_entry"], P["beta_btc"].astype("float64"), P["mdv21"], P["cap_w"], btc_ret, p, extra=extra)
    w = res["weights"]; pnl = res["pnl"]; nav = res["nav"]
    alt = [c for c in cols if c != BTC]
    rets = P["close"].pct_change(fill_method=None).fillna(0.0)
    attr = (w[alt].shift(1).fillna(0.0) * rets[alt]).sum(axis=0)                     # price P&L per name over the run (fraction of NAV)
    day = lambda s, how: getattr(s.groupby(s.index.floor("D")), how)()  # noqa: E731
    gross_h = w[alt].abs().sum(axis=1) + pnl["hedge_w"].abs()
    daily = pd.DataFrame({"ret": nav.resample("1D").last().pct_change(fill_method=None)})
    daily = daily.iloc[1:] if len(daily) else daily
    daily["gross"] = day(gross_h, "mean").reindex(daily.index); daily["delta"] = day(pnl["delta"], "mean").reindex(daily.index); daily["beta"] = day(pnl["beta"], "mean").reindex(daily.index)
    daily["turnover"] = day(pnl["turnover"], "sum").reindex(daily.index); daily["req_turnover"] = day(pnl["req_turnover"], "sum").reindex(daily.index)
    daily["costs"] = day(pnl["costs"], "sum").reindex(daily.index); daily["funding"] = day(pnl["funding"], "sum").reindex(daily.index)
    daily["mult"] = 1.0; daily["n_long"] = day((w[alt] > 0).sum(axis=1), "mean").reindex(daily.index); daily["n_short"] = day((w[alt] < 0).sum(axis=1), "mean").reindex(daily.index)
    daily["hedge_w"] = day(pnl["hedge_w"], "mean").reindex(daily.index)
    out = (daily.fillna({"ret": 0.0}), attr, n_rebal)
    if record:
        return out + (res,)
    del res, w, pnl, rets; gc_ = __import__("gc"); gc_.collect()
    return out
