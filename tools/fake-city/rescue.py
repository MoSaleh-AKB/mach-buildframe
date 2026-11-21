"""Rescue sweep: citizens left waiting on other cases get answered (never lose an "in corso").

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/rescue.py asst/_shared/agent.py
"""

import os
os.environ.setdefault("CITY_RESCUE_WARMUP", "0")  # scenarios start right after boot
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

ME = "agent:t07/gp"
NOW = datetime.now(timezone.utc)
seq = [0]


def at(minutes):
    return (NOW - timedelta(minutes=minutes)).isoformat()


def msg(sender, need, text, minutes, to=None, status=None):
    seq[0] += 1
    body = {"text": text}
    if status:
        body["status"] = status
    return Message(type="chat", sender=sender, recipient=to or (ME if sender != ME else "citizen:c"),
                   msg_id=f"m{seq[0]}", need_id=need, conversation_id=f"cv-{need}", body=body,
                   sent_at=at(minutes))


class Fake:
    def __init__(self, rows, refuse=(), slow=False, broken=False):
        self.agent_id = ME
        self.address = ""
        self.rows = rows
        self.inbound = None
        self.posts = []
        self.refuse = set(refuse)
        self.slow = slow
        self.broken = broken
        self.history_calls = 0

    async def say(self, to, text, *, status=None):
        need = self.inbound.need_id
        if need in self.refuse:
            raise CapError("NOT_FOUND", "relay send 404")
        self.posts.append((need, to, text))
        self.rows.append(Message(type="chat", sender=ME, recipient=to, msg_id=f"p{len(self.posts)}",
                                 need_id=need, body={"text": text}, sent_at=at(0)))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        self.history_calls += 1
        if need_id is None:
            if self.slow:
                await asyncio.sleep(5)
            if self.broken:
                raise CapError("UNAVAILABLE", "boom")
        rows = [r for r in self.rows if need_id is None or r.need_id == need_id]
        if self.inbound is not None and all(r.msg_id != self.inbound.msg_id for r in rows):
            rows.append(self.inbound)
        return rows


def reset():
    brain._RESCUE_LAST.clear()
    brain._RESCUED.clear()
    brain._RESCUE_REFUSED.clear()
    brain._REJECTED.clear()
    brain._CITIZEN_SENT.clear()
    brain._CITIZEN_RATE.clear()
    brain._DUPS.clear()
    brain._CANCELS_SENT.clear()


def world():
    c = "citizen:c"
    return [
        msg(c, "N-wait3", "ho mal di schiena, serve una visita", 3),
        msg(c, "N-wait12", "vorrei prenotare", 12),
        msg(c, "N-done", "ciao", 8), msg(ME, "N-done", "Buongiorno", 7),
        msg(c, "N-esc", "dolore al petto", 9),
        msg(ME, "N-esc", "escalate.emergency reason=red_flag", 9, to=c),
        msg(c, "N-old", "ci siete?", 55),
    ]


async def fake_handle(role, city, message, chat):
    await brain._say(city, message.sender, f"reply to {message.text}")


async def turn(city, current):
    city.inbound = current
    await brain.main(SimpleNamespace(summary="x", is_emergency=False), city)


