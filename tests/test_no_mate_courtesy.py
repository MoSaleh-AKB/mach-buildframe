"""L8: our own agents never trade free-text courtesy lines (live: the gp and the desk sent each
other 74 of them). To one of our five agents only protocol lines (key=value) go out; another
team's agent still gets the courtesy line once.
Run: <city python> -I tests/test_no_mate_courtesy.py asst/_shared/agent.py   (or asst/_gp/agent.py)"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone

from city_sdk.city import Message

os.environ.setdefault("CITY_ROLE", "booking_centre")
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

ME = "agent:y6hxsd766sys"
GP = "agent:jmqwr12vy2j6"
OTHER = "agent:zz9other000"
FAILS = []


class City:
    agent_id = ME

    def __init__(self):
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "ok"


def msg(sender, text, mid):
    return Message(type="chat", sender=sender, recipient=ME, msg_id=mid, need_id="N-1", conversation_id="cv-1",
                   body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


def check(ok, label, city):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        print("    ", city.out)


async def main():
    courtesy = brain._DESK_AGENT
    city = City()
    m = msg(GP, "Grazie, resto a disposizione per qualsiasi cosa.", "m1")
    await brain._note_agent(city, [m], m, courtesy)
    check(not city.out, "own agent: no free-text courtesy line", city)
    city = City()
    m = msg(GP, "need.request purpose=visit", "m2")
    await brain._note_agent(city, [m], m, "cancel booking_id=bk-1 reason=citizen_request\nAnnullata.")
    check(len(city.out) == 1, "own agent: a protocol line (key=value) still goes out", city)
    city = City()
    m = msg(GP, "booking.receipt booking_id=PB-1", "m4")
    await brain._note_agent(city, [m], m, brain._RECEIPT_UNEXPECTED)
    check(len(city.out) == 1, "own agent: a reason line (receipt we cannot relay) still goes out", city)
    city = City()
    m = msg(OTHER, "Ciao, come posso aiutarvi?", "m3")
    await brain._note_agent(city, [m], m, courtesy)
    check(len(city.out) == 1, "other team's agent: one courtesy line", city)
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
