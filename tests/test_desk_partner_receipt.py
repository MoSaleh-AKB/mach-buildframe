"""Desk: a cross-ASST partner's receipt or cancel after our confirm reaches the citizen (audit finding 11).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_desk_partner_receipt.py [asst/_shared/agent.py]
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


PA = "agent:other/hospital"


def confirmed():
    """A cross-ASST case up to our confirm to the partner (offer po-1 / PS1, consent given)."""

    return [row(CZ, BC, "Buongiorno, vorrei prenotare prescription_number=RX1"),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00"), row(LEDGER, BC, "slots page=1/1\n"),
            row(BC, PA, "need.request service_type=hospital.physiatry procedure=VIS-FIS priority=B request_id=N-1\nCerco"),
            row(PA, BC, f"offer offer_id=po-1 slot_id=PS1 doctor_id=D-x at=2026-11-23T09:00:00+00:00 facility={PA} procedure=VIS-FIS request_id=N-1"),
            row(BC, CZ, f"Nei tempi...\noffer offer_id=po-1 slot_id=PS1 doctor_id=D-x facility={PA} procedure=VIS-FIS "
                "agenda_date=2026-11-23T09:00:00+00:00 expiry=2026-11-23T09:00:00+00:00 priority=B prescription_number=RX1 "
                "cross_asst=true\noffer offer_id=wl-N-1-alt waiting_list=true"),
            row(BC, CZ, brain._XA_CONSENT),
            row(CZ, BC, "consent purpose=cross_asst consent_ref=cx-1"),
            row(CZ, BC, "confirm offer_id=po-1 consent_ref=cons-N-1-booking"),
            row(BC, PA, f"confirm offer_id=po-1 slot_id=PS1 citizen={CZ} consent_ref=cx-1 facility={PA} prescription_number=RX1 priority=B")]


async def main():
    for text, want in [("booking.receipt booking_id=bk-9 ledger_ref=L-9 offer_id=po-1 slot_id=PS1", "booking.receipt booking_id=bk-9"),
                       ("cancel offer_id=po-1 slot_id=PS1 reason=fault.cancel", "cancel offer_id=po-1")]:
        reset()
        f = Fake(confirmed() + [row(LEDGER, BC, "committed booking_id=bk-9 ledger_ref=L-9 slot_id=PS1")])  # impostor audit #4
        out = await send(f, row(PA, BC, text))
        check(f"partner '{text.split()[0]}' naming offer_id and slot_id reaches the citizen",
              any(to == CZ and t.startswith(want) for to, t in out), out)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
