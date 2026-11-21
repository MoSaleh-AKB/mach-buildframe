"""S17/S18: P1 (prescription validity) becomes known after the desk sent offers on RX1.

The citizen confirms an offer; the desk forwards ``confirm ... prescription_number=RX1`` to the
hospital. Under P1 the Ledger refuses it (-1 "rule ignored"). The desk must re-read RX1 first and,
when it is expired or not issued, ask for the renewal and forward nothing. A slow Ledger (8 s,
20 s) must not leave the confirm turn silent for more than 3 s.
P3: when the latest world row made the offered slot urgent_reserve=true and the priority is not U,
nothing is forwarded; the offer is withdrawn and re-offered once. Only a U patient is described as
urgent to the facility.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_booking_p1_confirm.py [asst/_shared/agent.py]
Exit 1 on any FAIL.
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import LEDGER, Message, Need

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC = "agent:t07/booking_centre"
HO = "agent:t07/hospital"
CZ = "citizen:t07/c1"
NOW = "2026-11-23T08:00:00+00:00"
ISS = "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"
EXP = "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=expired"
RULES = "rules\nrule_id=P1 name=prescription_validity validity_agenda_days=2"
SLOTS = (
    "slots page=1/1\n"
    f"slot_id=S2 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue facility={HO} procedure=VIS-FIS free=1"
)
OFFER = (
    f"offer offer_id=of-S2 slot_id=S2 doctor_id=D-h1 facility={HO} procedure=VIS-FIS "
    "agenda_date=2026-11-24 prescription_number=RX1 priority=B"
)
CONFIRM = "confirm offer_id=of-S2 consent_ref=cons-x-booking"
N = [0]
NEED = [500]
FAILS: list[str] = []


def new_need():
    NEED[0] += 1
    return f"N-{NEED[0]:04d}"


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    """Fake city. The Ledger answers ``prescription`` with ``rx`` after ``delay`` seconds (0 = at once)."""

    def __init__(self, first, rows, rx, delay=0.0, slots=SLOTS):
        self.agent_id = BC
        self.inbound = first
        self.rows = [*rows, first]
        self.out: list[tuple[str, str, float]] = []
        self.rx = rx
        self.delay = delay
        self.slots = slots
        self.t0 = time.perf_counter()
        for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_SENT", "_GATES"):
            getattr(brain, name, {}).clear()
        brain._RULES.clear()
        brain._RULES_TS = ""

    def _answer(self, text):
        head = text.split()[0] if text.split() else ""
        if head == "prescription":
            return [self.rx]
        if head == "agenda":
            return [f"agenda agenda_now={NOW}"]
        if head == "world":
            return [self.slots]
        return []

    async def _late(self, text):
        await asyncio.sleep(self.delay)
        for ans in self._answer(text):
            self.rows.append(msg(LEDGER, BC, ans, self.inbound.need_id))

    async def say(self, to, text, *, status=None):
        self.out.append((to, text, time.perf_counter() - self.t0))
        self.rows.append(msg(BC, to, text, self.inbound.need_id))
        if to == LEDGER:
            if self.delay:
                asyncio.get_running_loop().create_task(self._late(text))
            else:
                for ans in self._answer(text):
                    self.rows.append(msg(LEDGER, BC, ans, self.inbound.need_id))
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    def to(self, who):
        return [t for w, t, _ in self.out if w == who]


async def drive(city):
    os.environ["CITY_ROLE"] = "booking_centre"
    try:
        try:
            await brain.main(Need(summary=city.inbound.text, message=city.inbound), city)
        except Exception as exc:
            if type(exc).__name__ != "OutOfScope":
                raise
    finally:
        os.environ.pop("CITY_ROLE", None)
    return city


RESERVED = (
    "slots page=1/1\n"
    f"slot_id=S2 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue facility={HO} procedure=VIS-FIS free=1 urgent_reserve=true\n"
    f"slot_id=S3 doctor_id=D-h1 at=2026-11-25T10:00:00+00:00 day=wed facility={HO} procedure=VIS-FIS free=1"
)


def offered(need, *, rules=False, priority="B"):
    rows = [
        msg(CZ, BC, "prenota prescription_number=RX1 lang=it", need),
        msg(BC, LEDGER, "prescription prescription_number=RX1", need),
        msg(BC, LEDGER, "agenda", need),
        msg(BC, LEDGER, "world procedure=VIS-FIS page=1", need),
        msg(LEDGER, BC, ISS, need),
        msg(LEDGER, BC, f"agenda agenda_now={NOW}", need),
        msg(LEDGER, BC, SLOTS, need),
        msg(BC, CZ, OFFER.replace("priority=B", f"priority={priority}"), need),
    ]
    if rules:
        rows.append(msg(LEDGER, BC, RULES, need))
    return rows


def check(name, ok, c):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)
        for to, t, at in c.out:
            print(f"     > {at:5.1f}s {to[-24:]} : {t[:150]!r}")


def renewal(c):
    return any("prescription_renewal" in t or "medico di base" in t.lower() for t in c.to(CZ))


def forwarded(c):
    return any(t.startswith("confirm") and "prescription_number=RX1" in t for t in c.to(HO))


async def main():
    brain._ADVANCE_BUDGET, brain._PAGE_BUDGET, brain._CONSENT_WAIT = 0.4, 0.2, 0.3

    # a. P1 known and the Ledger already said expired after the rule change, then confirm.
    need = new_need()
    rows = offered(need, rules=True)
    rows += [msg(BC, LEDGER, "prescription prescription_number=RX1", need), msg(LEDGER, BC, EXP, need)]
    c = await drive(City(msg(CZ, BC, CONFIRM, need), rows, EXP))
    check("A rx expired in the case under P1 -> confirm NOT forwarded to the hospital", not c.to(HO), c)
    check("A rx expired -> citizen asked for the renewal", renewal(c), c)

    # b. Only the P1 rules line is new; the Ledger now says expired for RX1.
    need = new_need()
    c = await drive(City(msg(CZ, BC, CONFIRM, need), offered(need, rules=True), EXP))
    check("B P1 in force -> desk re-reads RX1 on the Ledger before forwarding",
          any(t.startswith("prescription prescription_number=RX1") for t in c.to(LEDGER)), c)
    check("B Ledger says expired -> nothing forwarded, renewal asked", not c.to(HO) and renewal(c), c)

    # c. P1 in force, RX1 still issued -> the confirm is forwarded as before.
    need = new_need()
    c = await drive(City(msg(CZ, BC, CONFIRM, need), offered(need, rules=True), ISS))
    check("C P1 in force, rx issued -> confirm forwarded with prescription_number=RX1", forwarded(c), c)

    # d. No rule known -> no extra Ledger read, the confirm is forwarded at once.
    need = new_need()
    c = await drive(City(msg(CZ, BC, CONFIRM, need), offered(need), ISS))
    check("D no rule -> confirm forwarded, no extra prescription read",
          forwarded(c) and not any(t.startswith("prescription") for t in c.to(LEDGER)), c)

    # f. P3: a later world read marks S2 urgent_reserve=true; a B patient confirms S2.
    need = new_need()
    rows = offered(need) + [msg(BC, LEDGER, "world procedure=VIS-FIS page=1", need), msg(LEDGER, BC, RESERVED, need)]
    c = await drive(City(msg(CZ, BC, CONFIRM, need), rows, ISS, slots=RESERVED))
    said = "\n".join(c.to(CZ))
    check("F P3 reserve now, priority B -> confirm NOT forwarded", not c.to(HO), c)
    check("F P3 reserve now -> offer of-S2 withdrawn (cancel) and a new offer without S2",
          "cancel offer_id=of-S2" in said
          and any(ln.startswith("offer") and "slot_id=S3" in ln for ln in said.splitlines())
          and not any(ln.startswith("offer") and "slot_id=S2" in ln for ln in said.splitlines()), c)
    c2 = City(msg(CZ, BC, CONFIRM, need), [*c.rows], ISS, slots=RESERVED)
    c2.rows = [*c.rows, c2.inbound]
    await drive(c2)
    check("F the same confirm again -> still not forwarded, one visible line, no second cancel",
          not c2.to(HO) and len(c2.to(CZ)) == 1 and not any(t.startswith("cancel") for t in c2.to(CZ)), c2)

    # g. Same reserve row, but the offer was for a U patient -> forwarded, described as urgent.
    need = new_need()
    rows = offered(need, priority="U") + [msg(LEDGER, BC, RESERVED, need)]
    c = await drive(City(msg(CZ, BC, CONFIRM, need), rows, ISS))
    check("G P3 reserve, priority U -> confirm forwarded, urgent ask", forwarded(c)
          and any("urgente" in t for t in c.to(HO)), c)

    # h. A B patient is not described as urgent to the facility.
    need = new_need()
    c = await drive(City(msg(CZ, BC, CONFIRM, need), offered(need), ISS))
    check("H priority B -> forwarded with the routine ask, not 'priorità urgente'",
          forwarded(c) and not any("urgente" in t for t in c.to(HO)), c)

    # e/f. Slow Ledger (8 s, 20 s) with real budgets: a visible line or the forward within 3 s,
    # and the turn ends inside the citizen turn budget.
    brain._ADVANCE_BUDGET, brain._PAGE_BUDGET, brain._CONSENT_WAIT = 5.0, 2.0, 3.0
    for delay in (8.0, 20.0):
        need = new_need()
        city = City(msg(CZ, BC, CONFIRM, need), offered(need, rules=True), EXP, delay=delay)
        t0 = time.perf_counter()
        await drive(city)
        took = time.perf_counter() - t0
        first = min((at for w, t, at in city.out if w in (CZ, HO)), default=None)
        check(f"E slow Ledger {delay:.0f}s -> citizen line (or forward) by 3 s, turn {took:.1f}s <= 7 s",
              first is not None and first <= 3.0 and took <= 7.0, city)
        check(f"E slow Ledger {delay:.0f}s -> the holding line promises no booking",
              not any("ricevuta" in t.lower() or "confermata" in t.lower() for t in city.to(CZ)), city)
    print(f"RESULT {'FAIL' if FAILS else 'PASS'} {len(FAILS)} failed")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
