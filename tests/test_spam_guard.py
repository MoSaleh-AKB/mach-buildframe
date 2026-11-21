"""Citizen anti-spam guard in _say (live journeys 050 and 091: same offers 66 and 100+ times).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_spam_guard.py
"""

import asyncio
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("brain", os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)


class Inbound:
    need_id = "N-spam-1"


class City:
    def __init__(self):
        self.inbound = Inbound()
        self.sent = []

    async def say(self, to, text, *, status=None):
        self.sent.append((to, text))


async def main():
    city = City()
    cit = "citizen:t07/c91"
    replay = "Le ripropongo le disponibilità già inviate.\noffer offer_id=of-1 slot_id=S1"
    for i in range(20):  # 20 refusals arriving at once: one goes out, the dropped repeats say False
        assert await brain._say(city, cit, replay) is (i == 0)
    assert sum(1 for _, t in city.sent if t == replay) == 1, city.sent
    for i in range(10):  # burst of different lines: capped per minute
        await brain._say(city, cit, f"line {i}")
    to_cit = [t for to, t in city.sent if to == cit]
    assert len(to_cit) <= brain._CITIZEN_BURST, len(to_cit)
    # Agents and the Ledger are never throttled by this guard.
    for _ in range(5):
        await brain._say(city, "city:ledger", "hold slot_id=S1")
        await brain._say(city, "agent:t07/hospital", "confirm offer_id=of-1")
    assert sum(1 for to, _ in city.sent if to == "city:ledger") == 5
    assert sum(1 for to, _ in city.sent if to.startswith("agent:")) == 5
    # Another case of the same citizen is independent.
    city.inbound.need_id = "N-spam-2"
    await brain._say(city, cit, replay)
    assert sum(1 for _, t in city.sent if t == replay) == 2
    print("OK test_spam_guard")


asyncio.run(main())
