"""P1 at the confirm uses the MOVING agenda clock (_agenda_now), not the agenda_now of the last read.

The agenda runs 60x real time from the row's sent_at. A citizen who confirms an hour (real) after the
agenda read is 60 agenda hours later: a prescription that ended 1 agenda day after that read is
expired now and the confirm must not be forwarded (renewal asked). A prescription valid long after
still books.
Run: <city python> -I tests/test_p1_agenda_now.py asst/_shared/agent.py   (exit 1 on FAIL)
"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message, Need

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET, brain._PAGE_BUDGET, brain._CONSENT_WAIT = 0.4, 0.2, 0.3

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1"
SEEN = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)
N, NEED = [0], [800]
FAILS = []


def msg(sender, to, text, need, ago=0.0):
    N[0] += 1
    sent = (datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat()
    return Message(type="chat", sender=sender, msg_id=f"pa{N[0]}", need_id=need, body={"text": text},
                   recipient=to, sent_at=sent)


class City:
    def __init__(self, first, rows):
        self.agent_id, self.inbound, self.address = BC, first, ""
        self.rows, self.out = [*rows, first], []
        for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_SENT", "_GATES"):
            getattr(brain, name, {}).clear()
        brain._RULES.clear()

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.inbound.need_id))
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    def to(self, who):
        return [t for w, t in self.out if w == who]


async def case(valid_until):
    NEED[0] += 1
    need = f"N-PA-{NEED[0]}"
    rx = (f"prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued "
          f"valid_until={valid_until.isoformat()}")
    offer = (f"offer offer_id=of-S2 slot_id=S2 doctor_id=D-h1 facility={HO} procedure=VIS-FIS "
             f"agenda_date={(SEEN + timedelta(days=6)).isoformat()} prescription_number=RX1 priority=B")
    slots = ("slots page=1/1\n" f"slot_id=S2 doctor_id=D-h1 at={(SEEN + timedelta(days=6)).isoformat()} "
             f"day=tue facility={HO} procedure=VIS-FIS free=1")
    hour = 3600.0  # real seconds since the agenda read: 60 agenda hours later
    rows = [
        msg(CZ, BC, "prenota prescription_number=RX1 lang=it", need, ago=hour + 30),
        msg(BC, LEDGER, "prescription prescription_number=RX1", need, ago=hour + 20),
        msg(LEDGER, BC, rx, need, ago=hour + 10),
        msg(LEDGER, BC, f"agenda agenda_now={SEEN.isoformat()}", need, ago=hour),
        msg(BC, LEDGER, "world procedure=VIS-FIS page=1", need, ago=hour - 1),
        msg(LEDGER, BC, slots, need, ago=hour - 2),
        msg(BC, CZ, offer, need, ago=20),
    ]
    first = msg(CZ, BC, "confirm offer_id=of-S2 consent_ref=cons-x-booking", need)
    c = City(first, rows)
    os.environ["CITY_ROLE"] = "booking_centre"
    try:
        try:
            await brain.main(Need(summary=first.text, message=first), c)
        except Exception as exc:
            if type(exc).__name__ != "OutOfScope":
                raise
    finally:
        os.environ.pop("CITY_ROLE", None)
    return c


def check(name, ok, c):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)
        for to, t in c.out:
            print(f"     > {to[-24:]} : {t[:150]!r}")


async def main():
    c = await case(SEEN + timedelta(days=1))
    forwarded = any(t.startswith("confirm") for t in c.to(HO))
    renewal = any("prescription_renewal" in t or "medico di base" in t.lower() for t in c.to(CZ))
    check("rx ended 1 agenda day after the read, confirm 60 agenda hours later: not forwarded, renewal asked",
          not forwarded and renewal, c)
    c = await case(SEEN + timedelta(days=30))
    check("rx valid for 30 more agenda days: confirm forwarded",
          any(t.startswith("confirm") for t in c.to(HO)), c)
    print(f"{len(FAILS)} FAIL")  # magic_pass.py reads "0 FAIL" as clean
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
