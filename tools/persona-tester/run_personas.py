"""Run citizen personas against our agents with `city run` (local, no login) and write a report.

Examples (from mach-app/tools/persona-tester/):
  python3 -I run_personas.py --root ../../asst                     # every persona
  python3 -I run_personas.py --root ../../asst --smoke             # smoke set after a deploy
  python3 -I run_personas.py --root ../../asst --level 3 --author sina
  python3 -I run_personas.py --root ../../asst --agent gp --parallel 4 --pace-override fast
  python3 -I run_personas.py --root ../../asst --xa                # only agent-to-agent personas
  python3 -I run_personas.py --slots                               # which persona files are still missing
Stdlib only, Python 3.12+.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from checks import OWNERS, Turn, run_checks  # noqa: E402

AGENTS = ("gp", "booking_centre", "hospital", "specialist", "family_counseling")
WHO = ("citizen", "agent", "ledger")
PACE = {"fast": (0.0, 0.0), "normal": (2.0, 5.0), "slow": (20.0, 60.0)}
AUTHORS = ("sina", "sobhan")
MSG_RE = re.compile(r"^\s{4}(agent:\S+) > (\S+) : (.*?)(?: \((wip|closed)\))?\s*$")
CONT_RE = re.compile(r"^\s{12,}(\S.*?)\s*$")  # second and later lines of a multi-line message


# ---------------------------------------------------------------- personas

def load_personas(folder: Path) -> list[dict]:
    out = []
    for f in sorted(folder.rglob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"SKIP {f}: invalid JSON ({exc})", file=sys.stderr)
            continue
        for p in data if isinstance(data, list) else [data]:
            p["_file"] = str(f.relative_to(folder))
            problems = validate(p)
            if problems:
                print(f"SKIP {f.name}:{p.get('id')}: {'; '.join(problems)}", file=sys.stderr)
                continue
            out.append(p)
    return out


def validate(p: dict) -> list[str]:
    bad = []
    for k in ("id", "agent", "level", "turns"):
        if k not in p:
            bad.append(f"missing {k}")
    if p.get("agent") not in AGENTS:
        bad.append(f"agent must be one of {AGENTS}")
    if not isinstance(p.get("level"), int) or not 1 <= p.get("level", 0) <= 8:
        bad.append("level must be 1-8")
    for t in p.get("turns", []):
        if t.get("as") not in WHO or not str(t.get("text", "")).strip():
            bad.append("each turn needs as=citizen|agent|ledger and text")
            break
    return bad


# ---------------------------------------------------------------- one run

def build_inputs(persona: dict) -> list[tuple[str, int | None]]:
    """Lines to type. Second item = persona turn index (None for menu / role switch lines)."""
    lines: list[tuple[str, int | None]] = []
    current = None
    for i, t in enumerate(persona["turns"], 1):
        if current is None:
            lines.append((t["as"], None))
        elif t["as"] != current:
            lines.append((f"/as {t['as']}", None))
        current = t["as"]
        lines.append((t["text"].replace("\n", " "), i))
    return lines


def run_one(persona: dict, root: Path, pace: str, timeout: float) -> dict:
    lines = build_inputs(persona)
    env = dict(os.environ, COLUMNS="2000", PYTHONUNBUFFERED="1", NO_COLOR="1", TERM="dumb")
    proc = subprocess.Popen(["city", "run", persona["agent"], "--root", str(root)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, bufsize=1)
    q: queue.Queue = queue.Queue()

    def reader():
        for line in proc.stdout:
            q.put((time.monotonic(), line.rstrip("\n")))
        q.put((time.monotonic(), None))

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    lo, hi = PACE.get(pace, PACE["fast"])
    start = time.monotonic()
    try:
        for n, (text, turn_idx) in enumerate(lines):
            if turn_idx is not None and turn_idx > 1 and hi > 0:
                time.sleep(random.uniform(lo, hi))
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
            if turn_idx is not None:
                time.sleep(0.4 if hi == 0 else 1.0)  # let the turn start before the next line
        time.sleep(1.5)
        proc.stdin.write("/quit\n")
        proc.stdin.flush()
        proc.stdin.close()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
    except BrokenPipeError:
        pass
    th.join(timeout=5)
    raw: list[tuple[float, str]] = []
    while True:
        try:
            ts, line = q.get_nowait()
        except queue.Empty:
            break
        if line is None:
            continue
        raw.append((ts - start, line.replace(chr(0x2014), "-")))  # city-cli menu uses em dashes; keep repo files clean
    return {"persona": persona, "raw": raw, "turns": parse(raw, lines, persona), "pace": pace}


def parse(raw: list[tuple[float, str]], lines: list[tuple[str, int | None]], persona: dict) -> list[Turn]:
    """Split output at the 'in  <text>' echoes, in the same order we typed them."""
    turns: dict[int, Turn] = {}
    expected = [(t, idx) for t, idx in lines]
    pos = 0
    current: Turn | None = None
    last_msg: dict | None = None
    echo_ts = 0.0
    for ts, line in raw:
        if not line.strip():
            last_msg = None
            continue
        if last_msg is not None and not MSG_RE.match(line):
            cont = CONT_RE.match(line)
            if cont:
                last_msg["text"] += "\n" + cont.group(1)
                continue
        if line.startswith("in  ") and pos < len(expected):
            text, idx = expected[pos]
            if line[4:].strip() == text.strip()[: len(line[4:].strip())] or line[4:].strip() in text:
                pos += 1
                echo_ts = ts
                if idx is not None:
                    who = persona["turns"][idx - 1]["as"]
                    current = turns.setdefault(idx, Turn(idx, who, text))
                else:
                    current = None
                continue
        if current is None:
            continue
        if line.startswith("out processed") and current.latency is None:
            current.latency = round(ts - echo_ts, 2)
            continue
        m = MSG_RE.match(line)
        if m:
            last_msg = {"sender": m.group(1), "to": m.group(2), "text": m.group(3).strip()}
            current.sent.append(last_msg)
    for i, t in enumerate(persona["turns"], 1):
        turns.setdefault(i, Turn(i, t["as"], t["text"]))
    return [turns[i] for i in sorted(turns)]


# ---------------------------------------------------------------- report

def write_outputs(results: list[dict], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        p = r["persona"]
        findings = run_checks(r["turns"], p)
        r["findings"] = findings
        with (out / f"{p['id']}.txt").open("w", encoding="utf-8") as f:
            f.write(f"# {p['id']} | agent {p['agent']} | level {p['level']} v{p.get('variant', '?')} | author {p.get('author', '?')} | pace {r['pace']}\n")
            f.write(f"# {p.get('title', '')}\n\n## turns\n")
            for t in r["turns"]:
                lat = "n/a" if t.latency is None else f"{t.latency:.2f}s"
                f.write(f"\n[{t.index}] {t.who}: {t.text}\n    latency: {lat}\n")
                for m in t.sent or [{"sender": "-", "to": "-", "text": "(no actions)"}]:
                    f.write(f"    -> {m['sender']} > {m['to']} : {m['text']}\n")
            f.write("\n## checks\n")
            for fd in findings:
                f.write(f"{fd.result:4} {fd.check}: {fd.evidence}\n")
            f.write("\n## raw output\n")
            for ts, line in r["raw"]:
                if line.startswith("log ") or line.startswith("    msg=") or line.startswith("    type="):
                    continue
                f.write(f"{ts:7.2f} {line}\n")
        for fd in findings:
            rows.append({
                "persona": p["id"], "agent": p["agent"], "level": p["level"], "variant": p.get("variant", ""),
                "author": p.get("author", ""), "turn": fd.turn, "check": fd.check, "result": fd.result,
                "what_happened": fd.evidence, "rubric_row": fd.rubric, "type": fd.kind, "severity": fd.severity,
                "suggested_owner": OWNERS.get(p["agent"], ""), "fix_idea": fd.fix,
                "max_latency_s": max([t.latency for t in r["turns"] if t.latency is not None] or [0]),
            })
    with (out / "report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["persona"])
        w.writeheader()
        w.writerows(rows)
    md = render_md(results, rows, out)
    (out / "report.md").write_text(md, encoding="utf-8")
    fails = sum(1 for x in rows if x["result"] == "FAIL")
    return {"fails": fails, "warns": sum(1 for x in rows if x["result"] == "WARN"), "personas": len(results)}


def render_md(results, rows, out: Path) -> str:
    def count(key, val):
        return sum(1 for x in rows if x[key] == val)
    lines = [f"# Persona run {out.name}", "",
             f"Personas: {len(results)} · checks: {len(rows)} · FAIL {count('result', 'FAIL')} · WARN {count('result', 'WARN')} · PASS {count('result', 'PASS')}", "",
             "## Summary per agent", "", "| agent | personas | FAIL | WARN | bugs | suggestions | suggested owner |", "|---|---|---|---|---|---|---|"]
    for a in AGENTS:
        ar = [x for x in rows if x["agent"] == a]
        if not ar:
            continue
        lines.append(f"| {a} | {len({x['persona'] for x in ar})} | {sum(x['result'] == 'FAIL' for x in ar)} | {sum(x['result'] == 'WARN' for x in ar)} | "
                     f"{sum(x['type'] == 'bug' and x['result'] != 'PASS' for x in ar)} | {sum(x['type'] == 'suggestion' and x['result'] != 'PASS' for x in ar)} | {OWNERS.get(a, '')} |")
    lines += ["", "## Summary per level", "", "| level | personas | FAIL | WARN |", "|---|---|---|---|"]
    for lv in range(1, 9):
        lr = [x for x in rows if x["level"] == lv]
        if lr:
            lines.append(f"| {lv} | {len({x['persona'] for x in lr})} | {sum(x['result'] == 'FAIL' for x in lr)} | {sum(x['result'] == 'WARN' for x in lr)} |")
    lines += ["", "## Findings (FAIL first, then WARN)", "",
              "| persona | agent | L | turn | what happened | check | rubric row | type | sev | suggested owner | fix idea |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    order = {"FAIL": 0, "WARN": 1}
    for x in sorted([x for x in rows if x["result"] != "PASS"], key=lambda x: (order[x["result"]], {"high": 0, "med": 1, "low": 2}[x["severity"]], x["agent"])):
        what = x["what_happened"].replace("|", "/")[:160]
        lines.append(f"| {x['persona']} | {x['agent']} | {x['level']} | {x['turn'] or ''} | {x['result']}: {what} | {x['check']} | {x['rubric_row']} | "
                     f"{x['type']} | {x['severity']} | {x['suggested_owner']} | {x['fix_idea']} |")
    lines += ["", "## Latency per persona (local approximation; the city's real timing differs)", "", "| persona | pace | turns | max s | avg s |", "|---|---|---|---|---|"]
    for r in results:
        lat = [t.latency for t in r["turns"] if t.latency is not None]
        if lat:
            lines.append(f"| {r['persona']['id']} | {r['pace']} | {len(r['turns'])} | {max(lat):.2f} | {sum(lat) / len(lat):.2f} |")
    lines += ["", "Raw transcripts: one `<persona id>.txt` per persona in this folder. CSV: `report.csv`.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- slots

def is_xa(p: dict) -> bool:
    """Agent-to-agent persona: tagged xa, id with _XA_, or a persona that plays another agent."""
    return bool(p.get("xa")) or "_XA_" in str(p.get("id", "")) or any(t.get("as") == "agent" for t in p.get("turns", []))


XA_SLOTS = 10  # dedicated agent-persona files per tester: XA_01.json ... XA_10.json


def slots(folder: Path) -> None:
    for a in AUTHORS:
        have = {f.name for f in (folder / a).glob("*.json")} if (folder / a).exists() else set()
        want = [f"L{l}_v{v}_{n:02d}.json" for l in range(1, 9) for v in range(1, 4) for n in (1, 2)] + ["speed_01.json", "speed_02.json"]
        missing = [w for w in want if w not in have]
        print(f"\n{a}: {len(want) - len(missing)}/50 written")
        for lv in range(1, 9):
            miss = [w for w in missing if w.startswith(f"L{lv}_")]
            print(f"  L{lv}: {6 - len(miss)}/6" + (f"  missing: {', '.join(miss)}" if miss else ""))
        sp = [w for w in missing if w.startswith("speed")]
        print(f"  speed: {2 - len(sp)}/2" + (f"  missing: {', '.join(sp)}" if sp else ""))
        xa_want = [f"XA_{n:02d}.json" for n in range(1, XA_SLOTS + 1)]
        xa_miss = [w for w in xa_want if w not in have]
        mine = load_personas(folder / a) if (folder / a).exists() else []
        n_xa = sum(1 for p in mine if is_xa(p))
        print(f"  agent personas (XA files + level files with an agent turn): {n_xa}/{XA_SLOTS} minimum"
              f" · XA slots {XA_SLOTS - len(xa_miss)}/{XA_SLOTS}" + (f"  missing: {', '.join(xa_miss)}" if xa_miss else ""))
    seed = list((folder / "seed").glob("*.json")) if (folder / "seed").exists() else []
    print(f"\nseed bank: {len(seed)} files (examples to copy from)")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(HERE.parent.parent / "asst"), help="folder that holds the 5 agent folders")
    ap.add_argument("--personas", default=str(HERE / "personas"))
    ap.add_argument("--level", type=int)
    ap.add_argument("--variant", type=int)
    ap.add_argument("--author", help="sina | sobhan | seed")
    ap.add_argument("--agent", choices=AGENTS)
    ap.add_argument("--id", action="append", help="persona id (repeatable)")
    ap.add_argument("--smoke", action="store_true", help="only personas tagged smoke=true")
    ap.add_argument("--xa", action="store_true", help="only agent-to-agent personas (other teams' agents talking to ours)")
    ap.add_argument("--parallel", type=int, default=1, help="run N personas at once (burst on this laptop)")
    ap.add_argument("--pace-override", choices=list(PACE), help="ignore each persona's pace")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--out", help="output folder (default runs/<author or all>/<timestamp>)")
    ap.add_argument("--slots", action="store_true", help="show which persona files each tester still has to write")
    a = ap.parse_args()

    folder = Path(a.personas)
    if a.slots:
        slots(folder)
        return 0
    ps = load_personas(folder)
    if a.level:
        ps = [p for p in ps if p["level"] == a.level]
    if a.variant:
        ps = [p for p in ps if p.get("variant") == a.variant]
    if a.author:
        ps = [p for p in ps if p.get("author") == a.author]
    if a.agent:
        ps = [p for p in ps if p["agent"] == a.agent]
    if a.id:
        ps = [p for p in ps if p["id"] in a.id]
    if a.smoke:
        ps = [p for p in ps if p.get("smoke")]
    if a.xa:
        ps = [p for p in ps if is_xa(p)]
    if not ps:
        print("no personas match the filters", file=sys.stderr)
        return 2
    root = Path(a.root).resolve()
    missing = [x for x in {p["agent"] for p in ps} if not (root / x / "card.yaml").exists()]
    if missing:
        print(f"agent folders missing under {root}: {missing}", file=sys.stderr)
        return 2
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_{os.getpid()}"  # two runs in the same second must not share a folder
    out = Path(a.out) if a.out else HERE / "runs" / (a.author or "all") / stamp
    print(f"running {len(ps)} personas against {root} (parallel {a.parallel}) -> {out}")

    def job(p):
        pace = a.pace_override or p.get("pace", "fast")
        r = run_one(p, root, pace, a.timeout)
        print(f"  done {p['id']} ({p['agent']}, L{p['level']}, {pace})", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        results = list(ex.map(job, ps))
    s = write_outputs(results, out)
    print(f"\n{s['personas']} personas · FAIL {s['fails']} · WARN {s['warns']}\nreport: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
