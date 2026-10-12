---
name: engine-and-bar
description: "What Judge's backtest engine does to a trade config and how a result is produced: data fields and point-in-time rules, the signal/positions/schedule semantics, the risk framework applied to every config (universe, caps, BTC hedge, CPPI governor, costs, funding), the windows, the calibration-only sweep, the verification battery, the pass bar, and the output files. Load before reading or explaining any result. Nothing here is tunable per experiment."
---

# Engine and bar

Everything below is implemented in `tools/` and parameterised only by `reference/RISK_FRAMEWORK.json`, `reference/TRADE_CONFIG_SCHEMA.json` and the constants at the top of `tools/judge_run.py`. Judge runs it; nobody edits it per test. Changes are a human decision (AGENTS.md §8).

## 1. Files
| file | role |
|---|---|
| `tools/data.py` | `Data(root, start, end)`: loads 1h klines, funding and daily metrics from the bucket layout (`binance-futures-data` skill) and builds the daily point-in-time fields |
| `tools/validate_config.py` | schema + semantic validation of a config (same file in scout-config) |
| `tools/engine.py` | `build_signal`, `build_targets`, `simulate`, `run(cfg, data, risk, cost_mult, governor, shuffle_seed, window)` |
| `tools/judge_run.py` | the whole procedure: validate → sweep → chosen config full sample → verification → bar → result files |
| `tools/extract_config.py` | round number + config from a saved ticket thread |
| `tools/backtest_report.py` | the HTML exhibit (fixed structure, no note box) |
| `examples/ref_*.json` | the three reference sleeves (funding carry, illiquidity, taker flow) used for the redundancy correlations |
| `reference/RISK_FRAMEWORK.json` / `.md` | the production risk model the engine applies |
| `reference/PASS_BAR.md` | the seven bar lines |

## 2. Data and point-in-time rules
Hourly klines are stamped at bar close; every daily field at row `t` (00:00 UTC) uses only data from before `t`: price fields are the 00:00 close, activity fields are day `t−1`'s aggregates stamped at `t`, metrics (OI, long/short ratios) are day `t−1`'s last snapshot stamped at `t` (forward-filled ≤ 2 days), funding is the sum of prints inside day `t−1` (positive = longs paid). Beta is a 60-day hourly beta to BTC, lagged. Symbols with `_` (delivery contracts) are dropped; `mcap` is NaN (unprovisioned). Fields: `close ret log_ret resid_ret btc_ret high low range_pct volume_usd taker_buy_usd taker_sell_usd taker_imbalance n_trades funding funding_3d mdv21 adv7 amihud21 vol30 oi_usd oi_change_1d ls_ratio_acct ls_ratio_top_acct ls_ratio_top_pos taker_ls_vol age_days mcap` — definitions in the docstring of `tools/data.py` and, for Scout, in scout-config's `trade-config-spec` skill (keep the two in sync).

Universe (`Data.eligible`): MDV21 ≥ `universe.min_mdv_usd` (default $3M) to enter, ≥ 2/3 of it to stay (hysteresis), age ≥ `min_age_days` (default 30), BTCUSDT excluded, names leaving the data are dropped seven days before their last candle (delisting ramp). Scout may raise the floor or pass `include_only`; nothing else about the universe is theirs.

## 3. Config semantics (what the engine does with each block)
- `signal.steps` are evaluated in order on daily frames (`engine.build_signal`); `"$param"` resolves to `params.<name>.value`. Rolling ops default `min_periods` to 70% of the window; `ewm_mean` uses `window` as span; `zscore_time` uses max(5, window/2); `cross_*` ops act across names per day; `gt`/`lt` give 1/0 with NaN preserved; `where(cond, then, else)`. `signal.output` is the ranking, higher = long.
- `positions` (`engine.build_targets`): on each rebalance day (`1d 2d 3d` = every n days; `1w 2w` = Mondays, every 1 or 2 weeks) the output is restricted to the eligible universe, NaN dropped, and a day with < 10 ranked names is skipped. Modes: `top_bottom_n` (N per side), `quantile` (top/bottom fraction), `threshold` (long above / short below), `proportional` (all names, weight ∝ demeaned output). Weighting: `equal`; `signal` (∝ |x − cross-sectional median|); `rank` (the most extreme name of each leg gets the largest weight). Size: `side_gross_pct` per side (default 50 → gross 1× notional) or `per_name_bps` × names. Entries are held `holding_days` (default = the rebalance interval) and overlapping entries are averaged; `delay_days` shifts execution.
- Targets are pre-risk weights of notional; the risk framework then rescales them.

