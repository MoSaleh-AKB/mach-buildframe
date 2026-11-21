"""Live N-2026-11-02-8909040002720008: the desk sent its holding line 40 s after the need.request,
then a re-pushed turn of the SAME message waited out the need lock (the first turn was still on
the Ledger) and said "Sto lavorando alla sua richiesta. Messaggio 1 ricevuto: mi dica se vuole
qualcosa di diverso…", which only invites another citizen turn. A message that already got a
holding line within _HELD_QUIET (5 min) gets no second line, also after a restart (history, not
memory). A NEW citizen message, or a holding line older than that, still gets its line.
Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_desk_no_second_hold.py [asst/_shared/agent.py]"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
BC, CZ = "agent:y6hxsd766sys", "citizen:W01QQQ-04-0008-000272"
N = [0]


def load():
    N[0] += 1
    spec = importlib.util.spec_from_file_location(f"brainh2_{N[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "booking_centre"
    if hasattr(b, "_LLM_NET"):
        b._LLM_NET = False
    b._RESCUE_WARMUP = 1e9
    b._CITIZEN_TURN = 3.0  # the need-lock wait times out after 1 s
    return b


def msg(sender, to, text, need, ago=0.0):
    N[0] += 1
    at = datetime.now(timezone.utc) - timedelta(seconds=ago)
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"h2-{N[0]}", need_id=need,
                   body={"text": text}, sent_at=at.isoformat())


class City:
    agent_id, address = BC, ""

    def __init__(self, rows, inbound):
        self.rows, self.out, self.inbound = list(rows), [], inbound

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


async def repushed(hold_ago, new_message=False):
    """A fresh process (restart: _HELD empty); the first turn of the need still holds the lock."""
    b = load()
    need = f"N-h2-{N[0]}"
    req = msg(CZ, BC, "need.request lang=it prescription_number=RX-H2 service_type=hospital.physiatry", need, ago=hold_ago + 40)
    hold = msg(BC, CZ, b._HOLD_NOTE + b._HOLD_TAG, need, ago=hold_ago)
    rows = [req, hold]
    current = req
    if new_message:
        current = msg(CZ, BC, "Scusi, preferirei di mattina.", need, ago=1)
        rows.append(current)
    city = City(rows, current)
    lock = b._need_lock(need)
    await lock.acquire()
    try:
        await asyncio.wait_for(b.main(SimpleNamespace(summary="x", is_emergency=False), city), 15)
    finally:
        lock.release()
    return [t for to, t in city.out if to == CZ]


async def main():
    fails = []
    told = await repushed(200)
    if told:
        fails.append(f"re-pushed turn, holding line 200 s ago: got {[t[:60] for t in told]}, want no second line")
    told = await repushed(600)
    if not told:
        fails.append("holding line 10 min ago: the citizen must hear where the case stands")
    told = await repushed(200, new_message=True)
    if not told:
        fails.append("a NEW citizen message after the holding line got no answer")
    for f in fails:
        print("FAIL", f)
    print(("OK" if not fails else f"FAIL {len(fails)}") + " test_desk_no_second_hold")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


asyncio.run(main())
