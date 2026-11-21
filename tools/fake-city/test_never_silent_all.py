"""Never silent, every role: hold line, timeout note, 5xx retry, timer rescue sweep.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/test_never_silent_all.py asst/_shared/agent.py
Exit 0 = pass.
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import CapError, Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

ME = "agent:t07/x"
NOW = datetime.now(timezone.utc)
seq = [0]
checks = [0]


def ok(cond, what):
    checks[0] += 1
    assert cond, what


def at(seconds):
    return (NOW - timedelta(seconds=seconds)).isoformat()


def msg(sender, need, text, age, to=None):
    seq[0] += 1
    return Message(type="chat", sender=sender, recipient=to or (ME if sender != ME else "citizen:c"),
                   msg_id=f"m{seq[0]}", need_id=need, conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=at(age))


class Fake:
    def __init__(self, rows=(), hang=False, fail_first=None):
        self.agent_id = ME
        self.address = ""
        self.rows = list(rows)
        self.inbound = None
        self.posts = []
        self.hang = hang
        self.fail_first = fail_first
        self.say_calls = 0

    async def say(self, to, text, *, status=None):
        self.say_calls += 1
        if self.fail_first is not None and self.say_calls == 1:
            raise CapError(self.fail_first, "boom")
        need = self.inbound.need_id if self.inbound else ""
        self.posts.append((need, to, text))
        self.rows.append(Message(type="chat", sender=ME, recipient=to, msg_id=f"p{len(self.posts)}",
                                 need_id=need, body={"text": text}, sent_at=at(0)))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        if self.hang:
            await asyncio.sleep(60)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]


def reset():
    for t in (brain._RESCUE_LAST, brain._SWEEP_CITY):
        t.clear()
    for t in (brain._RESCUED, brain._RESCUE_REFUSED, brain._REJECTED, brain._CITIZEN_SENT,
              brain._CITIZEN_RATE, brain._DUPS, brain._CANCELS_SENT, brain._HELD, brain._SWEEP_TOLD):
        t.clear()
    brain._LAST_FAIL.clear()


def need():
    return SimpleNamespace(summary="x", is_emergency=False)


async def case_hold_hang():
    real = brain.handle

    async def hang(role, city, message, chat):
        await asyncio.sleep(60)

    brain.handle = hang
    brain._HOLD_AFTER = 1.0
    try:
        for role in brain.ROLES:
            reset()
            brain.role_of = lambda role=role: role
            city = Fake(hang=True)
            city.inbound = msg("citizen:c", f"N-{role}", "mi serve una visita", 0)
            t0 = time.monotonic()
            task = asyncio.create_task(brain.main(need(), city))
            while not city.posts and time.monotonic() - t0 < 6.0:
                await asyncio.sleep(0.05)
            took = time.monotonic() - t0
            ok(city.posts, f"{role}: silent while history hangs")
            ok(took < 4.0, f"{role}: hold line after {took:.1f}s")
            ok(len([p for p in city.posts if p[1] == "citizen:c"]) == 1, f"{role}: one hold line only")
            task.cancel()
            with_exc = await asyncio.gather(task, return_exceptions=True)
            ok(isinstance(with_exc[0], asyncio.CancelledError), f"{role}: real cancel is re-raised")
    finally:
        brain.handle = real


async def case_timeout():
    real = brain.handle

    async def boom(role, city, message, chat):
        raise asyncio.TimeoutError()

    brain.handle = boom
    try:
        for role in brain.ROLES:
            reset()
            brain.role_of = lambda role=role: role
            city = Fake()
            city.inbound = msg("citizen:c", f"N-t-{role}", "ciao", 0)
            raised = False
            try:
                await brain.main(need(), city)
            except asyncio.TimeoutError:
                raised = True
            ok(raised, f"{role}: timeout still re-raised for the harness")
            ok(any(p[1] == "citizen:c" and "riscriva" in p[2] for p in city.posts), f"{role}: no line on timeout")
    finally:
        brain.handle = real


async def case_retry():
    real_sleep = asyncio.sleep
    reset()
    city = Fake(fail_first="DRAINING")
    city.inbound = msg("citizen:c", "N-r", "ciao", 0)
    ok(await brain._say(city, "citizen:c", "una riga") is True, "503 retry not delivered")
    ok(city.say_calls == 2 and len(city.posts) == 1, "503: exactly one retry")
    reset()
    city = Fake(fail_first="NOT_FOUND")
    city.inbound = msg("citizen:c", "N-r", "ciao", 0)
    ok(await brain._say(city, "citizen:c", "una riga") is False and city.say_calls == 1, "4xx must not retry")
    reset()
    city = Fake(fail_first="DRAINING")
    city.inbound = msg("citizen:c", "N-r", "ciao", 0)
    ok(await brain._say(city, brain.LEDGER, "hold x") is False and city.say_calls == 1, "ledger must not retry")
    del real_sleep


async def case_timer():
    reset()
    calls = []

    async def fake_handle(role, city, message, chat):
        calls.append(message.msg_id)
        await brain._say(city, message.sender, f"risposta {message.msg_id}")

    real = brain.handle
    brain.handle = fake_handle
    brain.role_of = lambda: "gp"
    try:
        # a waiting citizen (90 s) and a citizen whose last line from us was the crash note (SI-25)
        rows = [msg("citizen:a", "N-a", "vorrei una visita", 90),
                msg("citizen:b", "N-b", "vorrei prenotare", 120),
                msg(ME, "N-b", brain._CRASH_NOTE, 100, to="citizen:b")]
        city = Fake(rows)
        brain._SWEEP_CITY[:] = [city]
        await brain._sweep_once()
        ok(len(calls) == 2, f"timer sweep picked {len(calls)} cases, want 2")
        ok(city.inbound is None, "timer sweep must not touch the live city.inbound")
        await brain._sweep_once()
        ok(len(calls) == 2, "same case swept twice")
        # the guard also holds when the case was not settled: a new waiting message of the same
        # citizen within 5 min gets nothing from the timer
        city.rows.append(msg("citizen:a", "N-a2", "ancora io", 60))
        await brain._sweep_once()
        ok(len(calls) == 2, "citizen a spammed within 5 min")
        for k in list(brain._SWEEP_TOLD):
            brain._SWEEP_TOLD[k] -= 301.0
        await brain._sweep_once()
        ok(len(calls) == 3, "after 5 min the citizen is rescued again")
        # lazy start: one task per loop, restarted never twice
        brain._SWEEP_TASK.clear()
        brain._start_sweeper(city)
        first = brain._SWEEP_TASK[0]
        brain._start_sweeper(city)
        ok(brain._SWEEP_TASK[0] is first and not first.done(), "sweeper started twice")
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        # a broken city never crashes the loop body
        class Broken(Fake):
            async def history(self, *a, **k):
                raise RuntimeError("down")
        brain._SWEEP_CITY[:] = [Broken([])]
        brain._RESCUE_LAST.clear()
        await brain._sweep_once()
    finally:
        brain.handle = real


async def main():
    await case_hold_hang()
    await case_timeout()
    await case_retry()
    await case_timer()
    print(f"PASS never_silent_all: {checks[0]} checks")


asyncio.run(main())
