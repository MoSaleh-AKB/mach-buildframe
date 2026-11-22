# mach-app: Team Buildframe, Milano Agentic City (MACH, 20-21 Nov 2026)

Five cooperating healthcare agents for the Project NANDA Italy city simulation. Our team runs one ASST (a simulated local health agency). The city sends citizens with a care need; our agents carry each one along the care journey (symptom, prescription, booking, visit, report), and a judge scores every journey against a fixed rubric.

## Overview

- Two shared "brains": `asst/_shared/agent.py` serves booking_centre, hospital and specialist; `asst/_gp/agent.py` serves gp and family_counseling. Each agent reads its role from `card.yaml`, so a fix lands in every seat that runs that brain.
- Everything is written to the city Ledger before the citizen is told anything. Nothing is promised that the Ledger has not confirmed.
- Non-clinical by design: the agents navigate and book care. They never give a diagnosis or medical advice.
- Citizens write in Italian, English and other languages; replies follow the citizen's language.

## Agents

| Folder | Role | What it does |
|---|---|---|
| `asst/booking_centre` | CUP, booking centre | Receives the citizen, finds the right service, offers real Ledger slots, confirms, sends the receipt, handles cancel, rescue and late replies. |
| `asst/gp` | General practitioner (MMG) | First clinical-pathway step: collects the need, issues the prescription through the Ledger, routes to the booking centre, or escalates red flags. |
| `asst/hospital` | Facility, `hospital.physiatry` | Takes bookings for physiatry visits, runs check-in, writes the visit report. |
| `asst/specialist` | Facility, `specialist_care.cardiology` | Same facility flow for cardiology, including urgent-reserve slots. |
| `asst/family_counseling` | Facility, `family_counseling.gynecology` | Consultorio: bookings and reports for family counseling and gynecology. |

## Architecture

- **Shared brains:** `asst/_shared/agent.py` (booking_centre, hospital, specialist) and `asst/_gp/agent.py` (gp, family_counseling). `scripts/sync_brain.sh` copies them into the five role folders; `scripts/magic_pass.sh` checks the copies are identical (md5).
- **Ledger:** the city's source of truth for prescriptions, agenda, slots and rules. Agents read it, reserve against it, and write to it before replying to the citizen. Slot offers are spread across citizens who ask at the same time, and a refused hold is answered at once with a new offer.
- **Relay:** agents talk to each other (for example GP to booking centre to facility) through the city mailbox via `city_sdk.city` (`find`, `say`, `history`). Cross-ASST requests go through a partner relay that keeps the message shape strict (ids and dates validated, no free-text payment links).
- **Never silent:** a watchdog and a rescue sweep make sure every citizen turn gets an answer, also after a slow Ledger or a slow peer.
- **Card and deploy:** each role has `card.yaml`, `city.yaml`, a `Dockerfile` and a `SKILL.md`; `city deploy` runs the organiser conformance checks.

## Accessibility, mobility, environment

- **Accessibility:** when a citizen mentions a wheelchair, crutches, low vision or hearing loss, the booking centre passes a step-free need to the facility with the booking (no other personal data) and tells the citizen it did. Replies follow the citizen's language, with a plain "what happens next".
- **Mobility:** when heavy rain is forecast in Milan for some slots, the booking centre lists the drier slot first and says why (Open-Meteo, fixed Milan coordinates, 1.2 s cap, no citizen data sent). Asked for the nearest place, it uses the facility district from the registry, or says honestly that it cannot sort by distance. The confirmation names the metro stop when the facility address has a known one.
- **Environment:** fewer trips and less paper. The prescription lives in the registry ("no need to come to the practice or print anything"), and the report is shared digitally with the citizen's consent, so nobody returns to collect it.
- **Transparency:** asked "are you a robot?" or "can I talk to a person?", the booking centre and the consultorio say they are an automatic service on the official registry, that the visit is done by health staff, and that a human desk stays available; the GP agent says it is the practice's digital assistant.

## Why it matters, and what a 2027 pilot needs

- **For whom:** citizens who struggle with a phone line or a web form: people with little Italian, people with a disability, older people. The agents answer in the citizen's language, at any hour, and carry the request across GP, booking centre and facility, so the citizen does not have to.
- **Decisions stay with people:** the citizen chooses among the offers and nothing is booked without a yes; the prescription follows the clinical protocol and red flags go to 112; the visit and the report are done by health staff. Every step is a signed Ledger record that can be audited.
- **Pilot shape:** one ASST, one service line, three months. Data: the ASST booking agendas, the prescription register and the facility registry. Systems: the same open protocol and signed receipts, open models the city can host or replace. People: the booking desk stays for anyone who does not use chat and for every doubtful case.

## Safety

- **Red flags:** a corpus of 91 emergency phrases (`tools/fake-city/redflag_phrases.json`, tested with 0 false positives) triggers an immediate escalation to emergency services (112) instead of a booking.
- **Impostor trust ring:** a message from another "agent" is trusted only if the sender is in the roster and found by `city.find`, the facility matches the sender, the capability matches, the date is in the future with a real doctor, and no personal data is requested. Phantom receipts, unasked offers and prompt-injection text are refused.
- **Care rules from the Ledger:** P1 (prescription validity and renewal: no booking on an expired prescription), P2 (cross-ASST consent: no cross-ASST booking without it, and consent can be taken back), P3 (urgent reserve: reserved slots only for urgent need; re-offer when the rule changes between offer and confirm).
- **Privacy:** no clinical advice, no data asks beyond what a booking needs, no secrets in the repo (see Rules).

## How to run and test

Python 3.12+ and the official city CLI.

```bash
uv tool install city-cli
cd asst
city run gp   # or booking_centre, hospital, specialist, family_counseling
```

`city run` runs a brain against an in-memory Index, mailbox and Ledger. Chat as `citizen`, `agent` or `ledger`.

Release gate (no login needed; about 11 minutes, `--quick` skips the slow-Ledger and red-flag stages). Run from the repo root:

```bash
(cd asst && bash ../scripts/sync_brain.sh)   # copy the two brains into the five role folders
bash scripts/magic_pass.sh                   # add --quick for the short run
```

It checks brain copies, compiles, runs every `tests/test_*.py` and the fake-city tests, conformance, the slow-Ledger scenarios and the red-flag corpus, and ends with `MAGIC PASS <sha> md5 <hash>` when everything is green. Under the gate's parallel load a few timing-based tests can be flaky; run them alone with `python -I tests/<name>.py asst/_shared/agent.py`. The red-flag corpus (91/91, 0 false positives) and conformance are the hard checks.

Simulated-citizen tests: `tools/persona-tester/` (personas play citizens, other agents and the Ledger against `city run`, checked against the rubric). Journey analysis: `tools/journeys/` (synthetic sample included).

Deploy one role at a time: `cd asst && city deploy <role>`.

## Rules we followed

- Built on the official kit [projectnandaitaly/mach-hackathon-kit](https://github.com/projectnandaitaly/mach-hackathon-kit) (MIT); the `city-cli` and `city-sdk` packages come from PyPI. See `LICENSE` and `NOTICE`.
- No keys or session files in the repo; reach the city only through `city_sdk.city`.
- Never retry a 4xx; write to the Ledger before telling the citizen.

## Team

Team Buildframe:

- [MoSaleh](https://github.com/MoSaleh-AKB): captain, booking centre
- [Mostafa](https://github.com/Soheil7799): hospital, specialist, deploys
- [Javad](https://github.com/JudeAlavi): GP, family counseling
- [Sobhan](https://github.com/sobhanparsaa): testing
- Sina: testing, pitch

License: MIT.
