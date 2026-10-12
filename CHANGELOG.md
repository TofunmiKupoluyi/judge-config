# Changelog

## 2026-10-12 — redesign v2: config-driven engine, fixed bar, four statuses
- Judge now RUNS instead of critiquing: on a ticket in Idea Generated it extracts the newest trade config (`tools/extract_config.py`), validates it, runs `tools/judge_run.py` once (calibration-only sweep of Scout's declared ranges/alternatives → chosen config on the full sample under the production risk framework → verification battery → seven-line pass bar), posts the headline-first result with the HTML exhibit attached, and moves the ticket (PASS → Human Review; FAIL → Critiqued, or Discarded after round 5).
- New engine: `tools/data.py` (point-in-time daily fields from the S3 layout), `tools/engine.py` (signal DSL, positions, risk framework, costs, funding, CPPI governor, BTC hedge), `tools/judge_run.py`, `tools/validate_config.py`, `reference/TRADE_CONFIG_SCHEMA.json`, `reference/PASS_BAR.md`, `examples/` (three reference sleeves + the worked example), `tests/test_engine.py` (offline: noise → FAIL, planted premium → PASS).
- The result comment carries a code-written **For Scout** diagnosis in a fixed vocabulary; Judge adds a short reading and never parameter advice.
- Removed: the CRITIQUE/PREREG/VERIFY/VERDICT stages, the K1–K5 battery spec (`data-and-backtest` skill), `REPORT_FORMAT.md`, `DOSSIER_FORMAT.md`, `REJECTION_PATTERNS.md`. Added `WHAT_FAILED_BEFORE.md`, `TICKET_FORMAT.md` (identical in scout-config), the `engine-and-bar` skill, `requirements.txt`.
- `AGENTS.md`, `research-judge` skill and README rewritten for the new flow. Linear tool protocol unchanged.
