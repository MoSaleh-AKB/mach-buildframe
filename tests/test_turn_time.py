"""Live 21 Nov (DEPLOY-CHECK f #2): the platform serves ONE turn at a time per agent; our desk turns
took ~18 s each under a slow Ledger, so a wave of citizens queued and the first reply came ~400 s late.

Harness: N citizens write to the desk in the same second; the Ledger answers every request after
LEDGER s; the agent's turns run one at a time (a queue, like the platform); a Ledger push opens a
turn too (queued behind the citizens). Background tasks the brain starts keep running.

Report per-turn wall time (p50 / p95), first line to each citizen (wait in the queue included) and
when the offers reach each citizen. Pass: turn p95 <= MAX_TURN s, every citizen gets offers, no
citizen gets the same line twice.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_turn_time.py [brain.py]
     env: BURST=20 LEDGER=10 MAX_TURN=4 SETTLE=120
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
ROLE = os.environ.get("ROLE", "booking_centre")  # gp: the gp brain asst/_gp/agent.py
path = next((a for a in sys.argv[1:] if a.endswith(".py")),
            os.path.join(HERE, "..", "asst", "_gp" if ROLE == "gp" else "_shared", "agent.py"))
os.environ["CITY_ROLE"] = ROLE
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: ROLE
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False  # no network in tests

BURST = int(os.environ.get("BURST", "20"))
LEDGER_S = float(os.environ.get("LEDGER", "10"))
MAX_TURN = float(os.environ.get("MAX_TURN", "4"))
SETTLE = float(os.environ.get("SETTLE", "120"))
BC = "agent:jmqwr12vy2j6" if ROLE == "gp" else "agent:t07/booking_centre"
HO = "agent:t07/hospital"
DONE_WORD = "prescription_number=" if ROLE == "gp" else "offer_id=of-"
NOW = datetime.now(timezone.utc)
SEQ = [0]


def agenda_now():
    return (NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()


def slots_text():
    rows = []
    for d in range(3):
        base = (NOW + timedelta(days=2 + d)).replace(hour=9, minute=0, second=0, microsecond=0)
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[base.weekday()]
        for i in range(8):
            at = (base + timedelta(minutes=45 * i)).isoformat()
            rows.append(f"slot_id=S{d}{i} doctor_id=asst-centro-h{1 + i % 3} at={at} day={day} facility={HO} "
                        f"procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"q{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class Platform:
    """One agent, one turn at a time; history shared; Ledger answers after LEDGER_S."""

    def __init__(self):
        self.rows: list[Message] = []
        self.out: list[tuple[float, str, str, str]] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.turns: list[tuple[str, float]] = []
        self.t0 = time.monotonic()
        self.tasks: list[asyncio.Task] = []
        self.arrived: dict[str, float] = {}

    def now(self):
        return time.monotonic() - self.t0

    async def say(self, to, text, need):
        if to == LEDGER:
            self.tasks.append(asyncio.create_task(self._ledger(text, need)))
        else:
            self.out.append((self.now(), to, need, text))
        self.rows.append(msg(BC, to, text, need))
        return "ok"

    async def _ledger(self, text, need):
        await asyncio.sleep(LEDGER_S)
        head = text.split()[0]
        if head == "prescribe":
            f = dict(w.split("=", 1) for w in text.split() if "=" in w)
            body = (f"prescribed prescription_number={f.get('prescription_number', '')} patient={f.get('patient', '')} "
                    f"procedure={f.get('procedure', '')} priority={f.get('priority', '')}")
        elif head == "prescription":
            number = text.split("prescription_number=")[-1].split()[0]
            body = f"prescription prescription_number={number} procedure=VIS-FIS priority=D state=issued"
        elif head == "agenda":
            body = f"agenda agenda_now={agenda_now()}"
        elif head == "world":
            body = slots_text()
        elif head == "rules":
            body = "rules none"
        else:
            return
        row = msg(LEDGER, BC, body, need)
        self.rows.append(row)
        await self.queue.put(row)  # a Ledger push opens a turn too, behind the others

    async def history(self, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def serve(self):
        while True:
            row = await self.queue.get()
            view = View(self, row)
            start = time.monotonic()
            try:
                await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ != "OutOfScope":
                    print("   turn raised", type(exc).__name__, exc)
            self.turns.append((row.sender, time.monotonic() - start))
            if os.environ.get("SHOW_TURNS") and self.turns[-1][1] > 1.0:
                print(f"   turn {self.turns[-1][1]:5.1f}s at {self.now():6.1f} {row.sender[-10:]} {row.text[:50]!r}")


class View:
    def __init__(self, p, inbound):
        self._p, self.inbound, self.agent_id, self.address = p, inbound, BC, ""

    async def say(self, to, text, *, status=None):
        return await self._p.say(to, text, self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._p.history(need_id)

    async def find(self, query):
        return []


def pct(values, q):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


async def main():
    p = Platform()
    server = asyncio.create_task(p.serve())
    cits = [f"citizen:t07/q{i}" for i in range(BURST)]
    for i, cz in enumerate(cits):
        need = f"N-Q{i}"
        text = ("Buongiorno dottore, ho mal di schiena da due settimane, vorrei una visita." if ROLE == "gp" else
                f"Buongiorno, vorrei prenotare la visita. need.request prescription_number=RX-Q{i}")
        row = msg(cz, BC, text, need)
        p.rows.append(row)
        p.arrived[cz] = p.now()
        await p.queue.put(row)
    t_end = time.monotonic() + SETTLE
    while time.monotonic() < t_end:
        done = all(any(to == cz and DONE_WORD in x for _, to, _, x in p.out) for cz in cits)
        if done and p.queue.empty():
            break
        await asyncio.sleep(0.5)
    server.cancel()
    for t in p.tasks:
        t.cancel()
    cit_turns = [d for s, d in p.turns if s.startswith("citizen:")]
    all_turns = [d for _, d in p.turns]
    first = []
    offers = []
    dup = 0
    for cz in cits:
        lines = [(t, x) for t, to, _, x in p.out if to == cz]
        if lines:
            first.append(lines[0][0] - p.arrived[cz])
        got = [t for t, x in lines if DONE_WORD in x]
        if got:
            offers.append(got[0] - p.arrived[cz])
        texts = [x for _, x in lines]
        dup += len(texts) - len(set(texts))
    print(f"brain={os.path.basename(os.path.dirname(os.path.dirname(path))) or path} burst={BURST} ledger={LEDGER_S}s")
    print(f"turns: n={len(all_turns)} citizen p50={pct(cit_turns, .5):.1f}s p95={pct(cit_turns, .95):.1f}s "
          f"all p50={pct(all_turns, .5):.1f}s p95={pct(all_turns, .95):.1f}s max={max(all_turns or [0]):.1f}s")
    print(f"first line to citizen: p50={pct(first, .5):.1f}s p95={pct(first, .95):.1f}s ({len(first)}/{BURST} got one)")
    print(f"offers to citizen:     p50={pct(offers, .5):.1f}s p95={pct(offers, .95):.1f}s ({len(offers)}/{BURST} got them)")
    print(f"duplicate lines: {dup}")
    ok = pct(all_turns, .95) <= MAX_TURN and len(offers) == BURST and dup == 0 and len(first) == BURST
    print("OK test_turn_time" if ok else "FAILED test_turn_time")
    sys.exit(0 if ok else 1)


asyncio.run(main())
