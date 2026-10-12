#!/usr/bin/env python3
"""Validate a trade config against reference/TRADE_CONFIG_SCHEMA.json AND the engine's semantic rules — no data needed.
Scout runs this before posting (it is the same file in scout-config); Judge runs it before the engine.

    python3 tools/validate_config.py path/to/config.json          # exit 0 = valid; prints the resolved parameter table

Semantic rules checked beyond the JSON schema: every "$param" reference exists in params; every step input was produced by an
earlier step; every `field` name is a known data field; positions mode has its required keys; windows are positive after
substitution; ranges contain the value; the output series exists; the signal is not trivially constant.
Pure standard library (no jsonschema dependency): a small structural checker covers the parts of the schema the engine relies on.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "reference", "TRADE_CONFIG_SCHEMA.json")

# the data fields the engine exposes (daily, point-in-time; see the trade-config-spec skill) — keep in sync with tools/data.py
FIELDS = {
 "close", "ret", "log_ret", "resid_ret", "high", "low", "range_pct", "volume_usd", "taker_buy_usd", "taker_sell_usd", "taker_imbalance",
 "n_trades", "funding", "funding_3d", "mdv21", "adv7", "oi_usd", "oi_change_1d", "ls_ratio_acct", "ls_ratio_top_acct", "ls_ratio_top_pos", "taker_ls_vol",
 "btc_ret", "age_days", "amihud21", "vol30", "mcap"
}
OPS_1 = {"rolling_mean", "rolling_sum", "rolling_std", "rolling_max", "rolling_min", "rolling_median", "ewm_mean", "lag", "diff", "pct_change", "zscore_time"}
OPS_U = {"log", "abs", "neg", "sign", "cross_rank", "cross_zscore", "cross_demean"}
OPS_2 = {"add", "sub", "mul", "div", "pow", "and", "or"}
ALL_OPS = OPS_1 | OPS_U | OPS_2 | {"field", "clip", "where", "gt", "lt", "const"}
MODES = {"top_bottom_n": ["n"], "quantile": ["quantile"], "threshold": [], "proportional": []}
REBAL = {"1d", "2d", "3d", "1w", "2w"}


def _p(cfg, v, errors, where):
    """Resolve a literal or "$param" to a number."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.startswith("$"):
        p = cfg.get("params", {}).get(v[1:])
        if p is None:
            errors.append(f"{where}: unknown parameter {v}"); return None
        return float(p["value"])
    errors.append(f"{where}: expected a number or \"$param\", got {v!r}"); return None


