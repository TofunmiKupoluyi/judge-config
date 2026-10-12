# TICKET FORMAT — one Linear issue per experiment (identical copy in scout-config and judge-config; both agents write against it)

An **experiment** = one alpha hypothesis. It lives in ONE Linear issue (team NoSyntax). Scout writes the description once (round 1) and every later framework as a comment; Judge writes one result comment per round and attaches the exhibit files. The status is the whole state machine: **Idea Generated** (Judge's turn) → **Critiqued** (Scout's turn) → … → **Human Review** or **Discarded**. Maximum five frameworks per experiment. Descriptions are never edited after creation.

## Title
```
hypothesis: <slug> — <one-line hypothesis>
```
`slug` = 2–4 lowercase words joined by hyphens (`illiquidity-premium`, `post-cascade-reversal`). Keep the one-liner under 120 characters.

## Description (written by Scout at creation = Framework 1/5)
```
## Hypothesis
<one line, plain words, falsifiable>

## Why it should exist
<2–4 sentences: who creates the gap, why they behave predictably, why it is not arbitraged away, expected horizon. Cite the idea-map cell and the nearest ledger experiment by identifier.>

## Framework 1/5
<Plain words a trader can read: what is ranked and over what lookback; who is bought and who is sold (top/bottom N, quantile, threshold); how much per name or per side; how often it rebalances; how long a position is held; what the one-line reason is that THIS construction captures the mechanism.>

## Trade config
```json
{ ...the config, schema 1.0, "round": 1, "experiment": "" ... }
```

## Expectation
<One paragraph: where the dollars come from after funding and costs; what Scout expects (usually FAIL) and what would kill the hypothesis.>
```
Exactly one fenced ```json block in the description. Judge parses the LAST ```json block of the newest Scout post (description for round 1, comment for later rounds) — nothing else in the text is read by code.

## Scout's later rounds (comment on the same issue; bundle the move to Idea Generated)
```
[SCOUT FRAMEWORK n/5]

## What changed and why
<Which failure in Judge's "For Scout" section this answers, and how the construction differs materially (different observable / conditioning / horizon class / universe slice / leg — not a parameter change).>

## Framework n/5
<plain words, same elements as Framework 1>

## Trade config
```json
{ ... "round": n, "experiment": "<NOS-identifier>" ... }
```
```

## Judge's result (comment on the same issue; one per round; files attached with `attach_file_to_issue`)
```
[JUDGE RESULT n/5 — PASS|FAIL]
<hypothesis and the framework in one line each>

**Headline** (best configuration of the sweep, selected on the calibration window only; caps, hedge, costs, funding; no drawdown governor; from the first traded day)
| window / year | return | Sharpe | Sortino | max DD | worst day | hit rate | days |
...
<turnover · cost drag · funding · raw-sleeve Sharpe · worst 1/7/30 days>

**Bar** — one ✓/✗ line per criterion with the number

**Sweep** — grid size, the parameters swept, the chosen point, calibration-Sharpe best / median / worst
**Concentration** — top-5 names' share of positive P&L
**Redundancy** — correlation with the three reference sleeves

**For Scout** — plain-words diagnosis of what failed (or, on PASS, what to watch): no signal in either window · signal only early · costs eat it · concentrated · tracks sleeve X · not robust to its parameters · fails the placebo. No engine numbers beyond the headline; no suggestions of parameter values.

**Next** → Critiqued (round n+1/5 is Scout's) | Discarded (five frameworks failed) | Human Review (bar passed)
Exhibit: <ticket>_r<n>_backtest.html · numbers: <ticket>_r<n>_result.json · config: <ticket>_r<n>_best_config.json
```
Attachments per round: the backtest HTML, the result JSON, the best-config JSON (and the daily-returns CSV when under 20 MB).

## Status transitions (only these)
| who | from | to | when |
|---|---|---|---|
| Scout | — | Idea Generated | `create_issue` (round 1) |
| Judge | Idea Generated | Critiqued | `[JUDGE RESULT n/5 — FAIL]`, n < 5 |
| Judge | Idea Generated | Discarded | `[JUDGE RESULT 5/5 — FAIL]`, or an invalid config Scout cannot fix after Judge's note (counts as the round) |
| Judge | Idea Generated | Human Review | `[JUDGE RESULT n/5 — PASS]` |
| Scout | Critiqued | Idea Generated | `[SCOUT FRAMEWORK n+1/5]` posted |
| Scout | Critiqued | Discarded | no materially different framework exists / hard blocker named |

Round counting is mechanical: n = (number of `[JUDGE RESULT` comments on the issue) + 1 for the next Judge run; the framework Judge tests is the newest Scout post. A Judge run that finds its own result for the current round already posted ends without posting (idempotent re-triggers).
