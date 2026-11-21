"""B8 (export, 4 failed, consultorio): "Le ripropongo le disponibilità già inviate" re-listed
offers whose dates were already past on the agenda clock (60x the real clock). Past lines are
never listed again; when every line we sent is past, a fresh search runs instead.
Run: <city python> -I tests/gp/test_b8_no_past_relist.py asst/_gp/agent.py"""
import asyncio
import importlib.util
import inspect
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "family_counseling"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

ME = "agent:pv3a0t3tyqdq"
CZ = "citizen:c-b8"
FAILS = []
NOW = datetime.now(timezone.utc)


def msg(sender, to, text, mid, ago=0.0):
    return Message(type="chat", sender=sender, recipient=to, msg_id=mid, need_id="N-b8", conversation_id="cv-b8",
                   body={"text": text}, sent_at=(NOW - timedelta(seconds=ago)).isoformat())


class City:
    agent_id = ME
    inbound = None
    address = ""

    def __init__(self):
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return []


def offer(slot, at):
    return f"offer offer_id=of-{slot} slot_id={slot} facility={ME} procedure=CONS-GIN agenda_date={at} expiry={at}"


def check(ok, label, city):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for to, x in city.out:
            print("    ->", to, repr(x[:140]))


async def case(agenda_now, ago_real, slots):
    """Agenda read ``ago_real`` real seconds ago at ``agenda_now``; our offers for ``slots``."""
    city = City()
    rows = [
        msg(CZ, ME, "Buongiorno, vorrei una visita ginecologica", "c1", 600),
        msg(LEDGER, ME, f"agenda agenda_now={agenda_now}", "l1", ago_real),
        msg(ME, CZ, "\n".join(["Buongiorno, ho trovato alcune disponibilità:", *[offer(s, a) for s, a in slots]]), "o1", ago_real),
        msg(CZ, ME, "ci siete? aspetto", "c2", 0),
    ]
    calls = []
    real = brain._advance

    async def spy(*args, **kwargs):
        calls.append(kwargs)

    brain._advance = spy
    try:
        kw = {"fixed": "CONS-GIN"} if "fixed" in inspect.signature(brain._status).parameters else {}
        await brain._status(city, rows, rows[-1], ME, **kw)
    finally:
        brain._advance = real
    return city, calls


async def main():
    # Agenda 2026-12-03T09:00 read 2 real minutes ago = 11:00 now (60x). 10:30 is past, 12:00 is not.
    city, calls = await case("2026-12-03T09:00:00+01:00", 120,
                             [("c1-20261203-1030", "2026-12-03T10:30:00+01:00"), ("c1-20261203-1200", "2026-12-03T12:00:00+01:00")])
    said = "\n".join(x for to, x in city.out if to == CZ)
    check("c1-20261203-1030" not in said, "past slot (10:30, agenda 11:00) not listed again", city)
    check("c1-20261203-1200" in said, "future slot (12:00) still listed", city)
    # Every slot past: no relist, a fresh search instead.
    city, calls = await case("2026-12-03T09:00:00+01:00", 300,
                             [("c1-20261203-1030", "2026-12-03T10:30:00+01:00"), ("c1-20261203-1200", "2026-12-03T12:00:00+01:00")])
    said = "\n".join(x for to, x in city.out if to == CZ)
    check("offer_id=" not in said, "all slots past (agenda 14:00): nothing re-listed", city)
    check(len(calls) == 1 and calls[0].get("replacing") is True, f"all slots past: fresh search ({calls})", city)
    # Nothing past: same relist as before.
    city, calls = await case("2026-12-03T09:00:00+01:00", 10,
                             [("c1-20261203-1030", "2026-12-03T10:30:00+01:00")])
    said = "\n".join(x for to, x in city.out if to == CZ)
    check("c1-20261203-1030" in said and not calls, "no slot past: relisted as before", city)
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
