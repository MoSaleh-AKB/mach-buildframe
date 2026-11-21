"""R24 (#109): a hospital check-in that waits out the need lock still gets a line.

Another turn of the same need holds the lock (a slow Ledger, a slow confirm). Before the fix a
check-in message that timed out on the lock returned with no line at all: the citizen at the
desk heard nothing. Now it gets _CHECKIN_WAIT (the 112 variant once the case was escalated),
also when the case history cannot be read. A non check-in message keeps its never_silent line.
Run: <city python> -I tests/test_109_checkin_lock_timeout.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import itertools
import sys
import time

from city_sdk.city import CapError, Message, Need

BRAIN = sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py"
LOCK_TURN = 3.0  # lock wait = _CITIZEN_TURN - 2 s: 1 s in the test (live 38 s), same code path
_ids = itertools.count(1)


def load(role):
    spec = importlib.util.spec_from_file_location(f"brain109_{next(_ids)}", BRAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # one fresh module per case: no memory carried over
    mod.role_of = lambda: role
    mod._CITIZEN_TURN = LOCK_TURN
    return mod


def msg(sender, to, text, need):
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{next(_ids)}", need_id=need,
                   body={"text": text})


class Fake:
    def __init__(self, role, inbound, *, history_down=False, before=()):
        self.agent_id = f"agent:t109/{role}"
        self.address = ""
        self.inbound = inbound
        self.rows = [*before, inbound]
        self.citizen = []  # (seconds, text)
        self.t0 = time.monotonic()
        self.history_down = history_down

    async def say(self, to, text, *, status=None):
        self.rows.append(msg(self.agent_id, to, text, self.inbound.need_id))
        if to.startswith("citizen:"):
            self.citizen.append((round(time.monotonic() - self.t0, 2), text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        if self.history_down:
            raise CapError("UPSTREAM_TIMEOUT", "history unreachable")
        return list(self.rows)

    async def llm(self, messages, *, need_id=None):
        return {"content": ""}

    async def find(self, query):
        return []


async def run(name, text, *, escalated=False, **fault):
    brain = load("hospital")
    need = f"N-109-{next(_ids)}"
    citizen = "citizen:t109/rita"
    agent = "agent:t109/hospital"
    before = []
    if escalated:
        before = [msg(citizen, agent, "ho un dolore forte al petto", need),
                  msg(agent, citizen, brain._ESCALATE, need)]
    inbound = msg(citizen, agent, text, need)
    city = Fake("hospital", inbound, before=before, **fault)
    lock = brain._need_lock(need)
    await lock.acquire()  # a slow turn of the same need holds the lock for the whole test
    await asyncio.wait_for(brain.main(Need(summary=text, message=inbound), city), 30)
    return name, brain, city


async def main():
    fails = []
    cases = await asyncio.gather(
        run("checkin lock timeout", "checkin booking_id=BK-109"),
        run("checkin lock timeout history_down", "checkin booking_id=BK-110", history_down=True),
        run("checkin lock timeout after 112", "checkin booking_id=BK-111", escalated=True),
        run("other message lock timeout", "Buongiorno, a che ora è la mia visita?"),
    )
    for name, brain, city in cases:
        print(f"{name}: lines=" + " | ".join(f"{t}s {x[:60]!r}" for t, x in city.citizen))
        if not city.citizen:
            fails.append(f"{name}: citizen got no line")
            continue
        if len(city.citizen) > 1:
            fails.append(f"{name}: {len(city.citizen)} lines, want one")
    byname = {name: (brain, city) for name, brain, city in cases}
    for name in ("checkin lock timeout", "checkin lock timeout history_down"):
        brain, city = byname[name]
        if city.citizen and city.citizen[0][1] != brain._CHECKIN_WAIT:
            fails.append(f"{name}: line is not _CHECKIN_WAIT: {city.citizen[0][1][:60]!r}")
    brain, city = byname["checkin lock timeout after 112"]
    if city.citizen and city.citizen[0][1] != brain._CHECKIN_WAIT_RED:
        fails.append(f"after 112: line is not _CHECKIN_WAIT_RED: {city.citizen[0][1][:60]!r}")
    brain, city = byname["other message lock timeout"]
    if city.citizen and city.citizen[0][1] in (brain._CHECKIN_WAIT, brain._CHECKIN_WAIT_RED):
        fails.append("other message: got a check-in line")

    if fails:
        print("FAILED:", "; ".join(fails))
        sys.exit(1)
    print("ALL PASS test_109_checkin_lock_timeout")


asyncio.run(main())
