"""MUST 1 (21 Nov 13:10): the rescue deep read must never raise city_sdk.http._HISTORY_PAGES.

Every other mailbox read in the process reads that global, so a global 20-page cap during the deep
read (up to 45 s every 10 min) made the rescue read (5 s) and the duplicate-prescription scan (2 s)
time out. A real SDK HttpRelay on a mock relay (200 rows per page, endless cursor, 50 ms per page):
  (a) the deep read returns 20 pages (4000 rows);
  (b) a normal history() call running at the same time still gets 5 pages (1000 rows);
  (c) the SDK global stays 5 during and after the deep read.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_deep_read_no_global.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace

import httpx

os.environ["CITY_RESCUE_WARMUP"] = "0"
import city_sdk.http as sdk_http
from city.testkit import ephemeral

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

FAILS = []


def check(name, ok, detail):
    print(("PASS " if ok else "FAIL ") + name + ": " + detail)
    if not ok:
        FAILS.append(name)


async def handler(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(0.05)
    body = __import__("json").loads(request.content)
    cursor = (body.get("body") or {}).get("cursor") or "0"
    page = int(cursor)
    rows = [
        {"msg_id": f"m{page}-{i}", "type": "chat", "from": "citizen:x", "to": "agent:me", "need_id": f"N-{page}-{i}",
         "sent_at": "2026-11-21T10:00:00+00:00", "body": {"text": "ciao"}}
        for i in range(200)
    ]
    return httpx.Response(200, json={"messages": rows, "next_cursor": str(page + 1)})


async def main():
    key = ephemeral()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://relay")
    relay = sdk_http.HttpRelay(agent_id="agent:me", private=key.private, client=client, grant_body={"grant": {"grant_id": "g1"}})
    city = SimpleNamespace(_history=relay)
    seen = []

    async def watch():
        for _ in range(200):
            seen.append(sdk_http._HISTORY_PAGES)
            await asyncio.sleep(0.01)

    deep_task = asyncio.create_task(brain._deep_history(city, 20))
    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0.1)
    normal = await relay.history(peer=None, need_id=None)
    deep = await deep_task
    await watcher
    check("(a) deep read pages", deep is not None and len(deep) == 4000, f"{0 if deep is None else len(deep)} rows (want 4000)")
    check("(b) concurrent normal read", len(normal) == 1000, f"{len(normal)} rows (want 1000 = 5 pages)")
    check("(c) SDK global untouched", set(seen) == {5} and sdk_http._HISTORY_PAGES == 5, f"seen {sorted(set(seen))}, now {sdk_http._HISTORY_PAGES}")
    await client.aclose()
    print("ALL OK" if not FAILS else f"FAILED {len(FAILS)}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
