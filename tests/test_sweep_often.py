"""Sweep often: the rescue sweep runs after EVERY inbound message (agents and city:* too), every 15 s,
5 picks, and never ahead of the current message's own reply or inside its need lock.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_sweep_often.py asst/_shared/agent.py
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", os.path.abspath(path))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, PARTNER = "agent:t07/booking_centre", "agent:t07/hospital", "agent:t07/asst_partner"
SEQ = [0]
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
RX = "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=B state=issued"


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"s{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


class City:
    def __init__(self, me, rows):
        self.agent_id, self.address, self.rows, self.inbound = me, "", list(rows), None
        self.out = []  # (monotonic, to, need, current need lock held?)

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        if to != LEDGER:
            lock = brain._LOCKS.get(self.cur_need)
            self.out.append((time.monotonic(), to, need, bool(lock and lock.locked())))
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            body = {"agenda": f"agenda agenda_now={BASE.isoformat()}", "world": "slots page=1/1"}.get(text.split()[0])
            if body:
                self.rows.append(msg(LEDGER, self.agent_id, body, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


class View:
    """The turn's City view. Like the SDK City, ``say`` reads ``self.inbound``: the rescue sweep
    runs on a copy of the view and swaps its inbound, so posts carry that case's need_id."""

    def __init__(self, city, row):
        self._c, self.inbound, self.agent_id, self.address = city, row, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        getattr(brain, name).clear()
    for name in ("_REFUSAL_AT", "_PENDING_SEARCH", "_RESCUE_TRIED"):
        if hasattr(brain, name):
            getattr(brain, name).clear()
    brain._RESCUE_LAST[:] = []


def stuck(city, need, cz, age=120):
    city.rows += [msg(cz, BC, "Vorrei una visita.", need, ago=age), msg(LEDGER, BC, RX, need, ago=age + 5)]


async def turn(city, row):
    city.rows.append(row)
    city.inbound = row
    city.cur_need = row.need_id or row.sender
    view = View(city, row)
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)
    # Agent, Ledger and city turns run the sweep behind the turn (custody.ack fix): wait for it here.
    bg = list(getattr(brain, "_SWEEP_BG", ()))
    if bg:
        await asyncio.wait_for(asyncio.gather(*bg, return_exceptions=True), 40)


async def main():
    oks = []

    def verdict(label, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + label + (f" {detail}" if detail else ""))
        oks.append(ok)

    verdict("constants: 15 s, 5 picks", brain._RESCUE_EVERY == 15.0 and brain._RESCUE_MAX == 5,
            f"({brain._RESCUE_EVERY}, {brain._RESCUE_MAX})")

    # 1. an agent-to-agent message (partner ASST) sweeps and answers a stuck citizen case
    reset()
    city = City(BC, [])
    stuck(city, "N-S1", "citizen:t07/s1")
    await turn(city, msg(PARTNER, BC, "ping", "N-AG"))
    got = [o for o in city.out if o[1] == "citizen:t07/s1"]
    verdict("1a agent message sweeps and answers the stuck citizen", len(got) >= 1, f"({len(got)} lines)")

    # 1b. a city:* message too
    reset()
    city = City(BC, [])
    stuck(city, "N-S1b", "citizen:t07/s1b")
    await turn(city, msg("city:clock", BC, "tick", "N-CK"))
    got = [o for o in city.out if o[1] == "citizen:t07/s1b"]
    verdict("1b city:* message sweeps and answers the stuck citizen", len(got) >= 1, f"({len(got)} lines)")

    # 2. two messages 20 s apart both sweep; one 5 s later does not
    reset()
    city = City(BC, [])
    stuck(city, "N-A", "citizen:t07/a")
    await turn(city, msg(PARTNER, BC, "ping1", "N-AG1"))
    a1 = [o for o in city.out if o[1] == "citizen:t07/a"]
    stuck(city, "N-B", "citizen:t07/b")
    brain._RESCUE_LAST[:] = [time.monotonic() - 5]
    await turn(city, msg(PARTNER, BC, "ping2", "N-AG2"))
    early = [o for o in city.out if o[1] == "citizen:t07/b"]
    brain._RESCUE_LAST[:] = [time.monotonic() - 20]
    await turn(city, msg(PARTNER, BC, "ping3", "N-AG3"))
    b = [o for o in city.out if o[1] == "citizen:t07/b"]
    verdict("2 sweep at +0 s and +20 s, none at +5 s", len(a1) >= 1 and not early and len(b) >= 1,
            f"(a={len(a1)}, b@5s={len(early)}, b@20s={len(b)})")

    # 3. up to 5 picks per sweep, oldest first
    reset()
    city = City(BC, [])
    for i in range(7):
        stuck(city, f"N-M{i}", f"citizen:t07/m{i}", age=600 - i * 10)
    rows = list(city.rows)
    picks = brain._rescue_picks(rows, BC, msg(PARTNER, BC, "x", "N-X"), datetime.now(timezone.utc))
    verdict("3 five picks, oldest first", [p[1].need_id for p in picks] == [f"N-M{i}" for i in range(5)],
            f"({[p[1].need_id for p in picks]})")

    # 4. the current reply goes out first and outside the need lock; the sweep adds nothing before it
    reset()
    brain._RESCUE_LAST[:] = [time.monotonic()]
    warm = City(BC, [])
    await turn(warm, msg("citizen:t07/w", BC, "Vorrei una visita.", "N-W"))  # warm-up, no sweep
    reset()
    city = City(BC, [])
    stuck(city, "N-OLD", "citizen:t07/old")
    cur = msg("citizen:t07/cur", BC, "Vorrei una visita.", "N-CUR")
    city.rows.append(msg(LEDGER, BC, RX, "N-CUR", ago=1))
    t0 = time.monotonic()
    await turn(city, cur)
    t_end = time.monotonic() - t0
    mine = [o for o in city.out if o[1] == "citizen:t07/cur"]
    old = [o for o in city.out if o[1] == "citizen:t07/old"]
    first_old = min((o[0] for o in old), default=None)
    ok = bool(mine) and bool(old) and mine[0][0] <= first_old and not old[0][3]
    verdict("4a own reply first, sweep outside the need lock", ok,
            f"(mine={len(mine)}, old={len(old)}, lock held during sweep={old[0][3] if old else None})")
    reset()
    brain._RESCUE_LAST[:] = [time.monotonic()]  # sweep throttled: same turn without a sweep
    city2 = City(BC, [])
    stuck(city2, "N-OLD", "citizen:t07/old")
    city2.rows.append(msg(LEDGER, BC, RX, "N-CUR", ago=1))
    s0 = time.monotonic()
    await turn(city2, msg("citizen:t07/cur", BC, "Vorrei una visita.", "N-CUR"))
    mine2 = [o for o in city2.out if o[1] == "citizen:t07/cur"]
    delay_with = mine[0][0] - t0
    delay_without = mine2[0][0] - s0
    verdict("4b reply time not increased by the sweep", delay_with <= delay_without + 0.5,
            f"(with {delay_with:.3f}s, without {delay_without:.3f}s, turn {t_end:.3f}s)")

    print("ALL OK" if all(oks) else "SOME FAILED")
    raise SystemExit(0 if all(oks) else 1)


asyncio.run(main())
