"""S13/S14/S16: a refused hold or commit at a facility ends the case with words, never silence.
The desk gets ONE cancel offer_id= with the refusal code and other dates, the hold is freed,
no 4xx is sent again and no resend is invited; a repeat confirm of that slot hears the same.
Run: <city python> -I tests/test_fac_refusal.py asst/_shared/agent.py"""
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


def told(lines, code, case, alts=True):
    """``code``: the reason the cancel must carry (None: any reason). ``alts``: other dates wanted."""
    cancels = [t for t in lines if t.startswith("cancel offer_id=of-S1")]
    assert len(cancels) == 1, f"{case}: want ONE cancel offer_id=of-S1, got {lines}"
    head = cancels[0].split("\n")[0]
    assert (f"reason={code}" in head) if code else ("reason=" in head), f"{case}: reason: {head}"
    if alts:
        offers = [t for t in lines if "offer offer_id=" in t and "slot_id=S1 " not in t]
        assert offers, f"{case}: no alternatives: {lines}"
    assert not any("rimandatemi" in t for t in lines), f"{case}: a 4xx must not invite a resend: {lines}"
    assert not any(t.startswith("booking.receipt") for t in lines), f"{case}: receipt after a refusal"


async def main():
    # a. bare refusal without op word (429-style), pushed: answered at once, no 6 s wait.
    c = City(load(), "N-r1", refuse={"hold": "refused slot_id=S1 code=RATE_LIMITED"})
    t0 = asyncio.get_running_loop().time()
    told(await c.confirm(), None, "bare refusal")
    assert asyncio.get_running_loop().time() - t0 < 3.0, "bare refusal: the desk waited the full budget"
    assert c.tries.count("hold") == 1, c.tries

    # b. refusal lands after the turn, history only: the repeat confirm gets the cancel, hold freed.
    for op in ("hold", "commit"):
        c = City(load(), f"N-r2{op}", refuse={op: f"refused op={op} code=NO_CAPACITY"}, delay=1.6, push=False)
        await c.confirm()
        await asyncio.sleep(1.0)
        told(await c.confirm(), "NO_CAPACITY", f"late {op} refusal")
        if op == "commit":
            assert c.to(LEDGER, "cancel") == ["cancel hold_id=bk-1"], f"hold not freed: {c.out}"
        again = await c.confirm()
        assert any(t.startswith("cancel offer_id=of-S1") for t in again), f"third confirm silent: {again}"
        assert c.tries.count("hold") == 1 and c.tries.count("commit") == (op == "commit"), c.tries

    # c. the relay refuses our hold with a 4xx: one try, the desk told why, repeat answered too.
    for code, reason in (("CONSENT_MISSING", "CONSENT_MISSING"), ("SCHEMA_INVALID", None)):
        c = City(load(), f"N-r3{code}", relay={"hold": code})
        told(await c.confirm(), reason, f"relay {code} on hold", alts=reason is None)
        again = await c.confirm()
        assert any(t.startswith("cancel offer_id=of-S1") for t in again), f"repeat after relay 4xx silent: {again}"
        assert c.tries.count("hold") == 1, f"4xx hold sent again: {c.tries}"

    # c. the relay refuses our commit with a 4xx: hold freed, desk told, no "registro lento".
    c = City(load(), "N-r4", relay={"commit": "CONSENT_MISSING"})
    told(await c.confirm(), "CONSENT_MISSING", "relay 4xx on commit", alts=False)
    assert c.to(LEDGER, "cancel") == ["cancel hold_id=bk-1"], f"hold not freed: {c.out}"
    assert c.tries.count("commit") == 1, c.tries
    print("OK test_fac_refusal")


asyncio.run(main())
