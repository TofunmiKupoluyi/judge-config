# REPORT FORMAT — the `VERDICT` comment on a hypothesis ticket (headline first; a reader who sees a losing evaluation window stops at the first table)

```
# <ticket> — <title> → <VERDICT, verbatim from the battery>

## Headline (standalone sleeve: Monday Q5 long / Q1 short, 1× gross of notional, one-week hold, BTC-hedged, net of funding and 20 bp per unit turnover)
| window / year | return | Sharpe | Sortino | max DD | worst day | hit rate | turnover ×N/day | cost drag %/yr |
|---|---|---|---|---|---|---|---|---|
| calibration 2022-01 → 2023-12 | | | | | | | | |
| evaluation 2024-01 → <EVAL_END> | | | | | | | | |
| full | | | | | | | | |
| 2022 | | | | | | | | |
| 2023 | | | | | | | | |
| 2024 | | | | | | | | |
| 2025 | | | | | | | | |
| 2026 YTD | | | | | | | | |
Gates (both windows): K1 <calib t / eval t> · K2 <mono> · K3 <resid frac> · K4 <placebo t; lead/lag> · K5 <net bp/wk>. Master + 10% overlay: ΔSharpe <…>.
Backtest exhibit: <attachment> (tools/backtest_report.py; per year → monthly → $10M metric table → panels → charts)

## Findings
1. **Hypothesis** — mechanism, counterparty, why it resolves within the horizon (from the frozen PREREG)
2. **Signal definition** — exact, with as-of timing; classification (alpha / filter / veto / sizing / regime / exit / execution)
3. **Experiment** — battery variant (xs-7d / xs-1d / mft-H), windows, EVAL_END, weeks carrying the feature per window, universe size
4. **Core results** — per window: IC mean / t · residual IC t / fraction · monotonicity · Q5−Q1 raw / funding-adjusted / net bp per week (t) · placebo t · lead / lag t
5. **Where the dollars come from** — quintile means (is the spread the bottom quintile losing?), long vs short leg, MDV tercile, year table
6. **Redundancy** — residual after the funding-carry / illiquidity-size / taker-flow sleeves and log MDV / log mcap; nearest sleeve and the correlation
7. **Costs and execution** — net at 20 bp round trip; turnover; horizon vs the decay the mechanism implies; capacity (names carrying it, MDV tier, participation at $1M / $10M)
8. **Statistical concerns** — N (global), family count, how many of this family pointed the same way; concentration of the spread in top names / months
9. **Failure modes** — what would make this fake: lookahead, survivorship, one event, hidden beta, a data artefact
10. **Objection check** — the gate objection, borne out / not borne out, one line
11. **Verdict** — verbatim, and the ONE gate or number that decided it
12. **Next experiment** — exactly one; it becomes the Follow-up ticket if the verdict is WATCH or RESEARCH FURTHER

PREREG sha256 <…> · signal sha256 <…> · code verified <yes / findings> · N = <…> (family <f> count <k>)
```
Every number carries its window and unit. The verdict line is the battery's; a gate is never rounded; a re-run with a changed feature is a new ticket.
