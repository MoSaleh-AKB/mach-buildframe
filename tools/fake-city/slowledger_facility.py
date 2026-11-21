"""Facility flows under the live slow Ledger (NIGHT-STATUS 00:40: replies take 8-20 s).

Every scenario runs one facility journey against a fake Ledger that answers each write after
DELAY seconds. With turns=0 the Ledger reply lands in the case history but never opens a turn
(the live Relay often drops that push). With turns=2 the reply turn opens DURING the background
poll: it is built from the history the poll has just read, before the poll's own lines land, so
both paths see no citizen line yet (card 48). Every send takes SAY_LAG seconds, like a Relay POST,
so two sends of one line can be in flight at once. The judge looks at the case JUDGE_AT seconds after the
first turn (about one real minute after the journey ends) and checks:
  - the first turn returned within the gate (citizen 7.5 s, agent 2.5 s) and said something;
  - the Ledger got the writes the rubric needs (check-in, report; hold, commit);
  - the citizen or desk heard the outcome (check-in confirmed, receipt), never before the
    Ledger line that makes it true.
All scenarios run at once, so the whole file takes about JUDGE_AT seconds.

Run:  ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/slowledger_facility.py asst/_shared/agent.py
Exit 1 when a scenario fails.
"""

import asyncio
import importlib.util
import sys
import time

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

JUDGE_AT = 62.0
DELAYS = (8.0, 14.0, 20.0)
SAY_LAG = 0.3
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Case:
    """One need: the shared history, the fake Ledger and the turns it opens."""

    def __init__(self, role, agent, need, delay, turns):
        self.role, self.agent, self.need, self.delay, self.turns = role, agent, need, delay, turns
        self.rows = []
        self.tasks = []
        self.armed = []  # turns=2: Ledger rows whose reply turn opens at the next history read

    def view(self, inbound):
        return View(self, inbound)

    async def ledger(self, text):
        await asyncio.sleep(self.delay)
        f = brain.fields_of(text)
        head = text.split()[0]
        if head == "checkin":
            reply = f"checked_in booking_id={f['booking_id']}"
        elif head == "report":
            reply = f"reported booking_id={f['booking_id']}"
        elif head == "hold":
            reply = f"held hold_id=h-{self.need} slot_id={f['slot_id']} fence=1"
        elif head == "commit":
            reply = f"committed booking_id={f['hold_id']} ledger_ref=c-{self.need}"
        else:
            return
        row = msg(LEDGER, self.agent, reply, self.need)
        self.rows.append(row)
        if self.turns == 1:
            await self.turn(row, list(self.rows))
        elif self.turns == 2:
            self.armed.append(row)

    async def turn(self, row, rows):
        brain._TURN_END.set(0.0)  # like brain.main: a Ledger turn has no shared wait cap
        await brain.handle(self.role, self.view(row), row, rows)


class View:
    def __init__(self, case, inbound):
        self.case, self.inbound, self.agent_id, self.address = case, inbound, case.agent, ""

    async def say(self, to, text, *, status=None):
        await asyncio.sleep(SAY_LAG)
        self.case.rows.append(msg(self.agent_id, to, text, self.case.need))
        if to == LEDGER:
            self.case.tasks.append(asyncio.create_task(self.case.ledger(text)))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        rows = list(self.case.rows)
        for row in self.case.armed:  # the reply turn starts now, from this same history
            self.case.tasks.append(asyncio.create_task(self.case.turn(row, list(rows))))
        self.case.armed.clear()
        return rows

    async def find(self, query):
        return []


def after(rows, i, head):
    """True when a Ledger line starting with ``head`` is in the history before index ``i``."""
    return any(r.sender == LEDGER and r.text.startswith(head) for r in rows[:i])


async def scenario(flow, delay, turns):
    role = {"checkin_sp": "specialist_care", "checkin_ho": "hospital", "confirm_ho": "hospital",
            "confirm_fc": "family_counseling"}[flow]
    me = f"agent:t07/{flow}"
    need = f"N-{flow}-{int(delay)}-{turns}"
    cz = f"citizen:t07/{need}"
    bc = "agent:t07/booking_centre"
    case = Case(role, me, need, delay, turns)
    bk = f"bk-{need}"
    if flow.startswith("checkin"):
        first = msg(cz, me, f"checkin booking_id={bk}", need)
        limit, peer = 7.5, cz
    else:
        first = msg(bc, me, f"confirm offer_id=of-S1 slot_id=S-{need} citizen={cz} consent_ref=cons-{need} facility={me}", need)
        limit, peer = 2.5, bc
    case.rows.append(first)
    t0 = time.monotonic()
    # like brain.main: the turn's shared wait deadline (citizen 7 s, agent 1.5 s)
    brain._TURN_END.set(t0 + (brain._CITIZEN_TURN if peer == cz else brain._AGENT_TURN))
    await brain.handle(role, case.view(first), first, list(case.rows))
    took = time.monotonic() - t0
    await asyncio.sleep(max(0.0, JUDGE_AT - took))
    rows = case.rows
    sent = [(i, r) for i, r in enumerate(rows) if r.sender == me]
    to_ledger = [r.text.split()[0] for _, r in sent if r.recipient == LEDGER]
    fails = []
    if took > limit:
        fails.append(f"turn {took:.1f}s > {limit}s")
    if not any(r.recipient == peer for _, r in sent):
        fails.append("first sender never answered")
    if flow.startswith("checkin"):
        if "checkin" not in to_ledger:
            fails.append("no checkin to Ledger")
        told = [i for i, r in sent if r.recipient == cz and "check-in è registrato" in r.text]
        if not told:
            fails.append("citizen never told the check-in is registered")
        if any(not after(rows, i, "checked_in") for i in told):
            fails.append("check-in confirmed before checked_in")
        if flow == "checkin_sp":
            if "report" not in to_ledger:
                fails.append("no report to Ledger (Referto mancante -1)")
            if not any(r.recipient == cz and "consent" in r.text.split()[0] for _, r in sent):
                fails.append("no consent ask")
    else:
        if "hold" not in to_ledger:
            fails.append("no hold")
        if "commit" not in to_ledger:
            fails.append("no commit (hold expires, no booking)")
        rec = [i for i, r in sent if r.recipient == bc and r.text.startswith("booking.receipt")]
        if not rec:
            fails.append("desk never got booking.receipt")
        if any(not after(rows, i, "committed") for i in rec):
            fails.append("receipt before committed")
    lines = [(r.recipient, r.text) for _, r in sent]
    if len(lines) != len(set(lines)):
        fails.append("same line sent twice")
    for t in case.tasks:
        t.cancel()
    name = f"{flow:<11} delay={int(delay):>2}s reply_turns={('no ', 'yes', 'mid')[turns]}"
    print(f"{'OK  ' if not fails else 'FAIL'} {name} turn={took:4.1f}s ledger={to_ledger} {'; '.join(fails)}")
    return not fails


async def main():
    jobs = [scenario(flow, d, t) for flow in ("checkin_sp", "checkin_ho", "confirm_ho", "confirm_fc")
            for d in DELAYS for t in (1, 0, 2)]
    results = await asyncio.gather(*jobs)
    print(f"{sum(results)}/{len(results)} OK")
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
