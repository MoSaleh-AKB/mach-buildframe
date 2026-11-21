"""#139: the need lock is handed to the desk confirm's background task only while it still runs.

Case A: the background confirm finishes DURING the turn's holding-line send: the lock is released
exactly once (no RuntimeError), a second message of the same need waits for the lock (never two
turns on one need), and the lock is free at the end.
Case B: the turn is cancelled during the 2.5 s wait: the background task keeps the lock and frees it.

Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_139_lock_once.py [brain.py]
"""
import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain139b", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False
brain._RESCUE_WARMUP = 1e9

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c139"
NEED = "N-139-b"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
N = [0]
FAILS = []


def msg(sender, to, text, ago=0.0):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"l139-{N[0]}", need_id=NEED,
                   conversation_id="cv-139b", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


class City:
    agent_id = BC
    address = ""

    def __init__(self, rows, ledger_delay, hold_delay):
        self.rows, self.out, self.timers = list(rows), [], []
        self.ledger_delay, self.hold_delay = ledger_delay, hold_delay
        self.inbound = None
        self.fail_hold, self.t0 = False, time.monotonic()

    async def say(self, to, text, *, status=None):
        if to == CZ and self.fail_hold and time.monotonic() - self.t0 >= 2.4:
            self.fail_hold = False  # the first citizen line after the wait (the holding line) fails once
            raise RuntimeError('say failed')
        if to == CZ and self.hold_delay:
            await asyncio.sleep(self.hold_delay)  # slow send of the holding line
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text))
        if to == LEDGER and text.startswith("prescription"):
            async def later():
                await asyncio.sleep(self.ledger_delay)
                self.rows.append(msg(LEDGER, BC, "prescription prescription_number=RX139 "
                                     "procedure=VIS-FIS priority=B state=issued"))
            self.timers.append(asyncio.get_running_loop().create_task(later()))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def base_rows():
    at = (BASE + timedelta(hours=8)).isoformat()
    return [
        msg(CZ, BC, "vorrei una visita prescription_number=RX139", 90),
        msg(LEDGER, BC, "prescription prescription_number=RX139 procedure=VIS-FIS priority=B state=issued", 80),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", 80),
        msg(LEDGER, BC, "rules\nrule_id=P1 scope=prescription max_age_days=30", 80),
        msg(BC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=D1 facility={HO} procedure=VIS-FIS "
            f"agenda_date={at} expiry={at} prescription_number=RX139", 30),
    ]


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE", "_HELD",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT", "_DESK_BG", "_DESK_HOLD"):
        getattr(brain, name, {}).clear()
    brain._LOCK_HANDOFF.clear()
    brain._TAIL_DELAY[0] = 0.0
    brain._RESCUE_LAST[:] = [time.monotonic()]


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {info}" if not ok else ""))
    if not ok:
        FAILS.append(name)


SPAN = []  # (start, end) of every _main_locked run of the need
_orig_locked = brain._main_locked


brain._TAIL_DELAY = [0.0]


async def _spy(city, message, text):
    s = time.monotonic()
    try:
        r = await _orig_locked(city, message, text)
        if brain._TAIL_DELAY[0] and message.msg_id == "l139-first":
            await asyncio.sleep(brain._TAIL_DELAY[0])  # the turn tail awaits after _desk_confirm returned
        return r
    finally:
        SPAN.append((s, time.monotonic(), message.msg_id))


brain._main_locked = _spy
CONFIRM = "confirm offer_id=of-S1 consent_ref=cons-N-139-b-booking"


async def case_a():
    reset()
    SPAN.clear()
    inbound = msg(CZ, BC, CONFIRM)
    city = City(base_rows() + [inbound], ledger_delay=3.0, hold_delay=1.5)
    city.inbound = inbound
    errs = []
    first = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), city))
    await asyncio.sleep(3.3)  # in the holding send (2.5 .. 4.0); the background confirm is done at ~3.0
    second_msg = msg(CZ, BC, "grazie")
    city.rows.append(second_msg)
    city2 = City([], 0, 0)
    city2.rows, city2.out, city2.timers, city2.inbound = city.rows, city.out, city.timers, second_msg
    second = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), city2))
    for t in (first, second):
        try:
            await asyncio.wait_for(t, 20)
        except Exception as exc:
            errs.append(repr(exc))
    await asyncio.sleep(0.5)
    lock = brain._need_lock(NEED)
    check("A: no error (no double release)", not errs, str(errs))
    check("A: the lock is free at the end", not lock.locked())
    check("A: no handoff key left", NEED not in brain._LOCK_HANDOFF, str(brain._LOCK_HANDOFF))
    spans = sorted(SPAN)
    overlap = any(spans[i][1] > spans[i + 1][0] + 1e-3 for i in range(len(spans) - 1))
    check("A: second message of the need waited (no two turns at once)", not overlap, str(spans))
    for t in city.timers:
        t.cancel()


