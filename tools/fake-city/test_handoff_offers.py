"""GP hand-off to our desk: offers must reach the CITIZEN (live N-...260 and N-...1760003).

The citizen wrote to the GP, the GP handed off to our desk; the desk used to answer the GP with a
courtesy line (or a hold note to the citizen) and no offers ever came; the citizen's own
need.request then got only "Messaggio N ricevuto". Covers: (a) hand-off turn sends offers to the
citizen, also with a slow Ledger (agenda 14 s as a separate message); (b) memory-only or
agent-only offers never block a citizen's search; (c) the rescue sweep resumes a hand-off case
whose last line to the citizen is a hold note.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/test_handoff_offers.py
Exit 0 = pass.
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
spec = importlib.util.spec_from_file_location("brain", os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py"))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CONS, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "agent:t07/consultorio", "citizen:t07/c1"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
RX = "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=B state=issued"


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


def slots(first_hour):
    rows = []
    for i in range(1, 7):
        at = (BASE + timedelta(hours=first_hour + 7 + i)).isoformat()
        rows.append(f"slot_id=S{i} doctor_id=asst-centro-h1 at={at} day=mon facility={HO} procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    def __init__(self, me, rows, now_hours, answer_hold=False, delay=0.0):
        self.agent_id, self.address, self.rows, self.out = me, "", list(rows), []
        self.now_hours, self.answer_hold, self.inbound = now_hours, answer_hold, None
        self.delay = delay

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        if to != LEDGER:
            self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            head = text.split()[0]
            body = {"prescription": RX.replace("N-0001", "N-0001"), "agenda": f"agenda agenda_now={(BASE + timedelta(hours=self.now_hours)).isoformat()}",
                    "world": slots(self.now_hours)}.get(head)
            if body and self.delay:
                asyncio.get_running_loop().call_later(
                    self.delay, lambda: self.rows.append(msg(LEDGER, self.agent_id, body, need)))
            elif body:
                self.rows.append(msg(LEDGER, self.agent_id, body, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset(role):
    brain.role_of = lambda: role
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        getattr(brain, name).clear()
    brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, row):
    city.rows.append(row)
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find,
                           history=city.history, say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)
    return [text for to, text in city.out if to == CZ]


GP = "agent:t07/gp"
NUM = "prescription_number-N-0001"
RXLINE = f"prescription prescription_number={NUM} procedure=VIS-FIS priority=B state=issued"


def offers_to(city, to):
    return [t for r_to, t in city.out if r_to == to and "offer_id=" in t]


def handoff_case(need, hold=False, ago=200):
    rows = [
        msg(CZ, GP, "Ho mal di schiena da settimane.", need, ago=ago + 120),
        msg(GP, CZ, f"prescription prescription_number={NUM} procedure=VIS-FIS priority=B\nScriva al centro prenotazioni.",
            need, ago=ago + 60),
        msg(GP, BC, f"hand-off prescription_number={NUM} priority=B citizen={CZ}\nBuongiorno, la ricetta e nel registro.",
            need, ago=ago),
    ]
    if hold:
        rows.append(msg(BC, CZ, brain._HOLD_NOTE, need, ago=ago - 10))
    return rows



async def main():
    oks = []

    def check(ok, label, extra=""):
        print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if not ok else ""))
        oks.append(ok)

    # (a1) hand-off turn, fast Ledger: offers go to the citizen, none to the GP
    reset("booking_centre")
    need = "N-A1"
    city = City(BC, handoff_case(need, ago=5), now_hours=0)
    hand = city.rows[-1]
    city.rows.pop()
    await turn(city, hand)
    check(bool(offers_to(city, CZ)) and not offers_to(city, GP), "hand-off: offers to the citizen, not the GP",
          str([(to, t[:60]) for to, t in city.out]))
    # (a2) hand-off turn, slow Ledger: agenda and world arrive 14 s later as a separate message
    reset("booking_centre")
    need = "N-A2"
    city = City(BC, handoff_case(need, ago=5), now_hours=0, delay=14.0)
    hand = city.rows.pop()
    t0 = time.monotonic()
    await turn(city, hand)
    check(bool(offers_to(city, CZ)) and not offers_to(city, GP), "hand-off, Ledger 14 s: offers to the citizen",
          f"{time.monotonic() - t0:.0f}s " + str([(to, t[:60]) for to, t in city.out]))
    # (b) offers marked in memory and one sent to an agent: the citizen's own need.request still gets offers
    reset("booking_centre")
    need = "N-B"
    rows = handoff_case(need, hold=True, ago=800) + [
        msg(LEDGER, BC, RXLINE, need, ago=700),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", need, ago=700),
        msg(LEDGER, BC, slots(0), need, ago=700),
        msg(BC, GP, "offer offer_id=of-S1 slot_id=S1 doctor_id=asst-centro-h1 facility=" + HO, need, ago=690),
    ]
    brain._sent(need).offers.update({f"S{i}" for i in range(1, 7)})
    city = City(BC, rows, now_hours=0)
    got = await turn(city, msg(CZ, BC, f"need.request area=x lang=it prescription_number={NUM} service_type=hospital.physiatry", need))
    check(any("offer_id=" in t for t in got) and not any("Messaggio" in t for t in got),
          "citizen need.request: offers, no status line, when no offer reached the citizen", str(got))
    # (c) rescue: hold note only, 150 s old, no offer: the sweep resumes it and the offers go to the citizen
    reset("booking_centre")
    need = "N-C"
    rows = handoff_case(need, hold=True, ago=200)
    city = City(BC, rows, now_hours=0)
    city.inbound = None
    await brain._rescue(city, "booking_centre", SimpleNamespace(need_id="", msg_id=""), timer=True)
    check(bool(offers_to(city, CZ)) and not offers_to(city, GP), "rescue: hand-off with only a hold note is resumed",
          str([(to, t[:60]) for to, t in city.out]))
    again = len(offers_to(city, CZ))
    await brain._rescue(city, "booking_centre", SimpleNamespace(need_id="", msg_id=""), timer=True)
    check(len(offers_to(city, CZ)) == again, "rescue: no second offer message (anti-spam, case answered)")
    # (c2) too fresh (hold 30 s old): not picked; answered case: not picked
    now = datetime.now(timezone.utc)
    fresh = handoff_case("N-C2", hold=True, ago=40)
    check(not brain._rescue_picks(fresh, BC, SimpleNamespace(need_id="", msg_id=""), now), "rescue: fresh hold not picked")
    served = handoff_case("N-C3", hold=True, ago=200) + [
        msg(BC, CZ, "offer offer_id=of-S1 slot_id=S1 facility=" + HO, "N-C3", ago=100)]
    check(not brain._rescue_picks(served, BC, SimpleNamespace(need_id="", msg_id=""), now), "rescue: served hand-off not picked")
    # impostor: a hand-off from a stranger team is not ours
    stranger = [msg("agent:t99/gp", BC, f"hand-off prescription_number={NUM} priority=B citizen={CZ}", "N-D", ago=200)]
    check(brain._handoff_proxy(stranger[0], BC) is None, "stranger hand-off is not taken")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
