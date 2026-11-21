"""Time the brain on the conformance suite's custody.ack message, per role.

The city's suite (city_conform, stage custody.ack) sends ONE need.request from an agent
sender with body {"service": "lab", "summary": "visit", "district": 8} and waits only 5.0 s
(settle_wait) for the harness to renew, which happens AFTER the brain returns. In that
environment every relay post answers 404 (CapError) and history holds only the inbound.
A brain turn longer than about 4 s fails the deploy with:
    custody.ack ... expected one renew at fence 1 ... got renews=[] acks=[]

Run:  ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/conform_time.py asst/_shared/agent.py
Exit code 1 when any role takes longer than LIMIT seconds.
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import CapError, Message

LIMIT = 2.5  # seconds; the suite gives 5.0 including network round trips
LAG = 0.12   # simulated relay round trip per call

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)


class ConformCity:
    def __init__(self, agent, inbound):
        self.agent_id = agent
        self.inbound = inbound
        self.address = ""
        self.says = 0
        self.hist = 0
        self.finds = 0

    async def say(self, to, text, *, status=None):
        self.says += 1
        await asyncio.sleep(LAG)
        raise CapError("NOT_FOUND", "relay post 404")

    async def history(self, peer=None, *, need_id=None):
        self.hist += 1
        await asyncio.sleep(LAG)
        return [self.inbound]

    async def find(self, query):
        self.finds += 1
        await asyncio.sleep(LAG)
        return []


async def main():
    worst = 0.0
    for role in ("gp", "booking_centre", "hospital", "specialist", "family_counseling"):
        me = f"agent:t07/{role}"
        m = Message(
            type="need.request",
            sender="agent:t05/osp-x",
            msg_id="conf-1",
            need_id="N-conf",
            body={"service": "lab", "summary": "visit", "district": 8},
            recipient=me,
        )
        city = ConformCity(me, m)
        card = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "..", role, "card.yaml")
        if os.path.exists(card):
            # Like the live pod: the role comes from the card next to the brain.
            os.environ.pop("CITY_ROLE", None)
            os.environ["CITY_CARD"] = card
            seen = brain.role_of()
            if seen != {"specialist": "specialist_care"}.get(role, role):
                print(f"BAD {role:<18} card resolves to role {seen!r}")
                worst = 99.0
            role_run = seen or role
        else:
            os.environ["CITY_ROLE"] = role
            role_run = role
        t0 = time.monotonic()
        err = ""
        try:
            await brain.handle(role_run, city, m, [m])
        except Exception as exc:  # the harness acks "rejected" and never renews
            err = f" RAISED {type(exc).__name__}: {exc}"
        took = time.monotonic() - t0
        worst = max(worst, took)
        flag = "OK " if took <= LIMIT and not err else "BAD"
        print(f"{flag} {role:<18} {took:5.2f}s  says={city.says} history={city.hist} finds={city.finds}{err}")
    os.environ.pop("CITY_ROLE", None)
    return 1 if worst > LIMIT else 0


sys.exit(asyncio.run(main()))
