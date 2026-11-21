"""The report summary goes only to our GP, never to another of our agents (BACKLOG HA-27).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_report_gp.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

SPEC, GP, DESK = "agent:dqfw9nh15dn4", "agent:jmqwr12vy2j6", "agent:y6hxsd766sys"


class City:
    agent_id = SPEC

    def __init__(self):
        self.out = []
        self.inbound = None

    async def say(self, to, text, *, status=None):
        self.out.append((str(to), text))


def row(sender, text, n):
    return Message(type="chat", sender=sender, recipient=SPEC, msg_id=f"m{n}", need_id="n-ha27",
                   body={"text": text})


async def main():
    desk_line = row(DESK, "prescription prescription_number=RX1 procedure=VIS-CARD priority=B", 1)
    gp_line = row(GP, "prescription prescription_number=RX1 procedure=VIS-CARD priority=B", 2)
    city = City()
    await brain._share_with_gp(city, [desk_line, gp_line], "BK1", "cons-1")
    assert [to for to, _ in city.out] == [GP], city.out  # the desk came first, the GP gets it
    city = City()
    await brain._share_with_gp(city, [desk_line], "BK1", "cons-1")
    assert city.out == [], city.out  # only our desk wrote "prescription": nothing leaves
    print("OK test_report_gp")


asyncio.run(main())
