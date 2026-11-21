"""L8 B4 (39 failed journeys): the citizen turns down every offer in one burst of cancels (the
waiting list too) and the desk answers nothing new, so the judge ends no_booking / no_alternative.

1. A burst of cancels of all the offers (reason=day / past) gets ONE new offer set from the other
   days or doctors, never silence and never the old slots again.
2. Nothing new left after that: the next burst gets the ladder (waiting list / honest line), never
   silence and never a second offer set.

Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_desk_refusal_offers.py [brain.py]
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
path = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
brain._ADVANCE_BUDGET = 2.0

BC = "agent:t07/booking_centre"
HO = "agent:t07/hospital"
CZ = "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAILS: list[str] = []
NO_SLOT = ("nulla corrisponde", "non c'è un posto adatto", "nothing fits", "No suitable slot")


def agenda_now() -> str:
    return (NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()


def slots_text(days=3) -> str:
    rows = []
    for d in range(days):
        base = (NOW + timedelta(days=2 + d)).replace(hour=9, minute=0, second=0, microsecond=0)
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[base.weekday()]
        for i, doc in enumerate(("asst-centro-h1", "asst-centro-h2", "asst-centro-h3")):
            at = (base + timedelta(hours=2 * i)).isoformat()
            rows.append(f"slot_id=S{d}{i} doctor_id={doc} at={at} day={day} facility={HO} procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"r{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self, need, days=3, slow=0.0):
        self.agent_id, self.address, self.inbound = BC, "", None
        self.need, self.days, self.slow = need, days, slow
        self.searches = 0
        self.keep: set[str] | None = None  # later searches: only these slots are still free
        self.rows: list[Message] = []
        self.out: list[tuple[float, str, str]] = []
        self.t0 = time.monotonic()
        self.tasks: list[asyncio.Task] = []

    async def say(self, to, text, *, status=None, need=None):
        if to == LEDGER:
            self.tasks.append(asyncio.create_task(self._ledger(text)))
        else:
            self.out.append((time.monotonic() - self.t0, to, text))
        self.rows.append(msg(self.agent_id, to, text, need or self.need))
        return "ok"

    async def _ledger(self, text):
        head = text.split()[0]
        if head == "world":
            self.searches += 1
        # slow= the live L8 Ledger: every answer after the first search takes this long
        await asyncio.sleep(self.slow if self.slow and self.searches > 1 else 0.2)
        if head == "prescription":
            number = text.split("prescription_number=")[-1].split()[0]
            body = f"prescription prescription_number={number} procedure=VIS-FIS priority=D state=issued"
        elif head == "agenda":
            body = f"agenda agenda_now={agenda_now()}"
        elif head == "world":
            body = slots_text(self.days)
            if self.keep is not None and self.searches > 1:
                body = "\n".join(ln for ln in body.splitlines() if "slot_id=" not in ln
                                  or brain.fields_of(ln).get("slot_id") in self.keep)
        else:
            return
        self.rows.append(msg(LEDGER, self.agent_id, body, self.need))

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

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


async def turn(city, row, limit=30.0):
    try:
        await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), View(city, row)), limit)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ != "OutOfScope":
            check(False, f"turn raised {type(exc).__name__}: {exc}")


async def burst(city, ids, reasons):
    """All cancels in the same second, each its own turn (as the city delivers them)."""

    rows = [msg(CZ, BC, f"Non mi va bene. cancel offer_id={oid} reason={why}", city.need)
            for oid, why in zip(ids, reasons)]
    city.rows.extend(rows)
    await asyncio.gather(*(turn(city, r) for r in rows))


def offer_sets(city, since=0.0):
    # a real offer set: its own "offer ... offer_id=of-" lines (a ladder line may quote an offer_id)
    return [(t, x) for t, to, x in city.out if to == CZ and t >= since
            and any(ln.startswith("offer ") and "offer_id=of-" in ln for ln in x.splitlines())]


def offer_ids(text):
    return [brain.fields_of(ln).get("offer_id") for ln in text.splitlines() if "offer_id=" in ln]


def slot_ids(text):
    return {brain.fields_of(ln).get("slot_id") for ln in text.splitlines() if "slot_id=" in ln} - {None}


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for t, to, x in (city.out if city else []):
            print(f"      [{t:5.1f}s] {to[-6:]} : {x[:400]!r}")


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_ONCE", "_RESCUED", "_GATES", "_LOCKS",
                 "_RESUMED", "_RESUMING", "_GAVE_UP", "_HELD", "_STUCK", "_REFUSAL_AT"):
        getattr(brain, name, {}).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST.clear()
        brain._RESCUE_LAST.append(time.monotonic())
    sent = getattr(brain, "_SENT", None)
    if hasattr(sent, "clear"):
        sent.clear()


async def refuse_all(label, days, slow=0.0, reasons=("day", "past", "day", "day"), nothing_left=False):
    reset()
    need = f"N-RF{SEQ[0]}"
    city = City(need, days=days, slow=slow)
    first = msg(CZ, BC, "Buongiorno, vorrei prenotare la visita. need.request prescription_number=RX-B4-1", need)
    city.rows.append(first)
    await turn(city, first)
    await asyncio.sleep(1.0)
    sets = offer_sets(city)
    if not sets:
        check(False, f"{label}: first offers arrive", city)
        return
    ids = offer_ids(sets[-1][1])
    first_slots = slot_ids(sets[-1][1])
    if nothing_left:
        city.keep = set(first_slots)  # the other slots went to other citizens meanwhile
    t1 = time.monotonic() - city.t0
    await burst(city, ids, list(reasons[: len(ids)]) + ["waiting_list"] * 9)
    await asyncio.sleep(2.0 + 3 * slow)
    after = [(t, x) for t, to, x in city.out if to == CZ and t >= t1]
    new_sets = offer_sets(city, since=t1)
    if nothing_left:
        check(not new_sets, f"{label}: no offer set of the refused slots ({len(new_sets)})", city)
        useful = [x for t, x in after if not brain._is_hold_line(x)]
        check(bool(useful), f"{label}: the ladder answers (waiting list / alternative), not only a hold", city)
    else:
        check(len(new_sets) == 1, f"{label}: the refuse-all burst gets ONE new offer set ({len(new_sets)})", city)
        fresh = slot_ids(new_sets[0][1]) if new_sets else set()
        check(bool(fresh) and not (fresh & first_slots), f"{label}: the new set has other slots only", city)
        # One coherent answer: no "nothing fits" / "no suitable slot" line next to the new offers.
        bad = [x for t, x in after if any(w in x for w in NO_SLOT)]
        check(not bad, f"{label}: no no-slot line around the new offers ({len(bad)})", city)
    check(bool(after), f"{label}: never silence after the burst ({len(after)} lines)", city)
    for task in city.tasks:
        task.cancel()


async def main():
    await refuse_all("3 days, refuse all", 3)
    await refuse_all("1 day, refuse all", 1)
    await refuse_all("3 days, slow Ledger 9 s", 3, slow=9.0)
    await refuse_all("3 days, slow Ledger 20 s", 3, slow=20.0)
    # a partner desk's own reason word (psq "no_availability", not in the kit): the normal refusal path
    await refuse_all("nothing left after the burst", 3, nothing_left=True)
    await refuse_all("unknown reason no_availability", 3, reasons=("no_availability",) * 4)
    print("FAILED: " + "; ".join(FAILS) if FAILS else "OK test_desk_refusal_offers")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
