#!/usr/bin/env python3
"""Pre-fill a finding issue (.gitlab/issue_templates/persona-finding.md) from a persona run.

Fills the facts a run already knows (context table, transcript, reproduce command).
A person or their agent then fills the TODO parts: what happened, expected, where it went wrong.

  python3 draft_issue.py runs/sina/2026-11-20_2230            # list FAIL keys (agent + check)
  python3 draft_issue.py runs/sina/2026-11-20_2230 specialist act_word:consent > draft.md
  glab issue create --title "$(head -1 draft.md)" --description "$(tail -n +3 draft.md)"
"""
import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

TODO = "<!-- TODO: fill by hand -->"


def rows(run):
    with open(run / "report.csv", newline="") as f:
        return list(csv.DictReader(f))


def transcript(run, persona, check):
    """Turns section of the persona transcript, plus that check's result lines."""
    p = run / f"{persona}.txt"
    if not p.exists():
        return f"(transcript {p.name} not found)"
    text = p.read_text()
    turns = text.split("## turns", 1)[-1].split("## checks", 1)[0].strip()
    checks = [l for l in text.split("## checks", 1)[-1].split("## raw output", 1)[0].splitlines()
              if f" {check}:" in l]
    return turns + "\n\n" + "\n".join(checks)


def brain_version():
    try:
        return subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%H:%M", "--",
                               "asst/_shared/agent.py"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[2]).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def draft(run, agent, check):
    all_rows = [r for r in rows(run) if r["agent"] == agent and r["check"] == check]
    failed = [r for r in all_rows if r["result"] == "FAIL"] or [r for r in all_rows if r["result"] == "WARN"]
    if not failed:
        sys.exit(f"no FAIL or WARN rows for {agent} + {check} in {run}")
    fail_ids = sorted({r["persona"] for r in failed})
    pass_ids = sorted({r["persona"] for r in all_rows if r["result"] == "PASS"} - set(fail_ids))
    first = failed[0]
    levels = ", ".join(sorted({f"L{r['level']} v{r['variant']}" for r in failed}))
    latency = max(float(r["max_latency_s"] or 0) for r in failed)
    happened = "\n".join(sorted({f"- {r['persona']} turn {r['turn']}: {r['what_happened']}" for r in failed}))
    return f"""[{agent}] {check}: {TODO.replace('fill by hand', 'one-line symptom')}

**Key:** `{agent}` + `{check}`

## What happened
{TODO}

## Expected result
{TODO} (quote the rule: kit rubric row, level slide, or the checks.py check)

## Actual outcome
{happened}

## Where it went wrong
- **Turn:** {first['turn']} in {first['persona']}
- **Suspected area in the brain:** {TODO} {('(tester hint: ' + first['fix_idea'] + ')') if first['fix_idea'] else ''}
- **Rubric impact:** {first['rubric_row']}

## Context
| Field | Value |
|---|---|
| Agent | {agent} |
| Level and variant | {levels} |
| Check id | {check} |
| Result | {first['result']} |
| Severity (tester) | {first['severity']} |
| Where seen | local `city run` (persona tester) |
| Brain version | {brain_version()} |
| Run folder | {run} |
| Personas that failed | {', '.join(fail_ids)} ({len(fail_ids)} of {len(fail_ids) + len(pass_ids)}) |
| Personas that passed the same check | {', '.join(pass_ids) or 'none'} |
| Max latency | {latency:.1f} s |
| Citizen tone / language | {TODO} |

## Transcript (the turns that matter)
```
{transcript(run, first['persona'], check)}
```

## Reproduce
```bash
# from mach-app/
(cd asst && bash ../scripts/sync_brain.sh)
python3 tools/persona-tester/run_personas.py --id {first['persona']}
```

## Notes
{TODO}

/label ~"finding" ~"agent:{agent}" ~"level:L{first['level']}" ~"severity:{first['severity']}"
"""


def main():
    if len(sys.argv) not in (2, 4):
        sys.exit(__doc__)
    run = Path(sys.argv[1])
    if len(sys.argv) == 2:
        keys = Counter((r["agent"], r["check"]) for r in rows(run) if r["result"] == "FAIL")
        for (agent, check), n in keys.most_common():
            print(f"{n:3}  {agent} {check}")
        return
    print(draft(run, sys.argv[2], sys.argv[3]))


if __name__ == "__main__":
    main()
