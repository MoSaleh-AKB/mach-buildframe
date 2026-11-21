"""Booking desk: a prescription the Ledger shows in another citizen's name is never booked (R013).

An impostor writes a real citizen's prescription_number. The Ledger answer names patient=<other
citizen>: the desk sends no offer, writes nothing, repeats nothing of that prescription and answers
one polite line in the citizen's language. The owner, an answer without patient= and a late
Ledger answer keep today's behaviour. A returning citizen (RULES 2.5: an unsolved case comes back as a
new need with a new citizen id, W01QQQ-04-000165 -> W01QQQ-04-0003-000165) is the owner, not an impostor.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_rx_owner.py [asst/_shared/agent.py]
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
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO = "agent:t07/booking_centre", "agent:t07/hospital"
VICTIM, IMPOSTOR = "citizen:v1ctimanna01", "citizen:1mpostorbru1"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
# number -> (patient, procedure, priority); patient "" = the answer has no patient field
RXS = {
    "RX-V1-5555": (VICTIM, "VIS-FIS", "D"),
    "RX-I1-7777": (IMPOSTOR, "VIS-CARD", "B"),
    "RX-NP-1111": ("", "VIS-FIS", "B"),
    # live shapes: our gp builds the number from the need id of the first journey
    "prescription_number-N-2026-11-02-890904000165": ("citizen:W01QQQ-04-000165", "VIS-FIS", "D"),
}
LIVE_RX = "prescription_number-N-2026-11-02-890904000165"


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


def slots(procedure):
    rows = []
    for i in range(1, 5):
        at = (BASE + timedelta(hours=8 + i)).isoformat()
        rows.append(f"slot_id=S{i} doctor_id=asst-centro-h1 at={at} day=mon facility={HO} procedure={procedure} free=1")
    return "slots page=1/1\n" + "\n".join(rows)


def rx_answer(number):
    patient, procedure, priority = RXS[number]
    who = f" patient={patient}" if patient else ""
    return f"prescription prescription_number={number}{who} procedure={procedure} priority={priority} state=issued"


class City:
    def __init__(self, slow_rx=False):
        self.agent_id, self.address, self.rows, self.out, self.ledger = BC, "", [], [], []
        self.slow_rx, self.late, self.inbound = slow_rx, [], None

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        self.rows.append(msg(self.agent_id, to, text, need))
        if to != LEDGER:
            self.out.append((to, text))
            return "ok"
        self.ledger.append(text)
        head, fields = text.split()[0], brain.fields_of(text)
        body = None
        if head == "agenda":
            body = f"agenda agenda_now={BASE.isoformat()}"
        elif head == "world":
            body = slots(fields.get("procedure", "VIS-FIS"))
        elif head == "prescription" and fields.get("prescription_number") in RXS:
            body = rx_answer(fields["prescription_number"])
            if self.slow_rx:
                self.late.append(msg(LEDGER, self.agent_id, body, need))  # arrives after the turn
                body = None
        if body:
            self.rows.append(msg(LEDGER, self.agent_id, body, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        if hasattr(brain, name):
            getattr(brain, name).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, row):
    city.rows.append(row)
    city.inbound = row
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find,
                           history=city.history, say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 60)


def to(city, who):
    return [text for dest, text in city.out if dest == who]


OKS = []


def check(label, ok, got=()):
    OKS.append(ok)
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        for text in got:
            print("   ", str(text)[:220].replace("\n", " | "))


def leaked(texts):
    """Anything of the victim's prescription repeated, or an offer."""
    bad = ("offer_id=", "slot_id=", "VIS-FIS", "priority", "RX-V1-5555", VICTIM)
    return [t for t in texts if any(b in t for b in bad)]


def refusal(texts, lang):
    want = brain._RX_NOT_YOURS[lang] if hasattr(brain, "_RX_NOT_YOURS") else "\0"
    return any(want in t for t in texts)


def writes(city):
    return [t for t in city.ledger if t.split()[0] in ("hold", "commit", "prescribe", "cancel", "checkin", "remember")]