## 4. Risk framework applied to every config (`engine.simulate`, numbers from `RISK_FRAMEWORK.json`)
Per-name cap = min(5% NAV, 30% of MDV21, 20% of OI$ when known), floored at 0.1% NAV; max-loss stress caps by liquidity tier (5% loss budget ÷ stress move: shorts 200/300/500% → 2.5/1.67/1.0% NAV, longs 50/65/80% → capped by the 5% concentration line; tiers at $100M / $10M MDV); gross cap 4× NAV. BTC hedge solved daily so that |beta-$| and |delta-$| ≤ 10% NAV, re-trading only outside a 2% band; when the alt book itself cannot satisfy both, it is scaled down. CPPI governor: multiplier = clip((9.5% − drawdown) / (10 × σ_u), 5%, 100%) with σ_u the EWMA (span 10) of daily return ÷ multiplier, seeded 1.5%/day, and the DAL clamps 50% / 25% at 5.5% / 7.5% drawdown; de-risk trades always execute. Costs on every trade including the hedge: taker fee 4 bp + half-spread 1 / 3 / 8 bp by MDV tier (≥ $100M / ≥ $20M / below) + impact 15 bp × √(trade $ ÷ MDV); `cost_mult` scales all three (2× stress). Funding settles daily on held positions (longs pay positive funding). P&L is daily from the 00:00 close to the next; NAV compounds. `risk=False` gives the raw sleeve (costs and funding only; no caps, hedge or governor) — reported, not gated.

## 5. Windows and the sweep (`tools/judge_run.py`)
`CALIB = 2022-01-01 → 2023-12-31`; evaluation = 2024-01-01 → `--eval-end` (default: last completed month end). Data loads from 2021-10-01 for warm-up. The grid = five points per declared `params.*.range` (linearly spaced, the submitted value included, integers rounded) × every declared `alternatives` option; capped at `--max-grid` 48 combinations by a fixed-seed random sample that always keeps the submitted combination. Each combination is simulated on the calibration window only under the risk framework **without the governor** (caps, hedge, costs, funding); the best calibration Sharpe is chosen. Plateau = the median calibration Sharpe of the grid points within ±50% of every chosen numeric parameter with the same alternatives. The chosen config is then run once over the full sample without the governor (the headline: the alpha at full risk budget; statistics start on the first traded day, warm-up days are counted and excluded) and once with the production CPPI governor (the *deployable* line: Sharpe, max DD, average multiplier, share of days at the 5 % floor, longest floor run); the evaluation window is read for the first time there. A governor parked at its floor flattens the NAV line for months (see `reference/PASS_BAR.md`); that is why selection and six of the seven bar lines use the ungoverned run and only the drawdown line reads the governed one. The whole grid is written to `_result.json` (`sweep.top10`, best/median/worst in the markdown) so selection is visible.

## 6. Verification battery (chosen config)
All on the ungoverned risk-framework run from the first traded day: raw sleeve Sharpe · Sharpe at 2× costs · placebo = three runs with the symbol columns of the signal shuffled under fixed seeds 1–3 (mean t-stat of daily returns) · top-5 share of positive per-name P&L (and the five names) · worst 1 / 7 / 30-day returns · daily-return correlation with the three reference sleeves (skipped under `--quick`, which is not to be used to change an outcome) · per-year table · turnover, cost drag and funding P&L per year.

## 7. Bar and verdict
`reference/PASS_BAR.md`: all seven lines or FAIL. Verdict → status: PASS → Human Review; FAIL → Critiqued (round < 5) or Discarded (round 5).

## 8. Output files (`--out runs/<ticket>/`, prefix `<ticket>_r<n>`)
`_result.md` (the comment body: `[JUDGE RESULT n/5 — PASS|FAIL]`, hypothesis + framework lines, headline table per window and year, the drag line, the bar checklist, sweep summary, concentration, redundancy, **For Scout**, **Next**, file names) · `_result.json` (everything numeric incl. the grid, placebo seeds, `for_scout`) · `_best_config.json` (the chosen config, round and ticket filled in — the reproducible artefact) · `_daily.csv` (ret, gross, delta, beta, turnover, req_turnover, costs, funding, mult, n_long, n_short) · `_backtest.html` (exhibit: equity curve, drawdown, per-window stats, exposures, turnover, costs; fixed structure, no note box).

## 9. The "For Scout" line
Computed by code from the bar and the diagnostics in a fixed vocabulary (no signal in either window · signal only early / late · costs eat it · funding eats it · concentrated · tracks a known sleeve · not robust to its parameters · fails the placebo · drawdown too deep; on PASS: "passed the bar; watch: …"). It is the only diagnosis Scout designs from; Judge does not add parameter advice.

## 10. Testing the engine offline
A synthetic bucket in the S3 layout (any local directory with `klines/interval=1h/year=YYYY/data.parquet`, `funding/data.parquet`, `metrics/interval=1d/data.parquet`) runs the full procedure with `--data <dir>`; the four `examples/*.json` validate and run; a planted premium produces a PASS, noise produces a FAIL with the full diagnosis. There is no golden-master test in this repo yet — adding one (freeze a result JSON on a fixed synthetic bucket) is the first engine change to escalate for.
