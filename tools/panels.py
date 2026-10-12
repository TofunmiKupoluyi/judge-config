#!/usr/bin/env python3
"""Hourly research panels from the Binance USD-M bucket layout — the exact definitions of Obsidian's `cache_v2_oos`
(backtest/oos_build.py + staged_v2.py stages mdv21 / univ2 / beta_btc / build_caps), so the Obsidian engine (tools/obsidian_engine.py)
sees the same inputs it sees in production research.

Conventions (identical to the Obsidian cache):
  index        hourly, stamped at the bar's OPEN time (the bucket's `time`); engine row t earns close[t]/close[t-1] and trades at close[t]
  close, qv, tbq, high, low, trades   from the 1h klines (float64 for close/qv — float32 breaks hysteresis chains)
  fund         funding rate summed into the floor-hour of the settlement time; 0 where no print (float32)
  mdv21        21-day rolling MEDIAN of daily $ volume (daily sum of qv), min 21 days, forward-filled to hours
  univ         hysteresis membership: enter mdv21 >= $3M, stay >= $2M, close not NaN; delisting ramp: membership ends 7 days before a
               symbol's last candle and no entries in its final 14 days (only for symbols whose data ends > 3 days before the panel end)
  no_entry     the 14-day no-entry mask
  beta_btc     60-day (1440 h) rolling hourly beta to BTCUSDT, min 20 days, clipped [-3, 3] (mean-based cov/var, as staged_v2)
  cap_w        per-name cap in NAV weight: min(max(min(20% OI$, 30% MDV21, 15% mcap), 0.1% AUM), 5% NAV); OI$ from the daily metrics
               file (last snapshot of day D applied from D+1 00:00), 0 = missing (ffill); mcap unavailable → inert
Symbols: perpetuals only (no `_` delivery contracts) minus reference/NON_COIN_SYMBOLS.json (tokenised equities/indices), BTCUSDT kept
as the hedge instrument (never a universe member: univ[BTCUSDT] = False).

    P = load(root, start="2021-10-01", end="2026-09-30")   # dict of frames; P["hidx"] the hourly index
"""
from __future__ import annotations

