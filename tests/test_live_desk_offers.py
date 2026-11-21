"""Live 21 Nov: the desk passes run out before the Ledger answers (its push opens no turn).

The citizen must still get the offers once the Ledger lines land, and only one holding line.
Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_live_desk_offers.py [brain.py]
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")  # !220: the brain reads it at import, tests start right after boot

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
only = {a.upper() for a in sys.argv[1:] if not a.endswith(".py")}
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC = "agent:t07/booking_centre"
HO = "agent:t07/hospital"
CZ = "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAILS: list[str] = []


def agenda_now() -> str:
    return (NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()


def slots_text(facility: str = HO) -> str:
    base = (NOW + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    rows = []
    for i, doc in enumerate(("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"), start=1):
        at = (base + timedelta(hours=3 * (i - 1))).isoformat()
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[base.weekday()]
        rows.append(f"slot_id=S{i} doctor_id={doc} at={at} day={day} facility={facility} procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


class SlowCity:
    def __init__(self, need, delays=(6.0, 9.0, 12.0), opens_turn=False, rows=None, hc=(0.3, 0.3)):
        self.agent_id = BC
        self.address = ""
        self.need = need
        self.delays = {"prescription": delays[0], "agenda": delays[1], "world": delays[2]}
        self.hold_delay, self.commit_delay = hc
        self.opens_turn = opens_turn
        self.rows = list(rows or [])
        self.inbound = None
        self.out: list[tuple[float, str, str]] = []
        self.t0 = time.monotonic()
        self.tasks: list[asyncio.Task] = []

    def out_all(self):
        """Everything we sent: citizen lines with their time, plus the Ledger sends from rows."""

        sent = [(0.0, r.recipient, r.text) for r in self.rows if r.sender == self.agent_id and r.recipient == LEDGER]
        return sent + list(self.out)

    def reset_clock(self):
        self.t0 = time.monotonic()

    async def say(self, to, text, *, status=None, need=None):
        if to == LEDGER:
            head = text.split()[0]
            if head in self.delays or head in ("hold", "commit"):
                self.tasks.append(asyncio.create_task(self._ledger(head, text)))
        else:
            self.out.append((time.monotonic() - self.t0, to, text))
        self.rows.append(msg(self.agent_id, to, text, need or self.need))
        return "ok"

    async def _ledger(self, head, text):
        await asyncio.sleep(
            {"hold": self.hold_delay, "commit": self.commit_delay}.get(head) or self.delays.get(head, 0.3)
        )
        fields = dict(w.split("=", 1) for w in text.split() if "=" in w)
        if head == "hold":
            body = (f"held hold_id=H-{fields.get('slot_id')} slot_id={fields.get('slot_id')} "
                    f"citizen={fields.get('citizen', '')} expires_in=300")
        elif head == "commit":
            body = (f"committed booking_id={fields.get('hold_id')} ledger_ref=LR-{fields.get('hold_id')} "
                    f"slot_id={fields.get('hold_id', '').removeprefix('H-')}")
        elif head == "prescription":
            number = text.split("prescription_number=")[-1].split()[0]
            body = f"prescription prescription_number={number} procedure=VIS-FIS priority=B state=issued"
        elif head == "agenda":
            body = f"agenda agenda_now={agenda_now()}"
        else:
            body = slots_text()
        row = msg(LEDGER, self.agent_id, body, self.need)
        self.rows.append(row)
        if self.opens_turn:
            try:
                await self.turn(row)
            except Exception as exc:  # noqa: BLE001
                print('      ledger turn raised', type(exc).__name__, exc)

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        view = _View(self, row)
        await brain.main(SimpleNamespace(summary="x", is_emergency=False), view)


class _View:
    """One turn's city: shares rows and out with the SlowCity, own inbound."""

    def __init__(self, city, inbound):
        self._c = city
        self.inbound = inbound
        self.agent_id = city.agent_id
        self.address = ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_RESUMED", "_RESUMING", "_GAVE_UP",
                 "_HELD", "_STUCK"):
        getattr(brain, name, {}).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST.clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST.append(time.monotonic())  # no sweep unless a scenario asks for it
    sent = getattr(brain, "_SENT", None)
    if hasattr(sent, "clear"):
        sent.clear()


def offers(city):
    return [(t, text) for t, to, text in city.out if to == CZ and "offer_id=of-" in text]


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        if city is not None:
            for t, to, text in city.out:
                print(f"      [{t:5.1f}s] {to[-6:]} : {text[:110]!r}")


NEED_RX = "need.request area={'district':_6} lang=it prescription_number=prescription_number-N-0001"


async def run_turn(city, row, limit=40.0):
    city.rows.append(row)
    city.reset_clock()
    view = _View(city, row)
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), limit)


async def settle(city, until=24.0):
    """Let continuations (Ledger turns, rescue) run; the clock is the case clock."""
    while time.monotonic() - city.t0 < until:
        await asyncio.sleep(0.25)
    for task in city.tasks:
        task.cancel()


