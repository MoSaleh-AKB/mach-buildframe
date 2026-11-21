"""#125: a citizen who cancels the waiting list gets the nearest real slot, not "resta in lista d'attesa".

Live console scan 21 Nov 12:03 (200 journeys, 0 booked): every failed journey ends with the citizen
cancelling `wl-...-alt` reason=waiting_list (02990008, 03730002). The desk answered "Va bene: resta in
lista d'attesa", the opposite of the answer, and searched nothing.

Run: <city python> -I tests/test_wl_cancel_real_slot.py [asst/_shared/agent.py]
"""
import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ, C2 = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1", "citizen:t07/c2"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
RX = "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=" + __import__("os").environ.get("PRIO","B") + " state=issued"


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


def slots():
    rows = [f"slot_id=S{i} doctor_id=asst-centro-h1 at={(BASE + timedelta(hours=7 + i)).isoformat()} day=mon "
            f"facility={HO} procedure=VIS-FIS free=1" for i in range(1, 13)]
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    def __init__(self, rows):
        self.agent_id, self.address, self.rows, self.out, self.inbound = BC, "", list(rows), [], None
        self.ledger = []

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            self.ledger.append(text)
            head = text.split()[0]
            body = {"agenda": f"agenda agenda_now={BASE.isoformat()}", "world": (slots() if not getattr(self, "empty", False) else "slots page=1/1")}.get(head)
            if body:
                self.rows.append(msg(LEDGER, self.agent_id, body, need))
        else:
            self.out.append((to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        getattr(brain, name, {}).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, row):
    city.rows.append(row)
    city.inbound = row
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find,
                           history=city.history, say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)
    return [t for to, t in city.out if to == row.sender]


AT = (BASE + timedelta(hours=8)).isoformat()
OFFER = (f"offer offer_id=of-S1 slot_id=S1 doctor_id=asst-centro-h1 facility={HO} procedure=VIS-FIS "
         f"agenda_date={AT} expiry={AT} prescription_number=prescription_number-N-0001")


def offered(need, en=False, extra=()):
    first = "Hello, I need a visit. prescription_number=prescription_number-N-0001" if en else \
        "Vorrei prenotare. prescription_number=prescription_number-N-0001"
    return [
        msg(CZ, BC, first, need, ago=40),
        msg(LEDGER, BC, RX, need, ago=38),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", need, ago=38),
        msg(LEDGER, BC, slots(), need, ago=38),
        msg(BC, CZ, ("Here are the options.\n" if en else "Ecco le disponibilità.\n") + OFFER, need, ago=30),
        *extra,
    ]


def forwarded(need, ago):
    return msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=c-1 facility={HO} "
               "prescription_number=prescription_number-N-0001", need, ago=ago)


def to_ho(city):
    return [t for to, t in city.out if to == HO and t.split()[0] == "confirm"]




def case(n, en=False, real=True):
    rows = offered(n, en=en)
    rows[-1] = msg(BC, CZ, (("Here are the options.\n" if en else "Ecco le disponibilità.\n") + OFFER + "\n" if real else "Nessun posto nei tempi.\n")
                   + f"offer offer_id=wl-{n}-alt waiting_list=true", n, ago=30)
    return rows


def real(got):
    return [w for t in got for w in t.split() if w.startswith("offer_id=of-")]


async def main():
    oks = []

    def check(ok, name, got=()):
        print(("PASS " if ok else "FAIL ") + name)
        oks.append(ok)
        if not ok:
            for t in got:
                print("    ", t[:170].replace("\n", " | "))

    stay = ("resta in lista d'attesa", "you stay on the waiting list")
    for label, rows_kw in (("offers + waiting list", {}), ("only the waiting list", {"real": False})):
        reset(); n = "N-" + ("a" if rows_kw else "b"); city = City(case(n, **rows_kw))
        got = await turn(city, msg(CZ, BC, f"cancel offer_id=wl-{n}-alt reason=waiting_list", n))
        check(bool(real(got)) and not any(s in t.lower() for t in got for s in stay),
              f"{label}: waiting list cancelled -> real slots, never 'resta in lista d'attesa'", got)
        check(sum(1 for t in city.ledger if t.startswith("world")) == 1, f"{label}: exactly one new search", got)
    # the same waiting-list cancel again: no second search (no loop), still answered
    reset(); n = "N-c"; city = City(case(n))
    await turn(city, msg(CZ, BC, f"cancel offer_id=wl-{n}-alt reason=waiting_list", n))
    w0 = sum(1 for t in city.ledger if t.startswith("world"))
    got = await turn(city, msg(CZ, BC, f"cancel offer_id=wl-{n}-alt reason=waiting_list", n))
    check(bool(got) and sum(1 for t in city.ledger if t.startswith("world")) == w0, "repeat waiting-list cancel: answered, no new search", got)
    # the Ledger has nothing at all: an honest answer, never silence, no invented slot
    reset(); n = "N-d"; city = City(case(n, real=False)); city.empty = True
    got = await turn(city, msg(CZ, BC, f"cancel offer_id=wl-{n}-alt reason=waiting_list", n))
    check(bool(got) and not real(got), "no slot anywhere: answered, no invented slot", got)
    # English citizen: English offers
    reset(); n = "N-e"; city = City(case(n, en=True))
    got = await turn(city, msg(CZ, BC, f"cancel offer_id=wl-{n}-alt reason=waiting_list", n))
    check(bool(real(got)) and not any("Buongiorno" in t for t in got), "English: real slots, in English", got)
    # a normal (non waiting-list) cancel is unchanged: new offers as before
    reset(); n = "N-f"; city = City(case(n))
    got = await turn(city, msg(CZ, BC, "cancel offer_id=of-S1 reason=other", n))
    check(bool(real(got)), "a slot cancel still gets new offers", got)
    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_wl_cancel_real_slot")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
