#!/usr/bin/env python3
"""Pull the trade config and the round number out of a saved Linear thread — so Judge never retypes JSON.

Save the issue as text (description first, then comments oldest-first, as `read_issue` returns them) and run:

    python3 tools/extract_config.py thread.md --out config.json
    → prints  round=<n>  source=<description|comment k>  judge_results=<m>  and writes the config

Rules (reference/TICKET_FORMAT.md): the config to test is the LAST ```json block of the NEWEST Scout post — the description
when there is no `[SCOUT FRAMEWORK` comment, else the newest such comment. The round to run = (number of `[JUDGE RESULT`
headings in the thread) + 1. If a `[JUDGE RESULT <n>/5` heading already exists for that round, exit 3 (nothing to do:
idempotent re-trigger). Exit 2 when no JSON block is found or it does not parse — then Judge posts the validation note.
A heading is recognised only as a whole line on its own; a heading quoted mid-line inside a post (prose backticks, a
config's notes string) is not a post boundary.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

FENCE = re.compile(r"```json\s*(.*?)```", re.S)
# A heading counts only when it is a whole line on its own (TICKET_FORMAT), so a heading QUOTED
# inside a post — in prose backticks or a config's notes string — sits mid-line and never matches.
# Linear thread dumps may escape the brackets as \[ \].
SCOUT = re.compile(r"^\s*\\?\[SCOUT FRAMEWORK\s*(\d+)\s*/\s*5\\?\]\s*$", re.M)
JUDGE = re.compile(r"^\s*\\?\[JUDGE RESULT\s*(\d+)\s*/\s*5\s*[—-]\s*(PASS|FAIL)\\?\]\s*$", re.M)


def extract(text):
    judge_hits = list(JUDGE.finditer(text)); judge_rounds = [int(m.group(1)) for m in judge_hits]
    next_round = len(judge_rounds) + 1
    scout_posts = list(SCOUT.finditer(text))
    last_scout_pos = scout_posts[-1].start() if scout_posts else -1
    if judge_hits and judge_hits[-1].start() > last_scout_pos:
        return None, next_round, "none", judge_rounds, "ALREADY_ANSWERED"     # Judge's result is newer than Scout's newest post
    if scout_posts:
        last = scout_posts[-1]; seg = text[last.end():]
        nxt = JUDGE.search(seg); seg = seg[: nxt.start()] if nxt else seg      # the comment ends where Judge's next result starts
        source = f"comment [SCOUT FRAMEWORK {last.group(1)}/5]"
    else:
        first_judge = JUDGE.search(text); seg = text[: first_judge.start()] if first_judge else text
        source = "description"
    blocks = FENCE.findall(seg)
    if not blocks:
        return None, next_round, source, judge_rounds, "no ```json block in the newest Scout post"
    try:
        cfg = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        return None, next_round, source, judge_rounds, f"JSON does not parse: {e}"
    return cfg, next_round, source, judge_rounds, ""


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("thread"); ap.add_argument("--out", required=True); ap.add_argument("--ticket", default="")
    a = ap.parse_args(); text = open(a.thread, encoding="utf-8").read()
    cfg, rnd, source, judge_rounds, err = extract(text)
    if err == "ALREADY_ANSWERED" or rnd in judge_rounds or rnd > 5:
        print(f"nothing to do: judge results already posted for rounds {judge_rounds}; newest Scout post already answered"); sys.exit(3)
    if cfg is None:
        print(f"round={rnd} source={source} error={err}"); sys.exit(2)
    cfg["round"] = rnd
    if a.ticket:
        cfg["experiment"] = a.ticket
    json.dump(cfg, open(a.out, "w", encoding="utf-8"), indent=1)
    print(f"round={rnd} source={source} judge_results={len(judge_rounds)} wrote {a.out}")


if __name__ == "__main__":
    main()
