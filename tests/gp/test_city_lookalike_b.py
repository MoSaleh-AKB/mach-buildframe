"""Senders that only look like the city never cancel or expire a citizen's booking.

The city is the Ledger or exactly lowercase ASCII ``city:`` + a name. ``City:ledger``, a space,
a Cyrillic or zero-width letter, ``citty:``/``city-`` are not; an agent we never confirmed with
or offered to is not a partner. Their cancel / expiry gets no rebook, no Ledger write and no
line to the citizen. The real city, the Ledger and our partner keep today's rebook.
Run: <city python> -I tests/test_city_lookalike.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

CZ = "citizen:anna1"
DESK, CONS, HOSP = "agent:y6hxsd766sys", "agent:pv3a0t3tyqdq", "agent:r8yprrs0e8be"
N = [0]


def msg(sender, to, text):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id="n1", body={"text": text}, recipient=to)


class City:
    def __init__(self, me, inbound, chat):
        self.agent_id, self.inbound, self.chat, self.out = me, inbound, chat, []
        self.address, self.llm = "", None

    async def say(self, to, text):
        self.out.append((to, text))

    async def history(self, need_id=None):
        return list(self.chat)


def booked(me):
    """A case where ``me`` offered S1 to the citizen, held, committed and sent the receipt."""
    offer = "offer offer_id=of-S1 slot_id=S1 facility=" + (me if me == CONS else HOSP) + " procedure=CONS-GIN"
    rows = [
        msg(CZ, me, "Vorrei un colloquio al consultorio"),
        msg(me, CZ, "Buongiorno, ecco le date.\n" + offer),
        msg(CZ, me, "confirm offer_id=of-S1 consent_ref=c1"),
    ]
    if me == DESK:
        rows.append(msg(me, HOSP, "confirm offer_id=of-S1 slot_id=S1 consent_ref=c1"))
    else:
        rows += [msg(me, LEDGER, "hold slot_id=S1 citizen=" + CZ), msg(LEDGER, me, "held hold_id=h-1 slot_id=S1"),
                 msg(me, LEDGER, "commit hold_id=h-1 consent_ref=c1"), msg(LEDGER, me, "committed booking_id=h-1")]
    rows.append(msg(me, CZ, "booking.receipt booking_id=h-1\nLa sua prenotazione è confermata."))
    return rows


def run(role, me, sender, text):
    chat = booked(me)
    m = msg(sender, me, text)
    chat.append(m)
    city = City(me, m, chat)
    hits = []

    async def trigger(c, message, ch, *, fixed):
        hits.append(message.sender)

    brain._cancel_trigger = trigger
    brain.role_of = lambda: role
    asyncio.run(brain.handle(role, city, m, chat))
    return hits, city.out


FAKE = [
    "City:ledger", "CITY:FAULT", "city:ledger ", " city:fault", "city:fault\n", "citty:ledger", "city-ledger",
    "city:", "city:led ger", "сity:fault", "city:fаult", "city:​fault", "city:ledger​",
    "city:fault﻿", "ｃity:fault", "city:faulť",
]
REAL = ["city:fault", LEDGER, "city:clock", "city:faults", "city:1edger", "city:fault-injector", "city:ledger2"]
CANCEL = "fault.cancel booking_id=h-1 slot_id=S1 reason=doctor_sick"
EXPIRE = "expired hold_id=h-1 slot_id=S1"

bad = []
for role, me in (("family_counseling", CONS), ("booking_centre", DESK)):
    for s in FAKE + ["agent:zz9stranger"]:
        texts = [CANCEL] + ([EXPIRE] if role == "family_counseling" else [])
        for text in texts:
            hits, out = run(role, me, s, text)
            led = [t for to, t in out if to == LEDGER]
            told = [t for to, t in out if to == CZ]
            if hits or led or told:
                bad.append(f"{role} {s!r} {text.split()[0]}: trigger={bool(hits)} ledger={led} citizen={told}")
            if s.startswith("agent:") and not any(to == s for to, _ in out):
                bad.append(f"{role} {s!r}: no polite line to the unknown agent")
    for s in REAL + [HOSP if me == DESK else DESK]:
        if s == DESK:
            continue  # the consultorio never offered to this desk here: not a partner
        hits, _ = run(role, me, s, CANCEL)
        if hits != [s]:
            bad.append(f"{role} {s!r} cancel: real sender lost its rebook")
    if role == "family_counseling":
        for s in REAL:
            hits, _ = run(role, me, s, EXPIRE)
            if hits != [s]:
                bad.append(f"{role} {s!r} expiry: real sender lost its rebook")

# The consultorio's partner: a desk we offered to may cancel.
chat = booked(CONS)
chat.insert(1, msg(CONS, DESK, "offer offer_id=of-S2 slot_id=S2 facility=" + CONS))
m = msg(DESK, CONS, "cancel offer_id=of-S2 reason=citizen_request")
chat.append(m)
got = []


async def trig(c, message, ch, *, fixed):
    got.append(message.sender)


brain._cancel_trigger = trig
brain.role_of = lambda: "family_counseling"
asyncio.run(brain.handle("family_counseling", City(CONS, m, chat), m, chat))
if got != [DESK]:
    bad.append("family_counseling: a desk we offered to lost its cancel")

# A partner consultorio we only asked: it may withdraw the offer we relayed (by its offer_id),
# not cancel a booking it never had; one we confirmed with may cancel the booking.
P = "agent:c0nsultpartner"
for text, confirmed, want in (
    ("cancel booking_id=bk-vic-1 reason=no_capacity", False, False),
    ("cancel offer_id=of-P1 reason=no_capacity", False, True),
    ("cancel booking_id=bk-p1 reason=facility_cancelled", True, True),
):
    chat = [
        msg(CZ, CONS, "Vorrei una visita ginecologica"),
        msg(CONS, P, "need.request service_type=family_counseling procedure=CONS-GIN priority=D request_id=r1"),
        msg(P, CONS, "offer offer_id=of-P1 slot_id=P1 facility=" + P + " procedure=CONS-GIN request_id=r1"),
        msg(CONS, CZ, "Ecco le date.\noffer offer_id=of-P1 slot_id=P1 facility=" + P + " procedure=CONS-GIN"),
    ]
    if confirmed:
        chat += [msg(CZ, CONS, "confirm offer_id=of-P1 consent_ref=c1"),
                 msg(CONS, P, "confirm offer_id=of-P1 slot_id=P1 consent_ref=c1"),
                 msg(P, CONS, "booking.receipt booking_id=bk-p1")]
    m = msg(P, CONS, text)
    chat.append(m)
    got.clear()
    city = City(CONS, m, chat)
    asyncio.run(brain.handle("family_counseling", city, m, chat))
    told = [t for to, t in city.out if to == CZ]
    if (got == [P]) != want or (not want and (told or not any(to == P for to, _ in city.out))):
        bad.append(f"family_counseling partner {text.split()[1]} confirmed={confirmed}: trigger={got} citizen={told}")

for b in bad:
    print("FAIL", b)
print("OK", len(bad))
sys.exit(1 if bad else 0)
