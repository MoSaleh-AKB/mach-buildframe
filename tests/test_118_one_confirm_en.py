"""Issue 118 (R33): the one-confirm status reply is in English for an English citizen.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_118_one_confirm_en.py [asst/_shared/agent.py]
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


def start(prio="B", extra=(), hello="Buongiorno, vorrei prenotare prescription_number=RX1"):
    return [row(CZ, BC, hello),
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


async def offered(prio="D", ledger=None, hello="Buongiorno, vorrei prenotare prescription_number=RX1"):
    reset()
    rows = start(prio, hello=hello)
    f = Fake(rows, ledger)
    await brain.handle("booking_centre", f, rows[0], rows)
    return f, re.findall(r"offer_id=(of-\S+)", f.out[0][1])


EN = "Hello, I would like to book a visit please, prescription_number=RX1"
ITALIAN = re.compile(r"Ho gi\u00e0 inviato|La sua prenotazione|attendo la")


def to_cz(out):
    return [t for to, t in out if to == CZ]


async def main():
    f, ids = await offered(hello=EN)
    await send(f, row(CZ, BC, f"Yes, I confirm offer_id={ids[0]} consent_ref=cons-N-1-booking"))
    out = await send(f, row(CZ, BC, f"Actually I confirm offer_id={ids[1]} consent_ref=cons-N-1-booking"))
    txt = " ".join(to_cz(out))
    check("EN citizen, second confirm, no receipt yet: status in English",
          not confirms(out) and "already sent your confirmation" in txt and not ITALIAN.search(txt), out)

    f, ids = await offered(hello=EN)
    await send(f, row(CZ, BC, f"Yes, I confirm offer_id={ids[0]} consent_ref=cons-N-1-booking"))
    await send(f, row(HO, BC, "booking.receipt booking_id=bk-1 ledger_ref=L-1"))
    out = await send(f, row(CZ, BC, f"Actually I confirm offer_id={ids[1]} consent_ref=cons-N-1-booking"))
    txt = " ".join(to_cz(out))
    check("EN citizen, second confirm after the receipt: status in English",
          not confirms(out) and "booking_id=bk-1" in txt and "Your booking is registered" in txt
          and not ITALIAN.search(txt), out)

    f, ids = await offered()
    await send(f, row(CZ, BC, f"confirm offer_id={ids[0]} consent_ref=cons-N-1-booking"))
    out = await send(f, row(CZ, BC, f"confirm offer_id={ids[1]} consent_ref=cons-N-1-booking"))
    check("IT citizen keeps the Italian status", not confirms(out) and "Ho gi\u00e0 inviato" in " ".join(to_cz(out)), out)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
