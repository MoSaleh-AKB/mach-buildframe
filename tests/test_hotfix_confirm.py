"""Hotfix 21 Nov: a citizen confirm is never left without an answer.

Live journeys 137, 138, 139, 147: the citizen confirmed 16-32 real minutes (= agenda hours) after
the offers, the slot was already past, and the desk sent nothing back. A citizen who resent the
same confirm also got silence.
Run: <city python> -I tests/test_hotfix_confirm.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c147"
NEED = "N-2026-11-02-890904000147"
N = [0]
REAL_NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def msg(sender, to, text, ago_min=0.0):
    N[0] += 1
    return Message(
        type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=NEED, body={"text": text},
        recipient=to, sent_at=iso(REAL_NOW - timedelta(minutes=ago_min)),
    )


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows, inbound):
        self.rows, self.out, self.inbound = list(rows), [], inbound

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


OFFER = (
    "offer offer_id=of-W01QQQ-asst-centro-h1-20261117-1430 slot_id=W01QQQ-asst-centro-h1-20261117-1430 "
    f"doctor_id=asst-centro-h1 facility={HO} procedure=VIS-FIS agenda_date=2026-11-17T14:30:00.000Z "
    "agenda_day=tue expiry=2026-11-17T14:30:00.000Z"
)
CONFIRM = (
    "confirm consent_ref=cons-N-2026-11-02-890904000147-booking "
    "offer_id=of-W01QQQ-asst-centro-h1-20261117-1430"
)


def base_rows(agenda_now):
    return [
        msg(CZ, BC, "need.request area={'district':_6} lang=en prescription_number=rx-147 back pain", 33),
        msg(LEDGER, BC, "prescription prescription_number=rx-147 procedure=VIS-FIS priority=B", 33),
        msg(LEDGER, BC, f"agenda agenda_now={agenda_now}", 32.5),
        msg(BC, CZ, "Ho trovato alcune disponibilità.\n" + OFFER, 32),
    ]


async def run_case(rows, inbound):
    called = []

    async def fake_advance(city, message, chat, *, fixed, replacing):
        called.append(replacing)
        await city.say(message.sender, "offer offer_id=of-NEW slot_id=NEW agenda_date=2026-11-18T10:00:00.000Z")

    real = brain._advance
    brain._advance = fake_advance
    try:
        f = Fake(rows, inbound)
        await brain.handle("booking_centre", f, inbound, list(rows))
    finally:
        brain._advance = real
    return f, called


def to_citizen(f):
    return "\n".join(t for to, t in f.out if to == CZ)


async def main():
    fails = []

    # 1. Past slot: covered by tests/test_confirm_never_silent.py (booking-2-3), one message.

    # 2. Same confirm, slot still ahead: forwarded to the facility, no apology.
    N[0] = 100
    rows = base_rows("2026-11-17T08:00:00.000Z")
    rows[2] = msg(LEDGER, BC, "agenda agenda_now=2026-11-17T08:00:00.000Z", 1)
    inbound = msg(CZ, BC, CONFIRM)
    f, called = await run_case(rows + [inbound], inbound)
    forwarded = any(to == HO and t.startswith("confirm") for to, t in f.out)
    if not forwarded:
        fails.append("future slot: confirm not forwarded to the facility")
    if called:
        fails.append("future slot: re-offer called on a valid confirm")
    print("case future slot -> forwarded", forwarded)

    # 3. Resent confirm after it was already forwarded: an answer, never silence.
    N[0] = 200
    rows = base_rows("2026-11-17T08:00:00.000Z")
    rows[2] = msg(LEDGER, BC, "agenda agenda_now=2026-11-17T08:00:00.000Z", 1)
    first = msg(CZ, BC, CONFIRM, 0.5)
    fwd = msg(BC, HO, "confirm offer_id=of-W01QQQ-asst-centro-h1-20261117-1430 slot_id=W01QQQ-asst-centro-h1-20261117-1430 "
              f"citizen={CZ} consent_ref=cons-N-2026-11-02-890904000147-booking facility={HO}", 0.4)
    again = msg(CZ, BC, CONFIRM)
    f, called = await run_case(rows + [first, fwd, again], again)
    text = to_citizen(f)
    if not text.strip():
        fails.append("resent confirm: citizen got no answer")
    print("case resent confirm ->", repr(text[:160]))

    if fails:
        print("FAILED:", "; ".join(fails))
        sys.exit(1)
    print("ALL PASS test_hotfix_confirm")


asyncio.run(main())
