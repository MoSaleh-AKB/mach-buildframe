"""A citizen confirm that cannot be served gets exactly one reply (live: 16-32 min late confirms).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_confirm_never_silent.py
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
            f"agenda_date={(BASE + timedelta(hours=1)).isoformat()} expiry={(BASE + timedelta(hours=1)).isoformat()} "
            "prescription_number=prescription_number-N-0001", need, ago=offer_age),
    ]


def check(label, got, expect_new_offers):
    # train-0205: the "confirming, one moment" note (audit #12) may precede the real answer.
    got = [t for t in got if t != brain._CONFIRMING_NOTE and not t.startswith(brain._CONFIRMING_NOTE)]
    ok = len(got) == 1 and (("offer_id=of-" in got[0] and "slot_id=S1 " not in got[0]) if expect_new_offers else True)
    print(("PASS " if ok else "FAIL ") + label + f" ({len(got)} reply)")
    if not ok:
        for text in got:
            print("   ", text[:200].replace("\n", " | "))
    return ok


async def main():
    oks = []
    confirm = "confirm consent_ref=cons-N-0001 offer_id=of-S1"
    # 1. taken: the facility refused the slot, the desk was silent
    reset("booking_centre")
    rows = case(BC, "N-1", 90) + [msg(HO, BC, "cancel offer_id=of-S1 reason=facility_cancelled", "N-1", ago=5)]
    city = City(BC, rows, now_hours=0)
    oks.append(check("confirm on a taken slot", await turn(city, msg(CZ, BC, confirm, "N-1")), True))
    # 2. past: 20 agenda hours later, the offered slot is behind us
    reset("booking_centre")
    city = City(BC, case(BC, "N-2", 1200), now_hours=20)
    got = await turn(city, msg(CZ, BC, confirm, "N-2"))
    oks.append(check("confirm on a past slot", got, True))
    oks.append(all(f"at={(BASE + timedelta(hours=i)).isoformat()}" not in got[0] for i in range(0, 29)) if got else False)
    print(("PASS " if oks[-1] else "FAIL ") + "fresh offers start 8 agenda hours after now")
    # 3. hold timeout: the Ledger never answers the hold inside the turn
    reset("family_counseling")
    brain._PULL_BUDGET = 3.0
    city = City(CONS, case(CONS, "N-3", 60), now_hours=0)
    oks.append(check("confirm with the hold unanswered", await turn(city, msg(CZ, CONS, confirm, "N-3")), False))
    print("ALL OK" if all(oks) else "SOME FAILED")
    raise SystemExit(0 if all(oks) else 1)


asyncio.run(main())
