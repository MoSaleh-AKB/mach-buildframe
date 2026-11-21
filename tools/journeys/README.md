# Journeys analyzer

Turns the console tab **Percorsi dei cittadini** (Journeys) into fixable patterns: every red journey is
grouped by our agent's role + what went wrong, counted, shown with the newest `record_id`, the judge note,
the LLM review note and the last messages, plus BACKLOG-ready rows with a suggested owner and a Persian
summary line per pattern.

Owners by role: booking_centre = MoSaleh, hospital / specialist_care = Mostafa, gp / family_counseling = Javad.

Stdlib Python 3.9+, read-only. No token or cookie is printed or saved. Outputs go to `out/` (git-ignored).

## Where the data comes from

The console page calls these JSON endpoints with its session cookie (GET only):

| Path | What |
|---|---|
| `/v1/team/journeys` | list: `need_id`, `wave_id`, `citizen`, `level`, `state` (open, booked, done, escalated, failed), `updated_at` |
| `/v1/team/journeys/<need_id>` | detail: `journey`, `messages[]`, `verdict` (`record_id`, `facts[]`, `rows[]` with `score` + `reason`), `ratings[]`, `review` (LLM notes per agent: proactivity, collaboration, domain) |
| `/v1/team/assignment` | roster: role family -> agent ids (once confirmed) |
| `/v1/logs?agent_id=<id>&q=<text>` | agent logs (not used by the tool yet) |

**Red** = journey state `failed`, or any negative judge row, rating or review score. **Green** = judged with
no negative. **Pending** = not judged yet (the judge writes about one real minute after the journey ends).
Only negatives on OUR five agents count (ids from `roles.json` / `card.yaml`); `--all-agents` shows the rest.

## Testers (Sina, Sobhan): every 15 minutes

You need the console open and logged in at https://projectnandaitaly.ai/team in your own browser.

1. In that tab open DevTools (Cmd+Opt+J), paste the whole content of `tools/journeys/dump.js`, press Enter.
   The browser downloads `journeys-dump.json` (re-runs make `journeys-dump (1).json`, use the newest).
2. Run:

```
cd tools/journeys && python3 analyze.py --dump ~/Downloads/journeys-dump.json
```

3. Read the top of the report (worst pattern first). Copy the `BACKLOG rows` lines you agree with under
   your heading in `tools/persona-tester/BACKLOG.md` (renumber ids to SO-n / SI-n). The same report is in
   `out/report-latest.md`; each run also keeps `out/report-YYYYMMDD-HHMM.md`.

Only new reds since your last check:

```
cd tools/journeys && python3 analyze.py --dump ~/Downloads/journeys-dump.json --watch --once
```

## MoSaleh's Mac (hidden Edge, no paste)

`edge fetch` reads the endpoints with his console login in the hidden headless Edge (never his screen):

```
cd tools/journeys && python3 analyze.py --live
```

```
cd tools/journeys && python3 analyze.py --live --watch
```

`--watch` re-pulls every 5 minutes (`--every 300`) and prints only NEW reds; they are also appended to
`out/watch-new-reds.md`. Already seen reds are kept in `out/seen_reds.json` (delete it to start over).
Details are cached in `out/cache/` per journey until it changes. Run `edge stop` when done.

Alternative with `edge js` (needs `EDGE_ACT_OK=1`, only on his go), two calls because `edge js` does not
await promises:

```
EDGE_ACT_OK=1 edge js "window.__jdumpNoDownload=1; $(cat dump.js)"
```

```
EDGE_ACT_OK=1 edge js 'window.__jdumpErr || window.__jdump' > out/edge.dump.json && python3 analyze.py --dump out/edge.dump.json
```

## Files

- `pull.py` fetch (`--live`) or read (`--dump`) and normalise to `out/journeys.json`: need_id, record_id,
  time, state, agents, verdict colour, negatives (source, agent, role, score, reason, note), review notes,
  last 6 messages.
- `analyze.py` patterns, report, BACKLOG rows, Persian lines, `--watch`.
- `dump.js` browser snippet that saves the same JSON the live pull reads.
- `roles.json` our five agent ids -> role family (fallback when the deploy folder is not on the laptop).
- `sample/make_sample.py` writes `sample/synthetic-dump.json` (SYNTHETIC data, for testing only):

```
cd tools/journeys && python3 sample/make_sample.py && python3 analyze.py --dump sample/synthetic-dump.json
```

## Patterns

Judge reason codes map 1:1 to a pattern (`no_booking`, `booking_outside_set`, `no_alternative`,
`wrong_procedure`, `wrong_priority`, `prescription_demanded`, `report_missing`, `receipt_refused`,
`phantom_receipt`, `red_flag`, `consent_missing`, `expired_prescription`, `urgent_reserve`, `unserved`, ...),
each with the console's own fix hint. LLM review minus points are classed by keywords in the note
(Italian + English): `no_reply`, `late`, `pushed_back`, `cold_tone`, `ignored_concern`, `no_offer`,
`prescription_at_consultorio`, `no_collaboration`, `not_proactive`. Add keywords in `REVIEW_CLASSES` in
`analyze.py` when a note lands in the wrong class.
