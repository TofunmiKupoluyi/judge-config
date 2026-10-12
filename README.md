# judge-config

Configuration and engine for **Judge**, agent B of a two-agent alpha-research desk on Binance USDT-M perpetual futures (market-neutral, cross-sectional). **Scout** (its own repo, `scout-config`) posts experiments to Linear — a one-line alpha hypothesis, a plain-words trade framework and a trade config. Judge runs the config through a fixed backtest engine and reports PASS or FAIL with the headline numbers first and the backtest exhibit attached. Every run is a fresh-context session; Linear (team **NoSyntax**) is the only memory.

## What Judge does with a ticket in Idea Generated
1. Extracts the newest trade config from the thread (`tools/extract_config.py`; round n = number of previous `[JUDGE RESULT` comments + 1).
2. Validates it (`tools/validate_config.py`). Invalid → FAIL with the errors, round consumed.
3. Runs `tools/judge_run.py` once: sweep the declared parameter ranges and alternatives on the **calibration window only** (2022–23) → chosen config over the full sample through Obsidian's hourly production-replica engine without its drawdown governor (caps, no-trade band, participation cap, BTC hedge, cost model, per-print funding) → verification battery (raw sleeve, 2× costs, shuffled-name placebos, parameter plateau, concentration, reference-sleeve correlations) → the seven-line pass bar (`reference/PASS_BAR.md`).
4. Posts the engine's result markdown verbatim as `[JUDGE RESULT n/5 — PASS|FAIL]` (headline table, bar checklist, sweep, concentration, redundancy, a code-written **For Scout** diagnosis), attaches the HTML exhibit, result JSON and the chosen config, and moves the ticket: PASS → **Human Review**; FAIL → **Critiqued** (Scout proposes a materially different framework for the same hypothesis, up to five) or **Discarded** after round 5.

Judge never edits a config, never re-runs to change a verdict, never sees the evaluation window during selection, and never changes the engine, windows, costs or bar — those are human decisions, requested by escalation.

## Layout
| Path | What it is |
|---|---|
| `AGENTS.md` | Judge's master prompt: §0 Linear protocol and the four statuses, §2 never-do list, §4 the RUN, §5 how to read a result, §8 the human decisions. |
| `.agents/skills/research-judge/` | The RUN command by command, validation-fail and engine-failure handling, the reading, Linear tools, runtime. |
| `.agents/skills/engine-and-bar/` | What the engine does: data and point-in-time rules, config semantics, the risk framework applied to every config, windows and sweep, verification battery, bar, output files. |
| `.agents/skills/binance-futures-data/` | The S3 bucket (layout, schemas, access) the engine reads. |
| `tools/judge_run.py` | The whole procedure; `--config --ticket --round --out`; constants `CALIB` and `BAR` at the top. |
| `tools/obsidian_engine.py` | **Obsidian's production-replica engine, verbatim** (`backtest/engine_v2.py`), run hourly with the drawdown governors off. |
| `tools/engine.py` | Trade-config DSL → daily targets → `run_hourly` (targets handed to the Obsidian engine as its `extra` panel); the old daily simulator survives only as the raw-sleeve diagnostic. |
| `tools/panels.py` | Hourly panels from the bucket with Obsidian's exact definitions (universe hysteresis, delisting ramp, beta, mdv21, caps, funding). |
| `tools/data.py` | Point-in-time daily fields from the bucket layout (also works on a local mirror). |
| `tools/validate_config.py`, `reference/TRADE_CONFIG_SCHEMA.json` | Config language (identical copies in scout-config). |
| `tools/extract_config.py` | Round number + config from a saved ticket thread; exit 3 = already answered. |
| `tools/backtest_report.py` | The HTML exhibit (fixed structure, no note box). |
| `reference/PASS_BAR.md` | The seven bar lines and the windows. |
| `reference/TICKET_FORMAT.md` | Ticket layout and status transitions (identical copy in scout-config). |
| `reference/RISK_FRAMEWORK.md` / `.json` | The production risk model (prose + the machine-readable twin the engine loads). |
| `reference/WHAT_FAILED_BEFORE.md`, `RESEARCH_PRINCIPLES.md`, `GLOSSARY.md` | Context for the reading: the inherited failure patterns, the research stance, the names of the known sleeves. |
| `examples/` | Three reference sleeves (`ref_*.json`, used for redundancy correlations) and the brief's worked example. |
| `tests/test_engine.py` | Offline smoke test on a synthetic bucket (noise → FAIL; planted premium → PASS). |
| `.claude/skills` | Symlink to `.agents/skills`. |

## Running it
```
pip install -r requirements.txt
export AWS_S3_ACCESS_KEY=…  AWS_S3_ACCESS_KEY_SECRET=…        # read-only bucket credentials, by name only
python3 tools/validate_config.py examples/example_illiquidity_top10.json
python3 tools/judge_run.py --config examples/example_illiquidity_top10.json --ticket NOS-0 --round 1 --out runs/NOS-0/
# local mirror / synthetic bucket:  --data /path/to/mirror   (or BINANCE_DATA_ROOT)
python3 tests/test_engine.py                                    # offline, ~1–2 min, builds its own synthetic bucket
```
A full run on the real bucket: data load once (~minutes), ≤ 48 sweep simulations plus ~8 verification runs; 2–4 GB of memory.

## What must exist outside this repo
The four Linear statuses in team NoSyntax (**Idea Generated**, **Critiqued**, **Discarded**, **Human Review**); the Linear integration on the agent; the bucket credentials in the agent's environment; Python 3.10+ with pandas, numpy, pyarrow, s3fs. Scout's side is `scout-config`.
