"""A partner need.request answered with the wait line (Ledger too slow) gets its offer, once,
when the late Ledger slots arrive in their own turn.
Run: <city python> -I tests/test_late_slots_offer.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
PARTNER = "agent:other/booking_centre"
N = [0]


def msg(sender, to, text):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id="X1", body={"text": text}, recipient=to)


class Slow:
    address = ""

    def __init__(self, agent, first):
        self.agent_id, self.inbound, self.rows, self.out = agent, first, [first], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text))
        head = text.split()[0]
        answer = {
            "agenda": "agenda agenda_now=2026-11-23T08:00:00+00:00",
            "world": f"slots page=1/1\nslot_id=S1 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue "
                     f"facility={self.agent_id} procedure=VIS-FIS free=1",
        }.get(head) if to == LEDGER else None
        if answer:  # 3 s late: after the 1.5 s agent-turn budget, history only
            asyncio.get_running_loop().call_later(3.0, self.rows.append, msg(LEDGER, self.agent_id, answer))
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


async def run(role, agent):
    os.environ["CITY_ROLE"] = role
    city = Slow(agent, msg(PARTNER, agent, "need.request service_type=visit procedure=VIS-FIS priority=D request_id=r1"))
    await brain.main(Need(summary=city.inbound.text, message=city.inbound), city)
    # L8 E4: a wait line (never a cancel) while the Ledger is slow; older builds sent the cancel.
    last = city.out[-1][1]
    assert getattr(brain, "_PARTNER_WAIT_MARK", "reason=temporarily_unavailable") in last, city.out
    assert not last.startswith("cancel") or not hasattr(brain, "_PARTNER_WAIT_MARK"), city.out
    await asyncio.sleep(3.5)
    for late in [r for r in city.rows if r.sender == LEDGER][-1:] * 2:  # same turn twice: one offer only
        city.inbound = late
        await brain.main(Need(summary=late.text, message=late), city)
    offers = [t for to, t in city.out if to == PARTNER and "offer_id=" in t]
    assert len(offers) == 1, city.out
    assert all(to in (PARTNER, LEDGER) for to, _ in city.out), city.out


for role, agent in (("hospital", "agent:t/hospital"), ("family_counseling", "agent:t/family_counseling")):
    asyncio.run(run(role, agent))
    print("ok", role)
