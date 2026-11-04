#!/usr/bin/env python3
"""Offline smoke test of the whole Judge procedure on a synthetic bucket in the S3 layout (no network, ~1–2 min).

  1. builds two small buckets under a temp dir: NOISE (returns are BTC beta + noise) and PLANTED (the least liquid names drift up,
     the most liquid drift down — an illiquidity premium the reference illiquidity sleeve should find)
  2. validates the four example configs
  3. runs judge_run on the illiquidity sleeve against both: NOISE must FAIL, PLANTED must PASS, and the first line of the
     result markdown must carry the verdict
  4. hourly resolution: the planted bucket also carries an hour-of-day continuation (each name's 07:00–08:00 UTC bar is
     followed by a same-signed 21:00–22:00 bar); the hourly example config must PASS on it and FAIL on NOISE
  5. build_targets matches the pre-numpy pandas implementation (kept below as the reference) on random daily data
  6. the placebo relabelling keeps each row's count and values and stays within the universe; plateau_neighbours clips, rounds and dedupes
  7. checks extract_config's round logic on a synthetic thread

    python3 tests/test_engine.py            # prints PASS/FAIL per check, exit 1 on any failure
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); TOOLS = os.path.join(ROOT, "tools")
PY = sys.executable


def build_bucket(root, planted, seed=11, n_alts=60):
    rng = np.random.default_rng(seed); os.makedirs(root, exist_ok=True)
    hours = pd.date_range("2021-10-01", "2025-12-31 23:00", freq="h", tz="UTC")
    syms = ["BTCUSDT"] + [f"S{i:02d}USDT" for i in range(n_alts)]
    btc = rng.normal(0, 0.004, len(hours)); rows = []
    lvl = np.sort(rng.uniform(np.log(3e6), np.log(3e8), n_alts))
    for j, s in enumerate(syms):
        if j == 0:
            r = btc; L = np.log(5e9)
        else:
            L = lvl[j - 1]; rank = (j - 1) / (n_alts - 1)
            drift = ((0.0006 / 24) * (1.0 if rank < 0.34 else (-1.0 if rank > 0.66 else 0.0))) if planted else 0.0   # flat premium inside each tercile: no single name dominates
            r = rng.uniform(0.6, 1.6) * btc + rng.normal(0, 0.006, len(hours)) + drift
            if planted:                                   # hour-of-day continuation for the hourly example: bar 07:00 → bar 21:00 same day
                i7 = np.flatnonzero(hours.hour == 7); i7 = i7[i7 + 14 < len(hours)]
                r[i7 + 14] += 0.004 * np.sign(r[i7] - btc[i7])
        px = 100 * np.exp(np.cumsum(r)); qv = np.exp(rng.normal(L - np.log(24), 0.4, len(hours))); tb = qv * rng.uniform(0.4, 0.6, len(hours))
        rows.append(pd.DataFrame({"symbol": s, "time": hours, "open": px, "high": px * 1.001, "low": px * 0.999, "close": px, "volume": qv / px, "quote_volume": qv,
                                  "trades": 100, "taker_buy_volume": tb / px, "taker_buy_quote_volume": tb}))
    k = pd.concat(rows, ignore_index=True)
    for y, g in k.groupby(k["time"].dt.year):
        d = f"{root}/klines/interval=1h/year={y}"; os.makedirs(d, exist_ok=True); g.sort_values(["symbol", "time"]).to_parquet(f"{d}/data.parquet", index=False)
    ft = pd.date_range("2021-10-01", "2025-12-31 16:00", freq="8h", tz="UTC")
    f = pd.concat([pd.DataFrame({"symbol": s, "time": ft, "funding_rate": rng.normal(1e-4, 2e-4, len(ft)), "interval_hours": 8}) for s in syms])
    os.makedirs(f"{root}/funding", exist_ok=True); f.to_parquet(f"{root}/funding/data.parquet", index=False)
    days = pd.date_range("2021-10-01", "2025-12-31", freq="D", tz="UTC")
    m = pd.concat([pd.DataFrame({"symbol": s, "time": days, "sum_open_interest": 1e6, "sum_open_interest_value": 2e7, "count_toptrader_long_short_ratio": 1.0,
                                 "sum_toptrader_long_short_ratio": 1.0, "count_long_short_ratio": 1.0, "sum_taker_long_short_vol_ratio": 1.0}) for s in syms])
    os.makedirs(f"{root}/metrics/interval=1d", exist_ok=True); m.to_parquet(f"{root}/metrics/interval=1d/data.parquet", index=False)


def _ref_rebalance_days(E, dates, cfg):
    sch = cfg["schedule"]; n = E.REBAL_DAYS[sch["rebalance"]]
    if n >= 7 and sch.get("anchor", "monday") == "monday":
        base = dates[dates.dayofweek == 0]; return base[::n // 7]
    return dates[::n]


def _reference_targets(E, cfg, data, signal, universe):
    """The pre-2026-11-04 pandas positions rule, kept as the reference the numpy build_targets must reproduce. Per-day target weight frame (fraction of notional, pre-risk) from the positions rule, with holding-period averaging."""
    pos = cfg["positions"]; mode = pos["mode"]; weighting = pos.get("weighting", "equal")
    side = float(pos.get("side_gross_pct", 50)) / 100.0; per_name = pos.get("per_name_bps")
    do_long, do_short = pos.get("long", True), pos.get("short", True)
    hold = E._int(cfg, cfg["schedule"]["holding_days"]) if "holding_days" in cfg["schedule"] else E.REBAL_DAYS[cfg["schedule"]["rebalance"]]
    delay = int(cfg["schedule"].get("delay_days", 0))
    rdays = _ref_rebalance_days(E, data.dates, cfg); sig = signal.where(universe)
    entries = {}   # date -> weight Series
    for d in rdays:
        s = sig.loc[d].dropna()
        if len(s) < 10:
            continue
        w = pd.Series(0.0, index=s.index)
        if mode == "top_bottom_n":
            n = max(1, E._int(cfg, pos["n"])); r = s.rank(method="first", ascending=False)
            L = s[r <= n]; Sh = s[r > len(s) - n]
        elif mode == "quantile":
            q = float(E._resolve(cfg, pos["quantile"])); r = s.rank(pct=True, method="first")
            L = s[r > 1 - q]; Sh = s[r <= q]
        elif mode == "threshold":
            L = s[s > float(E._resolve(cfg, pos["long_above"]))] if "long_above" in pos else s.iloc[0:0]
            Sh = s[s < float(E._resolve(cfg, pos["short_below"]))] if "short_below" in pos else s.iloc[0:0]
        else:  # proportional: all names, weight ∝ demeaned signal
            z = s - s.mean(); L = z[z > 0]; Sh = z[z < 0]
        def leg(x, sign):
            if len(x) == 0:
                return
            if weighting == "equal" or mode == "proportional":
                base = x.abs() if mode == "proportional" else pd.Series(1.0, index=x.index)
            elif weighting == "signal":                       # weight ∝ distance from the cross-sectional median (extremes get more)
                base = (x - s.median()).abs() + 1e-12
            else:  # rank: the most extreme name of the leg gets the largest weight on either side
                base = (x if sign > 0 else -x).rank().astype(float)
            base = base / base.sum()
            w.loc[x.index] = sign * (per_name / 1e4 * len(x) if per_name else side) * base
        if do_long:
            leg(L, +1)
        if do_short:
            leg(Sh, -1)
        entries[d] = w
    # expand entries over the holding period (average of live entries); shift by the execution delay
    T = pd.DataFrame(0.0, index=data.dates, columns=data.symbols); cnt = pd.Series(0, index=data.dates, dtype=float)
    pos_idx = {d: i for i, d in enumerate(data.dates)}
    for d, w in entries.items():
        wv = w.reindex(data.symbols).fillna(0.0).to_numpy()  # align by label first: .iloc row writes are positional, and the panel columns are not sorted (NOS-40)
        i0 = pos_idx[d] + delay
        for i in range(i0, min(i0 + hold, len(data.dates))):
            T.iloc[i] = T.iloc[i].to_numpy() + wv; cnt.iloc[i] += 1
    T = T.div(cnt.replace(0, np.nan), axis=0).fillna(0.0)
    return T, len(entries)


def run(cmd, **kw):
    return subprocess.run([PY] + cmd, capture_output=True, text=True, **kw)


def main():
    ok = True
    def check(name, cond, info=""):
        nonlocal ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  ({info})" if info else ""))
    with tempfile.TemporaryDirectory() as tmp:
        for ex in sorted(os.listdir(os.path.join(ROOT, "examples"))):
            r = run([os.path.join(TOOLS, "validate_config.py"), os.path.join(ROOT, "examples", ex)])
            check(f"validate {ex}", r.returncode == 0 and r.stdout.startswith("valid:"), r.stdout.strip().splitlines()[0] if r.stdout else r.stderr[-200:])
        # "$param" in const.value and clip.lo/hi (NOS-20 escalation): the validator accepts a declared
        # reference and rejects an unknown one or a non-numeric literal; the engine resolves them.
        base = json.load(open(os.path.join(ROOT, "examples", "ref_illiquidity.json")))
        def vrun(value, lo=None):
            c = json.loads(json.dumps(base)); c.setdefault("params", {})["rho"] = {"value": 2.0}
            c["signal"]["steps"].insert(0, {"op": "const", "value": value, "out": "rho_c"})
            if lo is not None:
                c["signal"]["steps"].insert(1, {"op": "clip", "in": "rho_c", "lo": lo, "out": "rho_cc"})
            p = os.path.join(tmp, "v.json"); json.dump(c, open(p, "w"))
            return run([os.path.join(TOOLS, "validate_config.py"), p])
        for name, args, want_ok in (("const value $param", ("$rho",), True),
                                    ("clip lo $param", (1.0, "$rho"), True),
                                    ("const unknown $ref", ("$ghost",), False),
                                    ("const non-numeric value", ("abc",), False)):
            r = vrun(*args)
            check(f"validate: {name} → {'valid' if want_ok else 'error'}", (r.returncode == 0) == want_ok,
                  (r.stdout or r.stderr).strip().splitlines()[0][:100])
        sys.path.insert(0, TOOLS); import engine as eng; import types
        stub = types.SimpleNamespace(dates=pd.date_range("2024-01-01", periods=3, tz="UTC"),
                                     symbols=["AUSDT", "BUSDT"], field=None)
        outf, _ = eng.build_signal({"params": {"rho": {"value": 7.0}, "hi": {"value": 5.0}},
                                    "signal": {"steps": [{"op": "const", "value": "$rho", "out": "c"},
                                                         {"op": "clip", "in": "c", "lo": 0.0, "hi": "$hi", "out": "o"}],
                                               "output": "o"}}, stub)
        check("engine: const/clip resolve $param", outf.shape == (3, 2) and float(outf.iloc[0, 0]) == 5.0)

        # NOS-40 regression: panel columns are not sorted — targets must land on the ranked names, not on positions
        dts = pd.date_range("2024-01-01", periods=14, freq="D", tz="UTC"); usyms = pd.Index([f"S{i:02d}" for i in range(20)][::-1])
        uni = pd.DataFrame(False, index=dts, columns=usyms); uni[list(usyms[:12])] = True
        sig = pd.DataFrame(np.tile(np.arange(20.0), (14, 1)), index=dts, columns=usyms)
        T, _ = eng.build_targets({"params": {}, "positions": {"mode": "quantile", "quantile": 0.25, "weighting": "equal", "side_gross_pct": 50},
                                  "schedule": {"rebalance": "1w", "anchor": "monday", "holding_days": 7}},
                                 types.SimpleNamespace(dates=dts, symbols=usyms), sig, uni)
        held = {c for c in T.columns if (T[c] != 0).any()}; want_long, want_short = {"S10", "S09", "S08"}, {"S19", "S18", "S17"}
        check("engine: build_targets aligns weights by label on unsorted columns (NOS-40)",
              held == want_long | want_short and all(abs(T[c].max() - 0.5 / 3) < 1e-12 for c in want_long)
              and all(abs(T[c].min() + 0.5 / 3) < 1e-12 for c in want_short), f"held: {sorted(held)}")

        # the numpy positions rule reproduces the pandas one it replaced, on every mode × weighting, with holding overlap and delay
        rng = np.random.default_rng(5); dts = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC"); syms = pd.Index([f"S{i:02d}" for i in range(40)][::-1])
        sig = pd.DataFrame(rng.normal(size=(60, 40)), index=dts, columns=syms); sig.iloc[:, :3] = sig.iloc[:, :3].round(0)   # ties
        uni = pd.DataFrame(rng.uniform(size=(60, 40)) > 0.2, index=dts, columns=syms); stub = types.SimpleNamespace(dates=dts, symbols=syms)
        worst = 0.0
        for posn in ({"mode": "top_bottom_n", "n": 6}, {"mode": "quantile", "quantile": 0.3}, {"mode": "threshold", "long_above": 0.5, "short_below": -0.4},
                     {"mode": "proportional"}, {"mode": "top_bottom_n", "n": 25}, {"mode": "quantile", "quantile": 0.2, "per_name_bps": 40, "short": False}):
            for wt in ("equal", "signal", "rank"):
                for sch in ({"rebalance": "1d"}, {"rebalance": "3d", "holding_days": 7, "delay_days": 1}, {"rebalance": "1w", "anchor": "monday", "holding_days": 10}):
                    c = {"params": {}, "positions": dict(posn, weighting=wt), "schedule": sch}
                    a, na = eng.build_targets(c, stub, sig, uni); b, nb = _reference_targets(eng, c, stub, sig, uni)
                    worst = max(worst, float(np.abs(a.to_numpy() - b.reindex(index=a.index, columns=a.columns).to_numpy()).max())); assert na == nb
        check("engine: numpy build_targets = the pandas reference (max |diff| < 1e-12)", worst < 1e-12, f"max |diff| {worst:.1e}")

        # hourly schedule: entries only at the listed UTC hours, held for holding_hours, flat in between
        hrs = pd.date_range("2024-01-01", periods=72, freq="h", tz="UTC"); hs = pd.Index([f"S{i:02d}" for i in range(12)])
        hsig = pd.DataFrame(np.tile(np.arange(12.0), (72, 1)), index=hrs, columns=hs); huni = pd.DataFrame(True, index=hrs, columns=hs)
        T, n = eng.build_targets({"params": {}, "positions": {"mode": "top_bottom_n", "n": 2},
                                  "schedule": {"resolution": "1h", "rebalance": "1h", "rebalance_hours": [21], "holding_hours": 2}},
                                 types.SimpleNamespace(dates=hrs, symbols=hs), hsig, huni)
        live = sorted({h.hour for h in T.index[(T.abs().sum(axis=1) > 0)]})
        check("engine: hourly rebalance_hours enter at 21:00 and hold 2 h", n == 3 and live == [21, 22] and abs(T.loc[hrs[21], "S11"] - 0.25) < 1e-12, f"entries {n}, live hours {live}")

        # NOS-44 (2026-11-04b): the placebo keeps the book's shape — per row the same eligible names carry the same values, reassigned
        rng = np.random.default_rng(3); dts2 = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC"); s2 = pd.Index([f"S{i:02d}" for i in range(30)])
        sg = pd.DataFrame(rng.normal(size=(30, 30)), index=dts2, columns=s2); sg.iloc[:, 25:] = np.nan                    # five names never have a signal
        un = pd.DataFrame(rng.uniform(size=(30, 30)) > 0.3, index=dts2, columns=s2); un.iloc[:10] = un.iloc[0].to_numpy()  # a stable eligible set for ten rows
        rl = eng.relabel(sg, un, 1); rl2 = eng.relabel(sg, un, 2); base = sg.where(un)
        same_set = all((rl.iloc[i].notna() == base.iloc[i].notna()).all() for i in range(30))
        same_vals = all(np.allclose(np.sort(rl.iloc[i].dropna()), np.sort(base.iloc[i].dropna())) for i in range(30))
        persistent = all(rl.iloc[i].rank().fillna(-1).equals(rl.iloc[0].rank().fillna(-1)) for i in range(1, 10))
        moved = (rl.rank(axis=1) != base.rank(axis=1)).to_numpy()[base.notna().to_numpy()].mean() > 0.8
        check("engine: relabel keeps each row's eligible names and values, is persistent, and differs by seed",
              same_set and same_vals and persistent and moved and not rl.equals(rl2), f"moved {moved}, persistent {persistent}")
        import judge_run as JR
        ncfg = {"params": {"lb": {"value": 30, "range": [30, 120], "unit": "days", "integer": True}, "q": {"value": 0.3, "range": [0.1, 0.3]}}}
        nbs = JR.plateau_neighbours(ncfg, ["params.lb", "params.q", "schedule.rebalance"], {"params.lb": 30, "params.q": 0.3, "schedule.rebalance": "1w"},
                                    {(30, 0.3, "1w"), (38, 0.3, "1w")})
        got = sorted((o["params.lb"], o["params.q"]) for o in nbs)
        check("judge_run: plateau_neighbours clips to the range, rounds integers, drops the chosen and sampled points",
              got == [(30, 0.15), (30, 0.225), (45, 0.3)] and all(o["schedule.rebalance"] == "1w" for o in nbs), str(got))

        # hourly validation rules
        hx = json.load(open(os.path.join(ROOT, "examples", "example_hour_of_day.json")))
        for name, mut, want_ok in (("hourly example", lambda c: None, True),
                                   ("hour_utc on a daily config", lambda c: (c["schedule"].clear(), c["schedule"].update({"rebalance": "1d"})), False),
                                   ("holding_days at 1h", lambda c: c["schedule"].update({"holding_days": 2}), False),
                                   ("rebalance 1w at 1h", lambda c: (c["schedule"].pop("rebalance_hours"), c["schedule"].update({"rebalance": "1w"})), False),
                                   ("rebalance_hours with 4h", lambda c: c["schedule"].update({"rebalance": "4h"}), False),
                                   ("rebalance_hours out of range", lambda c: c["schedule"].update({"rebalance_hours": [24]}), False)):
            c = json.loads(json.dumps(hx))
            if name == "hour_utc on a daily config":
                c["signal"]["steps"].append({"op": "field", "name": "hour_utc", "out": "hr"})
            mut(c); p = os.path.join(tmp, "h.json"); json.dump(c, open(p, "w")); r = run([os.path.join(TOOLS, "validate_config.py"), p])
            check(f"validate: {name} → {'valid' if want_ok else 'error'}", (r.returncode == 0) == want_ok, (r.stdout or r.stderr).strip().splitlines()[-1][:110])

        cases = (("ref_illiquidity.json", "NOISE", False, "FAIL"), ("ref_illiquidity.json", "PLANTED", True, "PASS"),
                 ("example_hour_of_day.json", "NOISE", False, "FAIL"), ("example_hour_of_day.json", "PLANTED", True, "PASS"))
        for ex, name, planted, want in cases:
            cfg = os.path.join(ROOT, "examples", ex)
            b = os.path.join(tmp, name)
            if not os.path.exists(b):
                build_bucket(b, planted)
            out = os.path.join(tmp, f"out_{name}_{ex}")
            r = run([os.path.join(TOOLS, "judge_run.py"), "--config", cfg, "--ticket", "T", "--round", "1", "--out", out, "--data", b, "--eval-end", "2025-12-31", "--max-grid", "6", "--quick"])
            vl = [l for l in r.stdout.splitlines() if l.startswith("[JUDGE RESULT")]; first = vl[0] if vl else (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
            check(f"judge_run {ex} on {name} → {want}", r.returncode == 0 and first == f"[JUDGE RESULT 1/5 — {want}]", first)
            if r.returncode == 0:
                res = json.load(open(os.path.join(out, "T_r1_result.json")))
                check(f"{ex} {name}: result json verdict + for_scout present", res["verdict"] == want and bool(res.get("for_scout")), res.get("for_scout", "")[:80])
                check(f"{ex} {name}: files written", all(os.path.exists(os.path.join(out, f"T_r1_{x}")) for x in ("result.md", "result.json", "best_config.json", "daily.csv")))
        cfg = os.path.join(ROOT, "examples", "ref_illiquidity.json")
        # extract_config round logic
        c = json.load(open(cfg)); t = "## Trade config\n```json\n" + json.dumps(c) + "\n```\n"
        t2 = t + "\n[JUDGE RESULT 1/5 — FAIL]\n```json\n{\"x\": 1}\n```\n\n[SCOUT FRAMEWORK 2/5]\n```json\n" + json.dumps(dict(c, framework="second")) + "\n```\n"
        t3 = t2 + "\n[JUDGE RESULT 2/5 — PASS]\n"
        # NOS-66: headings quoted inside a post (prose backticks, the config's notes string) are not post
        # boundaries, and a real heading may arrive with Linear's markdown escaping (\[ \])
        c2 = dict(c, framework="third", notes="an earlier duplicate '[SCOUT FRAMEWORK 2/5]' was posted")
        t4 = (t2 + "\n[JUDGE RESULT 2/5 — FAIL]\nx\n\n\\[SCOUT FRAMEWORK 3/5\\]\n"
              "Answers `[JUDGE RESULT 2/5 — FAIL]`; note a duplicate `[SCOUT FRAMEWORK 2/5]` from another session.\n\n"
              "## Trade config\n```json\n" + json.dumps(c2) + "\n```\n")
        for i, (txt, want_rc, want_round, want_fw) in enumerate(((t, 0, 1, c["framework"]), (t2, 0, 2, "second"), (t3, 3, None, None), (t4, 0, 3, "third"))):
            p = os.path.join(tmp, f"th{i}.md"); open(p, "w").write(txt); o = os.path.join(tmp, f"c{i}.json")
            r = run([os.path.join(TOOLS, "extract_config.py"), p, "--out", o, "--ticket", "NOS-1"])
            good = r.returncode == want_rc and (want_rc != 0 or (json.load(open(o))["round"] == want_round and json.load(open(o))["framework"] == want_fw))
            check(f"extract_config thread {i} → rc {want_rc}" + (f", round {want_round}" if want_round else ""), good, r.stdout.strip())
    print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
