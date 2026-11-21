"""L7 (S23/S09): Policy A at the consultorio fails closed for agent confirms, and a case another
team's desk opened here never reaches that team's citizen. A junk consent_ref, a phantom slot,
or an offer_id we made to another desk is no hold; a partner's own cancel frees the slot at the
Ledger and is acked to the partner; a city fault.cancel is told to the partner desk; a pushed
Ledger page never becomes offers to the partner's citizen.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_fc_policy_a.py [asst/_shared/agent.py]

Full turns through brain.main on a local fake city with a stateful fake Ledger (hold, commit,
cancel, agenda, world answered at once and pushed back as turns).
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
os.environ["CITY_ROLE"] = "family_counseling"
ME, OBC, OBC2 = "agent:t07/family_counseling", "agent:t05/booking_centre", "agent:t09/booking_centre"
OGP = "agent:t05/gp"  # the partner team's GP, not the desk we offered to
CZ, OURCZ = "citizen:t05/c9", "citizen:t07/c1"
CONS = {"xc-c9": CZ}
NOW = datetime.now(timezone.utc)
DAY = (NOW + timedelta(days=(7 - NOW.weekday()) % 7 or 7)).replace(minute=0, second=0, microsecond=0)
SEQ = [0]
OKS = []


def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "family_counseling"
    if hasattr(b, "_LLM_NET"):
        b._LLM_NET = False
    return b


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self, b):
        self.b, self.agent_id, self.address = b, ME, ""
        self.rows, self.out, self.tasks, self.ops = [], [], [], []
        self.free = {s: True for s in ("S1", "S2", "S3", "S4")}
        self.holds, self.booked, self.n = {}, {}, 0
        self.t0 = time.monotonic()

    def ledger(self, text):
        f = {k: v for k, _, v in (t.partition("=") for t in text.split()) if v}
        op = text.split()[0]
        self.ops.append(text)
        if op == "hold":
            s = f.get("slot_id", "")
            if s not in self.free:
                return "refused op=hold code=SLOT_NOT_FOUND"
            if CONS.get(f.get("consent_ref", "")) != f.get("citizen"):
                return "refused op=hold code=CONSENT_MISSING"
            if not self.free[s]:
                return "refused op=hold code=SLOT_TAKEN"
            self.n += 1
            self.holds[f"bk-{self.n}"] = s
            self.free[s] = False
            return f"held hold_id=bk-{self.n} slot_id={s} fence=1"
        if op == "commit":
            hid = f.get("hold_id", "")
            if hid not in self.holds or hid in self.booked:
                return "refused op=commit code=HOLD_NOT_FOUND"
            self.booked[hid] = self.holds[hid]
            return f"committed booking_id={hid} ledger_ref=lr-{hid}"
        if op == "cancel":
            hid = f.get("hold_id") or f.get("booking_id", "")
            if hid not in self.holds:
                return "refused op=cancel code=HOLD_NOT_FOUND"
            self.free[self.holds.pop(hid)] = True
            self.booked.pop(hid, None)
            return f"cancelled hold_id={hid}"
        if op == "agenda":
            return "agenda agenda_now=" + DAY.replace(hour=8).isoformat()
        if op == "world":
            rows = [f"slot_id={s} doctor_id=asst-centro-h1 at={DAY.replace(hour=14 + i).isoformat()} day=mon "
                    f"facility={ME} procedure=CONS-GIN free=1" for i, s in enumerate(self.free) if self.free[s]]
            return "slots page=1/1\n" + "\n".join(rows)
        return None

    async def say(self, to, text, *, status=None, need=None):
        self.out.append((time.monotonic() - self.t0, need, to, text))
        self.rows.append(msg(ME, to, text, need))
        if to == LEDGER:
            body = self.ledger(text)
            if body:
                row = msg(LEDGER, ME, body, need)
                self.rows.append(row)
                self.tasks.append(asyncio.get_running_loop().create_task(self.turn(row)))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        rows = [r for r in self.rows if need_id is None or r.need_id == need_id]
        return [r for r in rows if not peer or peer in (r.sender, r.recipient)]

    async def find(self, query):
        return []

    async def turn(self, row):
        if all(r.msg_id != row.msg_id for r in self.rows):
            self.rows.append(row)
        await asyncio.wait_for(self.b.main(SimpleNamespace(summary="x", is_emergency=False), View(self, row)), 30)

    async def deliver(self, row, extra=0.2):
        await self.turn(row)
        await asyncio.sleep(extra)
        while any(not t.done() for t in self.tasks):
            await asyncio.gather(*[t for t in self.tasks if not t.done()], return_exceptions=True)

    def to(self, who, head=None):
        return [x for _, _, w, x in self.out if w == who and (head is None or x.split()[0] == head)]

    def holds_sent(self):
        return [x for x in self.ops if x.startswith("hold")]


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def check(label, ok, city=None):
    OKS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + label)
    if not ok and city is not None:
        for t, n, w, x in city.out:
            print(f"      [{t:5.1f}s] {w[-26:]:>26} : {x[:140]!r}")


def offer_id(city, to, slot):
    for x in city.to(to):
        for line in x.splitlines():
            f = dict(p.partition("=")[::2] for p in line.split() if "=" in p)
            if f.get("slot_id") == slot and f.get("offer_id"):
                return f["offer_id"]
    return ""


def confirm(need, oid, slot="S1", sender=OBC, consent="xc-c9"):
    return msg(sender, ME, f"confirm offer_id={oid} slot_id={slot} citizen={CZ} consent_ref={consent} facility={ME}", need)


async def offered(need):
    city = City(load())
    await city.deliver(msg(OBC, ME, "need.request service_type=family_counseling procedure=CONS-GIN priority=D "
                                    "request_id=rq-1", need))
    return city, offer_id(city, OBC, "S1")


async def booked(need):
    city, oid = await offered(need)
    await city.deliver(confirm(need, oid))
    return city, oid


async def valid_partner_confirm():
    city, _ = await booked("NA1")
    check("partner desk confirms our offer with a valid cross_asst consent: hold, commit, receipt once",
          len(city.holds_sent()) == 1 and len(city.booked) == 1 and len(city.to(OBC, "booking.receipt")) == 1, city)


async def junk_consent():
    city, oid = await offered("NA2")
    await city.deliver(confirm("NA2", oid, consent="ok"))
    check("consent_ref=ok on our offer: no hold, the partner is told", not city.holds_sent() and len(city.to(OBC)) >= 2, city)


async def phantom_slot():
    city = City(load())
    await city.deliver(confirm("NA3", "of-X9", slot="S99"))
    check("cold confirm for a phantom slot S99: no hold, the partner is told", not city.holds_sent() and city.to(OBC), city)


async def cold_real_slot():
    city = City(load())
    await city.deliver(confirm("NA4", "of-X1", slot="S2"))
    check("cold confirm for a real slot of our world with a valid consent: still booked",
          len(city.booked) == 1 and len(city.to(OBC, "booking.receipt")) == 1, city)


async def partner_gp():
    city, oid = await offered("NA5")
    await city.deliver(confirm("NA5", oid, sender=OGP))
    check("the partner's GP confirms the offer we made to its desk: no hold", not city.holds_sent(), city)


async def third_desk():
    city, oid = await offered("NA6")
    await city.deliver(confirm("NA6", oid, sender=OBC2))
    check("a third desk confirms the offer we made to the partner desk: no hold, told",
          not city.holds_sent() and city.to(OBC2), city)


def to_citizens(city):
    return [x for _, _, w, x in city.out if w.startswith("citizen:")]


async def partner_cancel():
    city, oid = await booked("NA7")
    await city.deliver(msg(OBC, ME, f"cancel offer_id={oid} booking_id=bk-1 reason=citizen_request", "NA7"))
    check("the partner desk cancels its own booking: freed at the Ledger, the partner acked, its citizen not written to",
          not city.booked and any(x.startswith("cancel") and "bk-1" in x for x in city.ops)
          and any("bk-1" in x for x in city.to(OBC)[-1:]) and not to_citizens(city), city)


async def city_fault():
    city, _ = await booked("NA8")
    await city.deliver(msg("city:fault", ME, "fault.cancel booking_id=bk-1", "NA8"))
    check("city fault.cancel in a partner case: the partner desk is told, its citizen is not",
          any(x.startswith("cancel ") and "bk-1" in x for x in city.to(OBC)) and not to_citizens(city), city)


async def ledger_page():
    city, _ = await booked("NA9")
    page = City.ledger(city, "world procedure=CONS-GIN page=1")
    await city.deliver(msg(LEDGER, ME, page, "NA9"))
    check("a pushed Ledger slots page in a partner case: no offers to the partner's citizen", not to_citizens(city), city)


async def lookalike():
    city, _ = await booked("NA10")
    await city.deliver(msg("city:ledgerr", ME, "cancel booking_id=bk-1 reason=fault", "NA10"))
    check("a city:ledgerr lookalike cancel: booking kept, nobody's citizen written to",
          city.booked and not to_citizens(city), city)


async def main():
    for case in (valid_partner_confirm, junk_consent, phantom_slot, cold_real_slot, partner_gp, third_desk,
                 partner_cancel, city_fault, ledger_page, lookalike):
        await case()
    print("ALL OK" if all(OKS) else "SOME FAILED")
    raise SystemExit(0 if all(OKS) else 1)


asyncio.run(main())
