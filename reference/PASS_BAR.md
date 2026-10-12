# PASS BAR — the seven lines every framework must clear (fixed; computed by `tools/judge_run.py`; changed only by a human via escalation)

All numbers are for the **chosen configuration** (the sweep's best point, selected on the calibration window only) run once over the full sample under the risk framework **without the drawdown governor** — caps, max-loss tiers, BTC hedge, full cost model, funding, 1× gross — i.e. the alpha at its full risk budget. The same book is also run **with** the production CPPI governor and reported as the *deployable* line (Sharpe, max DD, average multiplier, share of days at the 5 % floor); only line 3 reads that run. Statistics start on the **first traded day** (warm-up days before the signal exists are excluded and counted). Every line must pass; the result comment prints each with its number.

*Why the governor is kept out of the headline (2026-10-12):* once a sleeve's drawdown reaches ~9 % the CPPI multiplier hits its 5 % floor and, at 1–2 % gross, the book can no longer earn its way back — the NAV line goes flat for months (M-20261012-rf1: 44 % / 57 % of calibration / evaluation days at the floor, longest run 540 days). A headline computed on that path measures the governor, not the signal, and a calibration sweep selected on it is selection on governor luck.

| # | line | rule | why it is there |
|---|---|---|---|
| 1 | Sharpe in both windows | calibration (2022-01 → 2023-12) Sharpe ≥ 1.0 **and** evaluation (2024-01 → last completed month end) Sharpe ≥ 1.0 | a signal that only worked when it was selected is selection, not signal |
| 2 | Evaluation return | cumulative net return over the evaluation window > 0 | Sharpe can be positive on a rounding error; the untouched window must have made money |
| 3 | Drawdown (governed run) | maximum drawdown of the **governed** run ≤ 10 % **and** ≤ 25 % of its days at the governor's 5 % floor | the production program's hard stop is 10 %; a framework that would have been parked by the governor for a quarter of its life is not deployable, whatever its ungoverned Sharpe |
| 4 | Cost stress | Sharpe at 2× fee, spread and impact (full sample) > 0.5 | live costs have run near the model's; a framework that dies at 2× has no margin for a bad execution month |
| 5 | Placebo | mean |t| of the daily returns over three fixed random relabellings of the names (seeds 1–3) < 1.5 | if shuffled names do as well, the construction (hedge, governor, sizing) is earning the return, not the ranking |
| 6 | Parameter plateau | median calibration Sharpe of the grid points within ±50% of every chosen parameter ≥ 0.7 (alternatives must match the chosen ones) | a knife-edge optimum is a sweep artefact; "no tunables" passes vacuously |
| 7 | Concentration | the top 5 names' share of positive per-name P&L ≤ 50% | an effect that lives in five names is an anecdote, and is uncapturable at size |

Reported but not gated (they feed the reading and the **For Scout** line): the deployable (governed) line, first traded day and warm-up days excluded, raw-sleeve Sharpe (no caps / hedge / governor), funding P&L per year, cost drag per year, turnover, worst 1 / 7 / 30-day returns, per-year table, correlation with the three reference sleeves (`examples/ref_*.json`), the full sweep grid (best / median / worst calibration Sharpe).

Windows: `CALIB = 2022-01-01 → 2023-12-31`; evaluation from 2024-01-01 to `--eval-end` (default: the last completed month end at run time). Data begins 2021-10-01 so that 60-day betas and 21-day medians exist on the first calibration day. These constants live at the top of `tools/judge_run.py` and nowhere else.

Verdict: **PASS** → Human Review. **FAIL** → Critiqued (round < 5) or Discarded (round 5). No other outcomes; no partial credit; no "promising".
