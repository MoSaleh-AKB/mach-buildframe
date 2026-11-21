"""#124 (live): the desk's first offer came 750-1020 s after the request. The platform serves one
turn at a time per agent, and a desk search turn waited a slow case read with the 38 s citizen cap
(R22) plus a whole-mailbox read of up to 2 s (R23) before its first Ledger ask, so every citizen
queued behind it. The desk search turn now stays inside its short budget: the holding line goes
out, the Ledger is asked, and the background resume sends the offers.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_124_desk_slow_read.py   (or with -m pytest)
"""
import asyncio
import importlib.util
import os
import time
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("brain124", os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False
brain._RESCUE_WARMUP = 1e9  # the rescue sweep has its own budget; this test measures the turn itself

BC, CZ = "agent:t07/booking_centre", "citizen:t07/c124"
CASE_READ = 8.0  # a slow relay: one case read
MAILBOX_READ = 1.9  # a big desk mailbox: one whole-mailbox read
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"s124-{N[0]}", need_id=need,
                   body={"text": text})


class SlowCity:
    agent_id = BC
    address = ""

    def __init__(self, rows, inbound):
        self.rows, self.out, self.inbound = list(rows), [], inbound
        self.mailbox_reads = 0

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.inbound.need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        if need_id is None:
            self.mailbox_reads += 1
            await asyncio.sleep(MAILBOX_READ)
        else:
            await asyncio.sleep(CASE_READ)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


async def _turn(need):
    first = msg(CZ, BC, "vorrei prenotare la visita, prescription_number=RX124 lang=it", need)
    city = SlowCity([first], first)
    t0 = time.monotonic()
    await brain.main(SimpleNamespace(summary="x", is_emergency=False), city)  # the real turn entry
    took = time.monotonic() - t0
    for task in list(getattr(brain, "_RESUMING", {}).values()) + list(getattr(brain, "_RX_SCANNING", {}).values()):
        task.cancel()
    return city, took


def test_desk_search_turn_short_under_slow_reads():
    city, took = asyncio.run(_turn("N-124-a"))
    # Before the fix the turn waited the whole 8 s case read (cap 38 s) and the 1.9 s mailbox read.
    assert took < 4.0, f"desk search turn took {took:.1f} s: {city.out}"
    asked = [t for to, t in city.out if to == LEDGER]
    told = [t for to, t in city.out if to == CZ]
    assert asked, f"no Ledger ask in the turn: {city.out}"
    assert told, f"citizen got no line in the turn: {city.out}"


if __name__ == "__main__":
    test_desk_search_turn_short_under_slow_reads()
    print("test_124_desk_slow_read ok")
