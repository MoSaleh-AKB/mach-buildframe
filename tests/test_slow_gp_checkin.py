"""Slow Ledger (replies 5 s late, history only): a GP renewal still prescribes, and a specialist
check-in whose checked_in never comes still asks consent and files the report.
Run: <city python> -I tests/test_slow_gp_checkin.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 6.0  # shorter run; the live value (20 s) covers the live 8-20 s delay
CZ = "citizen:t/c1"
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Slow:
    address = ""

    def __init__(self, agent, first, ledger):
        self.agent_id, self.inbound, self.rows, self.out, self.ledger = agent, first, [first], [], ledger

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, self.inbound.need_id))
        for delay, answer in self.ledger(text) if to == LEDGER else []:
            asyncio.get_running_loop().call_later(
                delay, self.rows.append, msg(LEDGER, self.agent_id, answer, self.inbound.need_id))
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


async def turn(role, city):
    os.environ["CITY_ROLE"] = role
    await brain.main(Need(summary=city.inbound.text, message=city.inbound), city)
    return city.out


async def renew():
    led = lambda t: [(5.0, "prescription prescription_number=RX-OLD-1 procedure=VIS-CARD priority=B "
                           "patient=citizen:t/c1 state=expired")] if t.startswith("prescription ") else []
    out = await turn("gp", Slow("agent:t/gp", msg(CZ, "agent:t/gp", "prescription_number=RX-OLD-1", "N9"), led))
    assert any(to == LEDGER and t.startswith("prescribe ") for to, t in out), out


async def checkin():
    led = lambda t: [(0.1, "reported " + t[7:])] if t.startswith("report ") else []
    sp = "agent:t/specialist_care"
    out = await turn("specialist_care", Slow(sp, msg(CZ, sp, "checkin booking_id=bk-1", "N5"), led))
    assert any(to == CZ and t.startswith("consent purpose=report_sharing") for to, t in out), out
    assert any(to == LEDGER and t.startswith("report booking_id=bk-1") for to, t in out), out


for case in (renew, checkin):
    asyncio.run(case())
    print("ok", case.__name__)