import gc
import json
import os
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BTC = "BTCUSDT"
AUM = 10e6


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _aws_env():
    if os.environ.get("AWS_S3_ACCESS_KEY") and not os.environ.get("AWS_ACCESS_KEY_ID"):
        os.environ["AWS_ACCESS_KEY_ID"] = os.environ["AWS_S3_ACCESS_KEY"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ.get("AWS_S3_ACCESS_KEY_SECRET", "")
    os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-2")


def _read(path, columns=None):
    import pyarrow.parquet as pq
    if path.startswith("s3://"):
        import s3fs  # noqa: F401
        _aws_env()
    return pq.read_table(path, columns=columns).to_pandas()


def _years(root, sub):
    base = f"{root}/{sub}"
    if base.startswith("s3://"):
        import s3fs
        _aws_env()
        return sorted(int(p.split("year=")[-1]) for p in s3fs.S3FileSystem().ls(base.replace("s3://", "")) if "year=" in p)
    return sorted(int(d.split("=")[-1]) for d in os.listdir(base) if d.startswith("year="))


def non_coin():
    p = os.path.join(HERE, "..", "reference", "NON_COIN_SYMBOLS.json")
    return set(json.load(open(p))["symbols"]) if os.path.exists(p) else set()


def hysteresis(enter, stay):
    e = enter.fillna(False).to_numpy(); s = stay.fillna(False).to_numpy()
    out = np.zeros_like(e, dtype=bool); prev = np.zeros(e.shape[1], dtype=bool)
    for t in range(e.shape[0]):
        prev = e[t] | (prev & s[t]); out[t] = prev
    return pd.DataFrame(out, index=enter.index, columns=enter.columns)


def load(root, start="2021-10-01", end="2026-12-31", keep_extra=True):
    root = root.rstrip("/"); t0 = pd.Timestamp(start, tz="UTC"); t1 = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23)
    log("panels: 1h klines")
    cols_k = ["symbol", "time", "close", "quote_volume", "taker_buy_quote_volume"] + (["high", "low", "trades"] if keep_extra else [])
    drop = non_coin(); hidx = pd.date_range(t0, t1, freq="h", tz="UTC"); parts = {c: [] for c in cols_k[2:]}
    for y in _years(root, "klines/interval=1h"):                                    # one year at a time: the long table is the memory peak
        if not (t0.year <= y <= t1.year):
            continue
        k = _read(f"{root}/klines/interval=1h/year={y}/data.parquet", columns=cols_k)
        k["time"] = pd.to_datetime(k["time"], utc=True)
        k = k[~k["symbol"].str.contains("_") & ~k["symbol"].isin(drop) & (k["time"] >= t0) & (k["time"] <= t1)]
        for c in parts:
            if c not in ("close", "quote_volume"):
                k[c] = k[c].astype("float32")                                              # only close / qv need float64 (hysteresis chains)
            parts[c].append(k.pivot(index="time", columns="symbol", values=c))
        del k; gc.collect()
    def stitch(c):
        f = pd.concat(parts[c], axis=0); parts[c] = None; f = f[~f.index.duplicated(keep="last")].sort_index(); return f.reindex(hidx)
    P = {"hidx": hidx, "close": stitch("close").astype("float64"), "qv": stitch("quote_volume").astype("float64"), "tbq": stitch("taker_buy_quote_volume")}
    cols = P["close"].columns; P["qv"] = P["qv"].reindex(columns=cols); P["tbq"] = P["tbq"].reindex(columns=cols)
    if keep_extra:
        P["high"] = stitch("high").reindex(columns=cols); P["low"] = stitch("low").reindex(columns=cols); P["trades"] = stitch("trades").reindex(columns=cols)
    del parts; gc.collect()
    syms = P["close"].columns
    if BTC not in syms:
        raise RuntimeError("BTCUSDT missing from the klines — it is the hedge instrument")
    log(f"panels: {len(hidx)} hours × {len(syms)} symbols ({len(drop)} non-coin names excluded)")
    # funding: sum of prints into the floor hour of the settlement time (oos_build.batch)
    log("panels: funding")
    f = _read(f"{root}/funding/data.parquet", columns=["symbol", "time", "funding_rate"]); f["time"] = pd.to_datetime(f["time"], utc=True).dt.floor("h")
    f = f[(f["time"] >= t0) & (f["time"] <= t1) & f["symbol"].isin(syms)]
    P["fund"] = f.pivot_table(index="time", columns="symbol", values="funding_rate", aggfunc="sum").reindex(index=hidx, columns=syms).fillna(0.0).astype("float32"); del f
    # mdv21 (staged_v2.stage_mdv21) and the hysteresis universe (stage_univ2)
    mdv = P["qv"].resample("1D").sum().rolling(21, min_periods=21).median()
    P["mdv21"] = mdv.reindex(hidx, method="ffill").astype("float64"); del mdv            # float64 for the hysteresis chains; cast to float32 after the universe is built
    close = P["close"]; enter = (P["mdv21"] >= 3e6) & close.notna(); stay = (P["mdv21"] >= 2e6) & close.notna()
    univ = hysteresis(enter, stay); del enter, stay
    no_entry = pd.DataFrame(False, index=hidx, columns=syms)
    for s in syms:
        last = close[s].last_valid_index()
        if last is None or last >= hidx[-3 * 24]:
            continue
        univ.loc[last - pd.Timedelta(days=7):, s] = False; no_entry.loc[last - pd.Timedelta(days=14):, s] = True
    univ[BTC] = False
    P["univ"] = univ.astype(bool); P["no_entry"] = no_entry.astype(bool); del univ, no_entry
    # beta_btc (stage_beta_btc): mean-based rolling cov/var over 60 days of hourly returns, min 20 days, clip ±3
    log("panels: beta")
    btc = close[BTC].pct_change(fill_method=None).fillna(0.0); w, mp = 60 * 24, 20 * 24
    bm = btc.rolling(w, min_periods=mp).mean(); var = ((btc ** 2).rolling(w, min_periods=mp).mean() - bm ** 2).replace(0, np.nan)
    beta = pd.DataFrame(np.nan, index=hidx, columns=syms, dtype="float32")
    for i in range(0, len(syms), 80):                                              # column blocks keep the rolling temporaries small
        blk = list(syms[i:i + 80]); r = close[blk].pct_change(fill_method=None).fillna(0.0)
        rm = r.rolling(w, min_periods=mp).mean(); xy = r.mul(btc, axis=0).rolling(w, min_periods=mp).mean()
        beta[blk] = (xy.sub(rm.mul(bm, axis=0))).div(var, axis=0).clip(-3, 3).astype("float32"); del r, rm, xy
    P["beta_btc"] = beta; del beta, bm, var, btc; gc.collect()
    # caps (staged_v2.build_caps): OI$ from the daily metrics file, 0 = missing; mcap unavailable (inert)
    log("panels: caps")
    try:
        m = _read(f"{root}/metrics/interval=1d/data.parquet", columns=["symbol", "time", "sum_open_interest_value"]); m["time"] = pd.to_datetime(m["time"], utc=True) + pd.Timedelta(days=1)
        m = m[m["symbol"].isin(syms)]
        oi = m.pivot(index="time", columns="symbol", values="sum_open_interest_value").replace(0.0, np.nan).reindex(index=hidx, columns=syms).ffill(); del m
    except Exception as e:  # noqa: BLE001
        log(f"panels: metrics unavailable ({e}) — no OI cap leg"); oi = pd.DataFrame(np.nan, index=hidx, columns=syms)
    legs = np.fmin(0.20 * oi.to_numpy(), 0.30 * P["mdv21"].to_numpy())            # mcap leg: NaN (unprovisioned) → fmin ignores it
    cap = pd.DataFrame(legs, index=hidx, columns=syms).clip(lower=0.001 * AUM)
    P["cap_w"] = (cap / AUM).clip(upper=0.05).astype("float32"); P["oi_usd"] = oi.astype("float32"); del legs, cap, oi; gc.collect()
    P["mdv21"] = P["mdv21"].astype("float32")
    n = P["univ"].sum(axis=1); log(f"panels: universe mean {n.mean():.0f} names, min {n.min()}, max {n.max()}")
    return P


def slice_panels(P, a, b):
    """Hourly slice [a 00:00, b 23:00] of every frame (stage_sim's calib/eval segments)."""
    lo = pd.Timestamp(a, tz="UTC"); hi = pd.Timestamp(b, tz="UTC") + pd.Timedelta(hours=23)
    out = {}
    for k, v in P.items():
        if k == "hidx":
            out[k] = v[(v >= lo) & (v <= hi)]
        elif isinstance(v, (pd.DataFrame, pd.Series)):
            out[k] = v.loc[lo:hi]
    return out
