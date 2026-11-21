"""#123 L3 live: a hold refused with SLOT_TAKEN reached the desk as reason=facility_cancelled.

Live 21 Nov: slot h1-20261223-1600 was offered to many citizens; each confirm after the first one
was withdrawn about 18 s later with reason=facility_cancelled. The facility's hold was refused
"refused op=hold code=SLOT_TAKEN" (another citizen held it first), but the facility told the desk
facility_cancelled, so the desk never learnt the slot was taken and kept offering it.
Now the facility passes SLOT_TAKEN on: the desk gets ONE cancel offer_id=of-S1 reason=SLOT_TAKEN
plus other dates (never S1), and a repeat confirm of S1 sends no second hold.
With the Ledger contract of !233 the desk marks a SLOT_TAKEN slot dead for every need.
Run: <city python> -I tests/test_fac_slot_taken.py asst/_shared/agent.py   (exit 1 on FAIL)
"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, CapError, Message

os.environ["CITY_ROLE"] = "hospital"
HO, BC, CZ = "agent:t07/hospital", "agent:t07/booking_centre", "citizen:t07/c1"
DAY = (datetime.now(timezone.utc) + timedelta(days=8)).replace(hour=0, minute=0, second=0, microsecond=0)
n = [0]


def load():
    spec = importlib.util.spec_from_file_location(f"brain{n[0]}", sys.argv[1])
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "hospital"
    b._PULL_BUDGET = 1.0  # a Ledger answer later than this lands after the confirm turn
    return b


def msg(sender, to, text, need):
    n[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"t{n[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    """Fake relay + Ledger. ``refuse``: op -> Ledger refusal line; ``relay``: op -> 4xx code."""

    def __init__(self, b, need, refuse=None, relay=None, delay=0.05, push=True):
        self.b, self.need, self.agent_id, self.address = b, need, HO, ""
        self.refuse, self.relay, self.delay, self.push = refuse or {}, relay or {}, delay, push
        self.rows, self.out, self.tries, self.tasks, self.n = [], [], [], [], 0

    async def say(self, to, text, *, status=None):
        op = text.split()[0]
        if to == LEDGER:
            self.tries.append(op)
            if op in self.relay:
                raise CapError(self.relay[op], "relay refused")
        self.out.append((to, text))
        self.rows.append(msg(HO, to, text, self.need))
        if to == LEDGER:
            self.tasks.append(asyncio.create_task(self.ledger(op, text)))
        return "ok"

    async def ledger(self, op, text):
        f = dict(t.partition("=")[::2] for t in text.split() if "=" in t)
        if op in self.refuse:
            await asyncio.sleep(self.delay)
            body = self.refuse[op]
        else:
            await asyncio.sleep(0.05)
            if op == "hold":
                self.n += 1
                body = f"held hold_id=bk-{self.n} slot_id={f['slot_id']} fence=1"
            elif op == "commit":
                body = f"committed booking_id={f['hold_id']} ledger_ref=lr-1"
            elif op == "cancel":
                body = f"cancelled hold_id={f.get('hold_id', '')}"
            elif op == "agenda":
                body = f"agenda agenda_now={DAY.replace(hour=8).isoformat()}"
            elif op == "world":
                body = "slots page=1/1\n" + "\n".join(
                    f"slot_id={s} doctor_id=asst-centro-h1 at={DAY.replace(hour=14 + i).isoformat()} "
                    f"day=mon facility={HO} procedure=VIS-FIS free=1" for i, s in enumerate(("S2", "S3")))
            else:
                return
        row = msg(LEDGER, HO, body, self.need)
        self.rows.append(row)
        if self.push:
            self.tasks.append(asyncio.create_task(self.turn(row)))

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        if row not in self.rows:
            self.rows.append(row)
        view = SimpleNamespace(inbound=row, agent_id=HO, address="", say=self.say,
                               history=self.history, find=self.find)
        await self.b.main(SimpleNamespace(summary="x", is_emergency=False), view)

    async def settle(self):
        await asyncio.sleep(0.2)
        while [t for t in self.tasks if not t.done()]:
            await asyncio.gather(*self.tasks)

    async def confirm(self):
        m = msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=cons-{self.need}-booking "
                        f"facility={HO} prescription_number=RX1 priority=B", self.need)
        start = len(self.out)
        self.rows.append(m)
        await self.turn(m)
        await self.settle()
        return [t for to, t in self.out[start:] if to == BC]

    def to(self, who, head):
        return [t for to, t in self.out if to == who and t.split()[0] == head]


async def main():
    c = City(load(), "N-st1", refuse={"hold": "refused op=hold slot_id=S1 code=SLOT_TAKEN"})
    lines = await c.confirm()
    cancels = [t for t in lines if t.startswith("cancel offer_id=of-S1")]
    ok = len(cancels) == 1 and "reason=SLOT_TAKEN" in cancels[0].split("\n")[0]
    print(("PASS " if ok else "FAIL ") + f"hold refused SLOT_TAKEN: desk gets reason=SLOT_TAKEN ({cancels})")
    alts = [ln for t in lines for ln in t.splitlines() if ln.startswith("offer offer_id=")]
    ok2 = bool(alts) and not any("slot_id=S1" in ln.split() for ln in alts)
    print(("PASS " if ok2 else "FAIL ") + f"other dates offered, never S1 ({len(alts)} offers)")
    ok3 = not any("facility_cancelled" in t for t in lines)
    print(("PASS " if ok3 else "FAIL ") + "no facility_cancelled for a slot another citizen took")
    again = await c.confirm()
    ok4 = c.tries.count("hold") == 1 and any(t.startswith("cancel offer_id=of-S1") for t in again)
    print(("PASS " if ok4 else "FAIL ") + f"repeat confirm: no second hold, cancel again ({c.tries})")
    fails = [ok, ok2, ok3, ok4].count(False)
    print(f"{fails} FAIL")
    sys.exit(1 if fails else 0)


asyncio.run(main())
