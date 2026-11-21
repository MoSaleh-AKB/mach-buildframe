"""S13/S16 at the specialist: the Ledger refuses the hold or the commit, then the desk repeats the confirm.

The desk gets cancel offer_id= reason= and an alternative, never "receipt on its way" (no
receipt can come after a refusal), the refused op is never sent again, and the repeat confirm
gets a visible answer (silence fails). A repeat confirm while the commit is still in flight
(slow Ledger) gets a status line of its own, and the booking ends with one hold, one commit and
one receipt. A re-confirm that fixes the refused reason (a renewed prescription_number) is a
new attempt: a new hold, then the receipt. Every Ledger answer is also pushed as its own turn.
Run: <city python> -I tests/test_refused_repeat_confirm.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "specialist_care"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

SP, BC = "agent:t07/specialist", "agent:t07/booking_centre"
PROMISE = "ricevuta appena"  # _BOOKING_SLOW_AG: "Vi mando la ricevuta appena arriva"
n = [0]


def msg(sender, to, text, need):
    n[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"r{n[0]}", need_id=need, body={"text": text})


class City:
    def __init__(self, need, op, code, slow=None):
        self.need, self.op, self.code, self.slow = need, op, code, slow or {}
        self.rows, self.out, self.tasks = [], [], []

    async def say(self, to, text):
        self.out.append((to, text))
        self.rows.append(msg(SP, to, text, self.need))
        if to == LEDGER:
            self.tasks.append(asyncio.create_task(self.ledger(text)))
        return "ok"

    async def ledger(self, text):
        head, f = text.split()[0], brain.fields_of(text)
        await asyncio.sleep(self.slow.get(head, 0.05))
        if head == self.op and f.get("prescription_number") != "RX-renewed":
            body = f"refused op={head} code={self.code}"
        elif head == "hold":
            self.holds = getattr(self, "holds", 0) + 1  # the Ledger gives every hold its own id
            body = f"held hold_id=h-{self.need}-{self.holds} slot_id={f['slot_id']} fence=1"
        elif head == "commit":
            body = f"committed booking_id={f['hold_id']} ledger_ref=lr-1"
        elif head == "cancel":
            body = f"cancelled hold_id={f.get('hold_id', '')}"
        elif head == "agenda":
            body = "agenda agenda_now=2026-11-21T08:00:00+00:00"
        elif head == "world":
            body = "slots page=1/1\n" + "\n".join(
                f"slot_id=S{i} doctor_id=asst-centro-c1 at=2026-11-2{1 + i}T09:00:00+00:00 facility={SP} procedure=VIS-CARD free=1"
                for i in (1, 2))
        else:
            return
        row = msg(LEDGER, SP, body, self.need)
        self.rows.append(row)
        await self.turn(row)

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []

    async def turn(self, row):
        view = SimpleNamespace(agent_id=SP, address="", inbound=row, history=self.history, find=self.find)

        async def say(to, text, *, status=None):
            return await self.say(to, text)

        view.say = say
        await brain.main(SimpleNamespace(summary="x", is_emergency=False), view)

    async def confirm(self, wait=True, rx=None):
        row = msg(BC, SP, f"confirm offer_id=of-S1 slot_id=S1 citizen=citizen:c1 consent_ref=cons-{self.need}-booking "
                  f"facility={SP} prescription_number={rx or 'RX-' + self.need} priority=B procedure=VIS-CARD", self.need)
        self.rows.append(row)
        start = len(self.out)
        await self.turn(row)
        await asyncio.sleep(0.6)  # pushed Ledger turns land
        while wait and not all(t.done() for t in self.tasks):
            await asyncio.gather(*self.tasks)
        return self.out[start:]


async def case(op, code):
    city = City(f"N-RR-{op}-{code}", op, code)
    first = await city.confirm()
    lab = f"{op} refused {code}"
    to_desk = [t for to, t in first if to == BC]
    reason = code if code in ("NO_CAPACITY", "CONSENT_MISSING", "PRESCRIPTION_EXPIRED") else "facility_cancelled"
    assert any(t.startswith(f"cancel offer_id=of-S1 reason={reason}") for t in to_desk), (lab, first)
    assert not any(PROMISE in t for t in to_desk), (lab, "first turn promises a receipt after the refusal", first)
    writes = [t.split()[0] for to, t in city.out if to == LEDGER]
    again = await city.confirm()
    assert [t.split()[0] for to, t in city.out if to == LEDGER].count(op) == writes.count(op) == 1, (lab, "refused op sent again", city.out)
    told = [t for to, t in again if to == BC]
    assert told, (lab, "repeat confirm after the refusal got no answer", again)
    assert not any(PROMISE in t for t in told), (lab, "repeat confirm promised a receipt", told)
    assert not any(t.startswith("booking.receipt") for _, t in city.out), (lab, city.out)
    if code != "PRESCRIPTION_EXPIRED":
        assert any("offer_id=of-S2" in t for t in told), (lab, "repeat confirm gets no alternative", told)


async def inflight():
    city = City("N-RR-inflight", "", "", slow={"commit": 6.0})
    first = await city.confirm(wait=False)
    again = await city.confirm(wait=False)
    told = [t for to, t in again if to == BC]
    print("inflight first", [t[:60] for to, t in first if to == BC], "again", [t[:60] for t in told]) if os.environ.get("DBG") else None
    assert told and not any(t in [x for _, x in first] for t in told), ("repeat while the commit is in flight", first, again)
    await asyncio.gather(*city.tasks)
    writes = [t.split()[0] for to, t in city.out if to == LEDGER]
    receipts = [t for to, t in city.out if to == BC and t.startswith("booking.receipt")]
    assert writes.count("hold") == 1 and writes.count("commit") == 1 and len(receipts) == 1, city.out


async def renewed(op):
    city = City(f"N-RR-renew-{op}", op, "PRESCRIPTION_EXPIRED")
    await city.confirm()
    again = await city.confirm(rx="RX-renewed")
    holds = [t for to, t in city.out if to == LEDGER and t.startswith("hold ")]
    receipts = [t for to, t in again if to == BC and t.startswith("booking.receipt")]
    assert len(holds) == 2 and "prescription_number=RX-renewed" in holds[-1] and len(receipts) == 1, (op, city.out)
    assert not any(PROMISE in t for to, t in again if to == BC), (op, again)


async def main():
    await inflight()
    await renewed("hold")
    await renewed("commit")
    for op in ("hold", "commit"):
        for code in ("NO_CAPACITY", "HOLD_EXPIRED", "PRESCRIPTION_EXPIRED"):
            await case(op, code)
    print("OK test_refused_repeat_confirm")


asyncio.run(main())
