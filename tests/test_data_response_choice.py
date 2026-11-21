"""A citizen data.response that names a choice is a confirm, never "Grazie, ne tengo conto." (issue #56).

Live journey 890904000197: 'data.response purpose=booking_confirmation' got "Grazie, ne tengo conto."
and the journey failed (-1 no_booking). With an offer_id / slot_id of ours it is the confirm path;
without one the open offers come again with the exact confirm line to copy (never a guessed booking,
never a new search). A real preferences answer keeps the new-search path.

Run: <city python> -I tests/test_data_response_choice.py [brain.py]
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
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ, C2 = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1", "citizen:t07/c2"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
RX = "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=B state=issued"


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


def slots():
    rows = [f"slot_id=S{i} doctor_id=asst-centro-h1 at={(BASE + timedelta(hours=7 + i)).isoformat()} day=mon "
            f"facility={HO} procedure=VIS-FIS free=1" for i in range(1, 7)]
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    def __init__(self, rows):
        self.agent_id, self.address, self.rows, self.out, self.inbound = BC, "", list(rows), [], None
        self.ledger = []

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            self.ledger.append(text)
            head = text.split()[0]
            body = {"agenda": f"agenda agenda_now={BASE.isoformat()}", "world": slots()}.get(head)
            if body:
                self.rows.append(msg(LEDGER, self.agent_id, body, need))
        else:
            self.out.append((to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        getattr(brain, name, {}).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, row):
    city.rows.append(row)
    city.inbound = row
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find,
                           history=city.history, say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)
    return [t for to, t in city.out if to == row.sender]


AT = (BASE + timedelta(hours=8)).isoformat()
OFFER = (f"offer offer_id=of-S1 slot_id=S1 doctor_id=asst-centro-h1 facility={HO} procedure=VIS-FIS "
         f"agenda_date={AT} expiry={AT} prescription_number=prescription_number-N-0001")


def offered(need, en=False, extra=()):
    first = "Hello, I need a visit. prescription_number=prescription_number-N-0001" if en else \
        "Vorrei prenotare. prescription_number=prescription_number-N-0001"
    return [
        msg(CZ, BC, first, need, ago=40),
        msg(LEDGER, BC, RX, need, ago=38),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", need, ago=38),
        msg(LEDGER, BC, slots(), need, ago=38),
        msg(BC, CZ, ("Here are the options.\n" if en else "Ecco le disponibilità.\n") + OFFER, need, ago=30),
        *extra,
    ]


def forwarded(need, ago):
    return msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=c-1 facility={HO} "
               "prescription_number=prescription_number-N-0001", need, ago=ago)


def to_ho(city):
    return [t for to, t in city.out if to == HO and t.split()[0] == "confirm"]



def worlds(city):
    return [t for t in city.ledger if t.startswith("world")]


async def main():
    oks = []

    def check(ok, name, got=()):
        print(("PASS " if ok else "FAIL ") + name)
        oks.append(ok)
        if not ok:
            for t in got:
                print("    ", t[:170].replace("\n", " | "))

    # 1. the live line: a choice with no id -> the open offers + the line to copy, no search, no hold
    reset(); city = City(offered("N-1"))
    got = await turn(city, msg(CZ, BC, "data.response purpose=booking_confirmation", "N-1"))
    check(any("confirm offer_id=of-S1" in t and "slot_id=S1" in t for t in got)
          and not any("ne tengo conto" in t for t in got), "no id: open offers + 'confirm offer_id=' line to copy", got)
    check(not to_ho(city) and not worlds(city) and not any(t.startswith("hold") for t in city.ledger),
          "no id: nothing forwarded, no new search, no hold", got)
    # 2. with offer_id and consent: forwarded like a confirm
    reset(); city = City(offered("N-2"))
    got = await turn(city, msg(CZ, BC, "data.response purpose=booking_confirmation offer_id=of-S1 consent_ref=c-1", "N-2"))
    check(len(to_ho(city)) == 1 and "slot_id=S1" in to_ho(city)[0] and f"citizen={CZ}" in to_ho(city)[0],
          "offer_id + consent: one confirm to the facility for slot S1", got)
    # 3. with slot_id only (purpose=selection): mapped to our offer of-S1
    reset(); city = City(offered("N-3"))
    got = await turn(city, msg(CZ, BC, "data.response purpose=selection slot_id=S1 consent_ref=c-1", "N-3"))
    check(len(to_ho(city)) == 1 and "offer_id=of-S1" in to_ho(city)[0], "slot_id only: mapped to our offer and forwarded", got)
    # 4. an offer_id that is not ours: nothing forwarded, answered
    reset(); city = City(offered("N-4"))
    got = await turn(city, msg(CZ, BC, "data.response purpose=offer_choice offer_id=of-FAKE consent_ref=c-1", "N-4"))
    check(not to_ho(city) and bool(got), "foreign offer_id: nothing forwarded, answered", got)
    # 5. a slot_id we never offered: no guess, the list again
    reset(); city = City(offered("N-5"))
    got = await turn(city, msg(CZ, BC, "data.response purpose=selection slot_id=S9 consent_ref=c-1", "N-5"))
    check(not to_ho(city) and any("confirm offer_id=of-S1" in t for t in got), "unknown slot_id: nothing forwarded, the list again", got)
    # 6. no consent / junk consent: never forwarded, answered
    for ref, need in (("", "N-6"), (" consent_ref=ok", "N-7")):
        reset(); city = City(offered(need))
        got = await turn(city, msg(CZ, BC, f"data.response purpose=booking_confirmation offer_id=of-S1{ref}", need))
        check(not to_ho(city) and bool(got), f"offer_id with {'junk' if ref else 'no'} consent: not forwarded, answered", got)
    # 7. English citizen: the line to copy in English
    reset(); city = City(offered("N-8", en=True))
    got = await turn(city, msg(CZ, BC, "data.response purpose=booking_confirmation", "N-8"))
    check(any("which offer you choose" in t for t in got) and not any("quale proposta" in t for t in got), "English: English line", got)
    # 8. after the receipt: the receipt again, not "Grazie"
    reset(); city = City(offered("N-9", extra=[forwarded("N-9", 60),
                                              msg(BC, CZ, "booking.receipt booking_id=bk-7 ledger_ref=c-7\nPrenotato.", "N-9", ago=30)]))
    got = await turn(city, msg(CZ, BC, "data.response purpose=booking_confirmation", "N-9"))
    check(any("bk-7" in t for t in got) and not to_ho(city) and not any("ne tengo conto" in t for t in got),
          "after the receipt: receipt repeated, nothing forwarded", got)
    # 9. a real preferences answer is not a choice: still a new search
    reset(); city = City(offered("N-10"))
    got = await turn(city, msg(CZ, BC, "data.response purpose=preferences_update days=tue", "N-10"))
    check(bool(worlds(city)) and not to_ho(city), "preferences_update: still a new search, nothing forwarded", got)
    # 10. a choice with a red flag in the text: the emergency first, no booking forwarded
    reset(); city = City(offered("N-11"))
    got = await turn(city, msg(CZ, BC, "data.response purpose=booking_confirmation offer_id=of-S1 consent_ref=c-1 "
                                       "ho un dolore al petto fortissimo e non respiro", "N-11"))
    check(any("112" in t or t.startswith("escalate.emergency") for t in got) and not to_ho(city),
          "choice + red flag: emergency line, nothing forwarded", got)
    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_data_response_choice")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
