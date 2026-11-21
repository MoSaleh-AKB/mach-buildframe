"""L8 (F05) at the booking desk: a cancel with no id that declines a need.request never cancels
the confirm in flight.

A facility (our hospital or a partner ASST) answers an EARLIER request late with
``cancel reason=temporarily_unavailable`` or ``reason=no_capacity`` and no offer_id, slot_id,
hold_id or booking_id, while our confirm of another slot waits for a slow Ledger. The citizen
must not hear "that date is no longer valid" with new offers, and the receipt that comes
afterwards is relayed as a normal booking. A refusal that names the offer (or the partner's id-less
SLOT_TAKEN) still cancels it.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_l8_idless_cancel.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
BC, HO, P1, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "agent:t05/hospital", "citizen:t07/c1"
RX = "prescription_number-N-0001"
ASK = f"Vorrei una visita fisiatrica. need.request lang=it prescription_number={RX}"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAILS = []

DECLINES = (
    ("it", "cancel reason=temporarily_unavailable\nGrazie per la richiesta, mi dispiace. Il registro non mi ha "
           "ancora risposto: mi riscriva tra poco e le rispondo subito, oppure interroghi nel frattempo un altro presidio."),
    ("en", "cancel reason=no_capacity request_id=N-old\nThank you for the request, sorry. For VIS-FIS I have no "
           "suitable free slots in the time given: try another facility or write again tomorrow."),
    ("typo", "cancel reason=Temporarly_Unavailable.\nil registro non risponde ancora, riprovate"),
    ("mixed", "cancel reason=temporarily_unavailable\nPer una nuova ricerca mandatemi una need.request con "
              "procedure e priority, vi rispondo subito."),
)


def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "booking_centre"
    b._LLM_NET = False
    b._wx_fetch = lambda: {}
    if hasattr(b, "_RESCUE_LAST"):
        b._RESCUE_LAST[:] = [time.monotonic() + 9999]
    b._XA_BUDGET, b._XA_GRACE = 1.0, 0.2
    return b


def iso(dt):
    return dt.isoformat()


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=iso(datetime.now(timezone.utc)))


def own_slots(n=4):
    base = (NOW + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    rows = [f"slot_id=S{i} doctor_id=asst-centro-h1 at={iso(base + timedelta(hours=2 * (i - 1)))} day=mon "
            f"facility={HO} procedure=VIS-FIS free=1" for i in range(1, n + 1)]
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    """Local fake city: the Ledger answers at once; facilities answer only when the test says."""

    def __init__(self, b, need, world, peers=()):
        self.b, self.need, self.world, self.peers = b, need, world, list(peers)
        self.rows, self.out, self.bots = [], [], {}

    async def say(self, to, text):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        head = text.split()[0]
        if to == LEDGER:
            body = {
                "prescription": f"prescription prescription_number={RX} procedure=VIS-FIS priority=B state=issued",
                "agenda": f"agenda agenda_now={iso((NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0))}",
                "world": self.world,
            }.get(head)
            if body:
                self.rows.append(msg(LEDGER, BC, body, self.need))
        elif to in self.bots and head == "need.request":
            asyncio.create_task(self.bots[to](self, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return list(self.peers)

    async def turn(self, row):
        self.rows.append(row)
        view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=self.find, history=self.history,
                               say=lambda to, text, *, status=None: self.say(to, text))
        await asyncio.wait_for(self.b.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)

    async def send(self, who, text, wait=0.4):
        await self.turn(msg(who, BC, text, self.need))
        await asyncio.sleep(wait)

    def cz(self, since=0):
        return [t for to, t in self.out[since:] if to == CZ]

    def to(self, who):
        return [t for to, t in self.out if to == who]


def partner_bot(pid, slots):
    at = iso((NOW + timedelta(days=2)).replace(hour=16, minute=0, second=0, microsecond=0))

    async def bot(city, text):
        f = city.b.fields_of(text.splitlines()[0])
        await asyncio.sleep(0.05)
        lines = [f"offer offer_id=of-{s} slot_id={s} doctor_id=D-{s} facility={pid} procedure=VIS-FIS "
                 f"agenda_date={at} expiry={at} priority=B request_id={f.get('request_id', '')}" for s in slots]
        await city.turn(msg(pid, BC, "Ecco.\n" + "\n".join(lines), city.need))
    return bot


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for to, t in (city.out if city else []):
            print(f"      > {to[-14:]:>14} : {t[:150]!r}")


def offers_in(texts, skip=""):
    return [w.split("=", 1)[1] for t in texts if not t.startswith("cancel") for w in t.split()
            if w.startswith("offer_id=of-") and w.split("=", 1)[1] != skip]


async def own_case(label):
    """Our hospital: confirm in flight, then the decline (or a real refusal) arrives."""

    b = load()
    city = City(b, f"N-own-{label}", own_slots())
    await city.send(CZ, ASK, 0.4)
    ids = offers_in(city.cz())
    if not ids:
        check(False, f"own {label}: offers sent", city)
        return None
    await city.send(CZ, f"confirm offer_id={ids[0]} consent_ref=cons-N-0001-booking", 0.3)
    b._CITIZEN_RATE.clear()
    return b, city, ids


async def own_decline(label, text):
    got = await own_case(label)
    if got is None:
        return
    b, city, ids = got
    i = len(city.out)
    await city.send(HO, text)
    told = city.cz(i)
    check(not any(t.startswith("cancel") for t in told), f"own {label}: decline does not cancel the confirm of {ids[0]}", city)
    check(not offers_in(told), f"own {label}: no new offers after the decline", city)
    j = len(city.out)
    await city.send(HO, "booking.receipt booking_id=bk-S1 ledger_ref=lr-S1", 0.3)
    rc = [t for t in city.cz(j) if t.startswith("booking.receipt")]
    check(len(rc) == 1 and "bk-S1" in rc[0] and "comunque" not in rc[0],
          f"own {label}: the receipt that follows is relayed once as a normal booking", city)
    check(len([t for t in city.to(HO) if t.startswith("confirm")]) == 1, f"own {label}: one confirm only", city)


async def own_real_refusal(label, text):
    got = await own_case(label)
    if got is None:
        return
    b, city, ids = got
    i = len(city.out)
    if label == "decline-then-refusal":
        await city.send(HO, DECLINES[0][1])
    await city.send(HO, text.format(oid=ids[0]), 0.6)
    told = city.cz(i)
    check(sum(1 for t in told if t.startswith(f"cancel offer_id={ids[0]}")) == 1,
          f"own {label}: the refusal naming {ids[0]} reaches the citizen once", city)
    check(bool(offers_in(told, skip=ids[0])), f"own {label}: alternatives sent", city)


async def partner_case(label, wait):
    b = load()
    b._XA_CONFIRM_WAIT = wait
    city = City(b, f"N-xa-{label}", "slots page=1/1", [Peer(agent_id=P1, role="facility", capabilities=("hospital.physiatry",))])
    city.bots[P1] = partner_bot(P1, ["P1a", "P1b"])
    await city.send(CZ, ASK, 0.5)
    await city.send(CZ, "consent purpose=cross_asst consent_ref=cons-x-cross", 0.2)
    b._CITIZEN_RATE.clear()
    await city.send(CZ, "confirm offer_id=of-P1a consent_ref=cons-x-cross", 0.2)
    b._CITIZEN_RATE.clear()
    sent = any(t.startswith("confirm") and "offer_id=of-P1a" in t for t in city.to(P1))
    check(sent, f"partner {label}: our confirm went to the partner", city)
    rid = next((city.b.fields_of(t).get("request_id", "") for t in city.to(P1) if t.startswith("need.request")), "")
    return b, city, rid


async def partner_decline_then_receipt():
    b, city, rid = await partner_case("receipt", 30.0)
    i = len(city.out)
    await city.send(P1, f"cancel reason=temporarily_unavailable request_id={rid}\nGrazie, il registro non risponde: "
                        "riprovate tra poco.")
    told = city.cz(i)
    check(not any(t.startswith("cancel") for t in told), "partner receipt: a late decline does not cancel the confirm of of-P1a", city)
    check(not any("waiting_list=true" in t for t in told), "partner receipt: no waiting list after the decline", city)
    j = len(city.out)
    city.rows.append(msg(LEDGER, BC, "committed booking_id=pk-1 ledger_ref=pl-1", city.need))  # impostor audit #4
    await city.send(P1, "booking.receipt booking_id=pk-1 ledger_ref=pl-1", 0.3)
    check(any(t.startswith("booking.receipt") and "pk-1" in t for t in city.cz(j)),
          "partner receipt: the receipt that follows reaches the citizen", city)


async def partner_decline_then_silence():
    b, city, rid = await partner_case("silent", 2.0)
    i = len(city.out)
    await city.send(P1, "cancel reason=NO_CAPACITY\nSorry, no free slots for VIS-FIS in the time given.", 0.1)
    check(not any(t.startswith("cancel") for t in city.cz(i)), "partner silent: the decline is not taken as a refusal", city)
    await asyncio.sleep(3.0)
    late = city.cz(i)
    check(any("non ha risposto" in t for t in late) and any("waiting_list=true" in t or "lista d'attesa" in t for t in late),
          "partner silent: the decline is no answer to the confirm, the wait still ends with alternatives", city)


async def partner_decline_then_kit_refusal():
    b, city, rid = await partner_case("kit", 30.0)
    i = len(city.out)
    await city.send(P1, "cancel reason=temporarily_unavailable\nil registro è lento", 0.2)
    await city.send(P1, "cancel reason=SLOT_TAKEN", 0.6)
    check(sum(1 for t in city.cz(i) if t.startswith("cancel offer_id=of-P1a")) == 1,
          "partner kit: the id-less SLOT_TAKEN after a decline still refuses of-P1a, once", city)


async def main():
    for label, text in DECLINES:
        await own_decline(label, text)
    await own_real_refusal("refusal", "cancel offer_id={oid} reason=SLOT_TAKEN")
    await own_real_refusal("decline-then-refusal", "cancel offer_id={oid} reason=facility_cancelled")
    await own_real_refusal("one-message", "cancel reason=temporarily_unavailable\ncancel offer_id={oid} reason=SLOT_TAKEN")
    await partner_decline_then_receipt()
    await partner_decline_then_silence()
    await partner_decline_then_kit_refusal()
    print(f"\n{'OK' if not FAILS else 'FAIL'} test_l8_idless_cancel ({len(FAILS)} failed)")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
