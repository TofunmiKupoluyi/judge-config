#!/usr/bin/env python3
"""Offline smoke test of the whole Judge procedure on a synthetic bucket in the S3 layout (no network, ~1–2 min).

  1. builds two small buckets under a temp dir: NOISE (returns are BTC beta + noise) and PLANTED (the least liquid names drift up,
     the most liquid drift down — an illiquidity premium the reference illiquidity sleeve should find)
  2. validates the four example configs
  3. runs judge_run on the illiquidity sleeve against both: NOISE must FAIL, PLANTED must PASS, and the first line of the
     result markdown must carry the verdict
  4. checks extract_config's round logic on a synthetic thread

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

        cfg = os.path.join(ROOT, "examples", "ref_illiquidity.json")
        for name, planted, want in (("NOISE", False, "FAIL"), ("PLANTED", True, "PASS")):
            b = os.path.join(tmp, name); build_bucket(b, planted)
            out = os.path.join(tmp, "out_" + name)
            r = run([os.path.join(TOOLS, "judge_run.py"), "--config", cfg, "--ticket", "T", "--round", "1", "--out", out, "--data", b, "--eval-end", "2025-12-31", "--max-grid", "6", "--quick"])
            vl = [l for l in r.stdout.splitlines() if l.startswith("[JUDGE RESULT")]; first = vl[0] if vl else (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
            check(f"judge_run on {name} → {want}", r.returncode == 0 and first == f"[JUDGE RESULT 1/5 — {want}]", first)
            if r.returncode == 0:
                res = json.load(open(os.path.join(out, "T_r1_result.json")))
                check(f"{name}: result json verdict + for_scout present", res["verdict"] == want and bool(res.get("for_scout")), res.get("for_scout", "")[:80])
                check(f"{name}: files written", all(os.path.exists(os.path.join(out, f"T_r1_{x}")) for x in ("result.md", "result.json", "best_config.json", "daily.csv")))
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
