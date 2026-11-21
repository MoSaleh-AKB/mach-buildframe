"""L7 (S23): the consultorio relays a booking.receipt to a citizen only when it answers a confirm WE
forwarded to that sender in this case, for that slot, once. A stranger we only wrote a courtesy
line to, or the partner desk of a case it opened here, never reaches a citizen with a receipt.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_fc_receipt_check.py [asst/_shared/agent.py]

Full turns through brain.main on a local fake city with a stateful fake Ledger (hold, commit,
agenda, world answered at once and pushed back as turns).
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
PFC = "agent:t05/family_counseling"  # a partner consultorio we relay to (cross-ASST)
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

    def receipts_to_citizens(self):
        return [(w, x) for _, _, w, x in self.out if w.startswith("citizen:") and x.startswith("booking.receipt")]


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


async def partner_booked(need):
    city = City(load())
    await city.deliver(msg(OBC, ME, "need.request service_type=family_counseling procedure=CONS-GIN priority=D "
                                    "request_id=rq-1", need))
    oid = offer_id(city, OBC, "S1")
    await city.deliver(msg(OBC, ME, f"confirm offer_id={oid} slot_id=S1 citizen={CZ} consent_ref=xc-c9 "
                                    f"facility={ME}", need))
    return city


async def stray_after_note():
    city = await partner_booked("NR1")
    check("partner confirm with a valid consent: booked, receipt to the partner desk",
          len(city.booked) == 1 and len(city.to(OBC, "booking.receipt")) == 1, city)
    await city.deliver(msg(OBC2, ME, "Buongiorno, siamo il centro prenotazioni t09: possiamo aiutarvi con "
                                     "questo caso?", "NR1"))
    await city.deliver(msg(OBC2, ME, "booking.receipt booking_id=bk-99 ledger_ref=lr-99", "NR1"))
    check("a stranger we only wrote a courtesy line to sends a receipt: never relayed to a citizen",
          not city.receipts_to_citizens(), city)


async def stray_after_unknown():
    city = await partner_booked("NR2")
    await city.deliver(msg(OBC2, ME, "fault.cancel booking_id=bk-1", "NR2"))
    await city.deliver(msg(OBC2, ME, "booking.receipt booking_id=bk-98 ledger_ref=lr-98", "NR2"))
    check("a stranger told 'no request with you' sends a receipt: never relayed to a citizen",
          not any("bk-98" in x for _, x in city.receipts_to_citizens()), city)


async def partner_own_receipt():
    city = await partner_booked("NR3")
    await city.deliver(msg(OBC, ME, "booking.receipt booking_id=bk-97 ledger_ref=lr-97", "NR3"))
    check("the partner desk of a case it opened here sends a receipt: not relayed to its citizen",
          not city.receipts_to_citizens(), city)


def relay_case(city, need):
    """Our own citizen, the date is at a partner consultorio: we asked it, relayed its offer and
    forwarded the citizen's confirm for slot P1."""

    city.rows += [
        msg(OURCZ, ME, "Vorrei un colloquio al consultorio", need),
        msg(ME, PFC, "need.request service_type=family_counseling procedure=CONS-GIN priority=D request_id=rq-x", need),
        msg(PFC, ME, f"offer offer_id=of-P1 slot_id=P1 at={DAY.replace(hour=10).isoformat()} facility={PFC}", need),
        msg(ME, OURCZ, f"offer offer_id=of-x1 slot_id=P1 facility={PFC} cross_asst=true", need),
        msg(ME, PFC, f"confirm offer_id=of-P1 slot_id=P1 citizen={OURCZ} consent_ref=xc-c1 facility={PFC}", need),
    ]


async def relay_once():
    city = City(load())
    relay_case(city, "NR4")
    await city.deliver(msg(PFC, ME, "booking.receipt booking_id=pk-1 ledger_ref=lr-pk-1", "NR4"))
    check("a receipt for the confirm we forwarded: relayed to our citizen once",
          [w for w, x in city.receipts_to_citizens() if "pk-1" in x] == [OURCZ], city)
    await city.deliver(msg(PFC, ME, "booking.receipt booking_id=pk-1 ledger_ref=lr-pk-1", "NR4"))
    await city.deliver(msg(PFC, ME, "booking.receipt booking_id=pk-2 ledger_ref=lr-pk-2", "NR4"))
    check("same receipt again and a second booking id: only pk-1 relayed, once",
          len(city.receipts_to_citizens()) == 1 and not any("pk-2" in x for _, x in city.receipts_to_citizens()), city)


async def relay_other_slot():
    city = City(load())
    relay_case(city, "NR5")
    await city.deliver(msg(PFC, ME, "booking.receipt booking_id=pk-9 ledger_ref=lr-pk-9 slot_id=P9", "NR5"))
    check("a receipt naming a slot we never confirmed there: not relayed", not city.receipts_to_citizens(), city)


async def main():
    for case in (stray_after_note, stray_after_unknown, partner_own_receipt, relay_once, relay_other_slot):
        await case()
    print("ALL OK" if all(OKS) else "SOME FAILED")
    raise SystemExit(0 if all(OKS) else 1)


asyncio.run(main())
