"""Desk: a cancelled booking's receipt is dead (audit findings 4 and 5).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_desk_receipt.py [asst/_shared/agent.py]
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


W2 = "slots page=1/1\n" + "\n".join([slot(5, "mon", 23, "11", "D-h2"), slot(6, "mon", 23, "12", "D-h1")])


def led(t):
    if t == "agenda":
        return "agenda agenda_now=2026-11-22T07:30:00+00:00"
    if t.startswith("world"):
        return W2
    return None


async def cancelled(step):
    """offer -> confirm -> receipt bk-1 -> fault.cancel (rebook offers go out) -> ``step``."""

    reset()
    rows = start()
    f = Fake(rows, led)
    await brain.handle("booking_centre", f, rows[0], rows)
    oid = re.findall(r"offer_id=(\S+) slot_id=S1", f.out[0][1])[0]
    await send(f, row(CZ, BC, f"confirm offer_id={oid} consent_ref=cons-N-1-booking"))
    await send(f, row(HO, BC, "booking.receipt booking_id=bk-1 ledger_ref=L-1"))
    await send(f, row(HO, BC, "cancel booking_id=bk-1 reason=fault.cancel"))
    return await send(f, row(CZ, BC, step))


async def rule_change():
    """Booked under P1 v1, then P1 changes: a plain question gets the status, no new offers."""

    reset()
    rows = start(extra=[row(LEDGER, BC, "rules\nrule_id=P1 version=1\nrule_id=P3 version=1")])
    f = Fake(rows, lambda t: {"agenda": "agenda agenda_now=2026-11-22T08:05:00+00:00"}.get(t, WORLD if t.startswith("world") else None))
    await brain.handle("booking_centre", f, rows[0], rows)
    oid = re.findall(r"offer_id=(\S+) slot_id=S1", f.out[0][1])[0]
    await send(f, row(CZ, BC, f"confirm offer_id={oid} consent_ref=cons-N-1-booking"))
    await send(f, row(HO, BC, "booking.receipt booking_id=bk-1 ledger_ref=L-1"))
    f.rows.append(row(LEDGER, BC, "rules\nrule_id=P1 version=2 valid_days=2\nrule_id=P3 version=1"))
    return await send(f, row(CZ, BC, "Perfetto. Una domanda: devo portare qualcosa per la visita?"))


async def main():
    out = await cancelled("Quindi adesso ho la visita o no?")
    texts = [t for to, t in out if to == CZ]
    check("status after a cancel does not repeat the dead receipt",
          bool(texts) and not any("booking.receipt" in t or "registrata" in t for t in texts), out)
    out = await cancelled("cancel offer_id=of-S5 reason=too_late")
    texts = [t for to, t in out if to == CZ]
    check("refusing a rebook offer looks again, never 'confermata'",
          bool(texts) and not any("confermata" in t or "booking.receipt" in t for t in texts), out)
    out = await rule_change()
    texts = [t for to, t in out if to == CZ]
    check("rule change after a booking gives the receipt, no new offer",
          any("booking.receipt booking_id=bk-1" in t for t in texts)
          and not any("offer_id=" in t for t in texts), out)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