async def main():
    brain.role_of = lambda: "gp"
    real_handle = brain.handle
    brain.handle = fake_handle

    # 1. two waiting cases rescued, current answered first, right need_id, once each.
    reset()
    cur = msg("citizen:c", "N-now", "buongiorno", 0)
    city = Fake(world())
    await turn(city, cur)
    assert city.posts[0][0] == "N-now", city.posts
    assert [n for n, _, _ in city.posts[1:]] == ["N-wait12", "N-wait3"], city.posts  # oldest first
    assert city.inbound is cur, "inbound restored"
    # 2. a second sweep sends nothing: throttled, and idempotent once the throttle is open.
    await turn(city, msg("citizen:c", "N-now", "ancora", 0))
    assert len(city.posts) == 4, city.posts
    brain._RESCUE_LAST.clear()
    await turn(city, msg("citizen:c", "N-now", "ancora due", 0))
    assert len(city.posts) == 5, city.posts
    # 3. an agent-sender turn triggers no sweep (conformance gate); the Ledger turn does.
    reset()
    city = Fake(world())
    await turn(city, msg("agent:t07/hospital", "N-ag", "need.request service=lab", 0))
    assert city.history_calls == 1 and not any(n in ("N-wait3", "N-wait12") for n, _, _ in city.posts)
    reset()
    city = Fake(world())
    await turn(city, msg(brain.LEDGER, "N-led", "prescription ok", 0))
    assert {"N-wait3", "N-wait12"} <= {n for n, _, _ in city.posts}
    # 4. timeouts and read errors are swallowed; the current reply is still out.
    for kw in ({"slow": True}, {"broken": True}):
        reset()
        brain._RESCUE_READ = 0.2
        city = Fake(world(), **kw)
        await turn(city, msg("citizen:c", "N-now", "ciao", 0))
        assert [n for n, _, _ in city.posts] == ["N-now"], city.posts
    brain._RESCUE_READ = 1.0
    # 5. a refused cross-need post: need cached and never retried, the other case still served.
    reset()
    city = Fake(world(), refuse={"N-wait12"})
    await turn(city, msg("citizen:c", "N-now", "ciao", 0))
    assert "N-wait12" in brain._RESCUE_REFUSED
    assert sorted(n for n, _, _ in city.posts) == ["N-now", "N-wait3"], city.posts
    brain._RESCUE_LAST.clear()
    await turn(city, msg("citizen:c", "N-now", "ciao2", 0))
    assert all(n != "N-wait12" for n, _, _ in city.posts)
    # 6. at most 3 cases per sweep (release-2: a citizen turn is never delayed more than ~3 s).
    reset()
    city = Fake([msg("citizen:c", f"N-m{i}", "aiuto", 2 + i) for i in range(8)])
    await turn(city, msg("citizen:c", "N-now", "ciao", 0))
    assert len(city.posts) == 1 + 3, len(city.posts)
    # 6b. one deadline for the whole sweep: three waiting confirms on a hung handle share one
    #     confirm budget (each used to get the full budget, holding the citizen's lock 3x as long).
    reset()
    budget = brain._RESCUE_CONFIRM_BUDGET
    brain._RESCUE_CONFIRM_BUDGET = 0.6

    async def hung(role, city, message, chat):
        await asyncio.sleep(30)

    brain.handle = hung
    city = Fake([msg("citizen:c", f"N-c{i}", "confirm slot_id=S1", 2 + i) for i in range(3)])
    t0 = time.monotonic()
    await brain._rescue(city, "gp", msg("citizen:c", "N-now", "ciao", 0))
    took = time.monotonic() - t0
    assert took < 0.9, f"sweep took {took:.2f}s for a 0.6 s budget"
    brain._RESCUE_CONFIRM_BUDGET = budget
    brain.handle = fake_handle
    # 6c. a case whose own turn holds its need lock is skipped, never handled twice at once.
    reset()
    city = Fake([msg("citizen:c", "N-busy", "aiuto", 3), msg("citizen:c", "N-free", "aiuto", 4)])
    busy = brain._need_lock("N-busy")
    await busy.acquire()
    await turn(city, msg("citizen:c", "N-now", "ciao", 0))
    busy.release()
    assert sorted(n for n, _, _ in city.posts) == ["N-free", "N-now"], city.posts
    # 7. the citizen rewrites on the same need: answered at once from the full history.
    brain.handle = real_handle
    reset()
    c = "citizen:c"
    city = Fake([msg(c, "N-rw", "ho mal di schiena", 16)])
    await turn(city, msg(c, "N-rw", "ho mal di schiena", 0))
    mine = [p for p in city.posts if p[0] == "N-rw"]
    assert any(p[1] == c and 'prescription' in p[2] for p in mine), city.posts
    print("OK rescue")


asyncio.run(main())
