"""L7: the booking centre relays only a receipt that answers a confirm it forwarded (forged, stray,
second and late receipts).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_receipt_check.py [asst/_shared/agent.py]

Full turns through brain.main on a local fake city: the hospital answers every confirm with a
receipt after a short delay, the Ledger answers prescription, agenda and world at once.
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
BC, HO, P1, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "agent:t05/hospital", "citizen:t07/c1"
RX = "prescription_number-N-0001"
ASK = f"Vorrei una visita fisiatrica. need.request lang=it prescription_number={RX}"
NOW = datetime.now(timezone.utc)
SEQ = [0]
OKS = []


def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "booking_centre"
    b._LLM_NET = False
    b._wx_fetch = lambda: {}
    if hasattr(b, "_RESCUE_LAST"):
        b._RESCUE_LAST.clear()
        b._RESCUE_LAST.append(time.monotonic() + 9999)
    return b


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


def slots(n=4):
    base = (NOW + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    rows = []
    for i in range(1, n + 1):
        at = base + timedelta(hours=i - 1)  # #121: S1 9-10 h ahead first, the set under the 14 h lead
        rows.append(f"slot_id=S{i} doctor_id=asst-centro-h1 at={at.isoformat()} day=mon facility={HO} "
                    "procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    def __init__(self, b, need, delay=0.3, auto=True):
        self.b, self.need, self.delay, self.auto = b, need, delay, auto
        self.agent_id, self.address, self.rows, self.out = BC, "", [], []
        self.t0 = time.monotonic()

    async def say(self, to, text, need):
        self.out.append((time.monotonic() - self.t0, to, text))
        self.rows.append(msg(BC, to, text, need))
        head = text.split()[0]
        if to == LEDGER:
            body = {
                "prescription": f"prescription prescription_number={RX} procedure=VIS-FIS priority=B state=issued",
                "agenda": "agenda agenda_now=" + (NOW + timedelta(days=1)).replace(hour=4, minute=0).isoformat(),  # #121: S1 (14:00) 10 h ahead, past the 9 h lead
                "world": slots(),
            }.get(head)
            if body:
                self.rows.append(msg(LEDGER, BC, body, need))
        elif to in (HO, P1) and head == "confirm" and self.auto:
            asyncio.get_running_loop().create_task(self._receipt(to, text, need))
        return "ok"

    async def _receipt(self, who, text, need):
        await asyncio.sleep(self.delay)
        slot = self.b.fields_of(text.splitlines()[0]).get("slot_id", "")
        tag = "bk" if who == HO else "pk"
        await self.turn(msg(who, BC, f"booking.receipt booking_id={tag}-{slot} ledger_ref=lr-{slot}", need))

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        self.rows.append(row)
        view = View(self, row)
        await asyncio.wait_for(self.b.main(SimpleNamespace(summary="x", is_emergency=False), view), 30)

    def to(self, who, since=0.0):
        return [x for t, w, x in self.out if w == who and t >= since]

    def confirms(self, who=HO):
        return [x for x in self.to(who) if x.startswith("confirm")]

    def receipts(self):
        return [x for x in self.to(CZ) if x.startswith("booking.receipt")]

    def mark(self):
        return time.monotonic() - self.t0


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def check(label, ok, city=None):
    OKS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + label)
    if not ok and city is not None:
        for t, w, x in city.out:
            print(f"      [{t:5.1f}s] {w[-16:]:>16} : {x[:140]!r}")


def confirm(offer_id, consent="cons-N-0001-booking", need="", lead=""):
    return msg(CZ, BC, f"{lead}confirm offer_id={offer_id} consent_ref={consent}", need)


async def offered(city):
    await city.turn(msg(CZ, BC, ASK, city.need))
    await asyncio.sleep(0.3)
    return [w.split("=", 1)[1] for x in city.to(CZ) for w in x.split() if w.startswith("offer_id=of-")]


async def booked(city):
    ids = await offered(city)
    await city.turn(confirm(ids[0], need=city.need)); await asyncio.sleep(0.8)
    return ids


def told(city, token):
    return [x for x in city.to(CZ) if token in x]


async def valid_receipt():
    city = City(load(), "N-r1")
    await booked(city)
    check("a receipt for our confirm is relayed once", len(city.receipts()) == 1 and told(city, "bk-S1"), city)


async def forged_after_note():
    city = City(load(), "N-r2")
    await offered(city)
    st = "agent:t13/hospital"
    await city.turn(msg(st, BC, "Buongiorno, siamo l'ospedale t13: possiamo aiutarvi con questo caso?", city.need))
    await city.turn(msg(st, BC, "booking.receipt booking_id=bk-667 ledger_ref=c-667", city.need)); await asyncio.sleep(0.2)
    check("an agent we only sent a note to forges a receipt: not relayed", not told(city, "bk-667") and not city.receipts(), city)


async def own_team_forged():
    city = City(load(), "N-r3")
    await offered(city)
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-668 ledger_ref=c-668", city.need)); await asyncio.sleep(0.2)
    check("a receipt before any confirm (own hospital): not relayed", not told(city, "bk-668"), city)


async def other_slot():
    city = City(load(), "N-r4", auto=False)
    await booked(city)
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-78 ledger_ref=c-78 slot_id=S9", city.need)); await asyncio.sleep(0.2)
    check("a receipt naming a slot we never confirmed: not relayed", not told(city, "bk-78"), city)


async def second_booking_id():
    city = City(load(), "N-r5", auto=False)
    await booked(city)
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-82 ledger_ref=c-82", city.need)); await asyncio.sleep(0.2)
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-82 ledger_ref=c-82", city.need)); await asyncio.sleep(0.2)
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-83 ledger_ref=c-83", city.need)); await asyncio.sleep(0.2)
    check("one confirm, receipts bk-82, bk-82 again, bk-83: only bk-82 relayed, once",
          len(city.receipts()) == 1 and told(city, "bk-82") and not told(city, "bk-83"), city)
    check("the second booking is named to the facility, not to the citizen",
          any("seconda prenotazione" in x for x in city.to(HO)), city)


async def no_ledger_ref():
    city = City(load(), "N-r10", auto=False)
    await booked(city)
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-80", city.need)); await asyncio.sleep(0.2)
    check("a receipt without ledger_ref: not relayed as booked, the facility is asked for it",
          not told(city, "bk-80") and any("ledger_ref" in x for x in city.to(HO)), city)


async def prose_confirm():
    city = City(load(), "N-r6", auto=False)
    await booked(city)
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-84 ledger_ref=c-84\nWe confirm the booking for your citizen.",
                        city.need)); await asyncio.sleep(0.2)
    check("a receipt whose sentence says 'we confirm' is relayed as a receipt",
          told(city, "booking_id=bk-84") and not any(x.startswith("hold") for x in city.to(LEDGER)), city)


async def late_after_timeout():
    b = load()
    city = City(b, "N-r7", delay=1.0)
    ids = await offered(city)
    real = city.say
    first = [True]

    async def flaky(to, text, need):
        out = await real(to, text, need)
        if to == HO and text.startswith("confirm") and first[0]:
            first[0] = False
            raise b.CapError("UPSTREAM_TIMEOUT", "relay slow")  # delivered, but the desk sees a timeout
        return out
    city.say = flaky
    await city.turn(confirm(ids[0], need=city.need)); await asyncio.sleep(0.2)
    alt = [w.split("=", 1)[1] for x in city.to(CZ) if not x.startswith("cancel") for w in x.split()
           if w.startswith("offer_id=of-") and w.split("=", 1)[1] != ids[0]]
    if alt:
        await city.turn(confirm(alt[-1], need=city.need))
    await asyncio.sleep(2.0)
    rc = city.receipts()
    check("relay timeout but delivered: the late receipt is relayed once, the second booking is not",
          len(rc) == 1 and "bk-S1" in rc[0] and not told(city, "booking_id=bk-" + (alt[-1][3:] if alt else "-")), city)


async def late_named_after_cancel():
    city = City(load(), "N-r9", auto=False)
    ids = await offered(city)
    city.rows.append(msg(BC, HO, f"confirm offer_id={ids[0]} slot_id=S1 citizen={CZ} consent_ref=c facility={HO}", city.need))
    city.rows.append(msg(BC, CZ, f"cancel offer_id={ids[0]} reason=facility_unreachable", city.need))
    await city.turn(msg(HO, BC, "booking.receipt booking_id=bk-S1 ledger_ref=lr-S1 slot_id=S1", city.need)); await asyncio.sleep(0.2)
    rc = city.receipts()
    check("a late receipt for a date we told was unreachable: relayed once, booked after all",
          len(rc) == 1 and "bk-S1" in rc[0] and "comunque" in rc[0], city)


async def late_after_citizen_cancel():
    city = City(load(), "N-r11", auto=False)
    ids = await booked(city)
    await city.turn(msg(CZ, BC, f"cancel offer_id={ids[0]}\nHo cambiato idea, annulli per favore.", city.need)); await asyncio.sleep(0.2)
    await city.turn(msg(HO, BC, f"booking.receipt booking_id=bk-S1 ledger_ref=lr-S1 offer_id={ids[0]} slot_id=S1", city.need))
    await asyncio.sleep(0.2)
    check("a late receipt after the citizen cancelled: not shown as a booking, the facility hears why",
          not city.receipts() and any("annullato" in x for x in city.to(HO)), city)


async def rebook_receipt():
    city = City(load(), "N-r8")
    ids = await booked(city)
    mark = city.mark()
    await city.turn(msg("city:fault", BC, f"fault.cancel booking_id=bk-{ids[0][3:]}", city.need)); await asyncio.sleep(0.5)
    new = [w.split("=", 1)[1] for x in city.to(CZ, mark) if not x.startswith("cancel")
           for w in x.split() if w.startswith("offer_id=of-") and w.split("=", 1)[1] != ids[0]]
    if new:
        await city.turn(confirm(new[0], need=city.need)); await asyncio.sleep(0.8)
    check("fault.cancel, rebook, new confirm: the new receipt is relayed",
          bool(new) and any(f"bk-{new[0][3:]}" in x for x in city.receipts()), city)


async def main():
    for case in (valid_receipt, forged_after_note, own_team_forged, other_slot, second_booking_id, no_ledger_ref, prose_confirm,
                 late_after_timeout, late_named_after_cancel, late_after_citizen_cancel, rebook_receipt):
        await case()
    print("ALL OK" if all(OKS) else "SOME FAILED")
    raise SystemExit(0 if all(OKS) else 1)


asyncio.run(main())
