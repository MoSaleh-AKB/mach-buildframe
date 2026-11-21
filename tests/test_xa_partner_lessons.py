"""Lessons from other teams' partner hospitals (live 21 Nov, DEPLOY-CHECK d):
1. A partner answered our asks 10-20 min late with PAST days: a late answer is judged on the agenda
   clock now (the case's old agenda read moved on), so a past day is never relayed.
2. The same partner slot went to 4 of our needs: a partner slot we relayed to one citizen is never
   relayed to another one of ours.
Run: <city python> -I tests/test_xa_partner_lessons.py asst/_shared/agent.py
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC = "agent:y6hxsd766sys"
PARTNER = "agent:k3m9partner1"
OLD_NOW = datetime(2026, 12, 8, 8, 0, tzinfo=timezone.utc)
N = [0]
FAILS: list[str] = []


def check(ok, what, extra=""):
    print(("PASS " if ok else "FAIL ") + what + (f" {extra}" if extra and not ok else ""))
    if not ok:
        FAILS.append(what)


def row(sender, to, text, need, ago=0.0):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"x{N[0]}", need_id=need, body={"text": text},
                   recipient=to, sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


def offer(oid, sid, at):
    day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[at.weekday()]
    return (f"offer offer_id={oid} slot_id={sid} doctor_id=D-p1 facility={PARTNER} procedure=VIS-FIS "
            f"agenda_date={at.isoformat()} agenda_day={day}")


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows):
        self.rows, self.out, self.inbound = list(rows), [], rows[0]

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(row(self.agent_id, to, text, self.rows[0].need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def case(citizen, need, agenda_ago):
    """A need whose agenda was read ``agenda_ago`` real seconds ago (60x: 20 min = 20 agenda h)."""
    return [
        row(citizen, BC, "need.request prescription_number=RX1 vorrei una visita", need),
        row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=D state=issued", need),
        row(LEDGER, BC, f"agenda agenda_now={OLD_NOW.isoformat()}", need, ago=agenda_ago),
    ]


def relayed(fake, citizen):
    return [t for to, t in fake.out if to == citizen and f"facility={PARTNER}" in t]


async def late(citizen, need, line, agenda_ago=1200.0):
    rows = case(citizen, need, agenda_ago)
    answer = row(PARTNER, BC, line, need)
    rows.append(answer)
    fake = Fake(rows)
    await brain._xa_late(fake, answer, rows)
    return relayed(fake, citizen)


async def main():
    # 1. Past day on the agenda clock now: the old read says Dec 8 08:00, 20 real min later it is
    #    Dec 9 04:00. A slot on Dec 8 18:00 fitted the old read and is past now.
    past = await late("citizen:t/p1", "n-past", offer("of-P1", "PP1", OLD_NOW + timedelta(hours=10)))
    check(not past, "a late partner answer with a day already past is not relayed", str(past))
    later = await late("citizen:t/p2", "n-fut", offer("of-P2", "PP2", OLD_NOW + timedelta(days=3)))
    check(bool(later), "a late partner answer with a future day is still relayed")

    # 2. One partner slot, two of our needs: only the first citizen gets it.
    brain._XA_GIVEN.clear() if hasattr(brain, "_XA_GIVEN") else None
    same = OLD_NOW + timedelta(days=4)
    a = await late("citizen:t/a1", "n-a", offer("of-S1", "PS1", same), agenda_ago=1.0)
    b = await late("citizen:t/b1", "n-b", offer("of-S1", "PS1", same), agenda_ago=1.0)
    check(bool(a), "the first need gets the partner slot")
    check(not b, "the same partner slot never goes to a second citizen of ours", str(b))
    print("FAILED: " + "; ".join(FAILS) if FAILS else "OK test_xa_partner_lessons")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
