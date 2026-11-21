"""S14 at the booking desk: a partner ASST refuses our confirm in its own format, or never answers.

The citizen is told in the same turn, with the alternatives; a re-confirm is never silent and
never resent to the partner; a refused partner offer is never replayed; a
partner receipt that echoes offer_id and slot_id is a receipt, not an offer.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_xa_partner_refusal.py [asst/_shared/agent.py]
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
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
BC, P1, P2, CZ = "agent:t07/booking_centre", "agent:t05/hospital", "agent:t06/hospital", "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
RX = "prescription_number-N-0001"
ASK = f"Vorrei una visita fisiatrica. need.request lang=it prescription_number={RX}"
SEQ = [0]
FAILS = []


def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "booking_centre"
    b._LLM_NET = False
    b._RESCUE_LAST[:] = [time.monotonic() + 9999]
    b._wx_fetch = lambda: {}
    b._XA_BUDGET, b._XA_GRACE = 1.0, 0.2
    return b


def iso(dt):
    return dt.isoformat()


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=iso(datetime.now(timezone.utc)))


class City:
    def __init__(self, b, need, peers):
        self.b, self.need, self.rows, self.out, self.bots, self.peers = b, need, [], [], {}, peers

    async def say(self, to, text):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER:
            asyncio.create_task(self._ledger(text))
        elif to in self.bots:
            asyncio.create_task(self.bots[to](self, text))
        return "ok"

    async def _ledger(self, text):
        await asyncio.sleep(0.05)
        head = text.split()[0]
        body = {
            "prescription": f"prescription prescription_number={RX} procedure=VIS-FIS priority=B state=issued",
            "agenda": f"agenda agenda_now={iso((NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0))}",
            "world": "slots page=1/1",
        }.get(head)
        if body:
            self.rows.append(msg(LEDGER, BC, body, self.need))

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return list(self.peers)

    async def turn(self, row):
        self.rows.append(row)
        view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=self.find, history=self.history,
                               say=lambda to, text, *, status=None: self.say(to, text))
        await asyncio.wait_for(self.b.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)

    def cz(self, since=0):
        return [t for to, t in self.out[since:] if to == CZ]


def partner(pid, slots, reply=None):
    at = iso((NOW + timedelta(days=2)).replace(hour=16, minute=0, second=0, microsecond=0))

    async def bot(city, text):
        f = city.b.fields_of(text.splitlines()[0])
        await asyncio.sleep(0.05)
        if text.startswith("need.request"):
            lines = [f"offer offer_id=of-{s} slot_id={s} doctor_id=D-{s} facility={pid} procedure=VIS-FIS "
                     f"agenda_date={at} expiry={at} priority=B request_id={f.get('request_id', '')}" for s in slots]
            await city.turn(msg(pid, BC, "Ecco.\n" + "\n".join(lines), city.need))
        elif text.startswith("confirm") and reply:
            line = reply.format(**f)
            if line.startswith("booking.receipt"):  # impostor audit #4: the Ledger's own commit row comes first
                rf = city.b.fields_of(line)
                city.rows.append(msg(LEDGER, BC, f"committed booking_id={rf['booking_id']} ledger_ref={rf['ledger_ref']}", city.need))
            await city.turn(msg(pid, BC, line, city.need))
    return bot


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for to, t in (city.out if city else []):
            print(f"      > {to[-14:]:>14} : {t[:140]!r}")


async def setup(label, reply, more=None):
    b = load()
    need = f"N-{label}"
    peers = [Peer(agent_id=P1, role="facility", capabilities=("hospital.physiatry",))]
    city = City(b, need, peers)
    city.bots[P1] = partner(P1, ["P1a", "P1b"], reply)
    if more:
        peers.append(Peer(agent_id=P2, role="facility", capabilities=("hospital.physiatry",)))
        city.bots[P2] = partner(P2, ["P2a"])
    await city.turn(msg(CZ, BC, ASK, need))
    await asyncio.sleep(0.5)
    await city.turn(msg(CZ, BC, "consent purpose=cross_asst consent_ref=cons-x-cross", need))
    await asyncio.sleep(0.2)
    b._CITIZEN_RATE.clear()  # the steps above are seconds apart here, minutes apart live
    return b, city, need


async def refusal(label, reply):
    b, city, need = await setup(label, reply)
    i = len(city.out)
    await city.turn(msg(CZ, BC, "confirm offer_id=of-P1a consent_ref=cons-x-cross", need))
    await asyncio.sleep(1.0)
    cz = city.cz(i)
    check(any(t.startswith("cancel offer_id=of-P1a") for t in cz), f"{label}: citizen told the partner refused of-P1a", city)
    check(any("offer_id=" in t and not t.startswith("cancel") for t in cz), f"{label}: alternatives or waiting list sent", city)
    check(not any("slot_id=P1a" in t for t in cz), f"{label}: refused slot P1a never replayed", city)
    j = len(city.out)
    await city.turn(msg(CZ, BC, "confirm offer_id=of-P1a consent_ref=cons-x-cross", need))
    await asyncio.sleep(0.5)
    check(bool(city.cz(j)), f"{label}: re-confirm of the refused offer gets a reply", city)
    check(sum(1 for to, t in city.out if to == P1 and t.startswith("confirm")) == 1, f"{label}: confirm sent to the partner once", city)
    k = len(city.out)
    await city.turn(msg(CZ, BC, "allora? ci sono novità?", need))
    await asyncio.sleep(0.3)
    check(not any("slot_id=P1a" in t for t in city.cz(k)), f"{label}: status never replays the refused offer", city)


async def silent():
    b, city, need = await setup("silent", None)
    b._XA_CONFIRM_WAIT = 2.0
    i = len(city.out)
    t0 = time.monotonic()
    await city.turn(msg(CZ, BC, "confirm offer_id=of-P1a consent_ref=cons-x-cross", need))
    cz = city.cz(i)
    check(bool(cz) and time.monotonic() - t0 < 3.0, "silent: the confirm turn tells the citizen it waits for the other ASST", city)
    j = len(city.out)
    await city.turn(msg(CZ, BC, "confirm offer_id=of-P1a consent_ref=cons-x-cross", need))
    check(bool(city.cz(j)), "silent: a re-confirm while waiting gets the status", city)
    await asyncio.sleep(3.5)
    late = city.cz(j)
    check(any("lista d'attesa" in t or "waiting_list=true" in t for t in late[1:]),
          "silent: past the wait the alternatives go out without a new citizen message", city)
    k = len(city.out)
    await city.turn(msg(CZ, BC, "confirm offer_id=of-P1a consent_ref=cons-x-cross", need))
    check(bool(city.cz(k)), "silent: a re-confirm after the wait gets a reply", city)
    check(sum(1 for to, t in city.out if to == P1 and t.startswith("confirm")) == 1, "silent: confirm sent to the partner once", city)


async def receipt_echo():
    b, city, need = await setup("receipt", "booking.receipt booking_id=bk-77 ledger_ref=c-77 offer_id={offer_id} slot_id={slot_id}")
    i = len(city.out)
    await city.turn(msg(CZ, BC, "confirm offer_id=of-P1a consent_ref=cons-x-cross", need))
    await asyncio.sleep(0.8)
    check(any("bk-77" in t for t in city.cz(i)), "receipt: a partner receipt echoing offer_id and slot_id reaches the citizen", city)


async def main():
    for label, reply in (
        ("kit", "cancel reason=SLOT_TAKEN"),
        ("refused", "refused op=confirm offer_id={offer_id} reason=SLOT_TAKEN"),
        ("error409", "error status=409 code=SLOT_TAKEN offer_id={offer_id}"),
        ("plain", "Mi dispiace, non posso confermare: il posto non è più disponibile."),
        ("hold_expired", "error code=HOLD_EXPIRED status=409"),
        ("cancel_id", "cancel offer_id={offer_id} reason=NO_CAPACITY"),
    ):
        await refusal(label, reply)
    await silent()
    await receipt_echo()
    print(f"\n{'OK' if not FAILS else 'FAIL'} test_xa_partner_refusal ({len(FAILS)} failed)")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
