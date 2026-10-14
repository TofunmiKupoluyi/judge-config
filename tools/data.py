#!/usr/bin/env python3
"""Daily point-in-time data fields from the Binance USD-M bucket layout (binance-futures-data skill) — the engine's input.

Every field is a wide daily frame: index = 00:00 UTC day t, columns = symbols, and the row at t contains ONLY information from
before t 00:00 UTC (day t−1 and earlier). That one rule is what makes every config point-in-time by construction.

Fields (keep in sync with tools/validate_config.py FIELDS and the trade-config-spec skill):
  close          last traded price at t 00:00 (the close of the hour ending at 00:00)
  ret            simple return close_t / close_{t-1} − 1     log_ret   ln(close_t / close_{t-1})
  resid_ret      ret − beta60d × btc_ret  (BTC-residual simple return; beta from hourly returns, lagged)       btc_ret  BTC's ret
  high, low      day t−1's high / low;  range_pct = (high − low) / close_{t−1}
  hl_spread_1m   day t−1's mean over its 1-minute bars of (high − low) / close, from the bucket's hl_spread dataset
                 (a spread/liquidity proxy; NaN until that dataset is backfilled);  hl_spread_1m_median = the median,
                 exactly 0 on days where most minutes traded at a single price (thin names)
  volume_usd     day t−1's quote volume; taker_buy_usd, taker_sell_usd, taker_imbalance = (buy − sell) / volume
  n_trades       day t−1's trade count
  funding        funding paid by a LONG during day t−1 (sum of prints; sign: positive = longs paid)  funding_3d = 3-day sum
  mdv21          21-day median daily $ volume;  adv7 = 7-day mean $ volume;  amihud21 = 21d mean(|ret| / volume_usd)
  vol30          30-day std of daily ret
  oi_usd         open interest in $ at the last 5-minute snapshot of day t−1;  oi_change_1d = oi_usd / prior − 1
  ls_ratio_acct, ls_ratio_top_acct, ls_ratio_top_pos, taker_ls_vol   Binance positioning ratios, last snapshot of day t−1
  age_days       days since the first candle;  mcap = NaN until provisioned
Universe helpers: `eligible(min_mdv_usd, min_age_days)` (hysteresis enter ≥ min, stay ≥ 2/3 min; delisting ramp: out 7 days before
the data ends), `beta` (60-day hourly beta to BTC, lagged), `mdv_tier` for cost tiers.

    from data import Data; d = Data("s3://…/v1/binance/um" or "/local/mirror", start="2021-10-01", end="2026-09-30"); d.field("funding")
"""
from __future__ import annotations

import gc
import os
import time

import numpy as np
import pandas as pd

BTC = "BTCUSDT"


