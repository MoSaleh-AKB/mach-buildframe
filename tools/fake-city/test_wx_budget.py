"""Hung Open-Meteo in the booking centre's agent turn: the weather wait stays inside the turn budget,
and a cancel of _weather() propagates (audit item 15).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/test_wx_budget.py asst/_shared/agent.py
"""

import asyncio
import importlib.util
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", path)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
b._wx_fetch = lambda: (time.sleep(3), {})[1]  # hung fetch
b._confirmed_slot = lambda *a: "S1"
b._visit_at = lambda *a: "2026-11-22T09:00"
b._ledger = lambda *a: {}
b._we = lambda *a, **k: True
b._say_lang = lambda c: "it"


async def _say(*a):
    return True


b._say = _say
CITY = types.SimpleNamespace(agent_id="agent:bc")


async def timed(left: float) -> float:
    b._WX.update(at=0.0, ttl=0.0)
    b._TURN_END.set(time.monotonic() + left)
    t = time.monotonic()
    await b._after_receipt(CITY, "agent:fac", [], "agent:cz", "B1", "L1")
    return time.monotonic() - t


async def main() -> None:
    took = await timed(0.4)
    assert took < 0.55, f"weather waited {took:.2f}s with 0.4 s of turn left"
    took = await timed(0.05)
    assert took < 0.1, f"weather waited {took:.2f}s with 0.05 s of turn left"
    b._WX.update(at=0.0, ttl=0.0)
    task = asyncio.create_task(b._weather())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled(), "_weather() swallowed CancelledError"
    print("OK wx_budget")


asyncio.run(main())
