# mach: read-only CLI for the MACH team console

`tools/console/mach` (python3, stdlib only). GET only, never `/v1/admin/*`, never `city deploy/login/retire/delete`.
It reads the console through `edge fetch` (the hidden headless Edge with the team login, never your screen) and
never prints or stores cookies or tokens.

Rate limit rules built in: one call every 2 s, every response cached 30 s in `out/cache/`, retry after 20-40 s
on HTTP 429. A command that reads every journey (`unanswered`, `citizens`, `agent ... messages`, `dump`) makes
one call per journey, so it takes about 2 s per journey. Do not run two `mach` commands at the same time.

Human output: aligned columns, role names (not ids), times in Rome local time, a one-line summary first.
`--json` on every command gives the raw data.

## Commands

| Command | What it does |
|---|---|
| `mach status [--fast]` | One screen: wave, roster per role (agent, brain folder, visibility), newest deployment per agent (step, status, failed stage + detail), container state from logs (UP / SHUT DOWN / STALE / NO LOGS), deliveries, brain runs, say failures, journeys by state, score. `--fast` skips the 3 extra log queries per agent. |
| `mach doctor [--fast]` | One diagnosis per role in plain words: evidence line and next action. EMPTY ROLE (-4 per citizen for gp, booking_centre, family_counseling), DEPLOY FAILED (stage + detail, known causes mapped: `custody.ack ... renews=[] acks=[]` = brain over 5 s, run `mach gate`; `agent ingress route did not become ready` = overlapping deploys or load; `QUOTA_EXCEEDED` = one update per agent per 5 min), IN FLIGHT, CONTAINER DOWN (last line "Finished server process" or last log older than 60 s), NO LOGS, NO DELIVERIES (up, 0 `delivery` lines, open journeys on the agent), SAY FAILED (`care.say.failed` lines). |
| `mach unanswered` | Open journeys whose last message is the citizen's: citizen number, minutes waiting, target role, first 80 chars, oldest first, total at the end. |
| `mach inflight` | Agents with a deployment not yet promoted or failed, and seconds since each agent's last deployment (300 s quota countdown). The console gives no deployment timestamps, so the age is the most recent of: a console field if one appears, the time `mach` first saw the deployment (`out/state.json`), the mtime of the matching `deploy-logs/*.log`. It can only err on the safe side (too young). |
| `mach gate` | Local pre-deploy gate, no network: `py_compile asst/_shared/agent.py`, `conform_time.py` (every role under 2.5 s), `exp2.py`, `check_tokens.py` with the city-cli python (`-I`), and each `asst/<role>/agent/agent.py` byte-identical to `_shared`. PASS/FAIL per line, exit 1 on any FAIL. |
| `mach tail ROLE [--every 20]` | Follow new non-health log lines of one agent. |
| `mach roster` / `wave` / `deployments [--agent ROLE]` / `reputation` / `usage` / `market` / `conversations` / `profile` / `team` / `agents` / `leaderboard` | One tab each. |
| `mach world [--slots] [--doctors] [--protocol] [--rules]` | World tab (all sections by default). Rules come from the wave banner. |
| `mach agent ROLE [overview\|logs\|messages] [--q TEXT] [--errors] [--no-health] [--tail N]` | One agent. `logs` = `/v1/logs?agent_id=..&q=..` (the console returns the newest 150 lines, filters are client side). `messages` = journey messages to or from the agent plus the console conversations (`/v1/team/conversations/{id}`). |
| `mach journeys [--state S] [--limit N] [--detail]` | Journey list (states: open, booked, done, escalated, failed). |
| `mach journey NEED_ID` | Full trace: every message (time, from, to, text), verdict, ratings, LLM review per agent. |
| `mach citizens` | First message of every journey grouped by symptom family (chest pain, palpitations, back pain, other). |
| `mach watch [--every 60]` | Prints only changes: roster, deployment status, container up/down, new journeys, answered journeys, new verdicts, new reds. |
| `mach dump DIR` | Raw JSON of every tab, `/v1/agents/{id}`, conversations, every journey, every agent's logs, team feed. |
| `mach get /v1/PATH` | Raw GET for debugging (same guards). |

ROLE is `gp`, `booking_centre`, `hospital`, `specialist` (the console family is `specialist_care`),
`family_counseling`. The agent id comes from the roster, else from the deployments (mapped to the role by the
`agent_id` in `asst/<folder>/card.yaml`).

## Notes

- Container state comes from `/v1/logs` (newest 150 lines per query): UP = last line under 60 s old and not a
  shutdown line. Delivery, brain-run and say-failure counts use the server-side `q` filter (`delivery`, `brain done`,
  `care.say.failed`) and are therefore also capped at 150.
