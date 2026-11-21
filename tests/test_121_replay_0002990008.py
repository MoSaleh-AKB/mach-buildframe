"""Replay of live N-2026-11-02-8909040002990008 (L5, priority B, VIS-FIS): the desk answered only
"Nei tempi previsti per la sua priorità non ho trovato un posto libero adatto" + waiting list, while
the judge's answer_set held 468 valid slots (verdict.answer_set.size). The journey JSON keeps only
the set size, so the world is rebuilt from the live shape: agenda now Wed 2026-12-23 17:19 (the
desk's first line), world page 1 = that Wednesday's slots (past or under the lead), pages 2-3 =
Thursday and Friday slots inside the B window, each page answered 5 s later (live Ledger 8-20 s).
Expect at least one real slot offer inside the B window, never the waiting-list-only line.
Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_121_replay_0002990008.py [asst/_shared/agent.py]"""
import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain121r", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
b.role_of = lambda: "booking_centre"
if hasattr(b, "_LLM_NET"):
    b._LLM_NET = False
b._RESCUE_WARMUP = 1e9


async def _no_weather():
    return {}


b._weather = _no_weather
BC, CZ, FAC, NEED = "agent:y6hxsd766sys", "citizen:t07/c121p", "agent:r8yprrs0e8be", "N-2026-11-02-8909040002990008"
N = [0]


def msg(sender, to, text):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"p-{N[0]}", need_id=NEED, body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


def slot(i, at):
    return f"slot_id=S{i} doctor_id=asst-centro-h1 facility={FAC} procedure=VIS-FIS at={at} free=1"


class City:
    agent_id, address = BC, ""

    def __init__(self):
        self.rows, self.out, self.inbound, self.t0 = [], [], None, time.monotonic()

    async def say(self, to, text, *, status=None):
        self.out.append((time.monotonic() - self.t0, to, text))
        self.rows.append(msg(BC, to, text))
        if to != LEDGER:
            return "x"
        head, f = text.split()[0], b.fields_of(text)
        ans, delay = None, 0.0
        if head == "prescription":
            ans = "prescription prescription_number=RX-121 procedure=VIS-FIS priority=B state=issued"
        elif head == "agenda":
            ans = "agenda agenda_now=2026-12-23T17:19:00Z"
        elif head == "world" and f.get("page", "1") == "1":
            ans = "slots page=1/3\n" + "\n".join(slot(i, f"2026-12-23T{8 + i}:30:00Z") for i in range(10))  # Wed: past
        elif head == "world" and f.get("page") in ("2", "3"):
            day = "24" if f["page"] == "2" else "25"
            ans = f"slots page={f['page']}/3\n" + "\n".join(
                slot(int(f["page"]) * 100 + i, f"2026-12-{day}T{8 + i}:30:00Z") for i in range(10))
            delay = 5.0
        if ans:
            async def later():
                await asyncio.sleep(delay)
                self.rows.append(msg(LEDGER, BC, ans))
            asyncio.get_running_loop().create_task(later())
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


async def main():
    city = City()
    m = msg(CZ, BC, "Vorrei una visita. need.request lang=it prescription_number=RX-121")
    city.rows.append(m)
    city.inbound = m
    try:
        await asyncio.wait_for(b.main(SimpleNamespace(summary="x", is_emergency=False), city), 30)
    except asyncio.TimeoutError:
        pass
    stop = time.monotonic() + 25
    while time.monotonic() < stop and not any(to == CZ and "slot_id=" in t for _, to, t in city.out):
        await asyncio.sleep(0.3)
    told = [t for _, to, t in city.out if to == CZ]
    fails = []
    import re as _re
    ats = [_re.search(r"agenda_date=(\S+)", ln).group(1)[:16] for t in told for ln in t.splitlines()
           if ln.startswith("offer ") and "slot_id=" in ln and "agenda_date=" in ln]
    if not ats:
        fails.append(f"no slot offer at all (live: waiting list only): {[t[:70] for t in told]}")
    elif not all("2026-12-24" <= a < "2026-12-25T18" for a in ats):
        fails.append(f"offers outside the B window (Thu 12-24 .. Fri 12-25 18:00, 9 h lead): {ats}")
    first_offer = next((i for i, t in enumerate(told) if "offer_id=" in t), None)
    if first_offer is not None and "slot_id=" not in told[first_offer]:
        fails.append("the first offer was the waiting list alone")
    for f in fails:
        print("FAIL", f)
    print(("OK" if not fails else f"FAIL {len(fails)}") + " test_121_replay_0002990008")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


asyncio.run(main())
