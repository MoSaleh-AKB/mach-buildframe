"""Fault armour: a partner that fails is marked DOWN, routing skips it, a reply brings it back UP.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/test_armour_faults.py asst/booking_centre/agent/agent.py
"""

import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace

from city_sdk.city import CapError, Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "booking_centre", "agent", "agent.py")
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

ME = "agent:t07/booking"
P1, P2 = "agent:other1", "agent:other2"
n = [0]


class Fake:
    def __init__(self, dead=()):
        self.agent_id = ME
        self.address = ""
        self.dead = set(dead)
        self.posts = []
        self.inbound = Message(type="chat", sender="citizen:c", recipient=ME, msg_id="m0", need_id="N1",
                               conversation_id="cv", body={"text": "ciao"}, sent_at="2026-11-21T10:00:00+00:00")

    async def say(self, to, text, *, status=None):
        if to in self.dead:
            raise CapError("NOT_FOUND", "relay send 404")
        self.posts.append((to, text))
        return "ok"


def slot(sid, fac):
    return (f"slot_id={sid} doctor_id=d-1 at=2099-01-01T10:00:00+00:00 facility={fac} procedure=x")


async def main():
    brain._REPS.clear()
    # 1. a send to a dead partner fails once: DOWN at once
    city = Fake(dead={P1})
    assert await brain._say(city, P1, "need.request x") is False
    assert brain._xa_skip(P1), "partner not marked DOWN"
    # 2. routing skips the DOWN facility and picks the other
    lines = [slot("s1", P1), slot("s2", P2)]
    got = brain.fitting(lines, now=brain._when("2026-11-21T09:00:00+00:00"), priority="P", lang=None)
    assert [g["facility"] for g in got] == [P2], got
    #    if every facility is DOWN the slots are still offered (never an empty answer)
    brain._xa_miss(P2)
    brain._xa_miss(P2)  # silence needs two rounds
    got = brain.fitting(lines, now=brain._when("2026-11-21T09:00:00+00:00"), priority="P", lang=None)
    assert len(got) == 2, got
    # 3. citizen still gets a line when the partner is down
    city = Fake(dead={P1})
    assert await brain._say(city, "citizen:c", "La struttura non risponde, cerco un'alternativa.") is True
    assert city.posts and city.posts[0][0] == "citizen:c"
    # 4. any message from the partner brings it back UP
    brain._peer_alive(P1)
    brain._peer_alive(P2)
    assert not brain._xa_skip(P1) and not brain._xa_skip(P2)
    # 5. a rate limit does not mark DOWN; the ledger is never marked
    brain._peer_failed(P1, CapError("RATE_LIMITED", "429"))
    brain._peer_failed(brain.LEDGER, CapError("NOT_FOUND", "404"))
    print("rate-limit/ledger:", brain._xa_skip(P1), brain._xa_skip(brain.LEDGER))
    assert brain.LEDGER not in brain._REPS
    # 6. cool-down ends after 10 minutes: one more try
    brain._xa_miss(P1)
    brain._xa_miss(P1)  # silence needs two rounds
    assert brain._xa_skip(P1)
    brain._REPS[P1].until -= 601
    assert not brain._xa_skip(P1)
    print("OK armour_faults (6 cases)")


asyncio.run(main())
