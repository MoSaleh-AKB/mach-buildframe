"""A prescription the gp has just issued is never a dead end at the desk (issue #57).

Live journey 890904000128: the gp issued the prescription and told the citizen its number in the
same turn, before the Ledger had written it. The desk's first lookup was refused ("not found"), and
the desk asks the Ledger about a number only once per case: every later turn waited, then said "La
ricetta indicata non risulta valida o è scaduta", even after the prescription existed.

Run: <city python> -I tests/test_rx_just_issued.py [brain.py]
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
brain._ADVANCE_BUDGET = 1.0  # the fake Ledger answers at once: keep the waits short
brain._HOLD_AFTER = 30.0  # no holding-line task in these turns

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
RX = "prescription_number-N-2026-11-02-890904000128"
BAD = "La ricetta indicata non risulta valida"


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


def slots():
    rows = [f"slot_id=S{i} doctor_id=asst-centro-h1 at={(BASE + timedelta(hours=7 + i)).isoformat()} day=mon "
            f"facility={HO} procedure=VIS-CARD free=1" for i in range(1, 7)]
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    """Fake Ledger: a lookup of ``number`` is refused until ``refuse`` lookups have been answered
    (the gp's prescribe is not written yet); ``state`` is the state it then reports."""

    def __init__(self, need, refuse=1, state="issued", known=RX):
        self.agent_id, self.address, self.need = BC, "", need
        self.rows, self.out, self.lookups, self.inbound = [], [], [], None
        self.refuse, self.state, self.known = refuse, state, known

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        self.rows.append(msg(self.agent_id, to, text, need))
        if to != LEDGER:
            self.out.append(text)
            return "ok"
        head = text.split()[0]
        if head == "prescription":
            number = next((w.split("=", 1)[1] for w in text.split() if w.startswith("prescription_number=")), "")
            self.lookups.append(number)
            if number != self.known or len([n for n in self.lookups if n == number]) <= self.refuse:
                body = f"refused prescription prescription_number={number} reason=not_found"
            else:
                body = f"prescription prescription_number={number} procedure=VIS-CARD priority=B state={self.state}"
        elif head == "agenda":
            body = f"agenda agenda_now={BASE.isoformat()}"
        elif head == "world":
            body = slots()
        else:
            return "ok"
        self.rows.append(msg(LEDGER, self.agent_id, body, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT", "_LEDGER_SENDS"):
        getattr(brain, name, {}).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, text):
    row = msg(CZ, BC, text, city.need)
    city.rows.append(row)
    city.inbound = row
    n0 = len(city.out)
    view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=city.find, history=city.history,
                           say=lambda to, t, *, status=None: city.say(to, t, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)
    return city.out[n0:]


def offered(lines):
    return any("offer_id=" in t for t in lines)


ASK = f"Buongiorno, vorrei prenotare un controllo al cuore. prescription_number={RX} lang=it"


async def main():
    oks = []

    def check(ok, name, city, got=()):
        print(("PASS " if ok else "FAIL ") + name)
        oks.append(ok)
        if not ok:
            print("     lookups:", len(city.lookups))
            for t in got or city.out:
                print("    ", t[:160].replace("\n", " | "))

    # 1. first lookup refused (the gp's prescribe not written yet), the next one finds it
    reset(); city = City("N-1", refuse=1)
    got = await turn(city, ASK)
    check(offered(got) and not any(BAD in t for t in got), "just issued: a refused first lookup is checked again, offers sent", city, got)
    # 2. refused for longer than one turn: no offers yet, but the citizen writing again gets them
    reset(); city = City("N-2", refuse=3)
    first = await turn(city, ASK)
    again = await turn(city, "Ho scritto il numero giusto, me lo ha appena dato il medico.")
    check(offered(again), "refused all of turn 1: the citizen's next message checks again and gets offers", city, first + again)
    check(len(city.lookups) <= 6, f"bounded lookups ({len(city.lookups)} for 2 turns)", city)
    # 3. a number the Ledger never knows: still refused, no offers, no booking, the clear line
    reset(); city = City("N-3", known="nothing")
    got = await turn(city, ASK)
    check(not offered(got) and any(BAD in t for t in got), "unknown number: no offers, the 'non valida' line", city, got)
    check(len(city.lookups) <= 3, f"unknown number: at most 3 lookups in one turn ({len(city.lookups)})", city)
    check(not any(t.startswith(("hold", "commit")) for t in city.out), "unknown number: nothing held", city)
    # 4. expired prescription: the renewal note, never offers
    reset(); city = City("N-4", refuse=0, state="expired")
    got = await turn(city, ASK)
    check(not offered(got) and bool(got), "expired: answered, no offers", city, got)
    # 5. an old refused number does not brand the new one
    reset(); city = City("N-5", refuse=0)
    city.rows += [msg(CZ, BC, "prescription_number=prescription_number-OLD-1", "N-5"),
                  msg(BC, LEDGER, "prescription prescription_number=prescription_number-OLD-1", "N-5"),
                  msg(LEDGER, BC, "refused prescription prescription_number=prescription_number-OLD-1 reason=not_found", "N-5")]
    got = await turn(city, ASK)
    check(offered(got) and not any(BAD in t for t in got), "an old refused number does not block the new valid one", city, got)
    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_rx_just_issued")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
