"""gp: the prescription line goes out before the holding line can fire. _PRESCRIBE_WAIT (2.5 s) was
longer than _HOLD_AFTER (2.0 s), so a Ledger prescribe slower than ~2 s sent "Sto verificando la sua
richiesta..." 0.5 s before every prescription: one extra message and 1 s per journey. Now 1.5 s.

Run: <city python> -I tests/gp/test_gp_rx_before_hold.py asst/_gp/agent.py   (exit 0 = pass)
"""


import asyncio
import importlib.util
import os
import sys
import time
from types import SimpleNamespace

from city_sdk.city import LEDGER, CapError, Message

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
os.environ.setdefault("CITY_ROLE", "gp")
os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "gp"

CZ = "citizen:t07/c1"
GP = sorted(getattr(brain, "_MATES", ["agent:t07/gp"]))[0]
DESK = next(a for a in sorted(brain._MATES) if a != GP)  # any other team agent: the hand-off needs role=booking_centre and a team id
N = [0]
LIMIT = 3.5


def msg(sender, to, text, need, mid=None):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=mid or f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    """Ledger prescribe takes ``slow`` seconds, then succeeds (or answers a 5xx when ``fail``)."""

    address = ""

    def __init__(self, first, slow, fail=False):
        self.agent_id, self.rows, self.inbound, self.out = GP, [first], first, []
        self.slow, self.fail, self.t0 = slow, fail, time.monotonic()

    async def say(self, to, text, *, status=None):
        if to == LEDGER and text.startswith("prescribe"):
            await asyncio.sleep(self.slow)
            if self.fail:
                raise CapError("UPSTREAM_ERROR", "ledger", 502)
        self.out.append((round(time.monotonic() - self.t0, 1), to, text))
        self.rows.append(msg(GP, to, text, self.inbound.need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return [SimpleNamespace(agent_id=DESK, role="booking_centre")]


async def push(city, m):
    view = SimpleNamespace(inbound=m, agent_id=GP, address="", find=city.find, history=city.history, say=city.say)
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)
    await asyncio.sleep(city.slow + 0.5)  # the Ledger write finishes in the background


def first_rx_at(city):
    return next((t for t, to, x in city.out if to == CZ and "prescription_number=" in x), None)




async def main():
    fails = []
    for slow in (1.2, 3.0):
        m = msg(CZ, GP, "Buongiorno, ho mal di schiena da due giorni", f"N-hb{slow}", mid=f"cz-hb{slow}")
        city = City(m, slow=slow)
        await push(city, m)
        told = [x for _, to, x in city.out if to == CZ]
        rx = next((i for i, x in enumerate(told) if "prescription_number=" in x), None)
        if rx is None:
            fails.append(f"Ledger {slow} s: no prescription line {told}")
        elif rx:
            fails.append(f"Ledger {slow} s: {rx} line(s) before the prescription: {[x[:50] for x in told[:rx]]}")
    for f in fails:
        print("FAIL", f)
    print(("ALL PASS" if not fails else f"FAIL {len(fails)}") + " test_gp_rx_before_hold")
    sys.exit(1 if fails else 0)


asyncio.run(main())
