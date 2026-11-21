"""gp: a slow Ledger prescribe never delays the citizen's prescription reply (audit R2A, 53 journeys).

The Ledger write (`prescribe ...`) used to be awaited before the citizen was answered; a Ledger that
takes 8-20 s left the citizen's reply window empty. The write now runs as a task: the citizen gets the
prescription number within about 3 s, and a failed write afterwards is told once (_GP_FAIL).

Run: <city python> -I tests/gp/test_gp_slow_prescribe.py asst/_gp/agent.py
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
DESK = brain._our_desk()  # #140: the hand-off goes to the known desk id first
GP = next(a for a in sorted(brain._MATES) if a != DESK)
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
    oks = []

    def check(ok, name, city):
        print(("PASS " if ok else "FAIL ") + name)
        oks.append(ok)
        if not ok:
            for t, to, x in city.out:
                print("    ", t, to[:16], x[:140].replace("\n", " | "))

    m = msg(CZ, GP, "Buongiorno, ho mal di schiena da due giorni", "N-slow", mid="cz-slow")
    city = City(m, slow=9.0)
    await push(city, m)
    at = first_rx_at(city)
    check(at is not None and at <= LIMIT, f"slow Ledger prescribe (9 s): prescription number to the citizen by {LIMIT} s (got {at})", city)
    check(any(to == LEDGER and x.startswith("prescribe") for _, to, x in city.out), "slow Ledger prescribe: the write still goes out", city)
    hand = [t for t, to, x in city.out if to == DESK and x.startswith("hand-off")]
    wrote = next((t for t, to, x in city.out if to == LEDGER and x.startswith("prescribe")), None)
    check(len(hand) == 1 and wrote is not None and hand[0] >= wrote,
          f"slow Ledger prescribe: the hand-off to the desk goes out once, only after the write succeeded (write {wrote}, hand-off {hand})", city)

    m = msg(CZ, GP, "Buongiorno, ho mal di schiena da due giorni", "N-fail", mid="cz-fail")
    city = City(m, slow=5.0, fail=True)
    await push(city, m)
    lines = [x for _, to, x in city.out if to == CZ]
    check(bool(lines), "failed slow prescribe: the citizen heard something", city)
    check(brain._tr(brain._GP_FAIL, "it") in lines, "failed slow prescribe: _GP_FAIL is sent", city)
    check(not any(to == DESK for _, to, _x in city.out), "failed slow prescribe: no hand-off to the desk", city)

    m = msg(CZ, GP, "Buongiorno, ho mal di schiena da due giorni", "N-fast", mid="cz-fast")
    city = City(m, slow=0.0)
    await push(city, m)
    hand = [t for t, to, x in city.out if to == DESK and x.startswith("hand-off")]
    check(len(hand) == 1, "fast Ledger prescribe: the hand-off still goes out once", city)
    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_gp_slow_prescribe")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
