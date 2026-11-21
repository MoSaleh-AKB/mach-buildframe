"""Review #17: the desk offer pass never waits for the weather network. _weather_cached() reads
the cache and warms it behind the call; the offer pass (_advance_pass) does not await _weather().

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_wx_cache_only.py
"""
import asyncio
import importlib.util
import inspect
import os
import time

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("brain_wx", os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

FETCHES = [0]


def slow_fetch():
    FETCHES[0] += 1
    time.sleep(1.0)
    return {"2026-11-22T09": 3.0}


b._wx_fetch = slow_fetch


async def run() -> None:
    b._WX.update(at=0.0, ttl=0.0, data={})
    t0 = time.monotonic()
    first = b._weather_cached()
    took = time.monotonic() - t0
    assert took < 0.1, f"cache read waited {took:.2f}s"
    assert first == {}, first
    assert [t for t in (b._weather_cached() for _ in range(5))] and FETCHES[0] <= 1
    await asyncio.sleep(1.3)
    assert b._WX["data"] == {"2026-11-22T09": 3.0}, b._WX
    assert b._weather_cached() == {"2026-11-22T09": 3.0}
    # near expiry: still served at once, one refresh behind it
    b._WX.update(at=time.monotonic() - 0.8 * 1800.0)
    t0 = time.monotonic()
    got = b._weather_cached()
    assert time.monotonic() - t0 < 0.1 and got, got
    await asyncio.sleep(1.3)
    assert FETCHES[0] == 2, FETCHES
    assert b._weather_cached()
    assert "await _weather(" not in inspect.getsource(b._advance_pass), "offer pass awaits the network"


asyncio.run(run())
print("OK wx_cache_only")
