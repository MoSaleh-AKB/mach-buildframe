"""#107 (R22): a partner's late offer (_xa_late) read the case inside the need lock with no time cap.
Its turn has the short agent budget (_AGENT_TURN), but a slow relay held the lock, and every citizen
turn of the case queued behind it, for the SDK's full read timeout. The read now waits only what the
turn has left, so the lock is free again within the turn budget.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_107_xa_late_read_cap.py [path/to/agent.py]
"""
import asyncio
import importlib.util
import os
import sys
import time

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain107", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False

BC, PEER, CZ = "agent:t07/booking_centre", "agent:t09/booking_centre", "citizen:t07/c107"
NEED = "need-107"
SLOW_READ = 20.0  # a stuck relay: one case read


class SlowCity:
    agent_id = BC
    address = ""

    def __init__(self):
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(SLOW_READ)
        return []


def late_offer():
    return Message(type="chat", sender=PEER, recipient=BC, msg_id="s107-1", need_id=NEED,
                   body={"text": "offer offer_id=o1 slot_id=s1 date=2026-12-01"})


async def case_late_offer_frees_lock():
    city = SlowCity()
    brain._TURN_END.set(time.monotonic() + brain._AGENT_TURN)  # a partner turn's short budget
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(brain._xa_late(city, late_offer(), []), 8.0)
    except asyncio.TimeoutError:
        raise AssertionError("_xa_late held the need lock past 8 s on a slow case read")
    took = time.monotonic() - t0
    assert took < 3.0, f"_xa_late took {took:.1f} s inside the need lock (agent turn budget {brain._AGENT_TURN} s)"
    assert NEED not in brain._GATES or not brain._GATES[NEED].lock.locked(), "need lock still held"
    return took


def main():
    took = asyncio.run(case_late_offer_frees_lock())
    print(f"ok  late partner offer: need lock free after {took:.2f} s")
    print("1/1 passed")


if __name__ == "__main__":
    main()