async def case_c():
    reset()
    SPAN.clear()
    brain._TAIL_DELAY[0] = 3.0
    inbound = msg(CZ, BC, CONFIRM)
    object.__setattr__(inbound, "msg_id", "l139-first")
    city = City(base_rows() + [inbound], ledger_delay=3.0, hold_delay=0)
    city.inbound = inbound
    errs = []
    first = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), city))
    await asyncio.sleep(3.5)  # hold line out at 2.5; the background confirm finished at ~3.0; the tail still awaits
    second_msg = msg(CZ, BC, "grazie")
    city.rows.append(second_msg)
    city2 = City([], 0, 0)
    city2.rows, city2.out, city2.timers, city2.inbound = city.rows, city.out, city.timers, second_msg
    second = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), city2))
    for t in (first, second):
        try:
            await asyncio.wait_for(t, 20)
        except Exception as exc:
            errs.append(repr(exc))
    await asyncio.sleep(0.5)
    lock = brain._need_lock(NEED)
    check("C: no error (released exactly once)", not errs, str(errs))
    check("C: the lock is free at the end", not lock.locked())
    check("C: no handoff key left", NEED not in brain._LOCK_HANDOFF, str(brain._LOCK_HANDOFF))
    spans = sorted(SPAN)
    overlap = any(spans[i][1] > spans[i + 1][0] + 1e-3 for i in range(len(spans) - 1))
    check("C: second message waited for the tail (no two turns at once)", not overlap, str(spans))
    for t in city.timers:
        t.cancel()


async def case_d():
    reset()
    SPAN.clear()
    inbound = msg(CZ, BC, CONFIRM)
    city = City(base_rows() + [inbound], ledger_delay=4.0, hold_delay=0)
    city.inbound = inbound
    city.fail_hold = True
    first = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), city))
    await asyncio.sleep(3.0)  # the holding line failed at 2.5 s; the background confirm runs until ~4.0
    lock = brain._need_lock(NEED)
    check("D: the turn did not release the lock under the running task", lock.locked())
    second_msg = msg(CZ, BC, "grazie")
    city.rows.append(second_msg)
    city2 = City([], 0, 0)
    city2.rows, city2.out, city2.timers, city2.inbound = city.rows, city.out, city.timers, second_msg
    second = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), city2))
    await asyncio.sleep(0.3)
    check("D: the second message waits for the lock", not second.done() and not any(m == second_msg.msg_id for _, _, m in SPAN))
    for t in (first, second):
        try:
            await asyncio.wait_for(t, 20)
        except Exception:
            pass  # the failed holding line may surface from the first turn; only the lock matters here
    await asyncio.sleep(0.5)
    check("D: the lock is free at the end", not lock.locked())
    check("D: no handoff key left", NEED not in brain._LOCK_HANDOFF, str(brain._LOCK_HANDOFF))
    spans = sorted(SPAN)
    overlap = any(spans[i][1] > spans[i + 1][0] + 1e-3 for i in range(len(spans) - 1))
    check("D: no two turns at once", not overlap, str(spans))
    for t in city.timers:
        t.cancel()


async def case_b():
    reset()
    SPAN.clear()
    inbound = msg(CZ, BC, CONFIRM)
    city = City(base_rows() + [inbound], ledger_delay=4.0, hold_delay=0)
    city.inbound = inbound
    first = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), city))
    await asyncio.sleep(1.0)  # inside the 2.5 s wait
    first.cancel()
    try:
        await first
    except BaseException:
        pass
    lock = brain._need_lock(NEED)
    check("B: after the cancel the background task still holds the lock", lock.locked())
    check("B: the handoff key is set", brain._LOCK_HANDOFF.get(NEED) == "turn_done", str(brain._LOCK_HANDOFF))
    await asyncio.sleep(6.0)
    check("B: the lock is freed when the background confirm ends", not lock.locked())
    check("B: no handoff key left", NEED not in brain._LOCK_HANDOFF)
    fw = [t for to, t in city.out if to == HO and t.startswith("confirm")]
    check("B: the confirm reached the facility exactly once", len(fw) == 1, str(city.out))
    for t in city.timers:
        t.cancel()


asyncio.run(case_a())
asyncio.run(case_b())
asyncio.run(case_c())
asyncio.run(case_d())
print("ALL OK" if not FAILS else "SOME FAILED")
sys.exit(1 if FAILS else 0)
