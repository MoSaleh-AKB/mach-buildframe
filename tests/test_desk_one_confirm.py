"""Desk: one confirm per need (audit finding 6), and the L6 rebook still confirms again.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_desk_one_confirm.py [asst/_shared/agent.py]
Exit 1 on any FAIL.
"""

import asyncio
import importlib.util
import os
import re
import sys

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 0.4
brain._PAGE_BUDGET = 0.2


async def _no_weather():
    return {}


brain._weather = _no_weather  # no network in tests

BC, HO, CZ = "agent:t/booking_centre", "agent:t/hospital", "citizen:t/c1"
N = [0]


def row(sender, to, text, need="N-1"):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    """Fake city with an instant Ledger (``ledger`` maps a query to its reply)."""

    agent_id = BC
    address = ""

    def __init__(self, rows, ledger=None):
        self.rows, self.out, self.inbound = list(rows), [], rows[0]
        self.ledger = ledger or (lambda t: None)

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(row(self.agent_id, to, text, need=self.inbound.need_id))
        if to == LEDGER:
            r = self.ledger(text)
            if r:
                self.rows.append(row(LEDGER, BC, r, need=self.inbound.need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return list(self.rows)

    async def find(self, query):
        return []


def slot(i, day, d, hh, doc):
    return f"slot_id=S{i} doctor_id={doc} at=2026-11-{d}T{hh}:00+00:00 day={day} facility={HO} procedure=VIS-FIS free=1"


WORLD = "slots page=1/1\n" + "\n".join([slot(1, "mon", 23, "09", "D-h1"), slot(2, "tue", 24, "09", "D-h2"), slot(3, "wed", 25, "10", "D-h1")])


def reset():
    for name in ("_DUPS", "_CITIZEN_SENT", "_CITIZEN_RATE", "_CANCELS_SENT", "_SENT", "_ONCE", "_HELD", "_GATES"):
        getattr(brain, name, {}).clear()


async def send(fake, m):
    fake.rows.append(m)
    fake.inbound = m
    n = len(fake.out)
    await brain.handle("booking_centre", fake, m, list(fake.rows))
    return fake.out[n:]


def start(prio="B", extra=()):
    return [row(CZ, BC, "Buongiorno, vorrei prenotare prescription_number=RX1"),
            row(LEDGER, BC, f"prescription prescription_number=RX1 procedure=VIS-FIS priority={prio} state=issued"),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00"), *extra, row(LEDGER, BC, WORLD)]


FAILS = []


def check(name, ok, out):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)
        for to, text in out:
            print("   ", to, text[:160].replace("\n", " | "))


def confirms(out):
    return [t for to, t in out if to == HO and t.startswith("confirm")]


async def offered(prio="D", ledger=None):
    reset()
    rows = start(prio)
    f = Fake(rows, ledger)
    await brain.handle("booking_centre", f, rows[0], rows)
    return f, re.findall(r"offer_id=(of-\S+)", f.out[0][1])


async def main():
    f, ids = await offered()
    out = await send(f, row(CZ, BC, f"confirm offer_id={ids[0]} consent_ref=cons-N-1-booking"))
    out2 = await send(f, row(CZ, BC, f"confirm offer_id={ids[1]} consent_ref=cons-N-1-booking"))
    check("a second offer confirmed: no second confirm, the citizen gets the status",
          len(confirms(out)) == 1 and not confirms(out2) and any(to == CZ for to, _ in out2), out + out2)

    # L6: receipt, then fault.cancel of the booking, then the citizen confirms a rebook offer.
    W2 = "slots page=1/1\n" + slot(5, "mon", 23, "11", "D-h2")
    f, ids = await offered("B", lambda t: {"agenda": "agenda agenda_now=2026-11-22T07:30:00+00:00"}.get(
        t, W2 if t.startswith("world") else None))
    await send(f, row(CZ, BC, f"confirm offer_id={ids[0]} consent_ref=cons-N-1-booking"))
    await send(f, row(HO, BC, "booking.receipt booking_id=bk-1 ledger_ref=L-1"))
    await send(f, row(HO, BC, "cancel booking_id=bk-1 reason=fault.cancel"))
    out = await send(f, row(CZ, BC, "confirm offer_id=of-S5 consent_ref=cons-N-1-booking"))
    check("after a fault.cancel the rebook confirm goes out", any("slot_id=S5" in t for t in confirms(out)), out)

    # The facility refuses the first confirm (cancel offer_id): the citizen may pick another.
    f, ids = await offered()
    await send(f, row(CZ, BC, f"confirm offer_id={ids[0]} consent_ref=cons-N-1-booking"))
    await send(f, row(HO, BC, f"cancel offer_id={ids[0]} reason=facility_cancelled"))
    out = await send(f, row(CZ, BC, f"confirm offer_id={ids[1]} consent_ref=cons-N-1-booking"))
    check("after the facility cancels the confirmed offer a new confirm goes out", len(confirms(out)) == 1, out)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
