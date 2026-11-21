"""!64 (card 38): a facility's incomplete confirm gets the facility courtesy, never the booking-desk
text ("Sono il centro prenotazioni").

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_fac_confirm_courtesy.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "hospital"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "hospital"

HO, CZ = "agent:t07ho", "citizen:t07-c1"
FAILS = []


class City:
    agent_id, address, inbound = HO, "", None

    def __init__(self):
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {detail[:200]!r}"))
    if not ok:
        FAILS.append(name)


async def run(own_world, sender):
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_ONCE", "_SENT", "_VERIFIED", "_XA_REP", "_REJECTED", "_CANCELS_SENT", "_ASKED", "_FILED", "_RESCUED"):
        g = getattr(brain, name, None)
        if hasattr(g, "clear"):
            g.clear()
    city = City()
    row = Message(type="chat", sender=sender, recipient=HO, msg_id="m1", need_id="N-fc1", body={"text": "confirm offer_id=of-1 slot_id=S1"},
                  sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    city.inbound = row
    offer = Message(type="chat", sender=HO, recipient=sender, msg_id="m0", need_id="N-fc1",
                    body={"text": "offer offer_id=of-1 slot_id=S1 doctor_id=d1 at=2026-11-23T09:00:00+00:00 facility=" + HO},
                    sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 5)))
    await brain._on_confirm(city, row, [offer, row], hold_self=False, own_world=own_world)
    return " | ".join(t for _, t in city.out)


async def main():
    got = await run(True, "agent:t07bc")
    check("facility world: no booking-desk text", "Sono il centro prenotazioni" not in got, got)
    check("facility world: said something", bool(got), got)
    got = await run(False, "agent:t07bc")
    check("desk world keeps its text", bool(got), got)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
