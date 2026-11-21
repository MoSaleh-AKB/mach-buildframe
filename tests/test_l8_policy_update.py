"""L8 desk: a care rule (P1/P3) changes in the middle of a journey (policy.update from the city).

A slot offered before P3 that is now kept for urgent cases is never confirmed for a non-U citizen
(cancel + valid offers instead); a prescription that P1 made expired goes to the renewal, never to
a refused commit; a receipted booking stands; a pushed rule is not undone by an older ``rules``
answer. Each rule also runs in English, with a typo and mixed with a normal question.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_l8_policy_update.py [asst/_shared/agent.py]
Exit 1 on any FAIL.
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
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAILS = []
RX = "prescription_number-N-0001"
ASK_IT = f"Vorrei una visita fisiatrica. need.request lang=it prescription_number={RX}"
ASK_EN = f"I would like a physiatry visit, please. need.request lang=en prescription_number={RX}"
P3 = "policy.update rule_id=P3 version=2 reserve=0.2"
P1 = "policy.update rule_id=P1 version=2 validity_days=2"


def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "booking_centre"
    b._LLM_NET = False
    if hasattr(b, "_RESCUE_LAST"):
        b._RESCUE_LAST.clear()
        b._RESCUE_LAST.append(time.monotonic() + 9999)

    async def no_weather():
        return {}

    b._weather = no_weather
    return b


def iso(dt):
    return dt.isoformat()


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=iso(datetime.now(timezone.utc)))


def world():
    base = (NOW + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    rows = []
    for i in range(1, 4):
        at = base + timedelta(hours=3 * (i - 1))
        rows.append(f"slot_id=S{i} doctor_id=asst-centro-h{1 + i % 2} at={iso(at)} facility={HO} procedure=VIS-FIS free=1")
    return rows


class City:
    """One case mailbox with an instant Ledger whose answers can change mid-journey."""

    def __init__(self, brain, need):
        self.b, self.need, self.agent_id, self.address = brain, need, BC, ""
        self.rows, self.out, self.tasks = [], [], []
        self.reserve, self.rx_state, self.rules = set(), "issued", "rules"

    async def say(self, to, text, need):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, need))
        if to == LEDGER:
            self.tasks.append(asyncio.create_task(self._ledger(text, need)))
        elif to == HO and text.startswith("confirm"):
            self.tasks.append(asyncio.create_task(self._hospital(text, need)))
        return "ok"

    async def _ledger(self, text, need):
        await asyncio.sleep(0.05)
        head = text.split()[0]
        if head == "prescription":
            body = f"prescription prescription_number={RX} procedure=VIS-FIS priority=B state={self.rx_state}"
        elif head == "agenda":
            body = "agenda agenda_now=" + iso((NOW + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0))  # #121: 9 h lead
        elif head == "world":
            lines = [ln + (" urgent_reserve=true" if ln.split()[0][8:] in self.reserve else "") for ln in world()]
            body = "slots page=1/1\n" + "\n".join(lines)
        elif head == "rules":
            body = self.rules
        else:
            return
        self.rows.append(msg(LEDGER, BC, body, need))

    async def _hospital(self, text, need):
        slot = self.b.fields_of(text.splitlines()[0]).get("slot_id", "")
        await asyncio.sleep(0.2)
        r = msg(HO, BC, f"booking.receipt booking_id=bk-{slot} ledger_ref=lr-{slot} slot_id={slot}", need)
        await self.turn(r)

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        if row not in self.rows:
            self.rows.append(row)
        view = SimpleNamespace(inbound=row, agent_id=BC, address="")

        async def say(to, text, *, status=None):
            return await self.say(to, text, row.need_id)

        async def history(peer=None, *, need_id=None):
            return await self.history(peer, need_id=need_id)

        view.say, view.history, view.find = say, history, self.find
        await asyncio.wait_for(self.b.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)

    def to_citizen(self, since=0):
        return [t for to, t in self.out[since:] if to == CZ]

    def confirms(self):
        return [t for to, t in self.out if to == HO and t.startswith("confirm")]


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for to, t in (city.out if city else []):
            print(f"      {to[-14:]:>14} : {t[:140]!r}")


async def offers(city, ask):
    await city.turn(msg(CZ, BC, ask, city.need))
    await asyncio.sleep(0.4)
    got = []
    for t in city.to_citizen():
        for ln in t.splitlines():
            f = city.b.fields_of(ln)
            if ln.startswith("offer ") and f.get("slot_id"):
                got.append((f["offer_id"], f["slot_id"]))
    return got


async def p3_after_offers(label, ask, confirm_fmt, en=False):
    b = load()
    city = City(b, f"N-p3-{label}")
    got = await offers(city, ask)
    if not got:
        check(False, f"P3 {label}: offers sent", city)
        return
    oid, slot = got[0]
    city.reserve, city.rules = {slot}, "rules\nrule_id=P3 version=2 reserve=0.2"
    await city.turn(msg(LEDGER, BC, P3, city.need))
    await asyncio.sleep(0.3)
    mark = len(city.out)
    await city.turn(msg(CZ, BC, confirm_fmt.format(oid=oid), city.need))
    await asyncio.sleep(0.8)
    after = city.to_citizen(mark)
    said = "\n".join(after)
    check(not [c for c in city.confirms() if f"slot_id={slot}" in c],
          f"P3 {label}: confirm of the now-reserved {slot} is not forwarded", city)
    check(bool(after), f"P3 {label}: the citizen gets a reply", city)
    if "confirm offer_id=" in confirm_fmt:
        check(f"cancel offer_id={oid}" in said and "offer_id=of-" in said.replace(f"cancel offer_id={oid}", ""),
              f"P3 {label}: cancel of {oid} plus valid offers", city)
        word = "rule" if en else "regola"
        check(word in said, f"P3 {label}: the citizen is told the rule changed ({word})", city)
    re_sent = [t for t in city.to_citizen() if any(
        ln.startswith("offer ") and f"slot_id={slot}" in ln.split() for ln in t.splitlines())]
    check(len(re_sent) <= 1, f"P3 {label}: reserved {slot} never offered again", city)


async def p1_after_offers(label, ask, confirm_fmt):
    b = load()
    city = City(b, f"N-p1-{label}")
    got = await offers(city, ask)
    if not got:
        check(False, f"P1 {label}: offers sent", city)
        return
    oid = got[0][0]
    city.rx_state, city.rules = "expired", "rules\nrule_id=P1 version=2 validity_days=2"
    await city.turn(msg(LEDGER, BC, P1, city.need))
    await asyncio.sleep(0.3)
    await city.turn(msg(CZ, BC, confirm_fmt.format(oid=oid), city.need))
    await asyncio.sleep(0.8)
    check(not city.confirms(), f"P1 {label}: no confirm with the expired prescription", city)
    check(any("purpose=prescription_renewal" in t for t in city.to_citizen()),
          f"P1 {label}: renewal data.request sent", city)


async def receipt_stands():
    b = load()
    city = City(b, "N-rcpt")
    got = await offers(city, ASK_IT)
    await city.turn(msg(CZ, BC, f"confirm offer_id={got[0][0]} consent_ref=cons-N-0001-booking", city.need))
    await asyncio.sleep(0.8)
    city.reserve, city.rules = {got[0][1]}, "rules\nrule_id=P3 version=2 reserve=0.2"
    mark = len(city.out)
    await city.turn(msg(LEDGER, BC, P3, city.need))
    await asyncio.sleep(0.3)
    after = city.to_citizen(mark)
    check(len(city.confirms()) == 1, "receipt: one confirm only", city)
    check(not any(t.startswith("cancel") or "\noffer offer_id=of-" in t for t in after),
          "receipt: a rule change after the receipt cancels nothing and offers nothing", city)


async def push_not_undone():
    b = load()
    city = City(b, "N-push")
    await offers(city, ASK_IT)  # learns the old ``rules`` answer
    await city.turn(msg(LEDGER, BC, P3, city.need))
    await asyncio.sleep(0.3)
    check("P3" in b._RULES, f"push: P3 still known after the push turn ({b._RULES})", city)


async def main():
    confirm = "confirm offer_id={oid} consent_ref=cons-N-0001-booking"
    await p3_after_offers("it", ASK_IT, confirm)
    await p3_after_offers("en", ASK_EN, "Yes, the first one please. " + confirm, en=True)
    await p3_after_offers("typo", ASK_IT, "confrim offer_id={oid} consent_ref=cons-N-0001-booking")
    await p3_after_offers("mixed", ASK_IT, "Va bene la prima. " + confirm + " E c'è un parcheggio vicino?")
    await p1_after_offers("it", ASK_IT, confirm)
    await p1_after_offers("en", ASK_EN, "Yes please. " + confirm)
    await p1_after_offers("typo", ASK_IT, "confrim offer_id={oid} consent_ref=cons-N-0001-booking")
    await p1_after_offers("mixed", ASK_IT, "Ok la prima. " + confirm + " Devo portare qualcosa?")
    await receipt_stands()
    await push_not_undone()
    print(f"\n{'FAIL' if FAILS else 'OK'} test_l8_policy_update ({len(FAILS)} failed)")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
