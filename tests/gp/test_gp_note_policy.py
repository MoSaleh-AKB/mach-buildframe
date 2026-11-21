"""Q2: gp and the consultorio learn a rule change pushed by the city (policy.update), as the shared
brain does. Before, they learned a rule only from a ``rules`` answer inside their own case. An
agent or a citizen can never change the rules.

Run: <city python> -I tests/gp/test_gp_note_policy.py [asst/_gp/agent.py]   (exit 0 = pass)
"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
N = [0]
FAILS = []


def load(role):
    N[0] += 1
    os.environ["CITY_ROLE"] = role
    spec = importlib.util.spec_from_file_location(f"brainq2_{N[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: role
    if hasattr(b, "_LLM_NET"):
        b._LLM_NET = False
    return b


class City:
    address = ""

    def __init__(self, me, inbound):
        self.agent_id, self.inbound, self.rows, self.out = me, inbound, [inbound], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


async def push(role, sender, text):
    b = load(role)
    me = f"agent:t07/{role}"
    N[0] += 1
    m = Message(type="chat", sender=sender, recipient=me, msg_id=f"q2-{N[0]}", need_id=f"N-q2-{N[0]}",
                body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())
    try:
        await asyncio.wait_for(b.main(SimpleNamespace(summary="x", is_emergency=False), City(me, m)), 10)
    except Exception as exc:  # a protocol line may end the turn early; the rule must be learned first
        if type(exc).__name__ not in ("OutOfScope", "TimeoutError"):
            raise
    return b._RULES


async def main():
    p1 = "policy.update rule_id=P1 version=2 validity_days=1"
    for role in ("gp", "family_counseling"):
        rules = await push(role, LEDGER, p1)
        if "validity_days=1" not in rules.get("P1", ""):
            FAILS.append(f"{role}: city policy.update not learned, _RULES={rules}")
        rules = await push(role, "agent:t99/booking_centre", p1)
        if rules.get("P1"):
            FAILS.append(f"{role}: an agent changed the rules: {rules}")
    for f in FAILS:
        print("FAIL", f)
    print(("OK" if not FAILS else f"FAIL {len(FAILS)}") + " test_gp_note_policy")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
