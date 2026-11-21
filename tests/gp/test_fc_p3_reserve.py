"""Consultorio, P3 urgent reserve (S18): a citizen confirms our own older offer of a slot whose
latest world line now says urgent_reserve=true. No hold, a cancel with reason=NO_CAPACITY,
the other free dates once. A U offer still books, a normal slot still books.
Late confirm (l8n_fc_07): the latest Ledger agenda_now is past the slot time -> no hold, a
"date has passed" line, one fresh offer; agenda_now unknown -> books.
Run: <city python> -I tests/gp/test_fc_p3_reserve.py asst/_gp/agent.py   (exit 1 on FAIL; gp + consultorio brain,
so it sits outside the tests/test_*.py set that magic_pass.py runs on asst/_shared/agent.py)"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 0.4
brain._PAGE_BUDGET = 0.2
brain._CONSENT_WAIT = 0.3

FC = "agent:t07/family_counseling"
CZ = "citizen:t07/c1"
NOW = "2026-11-23T08:00:00+00:00"
N = [0]
FAILS = []


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


def world(reserve_s1, s1_at="2026-11-23T15:00:00+00:00", s1_day="mon"):
    ex = f"facility={FC} procedure=CONS-GIN free=1"
    s1 = " urgent_reserve=true" if reserve_s1 else ""
    return "slots page=1/1\n" + "\n".join([
        f"slot_id=S1 doctor_id=D-h1 at={s1_at} day={s1_day} {ex}{s1}",
        f"slot_id=S2 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue {ex}",
        f"slot_id=S3 doctor_id=D-h1 at=2026-11-24T11:00:00+00:00 day=tue {ex}",
        f"slot_id=S4 doctor_id=D-h1 at=2026-11-25T10:00:00+00:00 day=wed {ex}",
    ])


class City:
    """Fake city, synchronous Ledger."""

    def __init__(self, first, rows, slots, now=NOW):
        self.agent_id = FC
        self.now = now
        self.inbound = first
        self.rows = [*rows, first]
        self.out = []
        self.slots = slots
        self.address = ""
        for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE"):
            getattr(brain, name, {}).clear()

    def _answer(self, text):
        head = text.split()[0] if text.split() else ""
        f = brain.fields_of(text)
        if head == "agenda":
            return [f"agenda agenda_now={self.now}"] if self.now else []
        if head == "world":
            return [self.slots]
        if head == "hold":
            return [f"held hold_id=h-{f.get('slot_id')} slot_id={f.get('slot_id')} fence=1"]
        if head == "commit":
            hid = f.get("hold_id", "")
            return [f"committed booking_id={hid} ledger_ref=c-1 hold_id={hid} slot_id={hid[2:]}"]
        return []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(FC, to, text, self.inbound.need_id))
        if to == LEDGER:
            for ans in self._answer(text):
                self.rows.append(msg(LEDGER, FC, ans, self.inbound.need_id))
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    def to(self, who):
        return [t for w, t in self.out if w == who]


async def drive(city):
    os.environ["CITY_ROLE"] = "family_counseling"
    try:
        await brain.main(Need(summary=city.inbound.text, message=city.inbound), city)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ != "OutOfScope":
            raise
    finally:
        os.environ.pop("CITY_ROLE", None)


def offers(city):
    return [brain.fields_of(ln) for t in city.to(CZ) for ln in t.splitlines() if ln.startswith("offer")]


def check(name, ok, city):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)
        for to, t in city.out:
            print(f"     > {to[-22:]} : {t[:160]!r}")


def case(offer_extra, reserve_s1, need):
    """Our older offer of S1 (world then: normal), now the latest world says reserve or not."""
    first = msg(CZ, FC, "vorrei un appuntamento al consultorio", need)
    old = world(False)
    off = msg(FC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=D-h1 facility={FC} procedure=CONS-GIN{offer_extra}", need)
    conf = msg(CZ, FC, "confirm offer_id=of-S1 consent_ref=cons-x-booking", need)
    rows = [first, msg(FC, LEDGER, "world procedure=CONS-GIN page=1", need), msg(LEDGER, FC, old, need), off,
            msg(FC, LEDGER, "world procedure=CONS-GIN page=1", need), msg(LEDGER, FC, world(reserve_s1), need)]
    return City(conf, rows, world(reserve_s1))


def late_case(late_now, need):
    """S1 offered on Mon 08:00 for Tue 09:00; the latest agenda line says ``late_now``."""
    first = msg(CZ, FC, "vorrei una visita ginecologica di controllo, qualsiasi giorno va bene", need)
    w = world(False, "2026-11-24T09:00:00+00:00", "tue")
    rows = [first]
    if late_now:
        rows += [msg(FC, LEDGER, "agenda", need), msg(LEDGER, FC, f"agenda agenda_now={NOW}", need)]
    rows += [msg(FC, LEDGER, "world procedure=CONS-GIN page=1", need), msg(LEDGER, FC, w, need),
             msg(FC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=D-h1 facility={FC} procedure=CONS-GIN", need)]
    if late_now:
        rows.append(msg(LEDGER, FC, f"agenda agenda_now={late_now}", need))
    conf = msg(CZ, FC, "confirm offer_id=of-S1 consent_ref=ok-7", need)
    return City(conf, rows, w, now=late_now)


def held_s1(city):
    return any(t.startswith("hold") and "slot_id=S1" in t for t in city.to(LEDGER))


async def run():
    # 1. B / unknown priority on a now-reserved slot: no hold, NO_CAPACITY cancel, one new offer set.
    c = case("", True, "N-0001")
    await drive(c)
    cz = c.to(CZ)
    got = offers(c)
    check("reserve slot, non-U: no hold of S1", not held_s1(c), c)
    check("reserve slot, non-U: cancel offer_id=of-S1 reason=NO_CAPACITY to the citizen",
          any(t.startswith("cancel offer_id=of-S1 reason=NO_CAPACITY") for t in cz), c)
    check("reserve slot, non-U: no receipt", not any("booking.receipt" in t for t in cz), c)
    check("reserve slot, non-U: new offers, never S1",
          bool(got) and all(o.get("slot_id") != "S1" for o in got), c)
    ids = {o.get("offer_id") for o in got}
    # 2. Same confirm again: no second new offer (no loop), no hold.
    again = msg(CZ, FC, "confirm offer_id=of-S1 consent_ref=cons-x-booking", "N-0001")
    c.rows.append(again)
    c.inbound = again
    c.out = []
    await drive(c)
    new = {o.get("offer_id") for o in offers(c)} - ids
    check("repeat confirm of the reserved slot: no hold, no second new offer",
          not held_s1(c) and not new and bool(c.to(CZ)), c)
    # 3. U offer on the reserved slot: books.
    c = case(" priority=U", True, "N-0002")
    await drive(c)
    check("reserve slot, U offer: hold + receipt", held_s1(c) and any("booking.receipt" in t for t in c.to(CZ)), c)
    # 4. Normal slot (latest world has no reserve flag): books.
    c = case("", False, "N-0003")
    await drive(c)
    check("normal slot, non-U: hold + receipt", held_s1(c) and any("booking.receipt" in t for t in c.to(CZ)), c)
    # 6. Late confirm (l8n_fc_07): the latest agenda_now (12:40) is past S1 (09:00 same day).
    late = "2026-11-24T12:40:00+00:00"
    c = late_case(late, "N-0005")
    await drive(c)
    cz = c.to(CZ)
    got = offers(c)
    check("late confirm: no hold of S1, no receipt", not held_s1(c) and not any("booking.receipt" in t for t in cz), c)
    check("late confirm: visible 'date has passed' line", any("passato" in t for t in cz), c)
    check("late confirm: one fresh offer set, never S1",
          bool(got) and all(o.get("slot_id") != "S1" for o in got)
          and len([t for t in cz if any(ln.startswith("offer") for ln in t.splitlines())]) == 1, c)
    # 7. agenda_now unknown (no agenda line in the case): do not block.
    c = late_case(None, "N-0006")
    await drive(c)
    check("late confirm, agenda_now unknown: books", held_s1(c), c)
    # 5. The citizen cannot type priority=U to pass the reserve.
    c = case("", True, "N-0004")
    c.inbound = msg(CZ, FC, "confirm offer_id=of-S1 consent_ref=cons-x-booking priority=U", "N-0004")
    c.rows[-1] = c.inbound
    await drive(c)
    check("typed priority=U by the citizen: still no hold", not held_s1(c), c)


asyncio.run(run())
print(f"{len(FAILS)} FAIL")  # magic_pass.py reads "0 FAIL" as clean
sys.exit(1 if FAILS else 0)