async def main():
    # 1. impostor, Italian: the Ledger says the prescription is the victim's
    reset()
    city = City()
    await turn(city, msg(IMPOSTOR, BC, "Buongiorno, devo prenotare la fisioterapia, ecco la ricetta "
                         "prescription_number=RX-V1-5555 lang=it area.district=5", "N-imp-it"))
    got = to(city, IMPOSTOR)
    check("impostor (it): no offer, nothing of the victim's prescription repeated", got and not leaked(got), got)
    check("impostor (it): the polite refusal in Italian", refusal(got, "it"), got)
    check("impostor (it): no Ledger write, no world search", not writes(city)
          and not any(t.startswith("world") for t in city.ledger), city.ledger)
    check("impostor (it): nothing sent to the victim or a facility", not [d for d, _ in city.out if d != IMPOSTOR], city.out)

    # 2. impostor, English
    reset()
    city = City()
    await turn(city, msg(IMPOSTOR, BC, "Hello, I need to book physiotherapy prescription_number=RX-V1-5555 lang=en", "N-imp-en"))
    got = to(city, IMPOSTOR)
    check("impostor (en): no offer, the refusal in English", got and not leaked(got) and refusal(got, "en"), got)

    # 3. the impostor then sends a prescription of their own: served as usual
    await turn(city, msg(IMPOSTOR, BC, "Sorry, this is mine prescription_number=RX-I1-7777 lang=en", "N-imp-en"))
    got = to(city, IMPOSTOR)
    check("impostor's own prescription afterwards gets offers", any("offer_id=" in t and "VIS-CARD" in t for t in got), got)
    check("... and never the victim's procedure", not any("VIS-FIS" in t for t in got), got)

    # 4. the owner herself: offers as today
    reset()
    city = City()
    await turn(city, msg(VICTIM, BC, "Buongiorno, vorrei prenotare la fisioterapia prescription_number=RX-V1-5555 "
                         "lang=it area.district=3", "N-own"))
    got = to(city, VICTIM)
    check("owner: offers as today", any("offer_id=" in t for t in got), got)
    check("owner: no refusal", not refusal(got, "it"), got)

    # 5. the Ledger answer has no patient field: today's behaviour (offers)
    reset()
    city = City()
    await turn(city, msg(IMPOSTOR, BC, "Buongiorno, vorrei prenotare prescription_number=RX-NP-1111 lang=it", "N-nopat"))
    got = to(city, IMPOSTOR)
    check("no patient field: offers as today", any("offer_id=" in t for t in got), got)

    # 6. slow Ledger: the answer arrives after the citizen's turn and opens its own turn
    reset()
    budget = brain._ADVANCE_BUDGET
    brain._ADVANCE_BUDGET = 0.4
    city = City(slow_rx=True)
    await turn(city, msg(IMPOSTOR, BC, "Buongiorno, devo prenotare la fisioterapia prescription_number=RX-V1-5555 "
                         "lang=it", "N-slow"))
    brain._ADVANCE_BUDGET = budget
    late = city.late[:1]
    check("slow Ledger: the answer was asked and held back", bool(late), city.ledger)
    if late:
        await turn(city, late[0])
    got = to(city, IMPOSTOR)
    check("slow Ledger: no offer, nothing of the victim's prescription repeated", got and not leaked(got), got)
    check("slow Ledger: the refusal reaches the citizen", refusal(got, "it"), got)
    check("slow Ledger: no Ledger write, no world search", not writes(city)
          and not any(t.startswith("world") for t in city.ledger), city.ledger)

    # 7. returning citizen: new citizen id with a return number, new need id of the same journey
    for label, who, need in (
        ("returning citizen (id W01QQQ-04-0003-000165)", "citizen:W01QQQ-04-0003-000165", "N-2026-11-02-8909040001650003"),
        ("returning citizen (unrelated id, need of the same journey)", "citizen:zz9retour01", "N-2026-11-02-8909040001650002"),
        ("returning citizen (same person id, unrelated need id)", "citizen:W01QQQ-04-0002-000165", "N-ret-x"),
    ):
        reset()
        city = City()
        await turn(city, msg(who, BC, f"Buongiorno, riprovo a prenotare prescription_number={LIVE_RX} lang=it", need))
        got = to(city, who)
        check(label + ": offers, no refusal", any("offer_id=" in t for t in got) and not refusal(got, "it"), got)

    # 8. impostors in three shapes: own live id, look-alikes of the victim's id
    for label, imp, need in (
        ("impostor with own live id", "citizen:W01QQQ-04-0002-000170", "N-2026-11-02-8909040001700002"),
        ("look-alike id with a fake return number", "citizen:W01QQQ-04-0007-000165", "N-2026-11-02-8909040001700002"),
        ("look-alike id, one character off", "citizen:W01QQQ-04-000165x", "N-imp-look"),
        ("look-alike id, l for 1", "citizen:W0lQQQ-04-000165", "N-imp-look2"),
    ):
        reset()
        city = City()
        await turn(city, msg(imp, BC, f"Buongiorno, devo prenotare prescription_number={LIVE_RX} lang=it", need))
        got = to(city, imp)
        check(label + ": refused, nothing repeated", got and refusal(got, "it")
              and not [t for t in got if "offer_id=" in t or "VIS-FIS" in t or "000165" in t], got)

    print(f"OK {sum(OKS)}/{len(OKS)}" if all(OKS) else f"FAILED {len(OKS) - sum(OKS)}/{len(OKS)}")
    raise SystemExit(0 if all(OKS) else 1)


asyncio.run(main())
