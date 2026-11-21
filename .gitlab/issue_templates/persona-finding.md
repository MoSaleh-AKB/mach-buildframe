<!--
Title format: [<agent>] <check id>: <one-line symptom>
Example:      [specialist] consent: report filed without asking report_sharing consent

One issue = one bug (same agent + same check id). Before filing, search open issues for the key below.
If an open issue already has it, add a comment with the new run and personas instead of a new issue.
No API keys, session tokens or ~/.config/city/ content in issues.
-->

**Key:** `<agent>` + `<check id>`  <!-- e.g. specialist + act_word:consent -->

## What happened
<!-- One or two sentences, in plain words. -->

## Expected result
<!-- What the agent should have done. Quote the rule: kit rubric row, level slide, or a checks.py check. -->

## Actual outcome
<!-- What the agent did instead. Paste the agent's exact reply. -->
```
<agent:team/name> > <to> : <exact text the agent sent>
```

## Where it went wrong
- **Turn:** <!-- turn number in the transcript where it broke -->
- **Suspected area in the brain:** <!-- function or section in asst/_shared/agent.py if known; "unknown" is fine -->
- **Rubric impact:** <!-- e.g. "report missing -1", "city:emergency -2", "review passive -1" -->

## Context
| Field | Value |
|---|---|
| Agent | <!-- gp / booking_centre / hospital / specialist / family_counseling --> |
| Level and variant | <!-- e.g. L3 v3 --> |
| Check id | <!-- from report.csv, e.g. escalated_on_red_flag --> |
| Result | <!-- FAIL / WARN --> |
| Where seen | <!-- local `city run` / live Journeys record_id=... --> |
| Brain version | <!-- git SHA of asst/_shared/agent.py used, or live tag live-HHMM / city version 1.0.x --> |
| Run folder | <!-- runs/<tester>/<timestamp>/ --> |
| Personas that failed | <!-- ids, e.g. seed_L3_v3_01 (3 of 4 in this level) --> |
| Personas that passed the same check | <!-- helps narrow the cause; "none" if none --> |
| Latency | <!-- reply time in seconds if relevant (fast-reply +1 is under ~10 s) --> |
| Citizen tone / language | <!-- e.g. anxious, Gen Z, non-native Italian, lang=es --> |

## Transcript (the turns that matter)
<!-- From runs/<tester>/<timestamp>/<persona id>.txt. Keep the turns before and after the failure. -->
```
<paste here>
```

## Reproduce
```bash
# from mach-app/
(cd asst && bash ../scripts/sync_brain.sh)
# one persona:
python3 tools/persona-tester/run_personas.py --id <persona id>
# or by hand:
cd asst && city run <agent>
# then type:
<the exact citizen or agent lines from the persona>
```

## Notes
<!-- Anything else: flaky (2 of 5 runs), started after a deploy, only with one LLM model, related issue #... -->

/label ~"finding"
<!-- Also add: ~"agent:<agent>" ~"level:L<n>" ~"severity:<high|med|low>" -->
