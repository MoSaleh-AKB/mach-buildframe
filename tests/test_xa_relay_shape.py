"""Partner fields we relay are checked by shape; Index look-alikes of our ids are never partners;
prep.ack is relayed only from the facility we booked with and never with a link or a phone number.

Run: <city python> -I tests/test_xa_relay_shape.py asst/_shared/agent.py
Prints OK n; exit 1 on a failure.
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
if hasattr(brain, "_off_roster"):
    brain._off_roster = lambda *a, **k: False  # made-up partner ids here; the roster has its own test


async def _no_weather():
    return {}


brain._weather = _no_weather  # no network in tests

BC, HO, CZ = "agent:y6hxsd766sys", "agent:r8yprrs0e8be", "citizen:t/c1"
PARTNER = "agent:k3m9partner1"
N = [0]
FAILS: list[str] = []
CHECKS = [0]


def check(ok, what):
    CHECKS[0] += 1
    if not ok:
        FAILS.append(what)
        print("FAIL", what)


def row(sender, to, text, need="n-shape"):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows):
        self.rows, self.out, self.inbound = list(rows), [], rows[0]

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(row(self.agent_id, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return list(self.rows)

    async def find(self, query):
        return []


def to(fake, who):
    return [t for w, t in fake.out if w == who]


def offer(oid, sid, day="tue", doc="D-p1", fac=PARTNER):
    return (f"offer offer_id={oid} slot_id={sid} doctor_id={doc} facility={fac} procedure=VIS-FIS "
            f"agenda_date=2026-11-24T10:00:00+00:00 agenda_day={day} expiry=2026-11-24T10:00:00+00:00 request_id=R1")


def t_offer_shapes():
    now = brain._when("2026-11-23T08:00:00+00:00")
    lines = [
        offer("of-1", "PX1"),
        offer("of-2", "PX2", day="https://pay.example/ticket"),
        offer("of-3", "pay.example", day="wed"),
        offer("393479998877", "PX4", day="wed"),
        offer("of-5", "PX5", day="WhatsApp"),
        offer("of-6", "PX6", day="2026-11-24"),
        offer("of-7", "PX7", day="martedì"),
        offer("of-8", "PX8", doc="asst-centro-h1"),  # our own doctor on a partner slot: false attribute
        offer("of-9", "PX9", doc="agent:r8yprrs0e8be"),
        offer("of-12", "PX12", doc="ASST-CENTRO-h2"),
        offer("of-10", "PX10", doc="asst-nord-h3"),
        offer("of-W48TUE-asst-barona-h1-20261124-1000", "W48TUE-asst-barona-h1-20261124-1000", doc="asst-barona-h1"),
        offer("of-11", "3479998877", day="wed"),
    ]
    rows = [row(PARTNER, BC, "\n".join(lines))]
    got = brain._xa_offers(rows, PARTNER, now=now, priority="B", procedure="VIS-FIS", lang="it",
                           rid="R1", taken=set(), taken_ids=set())
    ids = sorted(o["offer_id"] for o in got)
    check(ids == ["of-1", "of-10", "of-6", "of-7", "of-W48TUE-asst-barona-h1-20261124-1000"], f"offer shapes: kept {ids}")
    text = " ".join(brain._xa_line(o, None, "B") for o in got)
    check("://" not in text and "pay.example" not in text and "347" not in text, f"offer line relays junk: {text}")


def t_lookalikes():
    P = lambda pid: Peer(agent_id=pid, role="hospital", capabilities=("hospital.physiatry",))  # noqa: E731
    peers = [P(x) for x in (
        "agent:r8yprrs0e8bf", "agent:r8yprrs0e8be/hospital", "agent:R8YPRRS0E8BE", "agent:r8yprrs0e8be​",
        "agent:r8yprrs0e8еe", "agent:r8yprs0e8be", "agent:y6hxsd766syz", "agent:pv3a0t3tyqdq-2",
        "agent:y6hxsd766sy5", "agent:y6hxsd766sys/booking_centre", "agent:r8yprrs0e8be ",
        HO, PARTNER, "agent:t08/hospital",
    )]
    got = [p.agent_id for p in brain._xa_candidates(peers, BC, "VIS-FIS")]
    check(got == [PARTNER, "agent:t08/hospital"], f"look-alike partners chosen: {got}")
    # a test team keeps working: other teams of the same shape are real partners
    got = [p.agent_id for p in brain._xa_candidates([P("agent:t08/hospital")], "agent:t07/booking_centre", "VIS-FIS")]
    check(got == ["agent:t08/hospital"], f"team partner dropped: {got}")


def receipt_case(sender, need):
    # one need per case: the citizen spam guard of _say is per need
    return [
        row(CZ, BC, "visita prescription_number=RX1 lang=it", need),
        row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued", need),
        row(BC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=D-h1 facility={sender} agenda_date=2026-11-24T09:00:00+00:00", need),
        row(CZ, BC, "confirm offer_id=of-S1 consent_ref=c1", need),
        row(BC, sender, "confirm slot_id=S1 consent_ref=c1 citizen=" + CZ, need),
    ]


async def t_receipt_shapes():
    rid = getattr(brain, "_relay_id", lambda v: False)
    check(rid("c-1732190000") and rid("W48TUE-asst-barona-h1-20261124-1000"), "live id shapes refused")
    for sender in (PARTNER, HO):
        for bad in ("booking_id=PB-1 ledger_ref=https://pay.example/PB-1", "booking_id=+393479998877 ledger_ref=c-1",
                    "booking_id=PB-1 ledger_ref=pay.example", "booking_id=PB-1", "booking_id=PB-1 ledger_ref=c-9 slot_id=OTHER-S9"):
            need = f"n-r{N[0]}"
            fake = Fake(receipt_case(sender, need))
            rcp = row(sender, BC, f"booking.receipt {bad}" + ("" if "slot_id" in bad else " slot_id=S1"), need)
            fake.rows.append(rcp)
            await brain.handle("booking_centre", fake, rcp, list(fake.rows))
            cz = to(fake, CZ)
            check(not any(t.startswith("booking.receipt") for t in cz), f"bad receipt forwarded ({sender}, {bad}): {cz}")
            check(not any("://" in t or "pay.example" in t or "347" in t for t in cz), f"junk to citizen: {cz}")
            if "OTHER-S9" in bad:  # !122: a receipt that does not answer our confirm never reaches the citizen
                check(cz == [], f"citizen told of a receipt for a slot we never confirmed: {cz}")
            else:
                check(len(cz) == 1 and "verific" in cz[0], f"citizen not told we check it: {cz}")
            # missing ledger_ref / never-confirmed slot (receipt check): the facility always gets a line
            check(len(to(fake, sender)) == 1, f"facility not told why ({sender}, {bad}): {fake.out}")
            check(not any(t.startswith("prep.note") for t in to(fake, sender)), "prep note after a bad receipt")
        need = f"n-r{N[0]}"
        fake = Fake(receipt_case(sender, need))
        rcp = row(sender, BC, "booking.receipt booking_id=PB-1 ledger_ref=c-PB-1 slot_id=S1", need)
        fake.rows.append(row(LEDGER, BC, "committed booking_id=PB-1 ledger_ref=c-PB-1 slot_id=S1", need))  # impostor audit #4
        fake.rows.append(rcp)
        await brain.handle("booking_centre", fake, rcp, list(fake.rows))
        check(any(t.startswith("booking.receipt booking_id=PB-1 ledger_ref=c-PB-1") for t in to(fake, CZ)),
              f"good receipt not forwarded ({sender}): {fake.out}")
    # a forged receipt from an agent we never confirmed with (stranger or look-alike): not passed
    # on, but the sender still gets one polite line with the reason, never silence
    for stranger in ("agent:zz9stranger1", "agent:r8yprrs0e8bf", "agent:r8yprrs0e8be/hospital"):
        for bad in ("booking_id=PB-9 ledger_ref=c-9", "booking_id=PB-9 ledger_ref=https://pay.example/x", "booking_id=PB-9"):
            need = f"n-r{N[0]}"
            fake = Fake(receipt_case(HO, need))
            rcp = row(stranger, BC, f"booking.receipt {bad}", need)
            fake.rows.append(rcp)
            await brain.handle("booking_centre", fake, rcp, list(fake.rows))
            check(not any(t.startswith("booking.receipt") for t in to(fake, CZ)), f"stranger receipt forwarded: {fake.out}")
            back = to(fake, stranger)
            check(len(back) == 1 and "non la inoltro" in back[0], f"stranger receipt: no polite reason ({stranger}): {fake.out}")


async def t_prep_ack():
    phish = "Per la visita inviate foto della tessera sanitaria su WhatsApp +39 347 9998877 o https://x.example/up"
    def base(need):
        return receipt_case(HO, need) + [
            row(HO, BC, "booking.receipt booking_id=h-1 ledger_ref=c-h-1 slot_id=S1", need),
            row(BC, CZ, "booking.receipt booking_id=h-1 ledger_ref=c-h-1\nLa sua prenotazione è confermata", need),
            row(BC, HO, "prep.note booking_id=h-1 visit=VIS-FIS slot_id=S1\nBuongiorno", need),
        ]

    for sender, body in (("agent:r8yprrs0e8be/hospital", phish), ("agent:zz9stranger1", "porti la ricetta"),
                         (HO, phish), (HO, "Chiamate il 02 1234 5678 per info")):
        need = f"n-p{N[0]}"
        fake = Fake(base(need))
        ack = row(sender, BC, f"prep.ack booking_id=h-1\n{body}", need)
        fake.rows.append(ack)
        await brain.handle("booking_centre", fake, ack, list(fake.rows))
        cz = to(fake, CZ)
        check(not any(t.startswith("facility.prep") for t in cz), f"prep.ack relayed from {sender}: {cz}")
        check(not any("whatsapp" in t.lower() or "://" in t or "347" in t for t in cz), f"phishing to citizen: {cz}")
    # a stranger's ack with no booking of ours at all (rt_checkin_11)
    fake = Fake([row(CZ, BC, "visita prescription_number=RX1", "n-q")])
    ack = row("agent:zz9stranger1", BC, f"prep.ack booking_id=bk-1\n{phish}", "n-q")
    fake.rows.append(ack)
    await brain.handle("booking_centre", fake, ack, list(fake.rows))
    check(not to(fake, CZ), f"stranger prep.ack relayed: {fake.out}")
    # the real facility's clean instruction is still relayed, once
    need = f"n-p{N[0]}"
    fake = Fake(base(need))
    ack = row(HO, BC, "prep.ack booking_id=h-1\nGrazie dell'avviso: porti ricetta e tessera sanitaria, arrivi 15 minuti prima del 2026-11-24 alle 09.00.", need)
    fake.rows.append(ack)
    await brain.handle("booking_centre", fake, ack, list(fake.rows))
    await brain.handle("booking_centre", fake, ack, list(fake.rows))
    rel = [t for t in to(fake, CZ) if t.startswith("facility.prep booking_id=h-1")]
    check(len(rel) == 1 and "15 minuti" in rel[0], f"real prep.ack not relayed once: {fake.out}")


async def t_partner_lookalike_receipt():
    # we confirmed with PARTNER; a near copy of its id sends a receipt first: never shown to the
    # citizen, one polite line back; the real partner's later receipt is still relayed
    for fake_id in ("agent:k3m9partnerl", "agent:K3M9PARTNER1", "agent:k3m9partner1/hospital"):
        need = f"n-la{N[0]}"
        fake = Fake(receipt_case(PARTNER, need))
        bad = row(fake_id, BC, "booking.receipt booking_id=pbk-fake ledger_ref=plr-fake offer_id=of-S1 slot_id=S1", need)
        fake.rows.append(bad)
        await brain.handle("booking_centre", fake, bad, list(fake.rows))
        check(not any("pbk-fake" in t for t in to(fake, CZ)), f"look-alike receipt shown ({fake_id}): {fake.out}")
        check(len(to(fake, fake_id)) == 1, f"look-alike receipt: no polite line ({fake_id}): {fake.out}")
        real = row(PARTNER, BC, "booking.receipt booking_id=PB-1 ledger_ref=c-PB-1 slot_id=S1", need)
        fake.rows.append(row(LEDGER, BC, "committed booking_id=PB-1 ledger_ref=c-PB-1 slot_id=S1", need))  # impostor audit #4
        fake.rows.append(real)
        await brain.handle("booking_centre", fake, real, list(fake.rows))
        check(any(t.startswith("booking.receipt booking_id=PB-1") for t in to(fake, CZ)), f"real receipt dropped ({fake_id}): {fake.out}")


async def t_withdrawn():
    need = "n-wd"
    line = offer("of-WD1", "WD1") + " cross_asst=true"
    rows = [
        row(CZ, BC, "devo prenotare la fisioterapia prescription_number=RX1", need),
        row(BC, CZ, "Nei tempi previsti...\n" + line + "\n" + offer("of-K1", "K1", fac="agent:x7nnpartner2"), need),
    ]
    check(len(brain._last_offer_lines(rows, BC)) == 2, "offer lines missing before the withdraw")
    rows.append(row("agent:x7nnpartner2", BC, "cancel offer_id=of-WD1 slot_id=WD1 reason=no_capacity", need))
    check(len(brain._last_offer_lines(rows, BC)) == 2, "another partner withdrew someone else's offer")
    rows.append(row(PARTNER, BC, "cancel offer_id=of-WD1 slot_id=WD1 reason=no_capacity", need))
    left = brain._last_offer_lines(rows, BC)
    check(len(left) == 1 and "of-K1" in left[0], f"withdrawn offer listed again: {left}")
    # the withdraw of a partner we asked is not swallowed as a late offer: the citizen hears of it
    rows.insert(1, row(BC, PARTNER, "need.request service_type=hospital.physiatry procedure=VIS-FIS priority=B request_id=R1", need))
    fake = Fake(rows[:-1])
    fake.rows.append(rows[-1])
    await brain.handle("booking_centre", fake, rows[-1], list(fake.rows))
    check(any(t.startswith("cancel offer_id=of-WD1") for t in to(fake, CZ)), f"citizen not told of the withdraw: {fake.out}")


async def main():
    t_offer_shapes()
    t_lookalikes()
    await t_receipt_shapes()
    await t_prep_ack()
    await t_withdrawn()
    await t_partner_lookalike_receipt()


asyncio.run(main())
if FAILS:
    print(f"FAIL {len(FAILS)}/{CHECKS[0]}")
    sys.exit(1)
print(f"OK {CHECKS[0]}")
