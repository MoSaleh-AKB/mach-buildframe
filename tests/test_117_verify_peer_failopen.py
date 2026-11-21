"""Issue 117 (R32): _verify_peer must not cache a fail-open verdict (Index timeout) for 600 s, and
_fields_named must read structured ``fields`` from a mappingproxy body (the SDK's real body type).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_117_verify_peer_failopen.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import inspect
import os
import sys
import time
import types

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._VERIFY_BUDGET = 0.2

STRANGER = "agent:zz117-stranger"
OTHER = types.SimpleNamespace(agent_id="agent:zz117-listed", role="hospital")
FAILS = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


class City:
    agent_id = "agent:zz117-me"

    def __init__(self):
        self.calls = 0
        self.slow = True

    async def find(self, query):
        self.calls += 1
        if self.slow:
            await asyncio.sleep(5)
        return [OTHER]

    async def history(self, peer=None):
        return []


async def verify_case():
    brain._VERIFIED.clear()
    city = City()
    strict = "strict" in inspect.signature(brain._verify_peer).parameters
    kw = {"strict": True} if strict else {}
    first = await brain._verify_peer(city, [], STRANGER, "", **kw)
    check("Index timeout fails open (as before)", first is True)
    check("fail-open verdict is not cached", not any(STRANGER in k for k in brain._VERIFIED))
    calls = city.calls
    city.slow = False  # the Index answers now, without the stranger
    second = await brain._verify_peer(city, [], STRANGER, "", **kw)
    check("next message asks the Index again", city.calls > calls)
    if strict:
        check("strict change from an unlisted sender is refused once the Index answers", second is False)


def fields_case():
    body = types.MappingProxyType({"fields": ["phone", "Address"]})
    m = Message(type="data.request", sender=STRANGER, recipient=City.agent_id, msg_id="m1", need_id="n1",
                body=body, sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    check("body is a mappingproxy", isinstance(m.body, types.MappingProxyType))
    check("_fields_named reads structured fields from a mappingproxy", brain._fields_named(m) >= {"phone", "address"})


asyncio.run(verify_case())
fields_case()
print("FAILED: " + ", ".join(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
