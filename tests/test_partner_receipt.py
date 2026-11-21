"""Partner confirm at the specialist: hold, commit, then booking.receipt to the agent that confirmed.

Personas sobhan_FAC_S07 / sobhan_FAC_S08 (L4 partner confirm, L7 repeat confirm). The partner
gets exactly one receipt after the Ledger commit; a repeat confirm gets the same receipt again
with no second hold or commit; no commit means no receipt (a ghost booking costs -2).
Reads (agenda, world) answer inside the turn; hold and commit answer as a later turn (slow Ledger). World in the city shape "slots page=".
Run: <city python> -I tests/test_partner_receipt.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

os.environ["CITY_ROLE"] = "specialist_care"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

AG, CZ = "agent:t07/specialist", "citizen:t09/c1"
PEER, DESK = "agent:t09/booking_centre", "agent:t07/booking_centre"  # another team, ours
n = [0]


def msg(sender, to, text, need):
    n[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"t{n[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    """In-memory city; each Ledger answer arrives after the turn that asked."""

    def __init__(self, need, world, commit=True):
        for guard in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_VERIFIED"):
            getattr(brain, guard, {}).clear()
        self.agent_id, self.address, self.need, self.world, self.commit = AG, "", need, world, commit
        self.rows, self.out, self.later, self.inbound = [], [], [], None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text, self.need))
        if to != LEDGER:
            return "x"
        head, fields = text.split()[0], brain.fields_of(text)
        if head == "agenda":  # the read answers land in history inside the turn (the offer needs them)
            self.rows.append(msg(LEDGER, AG, "agenda agenda_now=2026-11-22T07:00:00+00:00", self.need))
        elif head == "world":
            self.rows.append(msg(LEDGER, AG, self.world, self.need))
        elif head == "hold":
            self.later.append(f"held hold_id=bk-1 slot_id={fields['slot_id']} fence=1")
        elif head == "commit" and self.commit:
            self.later.append("committed booking_id=bk-1 ledger_ref=c-2 ")
        elif head not in ("commit",):
            self.later.append("ledger: no answer for this operation here")
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def turn(self, message):
        self.rows.append(message)
        self.inbound = message
        await brain.main(Need(summary=message.text, message=message), self)
        while self.later:  # every Ledger answer is a turn of its own
            row = msg(LEDGER, AG, self.later.pop(0), self.need)
            self.rows.append(row)
            self.inbound = row
            await brain.main(Need(summary=row.text, message=row), self)


SLOT = "slot_id=slot-c1"
LIVE = f"slots page=1/1\n{SLOT} doctor_id=D-c1 at=2026-11-22T09:00:00+00:00 facility={AG} procedure=VIS-CARD free=1"
CONFIRM = f"confirm offer_id=of-slot-c1 consent_ref=cons-xr-s08-booking {SLOT} prescription_number=RX-c1 citizen={CZ}"


async def run(world, sender, confirms, commit=True):
    need = f"N-rcpt-{n[0]}"
    city = Fake(need, world, commit)
    await city.turn(msg(sender, AG, "need.request service_type=specialist procedure=VIS-CARD priority=D request_id=xr-s08", need))
    for _ in range(confirms):
        await city.turn(msg(sender, AG, CONFIRM, need))
    sent = [(to, t.split()[0]) for to, t in city.out]
    return city, sent


async def main():
    for label, sender in (("partner", PEER), ("desk", DESK)):
        world = LIVE
        city, sent = await run(world, sender, 1)
        assert sent.count((LEDGER, "hold")) == 1 and sent.count((LEDGER, "commit")) == 1, (label, sent)
        receipts = [(to, t) for to, t in city.out if t.startswith("booking.receipt")]
        assert [to for to, _ in receipts] == [sender], (label, "receipt must go once to the confirming agent", sent)
        assert "booking_id=bk-1" in receipts[0][1] and "ledger_ref=c-2" in receipts[0][1], receipts
        # L7: the same partner confirms again: the same receipt, never a second hold or commit
        city, sent = await run(world, sender, 2)
        assert sent.count((LEDGER, "hold")) == 1 and sent.count((LEDGER, "commit")) == 1, (label, sent)
        receipts = [t.splitlines()[0] for to, t in city.out if to == sender and t.startswith("booking.receipt")]
        assert len(receipts) == 2 and receipts[0] == receipts[1], (label, receipts)
        # no commit: no receipt (a receipt before the commit is a ghost booking)
        city, sent = await run(world, sender, 1, commit=False)
        assert not any(t.startswith("booking.receipt") for _, t in city.out), (label, sent)
    print("OK test_partner_receipt")


asyncio.run(main())