async def scenario(label, text, delays, budget):
    reset()
    brain._ADVANCE_BUDGET = budget
    need = f"N-L{SEQ[0]}"
    city = SlowCity(need, delays=delays)
    first = msg(CZ, BC, text, need)
    await run_turn(city, first)
    await settle(city, until=max(delays) + 3 * budget + 15)
    lines = [x for t, to, x in city.out if to == CZ]
    holds = [x for x in lines if brain._is_hold_line(x)]
    check(len(offers(city)) == 1, f"{label}: offers reach the citizen after the late Ledger ({len(offers(city))})", city)
    check(len(holds) <= 1, f"{label}: one holding line only ({len(holds)})", city)


def same_hold_twice(lines):
    holds = [x for x in lines if brain._is_hold_line(x)]
    return len(holds) != len(set(holds))


async def give_up(label, text, delays, want):
    """The Ledger never sends what the desk needs: after the background rounds, one real answer."""

    reset()
    brain._ADVANCE_BUDGET = 1.0
    wait = getattr(brain, "_RESUME_WAIT", None)
    brain._RESUME_WAIT = 2.0
    need = f"N-L{SEQ[0]}"
    city = SlowCity(need, delays=delays)
    try:
        await run_turn(city, msg(CZ, BC, text, need))
        await settle(city, until=3 * 1.0 + 3 * (2.0 + 1.5) + 8)
    finally:
        if wait is not None:
            brain._RESUME_WAIT = wait
    lines = [x for t, to, x in city.out if to == CZ]
    final = [x for x in lines if want in x]
    check(len(final) == 1, f"{label}: one final answer after the rounds ({len(final)} x {want!r})", city)
    check(not same_hold_twice(lines), f"{label}: never the same holding line twice", city)
    if want == "waiting_list=true":
        day = (datetime.fromisoformat(agenda_now()) + timedelta(days=1)).strftime("%d/%m/%Y")
        check(any(day in x for x in final), f"{label}: the waiting list names a concrete day ({day})", city)


async def resume_read_breaks():
    """FINAL-AUDIT: the background resume's history read raises a non-transport error: the citizen
    still gets a final answer (waiting list), never only the holding line."""

    reset()
    brain._ADVANCE_BUDGET = 1.0
    wait = getattr(brain, "_RESUME_WAIT", None)
    brain._RESUME_WAIT = 2.0
    need = f"N-L{SEQ[0]}"
    city = SlowCity(need, delays=(0.3, 0.3, 900.0))
    try:
        await run_turn(city, msg(CZ, BC, "Buongiorno, vorrei prenotare la fisiatria. " + NEED_RX, need))
        real = city.history

        async def broken(peer=None, *, need_id=None):
            raise ValueError("bad row from the city")

        city.history = broken
        await settle(city, until=12.0)
        city.history = real
    finally:
        if wait is not None:
            brain._RESUME_WAIT = wait
    lines = [x for t, to, x in city.out if to == CZ]
    final = [x for x in lines if "waiting_list=true" in x or "non riesco a prenotare" in x]
    check(len(final) == 1, f"resume read breaks: one final answer anyway ({len(final)})", city)


async def citizen_writes_meanwhile():
    """A citizen message lands while the background task waits: one offer set, the need lock held."""

    reset()
    brain._ADVANCE_BUDGET = 1.0
    need = f"N-L{SEQ[0]}"
    city = SlowCity(need, delays=(6.0, 6.0, 6.0))
    await run_turn(city, msg(CZ, BC, "Buongiorno, devo prenotare una visita fisiatrica. " + NEED_RX, need))
    await asyncio.sleep(max(0.0, 4.5 - (time.monotonic() - city.t0)))
    again = msg(CZ, BC, "Ci sono novità? Aspetto la visita.", need)
    city.rows.append(again)
    second = asyncio.create_task(city.turn(again))
    await asyncio.sleep(max(0.0, 6.2 - (time.monotonic() - city.t0)))
    third = msg(CZ, BC, "Allora?", need)
    city.rows.append(third)
    await asyncio.gather(second, city.turn(third), return_exceptions=True)
    await settle(city, until=30.0)
    lines = [x for t, to, x in city.out if to == CZ]
    check(len(offers(city)) == 1, f"citizen writes meanwhile: exactly one offer set ({len(offers(city))})", city)
    check(not same_hold_twice(lines), "citizen writes meanwhile: never the same holding line twice", city)


async def main():
    os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
    await scenario("it late Ledger", "Buongiorno, devo prenotare una visita fisiatrica. " + NEED_RX, (6.0, 6.0, 6.0), 1.0)
    await scenario("en late world", "Hello, I need to book a physiotherapy visit. " + NEED_RX.replace("lang=it", "lang=en"), (0.5, 0.5, 7.0), 1.0)
    await scenario("it 8/14/20 s Ledger", "Buongiorno, vorrei prenotare la fisiatria. " + NEED_RX, (8.0, 14.0, 20.0), 3.0)
    await give_up("no dates from the Ledger", "Buongiorno, vorrei prenotare la fisiatria. " + NEED_RX,
                  (0.5, 0.5, 900.0), "waiting_list=true")
    await give_up("no prescription answer", "Buongiorno, vorrei prenotare la fisiatria. " + NEED_RX,
                  (900.0, 0.5, 0.5), "non riesco a prenotare")
    await citizen_writes_meanwhile()
    await resume_read_breaks()
    print("FAILED: " + "; ".join(FAILS) if FAILS else "OK test_live_desk_offers")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
