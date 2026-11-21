"""Offer-id question and confirm of an unknown offer (live journeys 158, 190, 191).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_unknown_offer.py
"""

import asyncio
import importlib.util
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("brain", os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CONS, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "agent:t07/consultorio", "citizen:t07/c1"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
RX = "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=B state=issued"


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


def slots(first_hour):
    rows = []
    for i in range(1, 7):
        at = (BASE + timedelta(hours=first_hour + 7 + i)).isoformat()
        rows.append(f"slot_id=S{i} doctor_id=asst-centro-h1 at={at} day=mon facility={HO} procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    def __init__(self, me, rows, now_hours, answer_hold=False):
        self.agent_id, self.address, self.rows, self.out = me, "", list(rows), []
        self.now_hours, self.answer_hold, self.inbound = now_hours, answer_hold, None

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        if to != LEDGER:
            self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            head = text.split()[0]
            body = {"agenda": f"agenda agenda_now={(BASE + timedelta(hours=self.now_hours)).isoformat()}",
                    "world": slots(self.now_hours)}.get(head)
            if body:
                self.rows.append(msg(LEDGER, self.agent_id, body, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset(role):
    brain.role_of = lambda: role
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        getattr(brain, name).clear()
    brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, row):
    city.rows.append(row)
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find,
                           history=city.history, say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)
    return [text for to, text in city.out if to == CZ]


def case(me, need, offer_age):
    facility = me if me == CONS else HO
    return [
        msg(CZ, me, "Vorrei una visita.", need, ago=offer_age + 30),
        msg(LEDGER, me, RX, need, ago=offer_age + 20),
        msg(LEDGER, me, f"agenda agenda_now={BASE.isoformat()}", need, ago=offer_age + 20),
        msg(LEDGER, me, slots(0), need, ago=offer_age + 20),
        msg(me, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=asst-centro-h1 facility={facility} procedure=VIS-FIS "
            f"agenda_date={(BASE + timedelta(hours=12)).isoformat()} expiry={(BASE + timedelta(hours=12)).isoformat()} "
            "prescription_number=prescription_number-N-0001", need, ago=offer_age),
    ]


def withdrawn(text):
    return "ritiro questa proposta" in text or "I withdraw that proposal" in text


async def main():
    oks = []
    ask = "Which offer_id, slot_id?"
    # 1. open slot offers: the question re-sends them with their ids
    reset("booking_centre")
    city = City(BC, case(BC, "N-1", 90), now_hours=0)
    got = await turn(city, msg(CZ, BC, ask, "N-1"))
    ok = len(got) == 1 and "offer_id=of-S1" in got[0] and "slot_id=S1" in got[0] and not withdrawn(got[0])
    print(("PASS " if ok else "FAIL ") + "id question re-sends open offers"); oks.append(ok)
    # 2. journey 190/191: waiting-list offer cancelled, then the question: fresh offers, no withdrawal
    reset("booking_centre")
    rows = [
        msg(CZ, BC, "Mi serve una visita.", "N-2", ago=120),
        msg(LEDGER, BC, RX, "N-2", ago=110),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", "N-2", ago=110),
        msg(LEDGER, BC, slots(0), "N-2", ago=110),
        msg(BC, CZ, "Non ho trovato un posto.\noffer offer_id=wl-N-2-alt waiting_list=true", "N-2", ago=60),
        msg(CZ, BC, "Non posso. cancel offer_id=wl-N-2-alt reason=waiting_list", "N-2", ago=50),
        msg(BC, CZ, "Va bene: resta in lista d'attesa, la avviso appena si libera un posto.", "N-2", ago=40),
    ]
    city = City(BC, rows, now_hours=0)
    got = await turn(city, msg(CZ, BC, ask, "N-2"))
    ok = bool(got) and all(not withdrawn(t) for t in got) and any("offer_id=" in t for t in got)
    print(("PASS " if ok else "FAIL ") + "id question after a refused waiting-list offer"); oks.append(ok)
    if not ok:
        for t in got:
            print("   ", t[:200].replace("\n", " | "))
    # 3. Italian question
    reset("booking_centre")
    city = City(BC, case(BC, "N-3", 90), now_hours=0)
    got = await turn(city, msg(CZ, BC, "Quale offerta scelgo?", "N-3"))
    ok = len(got) == 1 and "offer_id=of-S1" in got[0] and not withdrawn(got[0])
    print(("PASS " if ok else "FAIL ") + "Italian id question"); oks.append(ok)
    # 4. English citizen after cancelling every offer: "Which slot_id?" / "slot_id?" get offers with ids
    for n, q in (("N-4", "Which slot_id?"), ("N-5", "slot_id?"), ("N-6", "which slot")):
        reset("booking_centre")
        rows = case(BC, n, 90) + [
            msg(CZ, BC, "Hello. I need an English speaking doctor. need.request lang=en", n, ago=100),
            msg(CZ, BC, "No, thank you. cancel offer_id=of-S1 reason=lang", n, ago=60),
        ]
        city = City(BC, rows, now_hours=0)
        got = await turn(city, msg(CZ, BC, q, n))
        ok = bool(got) and all(not withdrawn(t) and "received" not in t for t in got) and any("offer_id=" in t for t in got)
        print(("PASS " if ok else "FAIL ") + f"English {q!r} after refusal"); oks.append(ok)
        if not ok:
            for t in got:
                print("   ", t[:200].replace("\n", " | "))
    print("ALL OK" if all(oks) else "SOME FAILED")
    raise SystemExit(0 if all(oks) else 1)


asyncio.run(main())
