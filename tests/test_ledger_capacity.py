"""The Ledger contract: the booking desk must not offer slots the Ledger will refuse.

Live (200 journeys, 10:10): one capacity-1 slot was offered to up to 32 citizens, 169 cancels
reason=NO_CAPACITY (confirmed, refused, re-offered the next shared slot, refused again), near
slots went `past` while the citizen answered, and every NO_CAPACITY cancel said "riservato alle
urgenze (regola P3)" even for a slot that was merely full.

Run: <city python> -I tests/test_ledger_capacity.py asst/_shared/agent.py   (CITY_RESCUE_WARMUP=0)

a. 3 citizens ask at once, 4 free slots: each gets a distinct first slot.
b. After NO_CAPACITY on slot X for citizen A, citizen B is never offered X (the world page
   still lists X as free).
c. A slot 2 agenda hours away is not offered (a citizen needs 1-3 real minutes = agenda hours).
d. A NO_CAPACITY cancel says "just taken", and speaks of P3 only when the slot's latest world
   line has urgent_reserve=true (Italian and English).
e. One free slot, two citizens: both still get an offer (the desk is never silent).
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
NOW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RX = "Vorrei prenotare la visita. need.request lang=it prescription_number=RX-1"
FAILS: list[str] = []
SEQ = [0]


def check(ok, label):
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        FAILS.append(label)


def fields(text):
    return dict(t.split("=", 1) for t in (text or "").split() if "=" in t)


def slot_line(sid, hours, extra="", doctor="asst-centro-h1"):
    at = NOW + timedelta(hours=hours)
    return (f"slot_id={sid} doctor_id={doctor} at={at.isoformat()} day={DAYS[at.weekday()]} "
            f"facility={HO} procedure=VIS-FIS free=1{extra}")


def far_slots(n):
    """n free slots, 1 to 3 days ahead, two doctors (all far past any lead time)."""
    return [slot_line(f"S{i}", 26 + 7 * i, doctor="asst-centro-h1" if i % 2 else "asst-centro-h2")
            for i in range(1, n + 1)]


def offer_ids(text):
    return [fields(ln)["slot_id"] for ln in text.splitlines()
            if ln.startswith("offer ") and "offer_id=of-" in ln and "slot_id=" in ln]


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT", "_HELD", "_LEADS",
                 "_CLAIMS", "_DEAD"):
        table = getattr(brain, name, None)
        if hasattr(table, "clear"):
            table.clear()
    brain._RESCUE_LAST[:] = [time.monotonic() + 3600]  # no rescue sweep inside this test
    brain._WX.update(at=time.monotonic(), ttl=1e9, data={})  # no weather call: local only


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"f{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


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

    def cancels(self):
        return [t for to, t in self.out if to == self.cz and t.startswith("cancel offer_id=")]


async def one_turn(city, row):
    city.rows.append(row)
    view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=city.find, history=city.history,
                           say=lambda to, text, *, status=None: city.say(to, text))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 30)
    return city


async def ask(cz, need, slots, text=RX):
    city = Fast(cz, need, [], slots)
    await one_turn(city, msg(cz, BC, text, need))
    return city.offers()


async def refused_flow(cz, need, slots, code, *, lang="it"):
    """Citizen cz was offered every slot, confirmed of-S1; the hospital answers cancel <code>."""
    world = "slots page=1/1\n" + "\n".join(slots)
    offer = "\n".join(
        f"offer offer_id=of-{fields(ln)['slot_id']} slot_id={fields(ln)['slot_id']} "
        f"doctor_id={fields(ln)['doctor_id']} facility={HO} procedure=VIS-FIS "
        f"agenda_date={fields(ln)['at']} prescription_number=RX-1" for ln in slots)
    first = RX if lang == "it" else "I would like to book the visit. need.request lang=en prescription_number=RX-1"
    s1 = fields(slots[0])["slot_id"]
    rows = [
        msg(cz, BC, first, need),
        msg(BC, LEDGER, "prescription prescription_number=RX-1", need),
        msg(LEDGER, BC, "prescription prescription_number=RX-1 procedure=VIS-FIS priority=D state=issued", need),
        msg(LEDGER, BC, f"agenda agenda_now={NOW.isoformat()}", need),
        msg(LEDGER, BC, world, need),
        msg(BC, cz, offer, need),
        msg(cz, BC, f"confirm offer_id=of-{s1} consent_ref=cons-{need}-booking", need),
        msg(BC, HO, f"confirm offer_id=of-{s1} slot_id={s1} citizen={cz} consent_ref=cons-{need}-booking facility={HO}", need),
    ]
    city = Fast(cz, need, rows, slots)
    await one_turn(city, msg(HO, BC, f"cancel offer_id=of-{s1} reason={code}", need))
    return city


async def test_a():
    reset()
    slots = far_slots(4)
    firsts = await asyncio.gather(*(
        one_turn(Fast(f"citizen:t07/a{i}", f"N-a{i}", [], slots), msg(f"citizen:t07/a{i}", BC, RX, f"N-a{i}"))
        for i in range(3)))
    got = [c.offers()[:1] for c in firsts]
    print(f"      first slot per citizen (concurrent): {got}")
    check(all(got) and len({g[0] for g in got}) == 3, "a. 3 concurrent citizens, 4 free slots: 3 distinct first slots")
    reset()
    seq = [(await ask(f"citizen:t07/s{i}", f"N-s{i}", slots))[:1] for i in range(3)]
    print(f"      first slot per citizen (one after another): {seq}")
    check(all(seq) and len({g[0] for g in seq}) == 3, "a. 3 citizens one after another: 3 distinct first slots")
    # The soft claim goes further than the first slot: with 8 free slots the second citizen is not
    # offered the 6 slots the first one holds open (before: the same 6, only reordered).
    reset()
    eight = far_slots(8)
    one = await ask("citizen:t07/o1", "N-o1", eight)
    two = await ask("citizen:t07/o2", "N-o2", eight)
    print(f"      8 free slots: A offered {one}, B offered {two}")
    check(bool(one) and bool(two) and not set(one) & set(two), "a. 8 free slots: B is offered none of the slots A holds open")


async def test_b():
    reset()
    slots = far_slots(4)
    await refused_flow("citizen:t07/ba", "N-ba", slots, "NO_CAPACITY")
    got = await ask("citizen:t07/bb", "N-bb", slots)  # the world page still lists S1 free
    print(f"      B offered {got} after NO_CAPACITY on S1")
    check(bool(got) and "S1" not in got, "b. after NO_CAPACITY on S1 for A, B is never offered S1")
    again = await ask("citizen:t07/bc", "N-bc", slots)
    check(bool(again) and "S1" not in again, "b. nor a third citizen")


async def test_c():
    reset()
    slots = [slot_line("S1", 2), slot_line("S2", 10, doctor="asst-centro-h2")]  # #121: past the 9 h lead
    got = await ask("citizen:t07/c1", "N-c1", slots)
    print(f"      slots 2 h and 10 h away -> offered {got}")
    check(bool(got) and "S1" not in got, "c. a slot 2 agenda hours away is not offered (the 10 h one is)")
    reset()
    lone = await ask("citizen:t07/c2", "N-c2", [slot_line("S1", 2)])
    print(f"      only a slot 2 h away -> offered {lone}")
    check("S1" not in lone, "c. a lone slot 2 agenda hours away is not offered either")


async def test_d():
    reset()
    taken = await refused_flow("citizen:t07/d1", "N-d1", far_slots(4), "NO_CAPACITY")
    text = " ".join(taken.cancels())
    print(f"      cancel text: {text!r}")
    check(bool(text) and not any(w in text for w in ("P3", "urgenz", "riservat")),
          "d. NO_CAPACITY on a merely full slot: no P3 words")
    check("appena preso" in text, "d. NO_CAPACITY on a merely full slot says it was just taken")
    reset()
    reserved = far_slots(4)
    reserved[0] += " urgent_reserve=true"
    res = await refused_flow("citizen:t07/d2", "N-d2", reserved, "NO_CAPACITY")
    rtext = " ".join(res.cancels())
    check("P3" in rtext, "d. NO_CAPACITY on an urgent_reserve=true slot still says P3")
    reset()
    en = await refused_flow("citizen:t07/d3", "N-d3", far_slots(4), "NO_CAPACITY", lang="en")
    etext = " ".join(en.cancels())
    print(f"      EN cancel text: {etext!r}")
    check("just taken" in etext and "P3" not in etext, "d. English citizen: 'that slot was just taken', no P3")


async def test_e():
    reset()
    slots = far_slots(1)
    got = await asyncio.gather(*(
        one_turn(Fast(f"citizen:t07/e{i}", f"N-e{i}", [], slots), msg(f"citizen:t07/e{i}", BC, RX, f"N-e{i}"))
        for i in range(2)))
    offers = [c.offers() for c in got]
    print(f"      offers with 1 free slot, 2 citizens: {offers}")
    check(all(offers), "e. one free slot, two citizens: both still get an offer")


async def main():
    for fn in (test_a, test_b, test_c, test_d, test_e):
        await fn()
    print("ALL OK" if not FAILS else "FAILED: " + "; ".join(FAILS))
    raise SystemExit(1 if FAILS else 0)


asyncio.run(main())
