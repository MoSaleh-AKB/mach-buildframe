"""A dropped connection on the first history read must not leave the citizen silent (live gp 2.92.0,
21 Nov 02:55 and 03:02: "mal di schiena da diverse settimane", no reply).

The relay client raises httpx ConnectError / ReadError / RemoteProtocolError ("Server disconnected
without sending a response"), not a CapError and not a Timeout. It used to raise past the safety net
in main(): the harness acked the turn rejected (no re-push) and no line reached the citizen.

Run: <city python> -I tools/fake-city/test_read_drop.py <brain agent.py> <role> [role ...]
  asst/_gp/agent.py gp family_counseling
  asst/_shared/agent.py booking_centre hospital specialist_care gp family_counseling
Exit 0 = every role sends the citizen at least one line (gp/consultorio from _gp: the real answer).
"""

import asyncio
import importlib.util
import os
import sys

import httpx
from city_sdk.city import LEDGER, City, Message, Need, OutOfScope

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
ROLES = sys.argv[2:] or ["gp"]
GP_BRAIN = "_gp" in sys.argv[1]
TEXT = {
    "gp": "mal di schiena da diverse settimane",
    "family_counseling": "Buongiorno, vorrei prenotare una visita ginecologica al consultorio",
    "booking_centre": "Buongiorno, vorrei prenotare la visita, prescription_number=RX-T-1",
    "hospital": "Buongiorno, vorrei prenotare una visita in ospedale",
    "specialist_care": "Buongiorno, vorrei prenotare una visita specialistica",
}
ERRORS = {
    "RemoteProtocolError": lambda: httpx.RemoteProtocolError("Server disconnected without sending a response."),
    "ConnectError": lambda: httpx.ConnectError("All connection attempts failed"),
    "ReadError": lambda: httpx.ReadError(""),
}
FAILS: list[str] = []


class Relay:
    def __init__(self, role, inbound, error):
        self.me = f"agent:t07/{role}"
        self.rows = [inbound]
        self.error = error
        self.reads = 0
        self.citizen: list[str] = []

    async def post(self, *, to, message_type, body, need_id, conversation_id, traceparent):
        self.rows.append(Message(type="chat", sender=self.me, recipient=to, msg_id=f"o{len(self.rows)}",
                                 need_id=need_id, body=dict(body)))
        if to.startswith("citizen:"):
            self.citizen.append(body["text"])
        return f"o{len(self.rows)}"

    async def history(self, *, peer, need_id):
        self.reads += 1
        if self.reads == 1:
            raise self.error()  # only the first read of the turn drops
        return [r for r in self.rows if need_id is None or r.need_id == need_id]


class Index:
    async def search(self, query):
        return []


async def one(role, name, make):
    os.environ["CITY_ROLE"] = role
    m = Message(type="chat", sender=f"citizen:t/{role}-{name}", recipient=f"agent:t07/{role}",
                msg_id=f"rd-{role}-{name}", need_id=f"N-rd-{role}-{name}", body={"text": TEXT[role]})
    relay = Relay(role, m, make)
    city = City(index=Index(), messages=relay, history=relay, agent_id=relay.me, inbound=m)
    outcome = "processed"
    try:
        await asyncio.wait_for(brain.main(Need(summary=TEXT[role], message=m), city), 15.0)
    except OutOfScope:
        outcome = "out_of_scope"
    except Exception as exc:
        outcome = f"raised {type(exc).__name__}"
    lines = relay.citizen
    ok = bool(lines)
    if GP_BRAIN and role == "gp":
        ok = ok and any("prescription_number=" in t for t in lines)  # the real answer, not only a note
    print(("OK   " if ok else "FAIL ") + f"{role:17s} {name:19s} {outcome:28s} lines={len(lines)} "
          + (repr(lines[0].splitlines()[-1][:60]) if lines else "NO LINE"))
    if not ok:
        FAILS.append(f"{role} {name}")


async def main():
    for role in ROLES:
        for name, make in ERRORS.items():
            await one(role, name, make)
    print(f"{len(FAILS)} failed")
    return 1 if FAILS else 0


sys.exit(asyncio.run(main()))
