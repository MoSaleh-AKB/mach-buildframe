"""L7 shared slots: many citizens asking at once must not all get the same first slot.

Live (rehearsal R003, 11 citizens confirmed h1 14:30): every concurrent case got the same earliest
slots, all confirmed the first one, all but one got SLOT_TAKEN, were re-offered the same next slot
and looped.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_slot_spread.py asst/_shared/agent.py

1. Five citizens, same procedure and window, six free slots, a slow Ledger (answers pushed as
   turns), our hospital holds in one shared Ledger (first confirm wins, the others get the
   cancel our hospital sends on SLOT_TAKEN). Each citizen confirms the first offer of every new
   offer message, two rounds at most. At least 4 must be booked within 2 rounds, with at most
   2 SLOT_TAKEN refusals in total.
2. A slot refused SLOT_TAKEN is never offered again to that citizen, even when the world page
   still lists it as free.
3. A slot that led another case's offer in the last minute goes later in the list, never first
   (and stays in it); the citizen's favourite day still comes first; a lone citizen after the
   window gets the earliest slot first again.
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
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC, HO = "agent:t07/booking_centre", "agent:t07/hospital"
DELAY = float(os.environ.get("SLOT_SPREAD_DELAY", "6"))  # Ledger answer time (live 8-20 s)
NOW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FAILS: list[str] = []


def slot_lines(n=6):
    rows = []
    for i in range(1, n + 1):
        # Two slots a day (10:00, 15:00) from tomorrow on: every slot is inside the D window.
        at = NOW + timedelta(days=1 + (i - 1) // 2, hours=2 + 5 * ((i - 1) % 2))
        doc = "asst-centro-h1" if i % 2 else "asst-centro-h2"
        rows.append(f"slot_id=S{i} doctor_id={doc} at={at.isoformat()} day={DAYS[at.weekday()]} "
                    f"facility={HO} procedure=VIS-FIS free=1")
    return rows


def fields(text):
    return dict(t.split("=", 1) for t in (text or "").split() if "=" in t)


def offer_ids(text):
    return [fields(ln)["slot_id"] for ln in text.splitlines()
            if ln.startswith("offer ") and "offer_id=of-" in ln and "slot_id=" in ln]


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT", "_HELD", "_LEADS"):
        table = getattr(brain, name, None)
        if hasattr(table, "clear"):
            table.clear()
    brain._RESCUE_LAST[:] = [time.monotonic() + 3600]  # no rescue sweep inside this test
    brain._WX.update(at=time.monotonic(), ttl=1e9, data={})  # no weather call: local only


def check(ok, label):
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        FAILS.append(label)


class World:
    """One process, one desk: every case shares the module state, as live."""

    def __init__(self, delay, slots, hold_delay=None):
        self.delay, self.slots = delay, list(slots)
        self.hold_delay = delay if hold_delay is None else hold_delay
        self.rows, self.out, self.tasks = [], [], set()
        self.taken: set[str] = set()
        self.n = 0
        self.slot_taken = 0
        self.booked: dict[str, str] = {}
        self.confirms: dict[str, list[str]] = {}
        self.first_offer: dict[str, str] = {}
        self.max_rounds = 2

    def msg(self, sender, to, text, need):
        self.n += 1
        return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{self.n}", need_id=need,
                       conversation_id=f"cv-{need}", body={"text": text},
                       sent_at=datetime.now(timezone.utc).isoformat())

    def spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def run(self, row):
        view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=self.find, history=self.history,
                               say=lambda to, text, *, status=None: self.say(to, text, row.need_id))
        try:
            await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)
        except Exception as exc:  # noqa: BLE001 (a crash shows up as a missing booking)
            if type(exc).__name__ != "OutOfScope":
                print("      turn raised", type(exc).__name__, exc)

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def say(self, to, text, need):
        self.out.append((to, text, need))
        self.rows.append(self.msg(BC, to, text, need))
        if to == LEDGER:
            self.spawn(self._ledger(text, need))
        elif to == HO and text.startswith("confirm"):
            self.spawn(self._hospital(text, need))
        elif to.startswith("citizen:") and offer_ids(text):
            self.spawn(self._citizen(to, text, need))
        return "ok"

    async def _ledger(self, text, need):
        await asyncio.sleep(self.delay)
        head, f = text.split()[0], fields(text)
        if head == "prescription":
            body = (f"prescription prescription_number={f.get('prescription_number', '')} procedure=VIS-FIS "
                    "priority=D state=issued")
        elif head == "agenda":
            body = f"agenda agenda_now={NOW.isoformat()}"
        elif head == "world":
            free = [s for s in self.slots if fields(s)["slot_id"] not in self.taken]
            body = "slots page=1/1\n" + "\n".join(free) if free else "slots page=1/1 none"
        else:
            return
        row = self.msg(LEDGER, BC, body, need)
        self.rows.append(row)
        await self.run(row)

    async def _hospital(self, text, need):
        # Our hospital: hold + commit in the shared Ledger. The first confirm of a slot wins;
        # the others get SLOT_TAKEN, which reaches the desk as a cancel of that offer.
        f = fields(text)
        await asyncio.sleep(self.hold_delay)
        sid, offer = f.get("slot_id", ""), f.get("offer_id", "")
        if sid in self.taken:
            self.slot_taken += 1
            row = self.msg(HO, BC, f"cancel offer_id={offer} reason=facility_cancelled", need)
        else:
            self.taken.add(sid)
            row = self.msg(HO, BC, f"booking.receipt booking_id=bk-{sid} ledger_ref=lr-{sid} slot_id={sid}\nOk", need)
        self.rows.append(row)
        await self.run(row)

    async def _citizen(self, cz, text, need):
        if text.startswith("booking.receipt"):
            return
        ids = offer_ids(text)
        self.first_offer.setdefault(cz, ids[0])
        done = self.confirms.setdefault(cz, [])
        if cz in self.booked or len(done) >= self.max_rounds:
            return
        await asyncio.sleep(0.5)
        done.append(ids[0])
        row = self.msg(cz, BC, f"confirm offer_id=of-{ids[0]} consent_ref=cons-{need}-booking", need)
        self.rows.append(row)
        await self.run(row)

    def receipts(self):
        for to, text, _ in self.out:
            if to.startswith("citizen:") and text.startswith("booking.receipt"):
                self.booked[to] = fields(text).get("booking_id", "")
        return self.booked

    async def settle(self, cap):
        t0 = time.monotonic()
        while self.tasks and time.monotonic() - t0 < cap:
            await asyncio.wait(list(self.tasks), timeout=0.5)
            self.receipts()
        for task in list(self.tasks):
            task.cancel()


async def many_at_once():
    reset()
    w = World(DELAY, slot_lines(6))
    czs = [f"citizen:t07/r003-{i}" for i in range(5)]
    for i, cz in enumerate(czs):
        need = f"N-r003-{i}"
        row = w.msg(cz, BC, f"Vorrei prenotare la visita. need.request lang=it prescription_number=RX-{i}", need)
        w.rows.append(row)
        w.spawn(w.run(row))
    await w.settle(cap=12 * DELAY + 30)
    booked = w.receipts()
    print(f"      first offered slot per citizen: {[w.first_offer.get(c) for c in czs]}")
    print(f"      confirms per citizen: {[w.confirms.get(c) for c in czs]}")
    print(f"      booked {len(booked)}/5 {sorted(booked.values())}, SLOT_TAKEN refusals {w.slot_taken}")
    check(len(booked) >= 4, f"many at once: {len(booked)}/5 booked within 2 rounds (need >= 4)")
    check(w.slot_taken <= 2, f"many at once: {w.slot_taken} SLOT_TAKEN refusals (need <= 2)")
    check(len(set(booked.values())) == len(booked), "many at once: no slot booked twice")


class Fast:
    """A synchronous Ledger for one-turn checks (no hospital, no citizen bot)."""

    def __init__(self, cz, need, rows, slots):
        self.cz, self.need, self.slots = cz, need, slots
        self.rows, self.out = list(rows), []

    async def say(self, to, text):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER:
            head = text.split()[0]
            body = {"agenda": f"agenda agenda_now={NOW.isoformat()}",
                    "world": "slots page=1/1\n" + "\n".join(self.slots),
                    "prescription": "prescription prescription_number=RX-1 procedure=VIS-FIS priority=D state=issued"}.get(head)
            if body:
                self.rows.append(msg(LEDGER, BC, body, self.need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    def offers(self):
        got = [offer_ids(t) for to, t in self.out if to == self.cz and offer_ids(t)]
        return got[-1] if got else []


SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"f{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


async def one_turn(city, row):
    city.rows.append(row)
    view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=city.find, history=city.history,
                           say=lambda to, text, *, status=None: city.say(to, text))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 30)
    return city.offers()


async def ask(cz, need, text="Vorrei prenotare la visita. need.request lang=it prescription_number=RX-1"):
    city = Fast(cz, need, [], slot_lines(6))
    return await one_turn(city, msg(cz, BC, text, need))


async def refused_never_again():
    reset()
    cz, need = "citizen:t07/q1", "N-q1"
    lines = slot_lines(6)
    world = "slots page=1/1\n" + "\n".join(lines)
    offer = "\n".join(
        f"offer offer_id=of-S{i} slot_id=S{i} doctor_id={fields(lines[i - 1])['doctor_id']} facility={HO} "
        f"procedure=VIS-FIS agenda_date={fields(lines[i - 1])['at']} prescription_number=RX-1" for i in range(1, 7))
    rows = [
        msg(cz, BC, "Vorrei prenotare la visita. need.request lang=it prescription_number=RX-1", need),
        msg(BC, LEDGER, "prescription prescription_number=RX-1", need),
        msg(LEDGER, BC, "prescription prescription_number=RX-1 procedure=VIS-FIS priority=D state=issued", need),
        msg(LEDGER, BC, f"agenda agenda_now={NOW.isoformat()}", need),
        msg(LEDGER, BC, world, need),
        msg(BC, cz, offer, need),
        msg(cz, BC, f"confirm offer_id=of-S1 consent_ref=cons-{need}-booking", need),
        msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={cz} consent_ref=cons-{need}-booking facility={HO}", need),
    ]
    # The world page still shows S1 free (another ASST holds it: shared slot).
    city = Fast(cz, need, rows, lines)
    got = await one_turn(city, msg(HO, BC, "cancel offer_id=of-S1 reason=facility_cancelled", need))
    check(bool(got) and "S1" not in got, f"SLOT_TAKEN slot not offered again to that citizen (re-offer {got})")


async def recent_goes_later():
    reset()
    first_a = await ask("citizen:t07/a", "N-a")
    first_b = await ask("citizen:t07/b", "N-b")
    print(f"      case A offers {first_a}, case B offers {first_b}")
    check(bool(first_a) and bool(first_b) and first_b[0] != first_a[0],
          "a slot that led another case's offer a moment ago is not first for the next case")
    check(bool(first_a) and first_a[0] in first_b, "that slot is still in the list (ordering only)")
    # The favourite day is the day of the two slots that just led cases A and B.
    day = fields(slot_lines(6)[0])["day"]
    same_day = [fields(s)["slot_id"] for s in slot_lines(6) if fields(s)["day"] == day]
    fav = await ask("citizen:t07/c", "N-c",
                    f"Vorrei prenotare la visita. need.request lang=it prescription_number=RX-1 days={day}")
    check(bool(fav) and fav[0] in same_day, f"favourite day still first ({fav[:2]}, day {day} = {same_day})")
    # A lone citizen after the window: the earliest slot first again.
    window = getattr(brain, "_LEAD_WINDOW_S", None)
    if window is not None:
        brain._LEAD_WINDOW_S = 0.2
        await asyncio.sleep(0.3)
    lone = await ask("citizen:t07/d", "N-d")
    if window is not None:
        brain._LEAD_WINDOW_S = window
    check(bool(lone) and bool(first_a) and lone[0] == first_a[0],
          f"lone citizen after the window gets the earliest slot first ({lone[:2]})")


async def main():
    await refused_never_again()
    await recent_goes_later()
    await many_at_once()
    print("ALL OK" if not FAILS else "FAILED: " + "; ".join(FAILS))
    raise SystemExit(1 if FAILS else 0)


asyncio.run(main())
