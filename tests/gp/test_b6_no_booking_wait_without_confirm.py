"""B6 (export, 5 consultorio failed): "Sto registrando la sua prenotazione nel registro" went to a
citizen who had not confirmed anything: an open hold of ours (a reserve before any confirm) was
taken as the citizen's booking. The booking-wait line needs the citizen's own confirm.
Run: <city python> -I tests/gp/test_b6_no_booking_wait_without_confirm.py asst/_gp/agent.py"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "family_counseling"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._CONS_HOLD_AFTER = 0.2

ME = "agent:pv3a0t3tyqdq"
CZ = "citizen:c-b6"
FAILS = []
NOW = datetime.now(timezone.utc)
SEQ = [0]


def msg(sender, to, text, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"b6-{SEQ[0]}", need_id="N-b6-" + str(SEQ[0] // 10),
                   conversation_id="cv-b6", body={"text": text}, sent_at=(NOW - timedelta(seconds=ago)).isoformat())


class City:
    agent_id = ME
    address = ""

    def __init__(self, rows):
        self.rows = rows
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


def check(ok, label, city):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for to, x in city.out:
            print("    ->", to, repr(x[:120]))


async def case(confirmed):
    SEQ[0] = (SEQ[0] // 10 + 1) * 10
    rows = [
        msg(CZ, ME, "Buongiorno, vorrei una visita ginecologica", 300),
        msg(ME, CZ, "Buongiorno, ho trovato alcune disponibilità:\noffer offer_id=of-c1-1030 slot_id=c1-1030", 290),
        msg(ME, LEDGER, "hold slot_id=c1-1030 citizen=" + CZ + " consent_ref=cons-1", 280),
    ]
    if confirmed:
        rows.append(msg(CZ, ME, "confirm offer_id=of-c1-1030 consent_ref=cons-1", 200))
    last = msg(CZ, ME, "ci siete? aspetto", 0)
    rows.append(last)
    city = City(rows)
    await brain._cons_hold_soon(city, last)
    return city


async def main():
    wait = brain._BOOKING_WAIT
    city = await case(confirmed=False)
    said = [x for to, x in city.out if to == CZ]
    check(not any(wait[:40] in x for x in said), "open hold, no citizen confirm: no 'Sto registrando la sua prenotazione'", city)
    check(len(said) == 1, f"open hold, no citizen confirm: still one holding line ({len(said)})", city)
    city = await case(confirmed=True)
    said = [x for to, x in city.out if to == CZ]
    check(any(wait[:40] in x for x in said), "citizen confirmed, hold open: the booking-wait line", city)
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