def _aws_env():
    """Map the desk's secret names onto what s3fs/boto read; never print them."""
    if os.environ.get("AWS_S3_ACCESS_KEY") and not os.environ.get("AWS_ACCESS_KEY_ID"):
        os.environ["AWS_ACCESS_KEY_ID"] = os.environ["AWS_S3_ACCESS_KEY"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ.get("AWS_S3_ACCESS_KEY_SECRET", "")
    os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-2")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _read(path, columns=None):
    import pyarrow.parquet as pq
    if path.startswith("s3://"):
        import s3fs  # noqa: F401
        _aws_env()
    return pq.read_table(path, columns=columns).to_pandas()


def _exists(path):
    if path.startswith("s3://"):
        import s3fs
        _aws_env()
        return s3fs.S3FileSystem().exists(path.replace("s3://", ""))
    return os.path.exists(path)


def _years(root, sub):
    base = f"{root}/{sub}"
    if base.startswith("s3://"):
        import s3fs
        _aws_env()
        return sorted(int(p.split("year=")[-1]) for p in s3fs.S3FileSystem().ls(base.replace("s3://", "")) if "year=" in p)
    return sorted(int(d.split("=")[-1]) for d in os.listdir(base) if d.startswith("year="))


class Data:
    def __init__(self, root, start="2021-10-01", end="2026-12-31", perp_only=True, panels=None):
        """panels: the hourly frames from tools/panels.py (open-time stamped) — when given, the daily fields are built from them
        (one load of the bucket for both engines; symbols and universe identical); otherwise the klines are read here."""
        self.root = root.rstrip("/"); self.start = pd.Timestamp(start, tz="UTC"); self.end = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23)
        if panels is not None:
            sh = lambda f: f.set_axis(f.index + pd.Timedelta(hours=1), copy=False)         # open-time stamp -> close-time stamp (no copy)  # noqa: E731
            close_h, qv_h, tbq_h = sh(panels["close"]), sh(panels["qv"]), sh(panels["tbq"])
            high_h, low_h, ntr_h = (sh(panels[k]) if k in panels else close_h * np.nan for k in ("high", "low", "trades"))
            fund_src = sh(panels["fund"]); hidx = close_h.index
        else:
            log("data: 1h klines")
            frames = [_read(f"{self.root}/klines/interval=1h/year={y}/data.parquet", columns=["symbol", "time", "high", "low", "close", "quote_volume", "trades", "taker_buy_quote_volume"])
                      for y in _years(self.root, "klines/interval=1h") if self.start.year <= y <= self.end.year]
            k = pd.concat(frames, ignore_index=True); del frames
            k["time"] = pd.to_datetime(k["time"], utc=True) + pd.Timedelta(hours=1)          # open -> close stamp
            if perp_only:
                k = k[~k["symbol"].str.contains("_")]
            k = k[(k["time"] >= self.start) & (k["time"] <= self.end)]
            hidx = pd.date_range(k["time"].min().floor("D"), k["time"].max(), freq="h", tz="UTC")
            piv = lambda c: k.pivot(index="time", columns="symbol", values=c).reindex(hidx)  # noqa: E731
            close_h = piv("close").astype("float64"); qv_h = piv("quote_volume").astype("float64"); tbq_h = piv("taker_buy_quote_volume").astype("float64")
            high_h = piv("high").astype("float64"); low_h = piv("low").astype("float64"); ntr_h = piv("trades").astype("float64")
            del k; gc.collect(); fund_src = None
        self.symbols = close_h.columns
        day = lambda f, how: self._to_daily(f, how)  # noqa: E731
        F = {}
        F["close"] = close_h.loc[hidx[hidx.hour == 0]].copy(); F["close"].index = F["close"].index  # price at t 00:00 is known at t
        self.dates = F["close"].index
        F["ret"] = F["close"] / F["close"].shift(1) - 1.0; F["log_ret"] = np.log(F["close"]).diff()
        F["high"] = day(high_h, "max"); F["low"] = day(low_h, "min"); F["range_pct"] = (F["high"] - F["low"]) / F["close"].shift(1)
        F["volume_usd"] = day(qv_h, "sum"); F["taker_buy_usd"] = day(tbq_h, "sum"); F["taker_sell_usd"] = F["volume_usd"] - F["taker_buy_usd"]
        F["taker_imbalance"] = (F["taker_buy_usd"] - F["taker_sell_usd"]) / F["volume_usd"].replace(0, np.nan); F["n_trades"] = day(ntr_h, "sum")
        F["mdv21"] = F["volume_usd"].rolling(21, min_periods=21).median(); F["adv7"] = F["volume_usd"].rolling(7, min_periods=5).mean()
        F["amihud21"] = (F["ret"].abs() / F["volume_usd"].replace(0, np.nan)).rolling(21, min_periods=15).mean(); F["vol30"] = F["ret"].rolling(30, min_periods=20).std()
        first = close_h.apply(lambda s: s.first_valid_index()); last = close_h.apply(lambda s: s.last_valid_index())
        F["age_days"] = pd.DataFrame({s: (self.dates - first[s]).days if first[s] is not None else np.nan for s in self.symbols}, index=self.dates).astype("float64")
        F["age_days"] = F["age_days"].where(F["age_days"] >= 0)
        # beta: the panels' 60-day hourly beta (Obsidian definition) sampled at 00:00 when panels are given; else computed here
        if panels is not None:
            self.beta = sh(panels["beta_btc"]).reindex(index=self.dates, columns=self.symbols).astype("float64")
        else:
            r_h = close_h.pct_change(fill_method=None); rb = r_h[BTC]
            cov = r_h.rolling(24 * 60, min_periods=24 * 30).cov(rb); var = rb.rolling(24 * 60, min_periods=24 * 30).var()
            beta_h = cov.div(var, axis=0).clip(-3, 3).shift(1); self.beta = beta_h.loc[self.dates].astype("float64"); del r_h, cov, var, beta_h
        F["btc_ret"] = pd.DataFrame({s: F["ret"][BTC] for s in self.symbols}, index=self.dates)
        F["resid_ret"] = F["ret"] - self.beta.shift(1) * F["btc_ret"]
        log("data: funding")
        if fund_src is not None:
            fund_h = fund_src.reindex(index=hidx, columns=self.symbols).fillna(0.0)
        else:
            f = _read(f"{self.root}/funding/data.parquet"); f["time"] = pd.to_datetime(f["time"], utc=True)
            f = f[(f["time"] >= self.start) & (f["time"] <= self.end) & f["symbol"].isin(self.symbols)]; f["h"] = f["time"].dt.ceil("h")
            fund_h = f.pivot_table(index="h", columns="symbol", values="funding_rate", aggfunc="sum").reindex(index=hidx, columns=self.symbols).fillna(0.0); del f
        F["funding"] = day(fund_h, "sum").fillna(0.0); F["funding_3d"] = F["funding"].rolling(3, min_periods=1).sum(); del fund_h
        log("data: daily metrics")
        try:
            m = _read(f"{self.root}/metrics/interval=1d/data.parquet"); m["time"] = pd.to_datetime(m["time"], utc=True) + pd.Timedelta(days=1)
            m = m[m["symbol"].isin(self.symbols)]
            for src, dst in (("sum_open_interest_value", "oi_usd"), ("count_long_short_ratio", "ls_ratio_acct"), ("count_toptrader_long_short_ratio", "ls_ratio_top_acct"),
                             ("sum_toptrader_long_short_ratio", "ls_ratio_top_pos"), ("sum_taker_long_short_vol_ratio", "taker_ls_vol")):
                F[dst] = m.pivot(index="time", columns="symbol", values=src).replace(0.0, np.nan).reindex(index=self.dates, columns=self.symbols).ffill(limit=2)
            F["oi_change_1d"] = F["oi_usd"] / F["oi_usd"].shift(1) - 1.0; del m
        except Exception as e:  # noqa: BLE001
            log(f"data: metrics unavailable ({e})")
            for dst in ("oi_usd", "oi_change_1d", "ls_ratio_acct", "ls_ratio_top_acct", "ls_ratio_top_pos", "taker_ls_vol"):
                F[dst] = pd.DataFrame(np.nan, index=self.dates, columns=self.symbols)
        log("data: hl_spread")
        try:
            hs = _read(f"{self.root}/hl_spread/interval=1d/data.parquet")
            hs["time"] = pd.to_datetime(hs["time"], utc=True) + pd.Timedelta(days=1)       # day D's statistics are known at D+1 00:00
            hs = hs[hs["symbol"].isin(self.symbols)]
            for col in ("hl_spread_1m", "hl_spread_1m_median"):                            # 0.0 is a real value (thin names), keep it
                F[col] = hs.pivot(index="time", columns="symbol", values=col).reindex(index=self.dates, columns=self.symbols).ffill(limit=2)
            del hs
        except Exception as e:  # noqa: BLE001
            log(f"data: hl_spread unavailable ({e})")
            for col in ("hl_spread_1m", "hl_spread_1m_median"):
                F[col] = pd.DataFrame(np.nan, index=self.dates, columns=self.symbols)
        F["mcap"] = pd.DataFrame(np.nan, index=self.dates, columns=self.symbols)
        self.F = {k: v.reindex(index=self.dates, columns=self.symbols).astype("float64") for k, v in F.items()}
        self.last_valid = last; self.hourly_close = close_h if panels is None else None; self.hourly_end = close_h.index[-1]
        del qv_h, tbq_h, high_h, low_h, ntr_h, close_h; gc.collect()
        log(f"data: {len(self.dates)} days × {len(self.symbols)} symbols, {self.dates[0].date()} → {self.dates[-1].date()}")

    def _to_daily(self, frame, how):
        g = frame.groupby(frame.index.floor("D")); out = g.sum(min_count=1) if how == "sum" else getattr(g, how)()
        out.index = out.index + pd.Timedelta(days=1)                                      # day D's aggregate is known at D+1 00:00
        return out

    def field(self, name):
        if name not in self.F:
            raise KeyError(f"unknown field {name!r}; known: {sorted(self.F)}")
        return self.F[name]

    def eligible(self, min_mdv_usd=3e6, min_age_days=30, exclude=(BTC,), include_only=None):
        mdv = self.F["mdv21"]; enter = (mdv >= min_mdv_usd) & self.F["close"].notna(); stay = (mdv >= min_mdv_usd * 2 / 3) & self.F["close"].notna()
        out = np.zeros(enter.shape, dtype=bool); e, s = enter.to_numpy(), stay.to_numpy(); state = np.zeros(enter.shape[1], dtype=bool)
        for i in range(len(enter)):
            state = np.where(state, s[i], e[i]); out[i] = state
        u = pd.DataFrame(out, index=self.dates, columns=self.symbols) & (self.F["age_days"] >= min_age_days)
        for sym, lv in self.last_valid.items():                                         # delisting ramp
            if lv is not None and lv < self.hourly_end - pd.Timedelta(days=3):
                u.loc[u.index >= lv - pd.Timedelta(days=7), sym] = False
        for x in exclude or ():
            if x in u.columns:
                u[x] = False
        if include_only:
            u.loc[:, [c for c in u.columns if c not in set(include_only)]] = False
        return u

    def mdv_tier_bps(self):
        """Half-spread tier by MDV21 (1 / 3 / 8 bp) per the risk framework."""
        m = self.F["mdv21"]; return pd.DataFrame(np.where(m >= 100e6, 1.0, np.where(m >= 20e6, 3.0, 8.0)), index=m.index, columns=m.columns).where(m.notna(), 8.0)
