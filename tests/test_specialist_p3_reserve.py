"""S18 P3 at the facility: a non-U confirm never holds an urgent_reserve slot.

The latest Ledger world line of the slot says urgent_reserve=true and the case's priority
(our offer, the desk's priority=, or the Ledger prescription) is not U: no hold, the sender
gets "cancel offer_id=... reason=NO_CAPACITY" (a citizen also gets one re-offer, never twice).
A U case, a normal slot, or an unknown priority still books.
Run: <city python> -I tests/test_specialist_p3_reserve.py asst/_shared/agent.py   (exit 1 on FAIL)
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, CapError, Message, Need

ROLE = "specialist_care"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for name, value in (("_ADVANCE_BUDGET", 0.4), ("_PAGE_BUDGET", 0.2), ("_CONSENT_WAIT", 0.3)):
    if hasattr(brain, name):
        setattr(brain, name, value)

ME = f"agent:t07/{ROLE}"
BC = "agent:t07/booking_centre"
CZ = "citizen:t07/c1"
NOW = "2026-11-23T08:00:00+00:00"
PROC = {"hospital": "VIS-FIS", "specialist_care": "VIS-CARD"}[ROLE]
N, NEED = [0], [500]


def new_need():
    NEED[0] += 1
    return f"N-P3-{NEED[0]}"


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"p3m{N[0]}", need_id=need, body={"text": text}, recipient=to)


def world():
    base = f"facility={ME} procedure={PROC} free=1"
    return ("slots page=1/1\n"
            f"slot_id=S1 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue {base} urgent_reserve=true\n"
            f"slot_id=S2 doctor_id=D-h1 at=2026-11-25T09:00:00+00:00 day=wed {base}\n"
            f"slot_id=S3 doctor_id=D-h1 at=2026-11-26T09:00:00+00:00 day=thu {base}")


class City:
    """Fake city, synchronous Ledger: every write to LEDGER is answered at once."""

    def __init__(self, first, rows, rx):
        self.agent_id, self.inbound, self.address = ME, first, ""
        self.rows, self.out, self.rx = [*rows, first], [], rx
        for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE"):
            getattr(brain, name, {}).clear()

    def _ledger_answer(self, text):
        head = text.split()[0] if text.split() else ""
        f = brain.fields_of(text)
        if head == "prescription":
            row = self.rx.get(f.get("prescription_number", ""))
            return [row] if row else []
        if head == "agenda":
            return [f"agenda agenda_now={NOW}"]
        if head == "world":
            return [world()]
        if head == "hold":
            return [f"held hold_id=h-{f.get('slot_id')} slot_id={f.get('slot_id')} fence=1"]
        if head == "commit":
            hid = f.get("hold_id", "")
            return [f"committed booking_id={hid} ledger_ref=c-1 hold_id={hid} slot_id={hid[2:]}"]
        return []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(ME, to, text, self.inbound.need_id))
        if to == LEDGER:
            for ans in self._ledger_answer(text):
                self.rows.append(msg(LEDGER, ME, ans, self.inbound.need_id))
        return "x"

    async def find(self, query):
        raise CapError("UPSTREAM_TIMEOUT", "index")

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    def to(self, who):
        return [t for w, t in self.out if w == who]


async def drive(city):
    os.environ["CITY_ROLE"] = ROLE
    try:
        await brain.main(Need(summary=city.inbound.text, message=city.inbound), city)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    finally:
        os.environ.pop("CITY_ROLE", None)
    return city


def rx_row(prio):
    p = f" priority={prio}" if prio else ""
    return {"RX1": f"prescription prescription_number=RX1 procedure={PROC}{p} state=issued"}


async def desk_confirm(slot, prio):
    need = new_need()
    p = f" priority={prio}" if prio else ""
    text = (f"confirm offer_id=of-{slot} slot_id={slot} prescription_number=RX1{p} citizen={CZ} "
            f"consent_ref=cons-{need}-booking facility={ME}")
    pre = [msg(ME, LEDGER, f"world procedure={PROC} facility={ME} page=1", need), msg(LEDGER, ME, world(), need)]
    return await drive(City(msg(BC, ME, text, need), pre, rx_row(prio)))


def holds(c, slot):
    return [t for t in c.to(LEDGER) if t.startswith("hold") and f"slot_id={slot}" in t.split()]


def booked(c, slot):
    return bool(holds(c, slot)) and any(t.startswith("commit") for t in c.to(LEDGER)) and any(
        "booking.receipt" in t for _, t in c.out)


FAILS = []


def check(name, ok, c):
    print(("PASS " if ok else "FAIL ") + f"[{ROLE}] {name}")
    if not ok:
        FAILS.append(name)
        for to, t in c.out:
            print(f"     > {to[-26:]} : {t[:160]!r}")


def offers(c, who):
    return [brain.fields_of(l).get("slot_id") for t in c.to(who) for l in t.splitlines()
            if l.startswith("offer") and brain.fields_of(l).get("slot_id")]


async def main():
    c = await desk_confirm("S1", "B")
    check("B confirm on urgent_reserve S1: no hold", not holds(c, "S1"), c)
    check("B confirm on urgent_reserve S1: desk gets cancel offer_id=of-S1 reason=NO_CAPACITY",
          any(t.startswith("cancel offer_id=of-S1 reason=NO_CAPACITY") for t in c.to(BC)), c)
    c = await desk_confirm("S1", "U")
    check("U confirm on urgent_reserve S1: hold + commit + receipt", booked(c, "S1"), c)
    c = await desk_confirm("S2", "B")
    check("B confirm on normal S2: hold + commit + receipt", booked(c, "S2"), c)
    c = await desk_confirm("S1", "")
    check("unknown priority on urgent_reserve S1: not blocked (books)", booked(c, "S1"), c)
    c = await desk_confirm("S1", "urgente")
    check("priority=urgente (a word for U) on urgent_reserve S1: books", booked(c, "S1"), c)

    # A citizen confirms our own direct offer of S1 (made before P3 started); offer says priority=B,
    # the citizen types priority=U: our offer wins. Cancel + one re-offer (never S1); a repeat
    # confirm gets a visible line and no second re-offer.
    need = new_need()
    rows = [msg(CZ, ME, "vorrei prenotare la visita prescription_number=RX1", need),
            msg(ME, LEDGER, f"world procedure={PROC} facility={ME} page=1", need), msg(LEDGER, ME, world(), need),
            msg(LEDGER, ME, rx_row("B")["RX1"], need),
            msg(ME, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=D-h1 facility={ME} procedure={PROC} "
                        "priority=B prescription_number=RX1", need)]
    conf = msg(CZ, ME, "confirm offer_id=of-S1 priority=U consent_ref=cons-x-booking", need)
    c = await drive(City(conf, rows, rx_row("B")))
    got = offers(c, CZ)
    check("citizen B (typed priority=U) on reserved S1: no hold", not holds(c, "S1"), c)
    check("citizen B on reserved S1: cancel NO_CAPACITY + one re-offer, not S1",
          any(t.startswith("cancel offer_id=of-S1 reason=NO_CAPACITY") for t in c.to(CZ)) and bool(got)
          and "S1" not in got, c)
    again = msg(CZ, ME, "confirm offer_id=of-S1 consent_ref=cons-x-booking", need)
    c.rows.append(again); c.inbound = again; c.out = []
    await drive(c)
    check("citizen repeats confirm of reserved S1: visible line, no hold, no second re-offer",
          bool(c.to(CZ)) and not holds(c, "S1") and not offers(c, CZ), c)
    print(f"{len(FAILS)} FAIL")  # magic_pass.py reads "0 FAIL" as clean
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
