"""Issue 59: after an L6 fault.cancel + rebook the citizen checks in with the OLD booking id, or a
few minutes outside the half-day window. Refusing a valid receipt is -2.

1. Old id, same citizen, same prescription: the Ledger check-in goes out with the NEW id and the
   citizen hears it is registered, with a note naming both codes.
2. Old id, but the new booking is another citizen's: no remap (the old id goes to the Ledger).
3. Old id, same citizen, other prescription: no remap.
4. Ledger refuses TOO_EARLY (a few minutes): the citizen is received with a clear note, not
   told the code is wrong. 5. TOO_LATE minutes=240: still the refusal line.
Run: <city python> -I tests/test_59_checkin_old_id.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message

os.environ.setdefault("CITY_ROLE", "hospital")
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for key, value in (("_PULL_BUDGET", 0.6), ("_CONSENT_WAIT", 0.1), ("_ADVANCE_BUDGET", 2.0)):
    if hasattr(brain, key):
        setattr(brain, key, value)

AG, CZ, OTHER, CITY = "agent:fac", "citizen:rita", "citizen:other", "city"
n = [0]
need = ["N-59-0"]
fails = []


def msg(sender, to, text):
    n[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"t{n[0]}", need_id=need[0], body={"text": text}, recipient=to)


class Fake:
    """BK-OLD committed for CZ, fault.cancel, then BK-NEW committed for ``new_holder``."""

    def __init__(self, new_holder=CZ, new_rx="RX-1", answer=None):
        need[0] = f"N-59-{n[0]}"
        self.agent_id, self.address = AG, ""
        self.rows = [
            msg(AG, LEDGER, f"hold slot_id=S1 citizen={CZ} prescription_number=RX-1 consent_ref=cons-1"),
            msg(LEDGER, AG, "held hold_id=H1 slot_id=S1"),
            msg(AG, LEDGER, "commit hold_id=H1 consent_ref=cons-1 fence=1 prescription_number=RX-1"),
            msg(LEDGER, AG, "committed booking_id=BK-OLD ledger_ref=c-1 slot_id=S1"),
            msg(AG, CZ, "booking.receipt booking_id=BK-OLD\nLa sua visita è confermata."),
            msg(CITY, AG, "fault.cancel booking_id=BK-OLD reason=fault.cancel"),
            msg(AG, LEDGER, f"hold slot_id=S2 citizen={new_holder} prescription_number={new_rx} consent_ref=cons-2"),
            msg(LEDGER, AG, "held hold_id=H2 slot_id=S2"),
            msg(AG, LEDGER, f"commit hold_id=H2 consent_ref=cons-2 fence=1 prescription_number={new_rx}"),
            msg(LEDGER, AG, "committed booking_id=BK-NEW ledger_ref=c-2 slot_id=S2"),
            msg(AG, new_holder, "booking.receipt booking_id=BK-NEW\nLa sua nuova visita è confermata."),
        ]
        self.out, self.answer = [], answer

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text))
        if to == LEDGER and text.startswith("checkin"):
            bk = brain.fields_of(text).get("booking_id")
            if self.answer:
                line = self.answer.format(bk=bk)
            elif bk == "BK-NEW":
                line = f"checked_in booking_id={bk}"
            else:
                line = f"refused op=checkin booking_id={bk} code=BOOKING_CANCELLED"
            self.rows.append(msg(LEDGER, AG, line))
        elif to == LEDGER and text.startswith("report"):
            self.rows.append(msg(LEDGER, AG, "reported booking_id=BK-NEW"))
        return "x"

    async def history(self, *a, **kw):
        return list(self.rows)

    def find(self, *a, **kw):
        return []

    async def llm(self, *a, **kw):
        return {"content": ""}


async def turn(city, text, sender=CZ):
    brain._ONCE.clear()
    inbound = msg(sender, AG, text)
    city.rows.append(inbound)
    city.inbound = inbound
    start = len(city.out)
    await brain._on_checkin(city, inbound, list(city.rows), reports=False)
    return city.out[start:]


def ledger(out):
    return [t for to, t in out if to == LEDGER and t.startswith("checkin")]


def said(out):
    return [t for to, t in out if to == CZ]


def check(ok, what):
    if not ok:
        fails.append(what)


async def main():
    # 1. Old id after the rebook: checked in on the new booking, with a note.
    city = Fake()
    out = await turn(city, "checkin booking_id=BK-OLD")
    check(ledger(out) and all("booking_id=BK-NEW" in t for t in ledger(out)), f"1: Ledger got {ledger(out)}")
    lines = said(out)
    check(any("check-in è registrato" in t for t in lines), f"1: no confirmation {lines}")
    check(any("BK-OLD" in t and "BK-NEW" in t for t in lines), f"1: no note naming both codes {lines}")
    check(not any("non ha accettato" in t for t in lines), f"1: refused a valid receipt {lines}")

    # 2. The new booking is another citizen's: no remap.
    city = Fake(new_holder=OTHER)
    out = await turn(city, "checkin booking_id=BK-OLD")
    check(ledger(out) and all("booking_id=BK-OLD" in t for t in ledger(out)), f"2: remapped across citizens {ledger(out)}")

    # 3. Same citizen, other prescription: no remap.
    city = Fake(new_rx="RX-9")
    out = await turn(city, "checkin booking_id=BK-OLD")
    check(ledger(out) and all("booking_id=BK-OLD" in t for t in ledger(out)), f"3: remapped across prescriptions {ledger(out)}")

    # 4. A few minutes outside the half-day window: received with a clear note.
    city = Fake(answer="refused op=checkin booking_id={bk} code=TOO_EARLY minutes=7")
    out = await turn(city, "checkin booking_id=BK-NEW")
    lines = said(out)
    check(any("fascia" in t and "valida" in t for t in lines), f"4: no window note {lines}")
    check(not any("non ha accettato" in t for t in lines), f"4: refused a valid receipt {lines}")

    # 5. Hours off: the refusal line stays.
    city = Fake(answer="refused op=checkin booking_id={bk} code=TOO_LATE minutes=240")
    out = await turn(city, "checkin booking_id=BK-NEW")
    lines = said(out)
    check(any("non ha accettato" in t for t in lines), f"5: hours late accepted {lines}")

    for line in fails:
        print("FAIL", line)
    print("OK 0" if not fails else f"test_59_checkin_old_id: {len(fails)} failed")
    sys.exit(1 if fails else 0)


asyncio.run(main())