def validate(cfg):
    errors, warnings = [], []
    for k in ("schema_version", "hypothesis", "framework", "signal", "positions", "schedule"):
        if k not in cfg:
            errors.append(f"missing top-level key {k!r}")
    if errors:
        return errors, warnings
    if cfg["schema_version"] != "1.0":
        errors.append("schema_version must be \"1.0\"")
    if not (10 <= len(cfg["hypothesis"]) <= 300):
        errors.append("hypothesis must be 10–300 characters, one line")
    if "\n" in cfg["hypothesis"].strip():
        warnings.append("hypothesis should be a single line")
    if not (20 <= len(cfg["framework"]) <= 1200):
        errors.append("framework must be 20–1200 characters")
    # params
    for name, p in (cfg.get("params") or {}).items():
        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            errors.append(f"params.{name}: name must be snake_case")
        if not isinstance(p, dict) or "value" not in p:
            errors.append(f"params.{name}: needs {{\"value\": …}}"); continue
        if "range" in p:
            lo, hi = p["range"]
            if not (lo <= p["value"] <= hi):
                errors.append(f"params.{name}: value {p['value']} outside range [{lo}, {hi}]")
            if lo <= 0 and p.get("unit") in ("days", "hours", "count"):
                errors.append(f"params.{name}: a {p.get('unit')} range must be positive")
    # signal steps
    produced = set(); steps = cfg["signal"].get("steps", [])
    if not steps:
        errors.append("signal.steps is empty")
    for i, st in enumerate(steps):
        w = f"signal.steps[{i}] ({st.get('op')})"
        op = st.get("op"); out = st.get("out")
        if op not in ALL_OPS:
            errors.append(f"{w}: unknown op"); continue
        if not out or not re.match(r"^[a-z][a-z0-9_]*$", out):
            errors.append(f"{w}: 'out' must be a snake_case name")
        if op == "field":
            if st.get("name") not in FIELDS:
                errors.append(f"{w}: field {st.get('name')!r} is not a data field; known: {sorted(FIELDS)}")
        elif op == "const":
            if "value" not in st:
                errors.append(f"{w}: const needs 'value'")
        elif op in OPS_1:
            if st.get("in") not in produced:
                errors.append(f"{w}: input {st.get('in')!r} not produced by an earlier step")
            win = _p(cfg, st.get("window", None), errors, w) if "window" in st else None
            if win is None and op != "zscore_time":
                errors.append(f"{w}: needs 'window'")
            elif win is not None and win < 1:
                errors.append(f"{w}: window must be ≥ 1 day")
            elif win is not None and win > 365:
                warnings.append(f"{w}: window {win:.0f} days is long; lookbacks above 180 days are rarely justified")
        elif op in OPS_U:
            if st.get("in") not in produced:
                errors.append(f"{w}: input {st.get('in')!r} not produced by an earlier step")
        elif op in OPS_2:
            for k in ("a", "b"):
                if st.get(k) not in produced:
                    errors.append(f"{w}: input {k}={st.get(k)!r} not produced by an earlier step")
        elif op == "clip":
            if st.get("in") not in produced:
                errors.append(f"{w}: input not produced")
            if "lo" not in st and "hi" not in st:
                errors.append(f"{w}: clip needs lo and/or hi")
        elif op in ("gt", "lt"):
            if st.get("in") not in produced:
                errors.append(f"{w}: input not produced")
            if "threshold" not in st:
                errors.append(f"{w}: needs 'threshold'")
            else:
                _p(cfg, st["threshold"], errors, w)
        elif op == "where":
            for k in ("cond", "then", "else"):
                if st.get(k) not in produced:
                    errors.append(f"{w}: {k}={st.get(k)!r} not produced by an earlier step")
        if out:
            produced.add(out)
    if cfg["signal"].get("output") not in produced:
        errors.append(f"signal.output {cfg['signal'].get('output')!r} is not a produced series")
    if steps and all(s.get("op") in ("field", "const") for s in steps):
        warnings.append("the signal is a raw field with no transformation — usually a lookback or a normalisation is intended")
    # positions
    pos = cfg["positions"]; mode = pos.get("mode")
    if mode not in MODES:
        errors.append(f"positions.mode must be one of {sorted(MODES)}")
    else:
        for k in MODES[mode]:
            if k not in pos:
                errors.append(f"positions.{k} is required for mode {mode}")
            else:
                v = _p(cfg, pos[k], errors, f"positions.{k}")
                if v is not None and k == "quantile" and not (0 < v <= 0.5):
                    errors.append("positions.quantile must be in (0, 0.5]")
                if v is not None and k == "n" and v < 1:
                    errors.append("positions.n must be ≥ 1")
    if mode == "threshold" and "long_above" not in pos and "short_below" not in pos:
        errors.append("positions mode threshold needs long_above and/or short_below")
    if pos.get("weighting", "equal") not in ("equal", "signal", "rank"):
        errors.append("positions.weighting must be equal | signal | rank")
    if "side_gross_pct" in pos and not (1 <= pos["side_gross_pct"] <= 100):
        errors.append("positions.side_gross_pct must be 1–100")
    if not pos.get("long", True) and not pos.get("short", True):
        errors.append("positions: at least one side must be on")
    # schedule
    sch = cfg["schedule"]
    if sch.get("rebalance") not in REBAL:
        errors.append(f"schedule.rebalance must be one of {sorted(REBAL)}")
    if "holding_days" in sch:
        h = _p(cfg, sch["holding_days"], errors, "schedule.holding_days")
        if h is not None and not (1 <= h <= 60):
            errors.append("schedule.holding_days must be 1–60")
    # alternatives
    for k, v in (cfg.get("alternatives") or {}).items():
        if k == "signal.output":
            bad = [x for x in v if x not in produced]
            if bad:
                errors.append(f"alternatives.signal.output: {bad} are not produced series")
        elif k == "schedule.rebalance" and any(x not in REBAL for x in v):
            errors.append("alternatives.schedule.rebalance has an unknown value")
    return errors, warnings


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    errors, warnings = validate(cfg)
    for w in warnings:
        print("warning:", w)
    if errors:
        for e in errors:
            print("error:", e)
        sys.exit(1)
    print("valid:", cfg.get("hypothesis", "")[:80])
    for k, p in (cfg.get("params") or {}).items():
        print(f"  {k} = {p['value']}" + (f"  sweep {p['range']}" if "range" in p else "  (pinned)"))


if __name__ == "__main__":
    main()
