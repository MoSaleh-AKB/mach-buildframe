"""R35 (#120): a partner answer turn never waits the need lock or the rebook past its budget.

1. _xa_late: another turn of the same need holds the per-need lock (a long _advance). Before the
   fix the late-offer turn waited that lock with no timeout. Now it waits only the agent turn
   budget (_AGENT_TURN), then the relay runs in the background once the lock is free.
2. _cancel_trigger from an agent sender: before the fix the turn ran the whole rebook inline
   (up to _CITIZEN_TURN, 40 s live). Now the work is its own task; the turn waits it only for
   what is left of _AGENT_TURN, the task finishes in the background. A crash inside the budget
   still reaches the caller (F01 fallback).
Run: <city python> -I tests/test_120_xa_late_lock.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import itertools
import sys
import time

from city_sdk.city import Message

BRAIN = sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py"
SLOW = 4.0
_ids = itertools.count(1)


def load():
    spec = importlib.util.spec_from_file_location(f"brain120_{next(_ids)}", BRAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.role_of = lambda: "booking_centre"
    return mod


def msg(sender, to, text, need):
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{next(_ids)}", need_id=need,
                   body={"text": text})


class Fake:
    def __init__(self, inbound):
        self.agent_id = "agent:t120/booking_centre"
        self.address = ""
        self.inbound = inbound
        self.rows = [inbound]
        self.log = []  # shared by shallow copies (the brain copies City for background work)

    async def say(self, to, text, *, status=None):
        self.rows.append(msg(self.agent_id, to, text, self.inbound.need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        self.log.append(need_id)
        return list(self.rows)

    async def llm(self, messages, *, need_id=None):
        return {"content": ""}

    async def find(self, query):
        return []


async def xa_late_case(fails):
    brain = load()
    need = f"N-120-{next(_ids)}"
    peer = "agent:t120x/booking_centre"
    inbound = msg(peer, "agent:t120/booking_centre", "offer offer_id=OF-1 slot_id=S-1", need)
    city = Fake(inbound)
    held, free = asyncio.Event(), asyncio.Event()

    async def holder():
        async with brain._xa_gate(need):
            held.set()
            await free.wait()

    hold = asyncio.create_task(holder())
    await held.wait()
    brain._TURN_END.set(time.monotonic() + brain._AGENT_TURN)
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(brain._xa_late(city, inbound, list(city.rows)), SLOW)
    except asyncio.TimeoutError:
        fails.append(f"_xa_late waited the need lock over {SLOW} s (no timeout)")
        free.set()
        await hold
        return
    took = time.monotonic() - t0
    print(f"_xa_late with lock held: returned in {took:.2f}s")
    if took > brain._AGENT_TURN + 0.5:
        fails.append(f"_xa_late took {took:.2f}s, budget {brain._AGENT_TURN}s")
    if len(city.log):
        fails.append("_xa_late read history without the lock")
    free.set()
    await hold
    for _ in range(40):
        if len(city.log):
            break
        await asyncio.sleep(0.05)
    print(f"background relay after the lock freed: history reads={len(city.log)}")
    if not len(city.log):
        fails.append("the late relay never ran after the lock was freed")


async def cancel_case(fails):
    brain = load()
    need = f"N-120-{next(_ids)}"
    inbound = msg("agent:t120x/booking_centre", "agent:t120/booking_centre", "cancel offer_id=OF-9", need)
    city = Fake(inbound)
    done = asyncio.Event()

    async def slow_note(*a, **k):
        await asyncio.sleep(2.5)
        done.set()

    brain._note_agent = slow_note  # stands in for a slow rebook (fresh world reads)
    brain._TURN_END.set(time.monotonic() + brain._AGENT_TURN)
    t0 = time.monotonic()
    await asyncio.wait_for(brain._cancel_trigger(city, inbound, [inbound], fixed=None), 10)
    took = time.monotonic() - t0
    print(f"_cancel_trigger agent turn: returned in {took:.2f}s")
    if took > brain._AGENT_TURN + 0.5:
        fails.append(f"_cancel_trigger held the agent turn {took:.2f}s, budget {brain._AGENT_TURN}s")
    try:
        await asyncio.wait_for(done.wait(), 5)
    except asyncio.TimeoutError:
        fails.append("the cancel work did not finish in the background")

    async def crash(*a, **k):
        raise RuntimeError("boom")

    brain._note_agent = crash
    brain._TURN_END.set(time.monotonic() + brain._AGENT_TURN)
    try:
        await brain._cancel_trigger(city, inbound, [inbound], fixed=None)
        fails.append("a crash inside the budget did not reach the caller")
    except RuntimeError:
        print("crash inside the budget: reaches the caller")


async def main():
    fails = []
    await xa_late_case(fails)
    await cancel_case(fails)
    if fails:
        print("FAILED:", "; ".join(fails))
        sys.exit(1)
    print("ALL PASS test_120_xa_late_lock")


asyncio.run(main())
