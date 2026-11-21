"""Review 2026-11-21-1315 item 26: a check-in that waited out the need lock read the case once more
(up to 3 s) before its honest "not confirmed yet" line. The turn already spent the whole lock
budget: the line now comes within about 0.5 s of the lock timeout.
Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_checkin_lock_timeout_line.py [asst/_shared/agent.py]"""
import asyncio
import importlib.util
import os
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brainck", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
b.role_of = lambda: "hospital"
if hasattr(b, "_LLM_NET"):
    b._LLM_NET = False
b._RESCUE_WARMUP = 1e9
b._CITIZEN_TURN = 3.0  # lock budget 1 s in this test
ME, CZ, NEED = "agent:r8yprrs0e8be", "citizen:t07/cck", "N-ck"


class City:
    agent_id, address = ME, ""

    def __init__(self, inbound):
        self.inbound, self.out = inbound, []

    async def say(self, to, text, *, status=None):
        self.out.append((time.monotonic(), to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(5.0)  # a slow relay
        return [self.inbound]

    async def find(self, query):
        return []


async def main():
    m = Message(type="chat", sender=CZ, recipient=ME, msg_id="ck1", need_id=NEED,
                body={"text": "checkin booking_id=bk-9 sono arrivato"})
    city = City(m)
    lock = b._need_lock(NEED)
    await lock.acquire()  # another turn of this need holds the lock
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(b.main(SimpleNamespace(summary="x", is_emergency=False), city), 15)
    except Exception as exc:
        if type(exc).__name__ not in ("OutOfScope", "TimeoutError"):
            raise
    lock_wait = b._CITIZEN_TURN - 2.0
    told = [(t - t0, x) for t, to, x in city.out if to == CZ]
    fails = []
    if not told:
        fails.append("no line at all after the lock timeout")
    elif told[0][0] > lock_wait + 0.8:
        fails.append(f"line {told[0][0]:.1f} s after the turn began, lock budget {lock_wait:.1f} s: "
                     f"{told[0][0] - lock_wait:.1f} s more than the lock wait (want <= 0.8 s)")
    for f in fails:
        print("FAIL", f)
    print(f"line at {told[0][0] if told else None}")
    print(("OK" if not fails else f"FAIL {len(fails)}") + " test_checkin_lock_timeout_line")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


asyncio.run(main())
