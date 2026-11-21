"""L8 booking: weekend deadline, U late in the day, P1 expiry at first contact, rule change between offer
and confirm, spam guard exemptions, P2 consent purpose, deaf citizen + red flag.

Every case here FAILS on booking-train-0310 (c015ca0) and PASSES on booking-train-0420.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_l8_booking.py [asst/_shared/agent.py]
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
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", os.path.abspath(path))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1"
UTC = timezone.utc
THU = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)  # Thursday
FRI_LATE = datetime(2026, 11, 20, 18, 30, tzinfo=UTC)  # Friday after closing
SEQ = [0]


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(UTC) - timedelta(seconds=ago)).isoformat())


def slot_line(sid, at, free=1, reserve=False):
    return (f"slot_id={sid} doctor_id=asst-centro-h1 at={at.isoformat()} day=x facility={HO} "
            f"procedure=VIS-FIS free={free}" + (" urgent_reserve=true" if reserve else ""))


class City:
    def __init__(self, me, rows, now, world, rules=""):
        self.agent_id, self.address, self.rows, self.out, self.inbound = me, "", list(rows), [], None
        self.now, self.world, self.rules = now, world, rules

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        if to != LEDGER:
            self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            head = text.split()[0]
            body = {"agenda": f"agenda agenda_now={self.now.isoformat()}",
                    "world": "slots page=1/1\n" + "\n".join(self.world),
                    "rules": self.rules}.get(head)
            if body:
                self.rows.append(msg(LEDGER, self.agent_id, body, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_CITIZEN_TOKENS", "_DUPS", "_CANCELS_SENT", "_REJECTED",
                 "_ONCE", "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        if hasattr(brain, name):
            getattr(brain, name).clear()
    for name in ("_REFUSAL_AT", "_PENDING_SEARCH", "_RESCUE_TRIED"):
        if hasattr(brain, name):
            getattr(brain, name).clear()
    brain._RESCUE_LAST[:] = [time.monotonic()]
    brain._RULES = {}
    brain._RULES_TS = ""


async def turn(city, row):
    city.rows.append(row)
    city.inbound = row
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find,
                           history=city.history,
                           say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)


def rx(priority, extra=""):
    return f"prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority={priority} state=issued{extra}"


def case(need, priority, now, rx_extra="", rules_row=None):
    rows = [msg(CZ, BC, "Vorrei una visita.", need, ago=40),
            msg(LEDGER, BC, rx(priority, rx_extra), need, ago=30),
            msg(LEDGER, BC, f"agenda agenda_now={now.isoformat()}", need, ago=30)]
    if rules_row:
        rows.append(msg(LEDGER, BC, rules_row, need, ago=30))
    return rows


def to_citizen(city):
    return [t for to, t in city.out if to == CZ]


async def main():
    oks = []

    def verdict(label, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + label + (f" {detail}" if detail else ""))
        oks.append(ok)

    # 1. weekend deadline: Thursday 10:00, priority B (2 working days), slot only on Monday.
    monday = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
    got = brain.fitting([slot_line("SMON", monday)], now=THU, priority="B", lang=None)
    verdict("1a B on Thursday: Monday slot (2nd working day) is offered", [s["slot_id"] for s in got] == ["SMON"], str(got))
    tuesday = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
    got = brain.fitting([slot_line("STUE", tuesday)], now=THU, priority="B", lang=None)
    verdict("1b B on Thursday: Tuesday (3rd working day) is not", got == [], str(got))
    friday_d = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)  # 5th working day after Thursday = Thursday 26 Nov... D=5: Fri,Mon,Tue,Wed,Thu
    got = brain.fitting([slot_line("SD", datetime(2026, 11, 26, 17, 30, tzinfo=UTC)), slot_line("SX", friday_d)],
                        now=THU, priority="D", lang=None)
    verdict("1c D on Thursday: 5th working day 17:30 in, the 6th out", [s["slot_id"] for s in got] == ["SD"], str(got))

    # 2. U late in the day: no slot today, next working morning offered as out-of-window, consent asked.
    reset()
    monday9 = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    city = City(BC, case("N-2", "U", FRI_LATE), FRI_LATE, [slot_line("SMON9", monday9)])
    await turn(city, msg(CZ, BC, "Vorrei una visita.", "N-2"))
    said = "\n".join(to_citizen(city))
    verdict("2 U after closing: Monday morning offered, out of window, agreement asked",
            "slot_id=SMON9" in said and "18:00" in said and "fuori" in said and "accordo" in said,
            said[:200].replace("\n", " | "))

    # 3. P1 expired prescription at first contact: tell the citizen, do not offer.
    reset()
    issued = (THU - timedelta(days=3)).isoformat()
    rules = "rules\nrule_id=P1 version=1"
    city = City(BC, case("N-3", "B", THU, rx_extra=f" issued_at={issued}", rules_row=rules), THU,
                [slot_line("SMON", monday)], rules=rules)
    await turn(city, msg(CZ, BC, "Vorrei una visita.", "N-3"))
    said = "\n".join(to_citizen(city))
    verdict("3 P1 expired prescription: renewal asked, no offer",
            "prescription_renewal" in said and "slot_id=" not in said and "offer " not in said,
            said[:160].replace("\n", " | "))

    # 4. rule change between offer and confirm: NO_CAPACITY refused hold -> reason + new valid slots, same turn.
    reset()
    wed = datetime(2026, 11, 23, 11, 0, tzinfo=UTC)  # Monday: inside B's window from Thursday
    rows = case("N-4", "B", THU)
    rows.append(msg(BC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=asst-centro-h1 facility={HO} procedure=VIS-FIS "
                    f"agenda_date={monday.isoformat()} expiry={monday.isoformat()} prescription_number=prescription_number-N-0001",
                    "N-4", ago=10))
    city = City(BC, rows, THU, [slot_line("S1", monday, free=0), slot_line("S2", wed), slot_line("S3", wed + timedelta(hours=1))])
    await turn(city, msg(LEDGER, BC, "refused op=hold code=NO_CAPACITY slot_id=S1", "N-4"))
    said = "\n".join(to_citizen(city))
    # Ledger contract: the world line says free=0 (no urgent_reserve), so NO_CAPACITY means "just taken".
    verdict("4 NO_CAPACITY refusal: short reason and fresh offers in the same turn",
            "appena preso" in said and "P3" not in said and "slot_id=S2" in said and "Nei tempi previsti" not in said, said[:200].replace("\n", " | "))

    # 5. spam guard: lines carrying new offer_id/slot_id always go out; a replay still does not.
    reset()

    class Inbound:
        need_id = "N-5"

    class Plain:
        def __init__(self):
            self.inbound, self.sent = Inbound(), []

        async def say(self, to, text, *, status=None):
            self.sent.append((to, text))

    plain = Plain()
    for i in range(10):
        await brain._say(plain, CZ, f"Quel posto è appena stato preso, ecco due nuovi.\noffer offer_id=of-n{i} slot_id=SN{i}")
    sent_new = [t for _, t in plain.sent if "of-n" in t]
    await brain._say(plain, CZ, "Quel posto è appena stato preso, ecco due nuovi.\noffer offer_id=of-n3 slot_id=SN3")
    after = [t for _, t in plain.sent if "of-n3" in t]
    verdict("5 ten lines with new offer ids all go out, the replay of one does not",
            len(sent_new) == 10 and len(after) == 1, f"({len(sent_new)} new, {len(after)} of-n3)")
    reset()
    plain = Plain()
    brain._CITIZEN_SENT[("N-5", CZ, "x")] = time.monotonic()
    rec = "Ricevuta pronta\nbooking.receipt booking_id=bk-1 ledger_ref=l-1"
    brain._citizen_sent("N-5", CZ, rec)
    verdict("5b a receipt is never dropped by the repeat guard", brain._citizen_spam("N-5", CZ, rec) is False)

    # 8. P2: a consent for a normal booking never unlocks a cross-ASST hold.
    reset()
    xa = [msg(BC, CZ, "data.request purpose=cross_asst fields=consent_ref\nserve il consenso", "N-8", ago=30),
          msg(CZ, BC, "consent purpose=booking consent_ref=cons-N-8-booking", "N-8", ago=20)]
    ref = brain._xa_consent_ref(xa, BC, CZ)
    verdict("8a booking consent (consent_ref ...-booking) is not a cross_asst consent", ref == "", repr(ref))
    xa.append(msg(CZ, BC, "consent purpose=cross_asst consent_ref=cons-N-8-cross", "N-8", ago=10))
    ref = brain._xa_consent_ref(xa, BC, CZ)
    verdict("8b a real cross_asst consent unlocks it", ref == "cons-N-8-cross", repr(ref))

    # 9. deaf citizen + red flag: 112 text kept, SMS / app line added.
    reset()
    city = City(BC, [], THU, [])
    row = msg(CZ, BC, "Sono sordo, ho un forte dolore al petto e non riesco a respirare", "N-9")
    await turn(city, row)
    said = "\n".join(to_citizen(city))
    verdict("9 deaf + red flag: 112 and the SMS / Where ARE U line",
            "chiami subito il 112" in said and "SMS" in said and "Where ARE U" in said,
            said[:200].replace("\n", " | "))

    print("ALL OK" if all(oks) else "SOME FAILED")
    raise SystemExit(0 if all(oks) else 1)


asyncio.run(main())
