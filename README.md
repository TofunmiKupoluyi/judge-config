# judge-config

Configuration for **Judge**, agent B of a two-agent alpha-research desk on Binance USDT-M perpetual futures (market-neutral, cross-sectional). Judge is the systematic half: it critiques and gates the hypothesis tickets that **Scout** (its own config, `scout-config`) posts in Linear, freezes the pre-registration, and — once the test stages are wired — verifies the submitted signal code, runs each hypothesis as a structured standalone strategy through the historical perp data, posts a headline-first report (return and Sharpe per year on top, findings below) with the standard backtest exhibit, keeps the ledger honest, and carries survivors through robustness and portfolio stages to a dossier a human signs. Judge runs as fresh-context sessions triggered by a ticket's status; the ticket is the state machine and Linear (team **NoSyntax**) is Judge's only memory between runs — see `AGENTS.md` §0.

## Layout

| Path | What it is |
|---|---|
| `AGENTS.md` | Judge's master prompt. §0 the Linear protocol and the status table (which statuses are Judge's turn, today and when later stages are wired), §4 the actions, §5 how a result is read, §9 the three human decisions. |
| `.agents/skills/research-judge/` | Judge's run skill: actions by ticket status, the context-pack queries, the critique/gate JSON, the VERIFY checklist, the strategy pipeline, robustness r1–r9, the portfolio stage and adoption bar, ledger and audit duties. |
| `.agents/skills/data-and-backtest/` | The data model and panel contract (the `panel` object the battery must supply), point-in-time rules, the fixed battery (target, windows, gates K1–K5, verdicts, costs), the standalone strategy run, the replica stage. |
| `.agents/skills/binance-futures-data/` | The S3 data bucket: candles (1m/1h/1d), funding, open-interest metrics — layout, schemas, access, pitfalls. |
| `.claude/skills` | Symlink to `.agents/skills` so Claude Code discovers the skills. |
| `reference/RISK_FRAMEWORK.md` / `.json` | The production risk model every replica-stage simulation runs under (universe, sizing, caps, hedge, governor, costs, monitoring, adoption bar, rollback rule). The `.json` is the machine-readable twin the battery/replica code reads. |
| `reference/RESEARCH_PRINCIPLES.md` | The full research framework (41 principles); binding. |
| `reference/REJECTION_PATTERNS.md` | The five ways 140+ past tests failed on this universe; the critique names the pattern a proposal most risks. |
| `reference/GLOSSARY.md` | Human names for the production sleeves (`a4` FundingCarry, `a12` IlliquidityPremium, `a90x_ratio` TakerFlow-R) and governors. |
| `reference/REPORT_FORMAT.md` | The `VERDICT` comment: headline table + gate line first, then the twelve findings sections. |
| `reference/DOSSIER_FORMAT.md` | The CANDIDATE dossier attached to a Human Review ticket. |
| `tools/backtest_report.py` | Generates the standard backtest exhibit (per calendar year → monthly returns → $10M metric table → key-metric panels → time-series charts; no note box) from a daily-return series. numpy + pandas only. |

## What Judge touches, and what it never does
This is a config repo: no research code lives here. Judge's surface is the Linear API (records), the S3 bucket (read-only), the battery the harness author wires (`harness.py`, the robustness and portfolio runners — their contracts are in `data-and-backtest`), and `tools/backtest_report.py`. Judge never edits the battery, windows, gates, cost model, `reference/` or `tools/`; never pushes to a repo; never touches production; never moves a ticket to Human Review for anything but a dossier that passed the adoption bar. Secrets are referenced by name only: `LINEAR_API_KEY`, `AWS_S3_ACCESS_KEY`, `AWS_S3_ACCESS_KEY_SECRET`.

## Status machine (shared with scout-config; the harness author wires the triggers)
`Idea Generated` (Scout) → Judge: `Critiqued` (Scout revises → Idea Generated) · `Approved for Test` (PREREG frozen) · Closed. When wired: `Approved for Test` → build → Judge VERIFY/battery → `Verdict Posted` → Closed · `Follow-up` (one linked next ticket) · `Robustness` → `Portfolio` → `Human Review` (dossier) · `Discarded` (Scout, on an unfixable hard blocker). Tickets wait in a status whose stage is not wired; Judge never improvises a stage.

## What must exist outside this repo
The triggers (a Linear webhook on status change → a Judge run; a weekly schedule for AUDIT/DIGEST) and the battery: a `panel` object per the `data-and-backtest` contract, the K1–K5 gates and verdict, the standalone strategy run that writes the daily series `tools/backtest_report.py` consumes, the robustness runner, and the replica stage reading `reference/RISK_FRAMEWORK.json`. The specs here are its blueprint. Three panel inputs are unprovisioned in the data today (market cap, premium index, full symbol tags — `data-and-backtest` §1).
