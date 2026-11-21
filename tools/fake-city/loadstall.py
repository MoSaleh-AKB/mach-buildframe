"""Measure event-loop stalls of the booking brain under backlog traffic (conformance on a busy pod).

The city's suite needs GET /health < 0.3 s and a renew within 5 s while the new pod may already get
live traffic. One synchronous block longer than 0.3 s in a turn fails it. This runs N concurrent
booking citizen turns (plus the suite's agent need) on one loop with a 5 ms ticker and prints the
worst tick lag and the conformance need's time. Fake city: no network, 20 ms per relay call.

Run: <city python> -I tools/fake-city/loadstall.py [asst/_shared/agent.py] [N=40] [MAILBOX=9900] [WARMUP=300]
Exit 1 when the worst stall is >= 0.25 s.
"""

import asyncio
import importlib.util
import os
import sys
import time

path = sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 40
MAILBOX = int(sys.argv[3]) if len(sys.argv) > 3 else 9900
os.environ["CITY_RESCUE_WARMUP"] = sys.argv[4] if len(sys.argv) > 4 else "300"
os.environ["CITY_ROLE"] = "booking_centre"

from city_sdk.city import LEDGER, CapError, Message, Need  # noqa: E402

spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC = "agent:t07/booking_centre"
LAG = 0.02
SLOW = float(os.environ.get('SLOW', '0'))  # Ledger/find/history round trip
SAYS = []
TRACE = []
HIST = float(os.environ.get("HIST_LAG", "3.0"))  # whole-mailbox read on a 9900 message mailbox
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{SEQ[0]}", need_id=need, body={"text": text}, recipient=to)


SLOTS = "slots page=1/1\n" + "\n".join(
    f"slot_id=S{i} doctor_id=D-h1 at=2026-11-2{3 + i % 3}T0{8 + i % 8}:00:00+00:00 day=mon "
    f"facility=agent:t07/hospital procedure=VIS-FIS free=1"
    for i in range(1, 25)
)


def case(n, citizen):
    need = f"N-{n:05d}"
    rows = [msg(citizen, BC, "book please prescription_number=RX1 lang=it", need)]
    rows.append(msg(BC, LEDGER, "prescription prescription_number=RX1", need))
    rows.append(msg(BC, LEDGER, "agenda", need))
    rows.append(msg(BC, LEDGER, "world procedure=VIS-FIS page=1", need))
    rows.append(msg(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued", need))
    rows.append(msg(LEDGER, BC, "agenda agenda_now=2026-11-23T08:00:00+00:00", need))
    rows.append(msg(LEDGER, BC, SLOTS, need))
    rows.append(msg(BC, citizen, "Offerta: slot_id=S1 giorno lunedi", need))
    return rows


MAIL = []
by_need = {}
for n in range(MAILBOX // 8 + 1):
    rows = case(n, f"citizen:t07/c{n}")
    by_need[rows[0].need_id] = rows
    MAIL.extend(rows)


class Fake:
    def __init__(self, inbound, rows):
        self.agent_id = BC
        self.inbound = inbound
        self.rows = rows
        self.address = ""
        self.out = []

    async def say(self, to, text, *, status=None):
        await asyncio.sleep(LAG)
        SAYS.append(to)
        TRACE.append(("say", to[:30] + ' ' + text[:40].replace(chr(10), ' ')))
        self.out.append((to, text))
        return "x"

    async def find(self, query):
        TRACE.append(("find", query[:40]))
        await asyncio.sleep(max(LAG, SLOW))
        return []

    async def history(self, peer=None, *, need_id=None):
        TRACE.append(("history", need_id or peer or "ALL"))
        await asyncio.sleep(max(LAG, SLOW) if need_id or peer else LAG)
        if need_id:
            return list(by_need.get(need_id, [self.inbound]))
        await asyncio.sleep(HIST)
        return list(MAIL)


async def main():
    worst = [0.0]
    stop = [False]

    async def tick():
        last = time.perf_counter()
        while not stop[0]:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            worst[0] = max(worst[0], now - last - 0.005)
            last = now

    ticker = asyncio.create_task(tick())
    turns = []
    for n in range(N):
        rows = by_need[f"N-{n:05d}"]
        inbound = msg(rows[0].sender, BC, "confermo la prima", rows[0].need_id)
        chat = rows + [inbound]
        by_need[rows[0].need_id] = chat
        city = Fake(inbound, chat)
        turns.append(asyncio.create_task(brain.main(Need(service="booking", summary=inbound.text, message=inbound), city)))
    conf = Message(type="need.request", sender="agent:t05/osp-x", msg_id="conf-1", need_id="N-conf",
                   body={"service": "lab", "summary": "visit", "district": 8}, recipient=BC)
    await asyncio.sleep(0.05)
    t0 = time.perf_counter()
    cc = Fake(conf, [conf])
    try:
        await brain.main(Need(service="lab", district=8, summary="visit", message=conf), cc)
    except Exception as exc:
        print("conform turn raised", type(exc).__name__, exc)
    conf_took = time.perf_counter() - t0
    if os.environ.get('TRACE'):
        print('suite-turn calls:', [t for t in TRACE if True][-12:])
    res = await asyncio.gather(*turns, return_exceptions=True)
    stop[0] = True
    await ticker
    bad = [r for r in res if isinstance(r, Exception)]
    print(f"N={N} mailbox={len(MAIL)} conform-turn={conf_took:.2f}s worst-stall={worst[0]:.3f}s exceptions={len(bad)}")
    return 1 if worst[0] >= 0.25 else 0


sys.exit(asyncio.run(main()))
