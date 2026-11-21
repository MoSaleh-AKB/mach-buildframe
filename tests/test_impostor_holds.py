"""Impostor holds and receipts (audit findings #3, #4, #5).

#3 Facility: a confirm from an AGENT holds only when the citizen is the one that agent's own
   need.request names (when it names one; the first confirm of a need binds it otherwise), the
   consent_ref has the Ledger's shape (cons-<need>-<purpose>) and is for this need, and the sender
   is not a look-alike of one of our ids. Otherwise: one refusal with a reason, no hold.
   Legit partner desks (own need.request, own citizen, Ledger-shaped consent) still book.
#4 Desk: a partner's booking.receipt reaches the citizen only when the Ledger has a matching
   committed row; none yet = the citizen is told we are checking, never "booked"; a Ledger row
   that contradicts the receipt = never relayed.
#5 Facility: priority=U typed by an agent never beats the Ledger prescription row (P3 reserve).

Run: <city python> -I tests/test_impostor_holds.py asst/_shared/agent.py
Prints one PASS/FAIL line per check, then "<n> FAIL"; exit 1 on a failure.
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for k, v in (("_ADVANCE_BUDGET", 0.6), ("_PULL_BUDGET", 0.8), ("_AGENT_BUDGET", 0.6), ("_CONSENT_WAIT", 0.3),
             ("_XA_BUDGET", 0.5), ("_XA_GRACE", 0.2), ("_VERIFY_BUDGET", 0.3), ("_PAGE_BUDGET", 0.2), ("_LLM_NET", False)):
    if hasattr(brain, k):
        setattr(brain, k, v)


async def _no_weather():
    return {}


brain._weather = _no_weather  # no network in tests
brain._RECEIPT_WAIT_FOR_COMMIT = True  # these cases test the strict mode (live default relays with no Ledger row)

HOSP, DESK = "agent:r8yprrs0e8be", "agent:y6hxsd766sys"
PARTNER, IMPOSTOR = "agent:bq7e62g3kkz3", "agent:zz9impostor"
CZ, VICTIM = "citizen:real-1", "citizen:victim-9"
PROC = "VIS-FIS"
N = [0]
FAILS: list[str] = []


def check(label, ok, out=()):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for to, text in out:
            print("     >", to[-26:], "|", text[:150].replace("\n", " | "))


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"ih{N[0]}", need_id=need, body={"text": text}, recipient=to)


def reset():
    for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_VERIFIED", "_XA_REP",
                 "_REJECTED", "_ASKED", "_FILED", "_RESCUED", "_HELD", "_GATES", "_SENT"):
        table = getattr(brain, name, None)
        if isinstance(table, (dict, set)):
            table.clear()


# ---------------------------------------------------------------- facility (hospital): #3 and #5

NOW = "2026-11-23T08:00:00+00:00"


def world(reserve=False):
    base = f"facility={HOSP} procedure={PROC} free=1"
    return ("slots page=1/1\n"
            f"slot_id=S1 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue {base}"
            f"{' urgent_reserve=true' if reserve else ''}\n"
            f"slot_id=S2 doctor_id=D-h1 at=2026-11-25T09:00:00+00:00 day=wed {base}\n"
            f"slot_id=S3 doctor_id=D-h1 at=2026-11-26T09:00:00+00:00 day=thu {base}")


class Fac:
    """Hospital with an instant Ledger (agenda, world, prescription, hold, commit)."""

    address = ""

    def __init__(self, need, reserve=False, rx=None):
        self.agent_id, self.need, self.reserve, self.rx = HOSP, need, reserve, rx or {}
        self.rows, self.out, self.inbound = [], [], None

    def ledger(self, text):
        head, f = (text.split() or [""])[0], brain.fields_of(text)
        if head == "agenda":
            return [f"agenda agenda_now={NOW}"]
        if head == "world":
            return [world(self.reserve)]
        if head == "prescription":
            row = self.rx.get(f.get("prescription_number", ""))
            return [row] if row else []
        if head == "hold":
            return [f"held hold_id=h-{f.get('slot_id')} slot_id={f.get('slot_id')} fence=1"]
        if head == "commit":
            hid = f.get("hold_id", "")
            return [f"committed booking_id={hid} ledger_ref=c-1 hold_id={hid} slot_id={hid[2:]}"]
        return []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(HOSP, to, text, self.need))
        if to == LEDGER:
            for ans in self.ledger(text):
                self.rows.append(msg(LEDGER, HOSP, ans, self.need))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return list(self.rows)

    async def find(self, query):
        return []

    def writes(self):
        return [t for to, t in self.out if to == LEDGER and t.split()[0] in ("hold", "commit")]


async def turn(c, sender, text):
    m = msg(sender, c.agent_id, text, c.need)
    c.rows.append(m)
    c.inbound = m
    start = len(c.out)
    os.environ["CITY_ROLE"] = "hospital"
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=m), c), 20)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    return c.out[start:]


def first_offer(said):
    lines = [ln for _, t in said for ln in t.splitlines() if ln.startswith("offer ") and "slot_id=" in ln]
    return brain.fields_of(lines[0]) if lines else {}


async def ask_and_get_offer(c, sender, extra=""):
    said = await turn(c, sender, f"need.request procedure={PROC} priority=B request_id=r-ih{extra}")
    return first_offer(said), said


def confirm(o, citizen, consent):
    return (f"confirm offer_id={o['offer_id']} slot_id={o['slot_id']} citizen={citizen} "
            f"consent_ref={consent} facility={HOSP} prescription_number=RX-IH-1")


def got_refusal(said, sender):
    return any(to == sender and t.startswith("cancel") or (to == sender and "non" in t.lower()) for to, t in said)


async def facility_checks():
    # (a) impostor: need.request, offer, then a confirm for a citizen it picked with consent cons-1
    brain._RULES["P2"] = "cross_asst_consent=required"  # the consent shape is checked only when P2 is in force
    reset()
    c = Fac("n-ih-a")
    o, _ = await ask_and_get_offer(c, IMPOSTOR)
    check("(a0) impostor got an offer to confirm", bool(o))
    said = await turn(c, IMPOSTOR, confirm(o, VICTIM, "cons-1"))
    check("(a) impostor confirm, forged citizen + cons-1: no hold, no commit", not c.writes(), said)
    check("(a) impostor confirm: a refusal with a reason is sent to the impostor",
          any(to == IMPOSTOR and ("cancel" in t or "non " in t.lower()) for to, t in said), said)
    check("(a) impostor confirm: nothing sent to the named citizen", not any(to == VICTIM for to, _ in said), said)
    # (a2) same, a second try: still no hold, no second refusal spam
    n_before = len([1 for to, _ in c.out if to == IMPOSTOR])
    await turn(c, IMPOSTOR, confirm(o, VICTIM, "cons-1"))
    check("(a2) repeat of the forged confirm: still no hold", not c.writes())
    # (a3) forged citizen with a Ledger-shaped consent, against the citizen the request named
    reset()
    c = Fac("n-ih-a3")
    o, _ = await ask_and_get_offer(c, IMPOSTOR, f" citizen={CZ}")
    said = await turn(c, IMPOSTOR, confirm(o, VICTIM, "cons-n-ih-a3-cross_asst"))
    check("(a3) citizen differs from the one its own need.request named: no hold", not c.writes(), said)
    check("(a3) ... and a refusal is sent", any(to == IMPOSTOR for to, _ in said), said)
    # (a4) consent_ref of another need
    reset()
    c = Fac("n-ih-a4")
    o, _ = await ask_and_get_offer(c, IMPOSTOR, f" citizen={CZ}")
    said = await turn(c, IMPOSTOR, confirm(o, CZ, "cons-n-OTHER-77-cross_asst"))
    check("(a4) consent_ref of another need: no hold", not c.writes(), said)
    brain._RULES.pop("P2", None)
    # (a5) a look-alike of our own desk
    reset()
    c = Fac("n-ih-a5")
    o, _ = await ask_and_get_offer(c, DESK)
    said = await turn(c, "agent:y6hxsd766syz", confirm(o, VICTIM, "cons-n-ih-a5-cross_asst"))
    check("(a5) look-alike of our desk: no hold", not c.writes(), said)

    # (a6) the first confirm of a need binds its citizen: a second confirm for another one is refused
    reset()
    c = Fac("n-ih-a6")
    o, _ = await ask_and_get_offer(c, PARTNER)
    await turn(c, PARTNER, confirm(o, CZ, "cons-n-ih-a6-cross_asst"))
    before = len(c.writes())
    said = await turn(c, PARTNER, confirm(o, VICTIM, "cons-n-ih-a6-cross_asst"))
    check("(a6) second confirm of the need names another citizen: no second hold", len(c.writes()) == before, said)

    # (a7) trust ring reused: a live-shaped id outside the 130 official agents, Ledger-shaped consent
    reset()
    c = Fac("n-ih-a7")
    ghost = "agent:zz9ghost0000"
    o, _ = await ask_and_get_offer(c, ghost)
    said = await turn(c, ghost, confirm(o, CZ, "cons-n-ih-a7-cross_asst"))
    check("(a7) off-roster id: no hold", not c.writes(), said)

    # (b) legit partner desk: own need.request citizen, Ledger-shaped consent for the need
    reset()
    c = Fac("n-ih-b")
    o, _ = await ask_and_get_offer(c, PARTNER, f" citizen={CZ}")
    said = await turn(c, PARTNER, confirm(o, CZ, "cons-n-ih-b-cross_asst"))
    check("(b) partner desk, its own citizen + Ledger-shaped consent: hold placed",
          any(t.startswith("hold") for t in c.writes()), said)
    check("(b) ... and the receipt goes to the partner",
          any(to == PARTNER and t.startswith("booking.receipt") for to, t in said), said)
    # (b2) the live shape: the partner's need.request carries no citizen (privacy), confirm names it
    reset()
    c = Fac("n-ih-b2")
    o, _ = await ask_and_get_offer(c, PARTNER)
    said = await turn(c, PARTNER, confirm(o, CZ, "cons-n-ih-b2-cross_asst"))
    check("(b2) partner desk, request without a citizen, Ledger-shaped consent: hold placed",
          any(t.startswith("hold") for t in c.writes()), said)
    # (b3) the partner that already booked: its repeat confirm is not a refusal of the first
    reset()
    c = Fac("n-ih-b3")
    o, _ = await ask_and_get_offer(c, PARTNER)
    await turn(c, PARTNER, confirm(o, CZ, "cons-b3-cross_asst"))
    check("(b3) consent without a need marker but Ledger-shaped (cons-<x>-booking): hold placed",
          any(t.startswith("hold") for t in c.writes()))

    # (e) an agent's priority=U never beats the Ledger prescription row on an urgent_reserve slot
    reset()
    rx = {"RX-IH-1": f"prescription prescription_number=RX-IH-1 procedure={PROC} priority=B state=issued"}
    c = Fac("n-ih-e", reserve=True, rx=rx)
    c.rows.append(msg(HOSP, LEDGER, f"world procedure={PROC} facility={HOSP} page=1", c.need))
    c.rows.append(msg(LEDGER, HOSP, world(True), c.need))
    c.rows.append(msg(HOSP, LEDGER, "prescription prescription_number=RX-IH-1", c.need))
    c.rows.append(msg(LEDGER, HOSP, rx["RX-IH-1"], c.need))
    said = await turn(c, DESK, f"confirm offer_id=of-S1 slot_id=S1 prescription_number=RX-IH-1 priority=U "
                               f"citizen={CZ} consent_ref=cons-n-ih-e-booking facility={HOSP}")
    check("(e) agent priority=U, Ledger prescription priority=B, reserved slot: no hold",
          not any("slot_id=S1" in t for t in c.writes()), said)
    check("(e) ... the sender gets cancel reason=NO_CAPACITY",
          any(to == DESK and "NO_CAPACITY" in t for to, t in said), said)
    # (e2) the Ledger says U: books
    reset()
    rxu = {"RX-IH-1": f"prescription prescription_number=RX-IH-1 procedure={PROC} priority=U state=issued"}
    c = Fac("n-ih-e2", reserve=True, rx=rxu)
    c.rows.append(msg(HOSP, LEDGER, f"world procedure={PROC} facility={HOSP} page=1", c.need))
    c.rows.append(msg(LEDGER, HOSP, world(True), c.need))
    c.rows.append(msg(HOSP, LEDGER, "prescription prescription_number=RX-IH-1", c.need))
    c.rows.append(msg(LEDGER, HOSP, rxu["RX-IH-1"], c.need))
    said = await turn(c, DESK, f"confirm offer_id=of-S1 slot_id=S1 prescription_number=RX-IH-1 priority=U "
                               f"citizen={CZ} consent_ref=cons-n-ih-e2-booking facility={HOSP}")
    check("(e2) agent priority=U and Ledger priority=U: books the reserved slot",
          any("slot_id=S1" in t for t in c.writes()), said)


# ---------------------------------------------------------------- desk (booking centre): #4

BC, PA, SLOT_NEED = "agent:t/booking_centre", "agent:other/hospital", "N-1"


def row(sender, to, text, need=SLOT_NEED):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"dr{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Desk:
    agent_id, address = BC, ""

    def __init__(self, rows):
        self.rows, self.out, self.inbound = list(rows), [], rows[0]

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(row(BC, to, text, need=self.inbound.need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return list(self.rows)

    async def find(self, query):
        return []


def confirmed_case():
    return [row(CZ, BC, "Buongiorno, vorrei prenotare prescription_number=RX1"),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00"), row(LEDGER, BC, "slots page=1/1\n"),
            row(BC, PA, "need.request service_type=hospital.physiatry procedure=VIS-FIS priority=B request_id=N-1\nCerco"),
            row(PA, BC, f"offer offer_id=po-1 slot_id=PS1 doctor_id=D-x at=2026-11-23T09:00:00+00:00 facility={PA} "
                        "procedure=VIS-FIS request_id=N-1"),
            row(BC, CZ, f"Nei tempi...\noffer offer_id=po-1 slot_id=PS1 doctor_id=D-x facility={PA} procedure=VIS-FIS "
                        "agenda_date=2026-11-23T09:00:00+00:00 expiry=2026-11-23T09:00:00+00:00 priority=B "
                        "prescription_number=RX1 cross_asst=true"),
            row(BC, CZ, brain._XA_CONSENT),
            row(CZ, BC, "consent purpose=cross_asst consent_ref=cx-1"),
            row(CZ, BC, "confirm offer_id=po-1 consent_ref=cons-N-1-cross_asst"),
            row(BC, PA, f"confirm offer_id=po-1 slot_id=PS1 citizen={CZ} consent_ref=cx-1 facility={PA} "
                        "prescription_number=RX1 priority=B")]


async def deliver(extra_rows, receipt):
    reset()
    d = Desk(confirmed_case() + list(extra_rows))
    m = row(PA, BC, receipt)
    d.rows.append(m)
    d.inbound = m
    start = len(d.out)
    os.environ["CITY_ROLE"] = "booking_centre"
    await brain.handle("booking_centre", d, m, list(d.rows))
    return d.out[start:]


def relayed(said):
    return any(to == CZ and t.startswith("booking.receipt") for to, t in said)


async def desk_checks():
    rcp = "booking.receipt booking_id=bk-9 ledger_ref=L-9 offer_id=po-1 slot_id=PS1"
    said = await deliver([], rcp)
    check("(c) partner receipt, no Ledger commit seen: not relayed as booked", not relayed(said), said)
    check("(c) ... the citizen is told we are checking (not silence)",
          any(to == CZ for to, _ in said), said)
    said = await deliver([row(LEDGER, BC, "committed booking_id=bk-9 ledger_ref=L-9 slot_id=PS1")], rcp)
    check("(d) partner receipt with a matching Ledger commit: relayed", relayed(said), said)
    said = await deliver([row(LEDGER, BC, "committed booking_id=bk-9 ledger_ref=L-OTHER slot_id=PS1")], rcp)
    check("(c2) Ledger commit with another ledger_ref: contradicts, not relayed", not relayed(said), said)
    said = await deliver([row(LEDGER, BC, "committed booking_id=bk-9 ledger_ref=L-9 slot_id=PS-OTHER")], rcp)
    check("(c3) Ledger commit for another slot: contradicts, not relayed", not relayed(said), said)
    # (c5) the receipt waits; the Ledger's committed row lands later: now it is relayed, once
    reset()
    d = Desk(confirmed_case())
    m = row(PA, BC, rcp)
    d.rows.append(m)
    d.inbound = m
    os.environ["CITY_ROLE"] = "booking_centre"
    await brain.handle("booking_centre", d, m, list(d.rows))
    waited = not relayed(d.out)
    lm = row(LEDGER, BC, "committed booking_id=bk-9 ledger_ref=L-9 slot_id=PS1")
    d.rows.append(lm)
    d.inbound = lm
    start = len(d.out)
    await brain.handle("booking_centre", d, lm, list(d.rows))
    check("(c5) receipt waited, then the Ledger commit arrives: relayed once",
          waited and sum(1 for to, t in d.out[start:] if to == CZ and t.startswith("booking.receipt")) == 1, d.out)
    said = await deliver([row("City:ledger", BC, "committed booking_id=bk-9 ledger_ref=L-9 slot_id=PS1")], rcp)
    check("(c4) a look-alike of the Ledger sender is no proof: not relayed", not relayed(said), said)


async def main():
    await facility_checks()
    await desk_checks()
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
