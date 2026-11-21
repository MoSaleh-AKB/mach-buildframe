"""Live 21 Nov (hospital): a Ledger check-in write that is never answered stays "sent" for ever.

N-2026-11-02-8909040001240006 and ...1930007: the citizen retried three times, each retry got the
same wait line and no new Ledger write, and the visit ended receipt_refused (-2). A try that has
been "sent" for more than _CHECKIN_RESEND_AFTER s is now written again; a younger one is still in
flight (no second write), and a late answer to the resend never downgrades an accepted check-in.
The wait line is always the same _CHECKIN_WAIT text (the spam guard drops a repeat).
Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_checkin_resend.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import itertools
import os
import sys

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message, Need  # noqa: E402

BRAIN = sys.argv[1]
_ids = itertools.count(1)
AFTER = 1.5  # the test's resend age (live 20 s)
CIT = "citizen:t/rita"


def load(role):
    spec = importlib.util.spec_from_file_location(f"brain_{next(_ids)}", BRAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.role_of = lambda: role
    mod._ADVANCE_BUDGET = 0.6
    if hasattr(mod, "_CHECKIN_RESEND_AFTER"):
        mod._CHECKIN_RESEND_AFTER = AFTER
    return mod


def msg(sender, to, text, need):
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{next(_ids)}", need_id=need,
                   body={"text": text})


class Fake:
    """The Ledger drops the first ``drop`` check-in writes (no answer at all), then accepts."""

    def __init__(self, role, drop):
        self.agent_id = f"agent:t/{role}"
        self.address = ""
        self.need = f"N-RS-{next(_ids)}"
        self.rows = []
        self.inbound = None
        self.drop = drop
        self.writes = 0
        self.citizen = []

    async def say(self, to, text, *, status=None):
        self.rows.append(msg(self.agent_id, to, text, self.need))
        if to.startswith("citizen:"):
            self.citizen.append(text)
        if to == LEDGER and text.startswith("checkin"):
            self.writes += 1
            if self.writes > self.drop:
                self.rows.append(msg(LEDGER, self.agent_id, "checked_in booking_id=BK-1", self.need))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def llm(self, messages, *, need_id=None):
        return {"content": ""}

    async def find(self, query):
        return []


async def turn(brain, city, text="checkin booking_id=BK-1 ledger_ref=L1"):
    inbound = msg(CIT, city.agent_id, text, city.need)
    city.rows.append(inbound)
    city.inbound = inbound
    await asyncio.wait_for(brain.main(Need(summary=text, message=inbound), city), 30)


async def lost_then_retry(fails):
    brain = load("hospital")
    city = Fake("hospital", drop=1)
    await turn(brain, city)  # the write goes out, the Ledger never answers
    if city.writes != 1 or city.citizen[-1:] != [brain._CHECKIN_WAIT]:
        fails.append(f"first try: writes={city.writes} citizen={city.citizen}")
    await turn(brain, city)  # an immediate repeat: the first write is still in flight
    if city.writes != 1:
        fails.append(f"retry before {AFTER} s wrote again ({city.writes} writes): not idempotent")
    await asyncio.sleep(AFTER + 0.2)
    await turn(brain, city)  # the retry after the wait: the lost write is repeated
    if city.writes != 2:
        fails.append(f"retry after {AFTER} s: {city.writes} Ledger writes, want 2 (stuck on 'sent')")
    if not any(t.startswith("checkin booking_id=BK-1") for t in city.citizen):
        fails.append(f"retry after {AFTER} s: the citizen never heard the check-in was registered")
    waits = [t for t in city.citizen if "non è ancora arrivata la conferma" in t]
    if any(t != brain._CHECKIN_WAIT for t in waits):
        fails.append("a wait line other than _CHECKIN_WAIT went out")
    await turn(brain, city)  # a repeat after the check-in: one short line, no third write
    if city.writes != 2:
        fails.append(f"repeat after the check-in wrote again ({city.writes} writes)")


async def late_refusal_keeps_checked_in(fails):
    brain = load("hospital")
    rows = [
        msg("agent:t/hospital", LEDGER, "checkin booking_id=BK-1 citizen=" + CIT, "N-RS-x"),
        msg("agent:t/hospital", LEDGER, "checkin booking_id=BK-1 citizen=" + CIT, "N-RS-x"),
        msg(LEDGER, "agent:t/hospital", "checked_in booking_id=BK-1", "N-RS-x"),
        msg(LEDGER, "agent:t/hospital", "refused op=checkin booking_id=BK-1 code=ALREADY_CHECKED_IN", "N-RS-x"),
    ]
    got = brain._checkin_tries(rows, "agent:t/hospital", "BK-1")
    if got.get(CIT) != "checked_in":
        fails.append(f"a refusal of the resend downgraded the accepted check-in: {got}")


async def main():
    fails = []
    await lost_then_retry(fails)
    await late_refusal_keeps_checked_in(fails)
    if fails:
        print("FAILED:", "; ".join(fails))
        sys.exit(1)
    print("ALL PASS test_checkin_resend")


asyncio.run(main())
