"""R14 (#99): at the consultorio, a check-in whose Ledger send fails still gets a line to the citizen.
Before, `_on_checkin` returned without a word when `_say(LEDGER, "checkin ...")` failed, so the
citizen at arrival was left unanswered.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/gp/test_fc_checkin_ledger_down.py asst/_gp/agent.py

Full turns through brain.main on a local fake city whose Ledger refuses (4xx) or times out (5xx)
every send.
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, CapError, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py"))
os.environ["CITY_ROLE"] = "family_counseling"
ME, CZ = "agent:t07/family_counseling", "citizen:t07/c1"
SEQ = [0]
OKS = []


def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "family_counseling"
    if hasattr(b, "_LLM_NET"):
        b._LLM_NET = False
    return b


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self, b, code):
        self.b, self.code, self.agent_id, self.address = b, code, ME, ""
        self.rows, self.out = [], []

    async def say(self, to, text, *, status=None, need=None):
        if to == LEDGER:
            raise CapError(self.code, "ledger down")
        self.out.append((to, text))
        self.rows.append(msg(ME, to, text, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        rows = [r for r in self.rows if need_id is None or r.need_id == need_id]
        return [r for r in rows if not peer or peer in (r.sender, r.recipient)]

    async def find(self, query):
        return []


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def check(label, ok, city):
    OKS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        for w, x in city.out:
            print(f"      {w[-26:]:>26} : {x[:140]!r}")


async def checkin_with_ledger(code, need):
    b = load()
    city = City(b, code)
    row = msg(CZ, ME, "checkin booking_id=bk-1", need)
    city.rows.append(row)
    await asyncio.wait_for(b.main(SimpleNamespace(summary="x", is_emergency=False), View(city, row)), 60)
    told = [x for w, x in city.out if w == CZ]
    check(f"Ledger {code}: the citizen gets a line", bool(told), city)
    check(f"Ledger {code}: no false 'check-in registered' line",
          not any(x.startswith("checkin booking_id=") for x in told), city)


async def main():
    await checkin_with_ledger("FORBIDDEN", "LD1")
    await checkin_with_ledger("UPSTREAM_TIMEOUT", "LD2")
    print("ALL OK" if all(OKS) else "SOME FAILED")
    raise SystemExit(0 if all(OKS) else 1)


asyncio.run(main())
