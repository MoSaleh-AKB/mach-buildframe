#!/usr/bin/env python3
"""One-shot read of every team-console tab, plus each agent's logs (read-only).

Uses MoSaleh's hidden headless Edge (`edge fetch`, his console login, never his screen),
so it runs on his Mac. Only GET on /v1/team/*, /v1/agents and /v1/logs. Never /v1/admin/*.

    python3 tools/console/check.py            # full report to stdout + tools/console/out/<time>/
    python3 tools/console/check.py --short    # roster, deployments, agent health, journeys only
    python3 tools/console/check.py --logs 400 # more log lines per agent (default 300)

What it shows:
  wave        level, multiplier, rules in force, agenda clock
  roster      role -> confirmed agent, visibility
  deployments per deployment: agent, role, step, status, failed conformance stages
  agents      per live agent: last log time, process up or SHUT DOWN, deliveries, brain runs,
              errors, last non-health lines
  journeys    open / closed / red counts, newest 10 with last message and verdict
  reputation, usage (LLM calls, tokens, spend), market, conversations, world (counts)
Raw JSON of every tab is saved next to the report.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = "https://projectnandaitaly.ai"
TABS = ["team", "assignment", "deployments", "wave", "reputation", "usage", "market",
        "conversations", "world", "journeys", "profile"]
OUT = Path(__file__).resolve().parent / "out"


def fetch(path: str) -> object | None:
    # The console rate-limits (HTTP 429): space calls out and retry once after a pause.
    for attempt in range(2):
        time.sleep(1.5)
        try:
            res = subprocess.run(["edge", "fetch", BASE + path], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"_error": str(exc)}
        text = res.stdout.strip()
        if "429" in (res.stderr or "") or text.startswith("<!doctype"):
            if attempt == 0:
                time.sleep(20)
                continue
            return {"_error": "HTTP 429 rate limited"}
        break
    if not text:
        return {"_error": (res.stderr or "empty").strip()[:200]}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_error": "not json: " + text[:200]}


def rows_of(data: object, *keys: str) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def short(value: object, n: int = 150) -> str:
    return str(value).replace("\n", " / ")[:n]


def agent_ids(assignment: object, deployments: object) -> dict[str, str]:
    ids: dict[str, str] = {}
    if isinstance(assignment, dict):
        for r in assignment.get("roles", []):
            for a in r.get("agents", []):
                if isinstance(a, dict) and a.get("agent_id"):
                    ids[a["agent_id"]] = r["family"]
            if r.get("confirmed_agent"):
                ids[r["confirmed_agent"]] = r["family"]
    for d in rows_of(deployments, "deployments"):
        aid = d.get("agent_id")
        if aid and aid not in ids:
            ids[aid] = d.get("family") or d.get("role") or "?"
    return ids


def logs_summary(aid: str, n: int) -> dict:
    data = fetch(f"/v1/logs?agent_id={aid}&q=")
    rows = rows_of(data, "logs", "items", "lines")
    rows = sorted(rows, key=lambda r: str(r.get("time", "")))
    msgs = [str(r.get("message", "")) for r in rows]
    non_health = [r for r in rows if "/health" not in str(r.get("message", ""))]
    shut = [r for r in rows if "Shutting down" in str(r.get("message", "")) or "Finished server process" in str(r.get("message", ""))]
    started = [r for r in rows if "Application startup complete" in str(r.get("message", "")) or "Uvicorn running" in str(r.get("message", ""))]
    last_shut = shut[-1]["time"] if shut else ""
    last_start = started[-1]["time"] if started else ""
    down = bool(last_shut) and (not last_start or last_start < last_shut)
    return {
        "rows": len(rows),
        "first": rows[0]["time"] if rows else "",
        "last": rows[-1]["time"] if rows else "",
        "process": "SHUT DOWN at " + last_shut if down else ("up" if rows else "no logs"),
        "deliveries": sum("delivery" in m for m in msgs),
        "brain_done": sum("brain done" in m for m in msgs),
        "errors": sum(str(r.get("level")) in ("error", "warning") for r in rows),
        "tail": [f"{str(r.get('time'))[11:19]} {r.get('level')} {short(r.get('message'), 120)}" for r in non_health[-8:]],
        "_error": data.get("_error") if isinstance(data, dict) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--logs", type=int, default=300)
    args = ap.parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = OUT / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    tabs = ["assignment", "deployments", "wave", "journeys"] if args.short else TABS
    data = {t: fetch(f"/v1/team/{t}" if t != "team" else "/v1/team") for t in tabs}
    for t, d in data.items():
        (outdir / f"{t}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
    p = print
    p(f"# Console check {stamp}")
    w = data.get("wave") or {}
    if isinstance(w, dict) and not w.get("_error"):
        p(f"\n## Wave: level {w.get('level')} state {w.get('state')} multiplier x{w.get('multiplier')} "
          f"rules {w.get('active_rules')} notice {w.get('rule_notice')} agenda {w.get('agenda_now')}")
    a = data.get("assignment") or {}
    p("\n## Roster (La vostra ASST)")
    if isinstance(a, dict):
        p(f"asst {a.get('asst')} roster_confirmed {a.get('roster_confirmed')}")
        for r in a.get("roles", []):
            vis = [x.get("visibility") or x.get("status") for x in r.get("agents", []) if isinstance(x, dict)]
            p(f"- {r['family']:<18} confirmed {r.get('confirmed_agent') or 'EMPTY'} {vis}")
    p("\n## Deployments (newest first)")
    deps = rows_of(data.get("deployments"), "deployments")
    deps = sorted(deps, key=lambda d: str(d.get("created_at") or d.get("updated_at") or ""), reverse=True)
    for d in deps[:20]:
        failed = [f"{c.get('name')}: {short(c.get('detail'), 80)}" for c in d.get("conformance") or [] if c.get("outcome") == "FAIL"]
        p(f"- {str(d.get('created_at') or '')[11:19]} {d.get('agent_id')} v{d.get('version')} step={d.get('step')} "
          f"status={d.get('status')} {('FAIL ' + '; '.join(failed)) if failed else ''} {short(d.get('error') or d.get('message') or '', 100)}")
    p("\n## Agents (logs)")
    for aid, fam in agent_ids(a, data.get("deployments")).items():
        s = logs_summary(aid, args.logs)
        p(f"- {fam:<18} {aid}: process {s['process']}, deliveries {s['deliveries']}, brain_done {s['brain_done']}, "
          f"errors {s['errors']}, logs {s['first'][11:19]}..{s['last'][11:19]} ({s['rows']} rows)")
        for line in s["tail"]:
            p(f"    {line}")
        (outdir / f"logs_{aid.replace('agent:', '')}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False))
    j = rows_of(data.get("journeys"), "journeys", "items")
    states: dict[str, int] = {}
    for x in j:
        states[str(x.get("state"))] = states.get(str(x.get("state")), 0) + 1
    p(f"\n## Journeys (Percorsi): {len(j)} total {states}")
    for x in j[:3]:
        det = fetch(f"/v1/team/journeys/{x.get('need_id')}")
        if isinstance(det, dict) and det.get("_error"):
            p(f"- {x.get('need_id')} {x.get('state')} (detail: {det['_error']})")
            continue
        msgs = rows_of(det, "messages") if isinstance(det, dict) else []
        last = msgs[-1] if msgs else {}
        verdict = det.get("verdict") if isinstance(det, dict) else None
        p(f"- {x.get('need_id')} {x.get('state')} msgs {len(msgs)} last: {last.get('sender') or last.get('from')} -> "
          f"{last.get('to') or last.get('recipient')} | {short(last.get('text'), 90)} | verdict {short(verdict, 80)}")
    if not args.short:
        for t in ("reputation", "usage", "market", "conversations", "world", "team", "profile"):
            d = data.get(t)
            if isinstance(d, dict):
                summ = {k: (f"list[{len(v)}]" if isinstance(v, list) else (short(v, 60) if not isinstance(v, dict) else "{...}")) for k, v in d.items()}
                p(f"\n## {t}: {summ}")
            else:
                p(f"\n## {t}: list[{len(d) if isinstance(d, list) else 0}]")
    p(f"\nRaw JSON: {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
