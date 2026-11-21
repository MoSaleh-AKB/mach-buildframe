"""#110 (R25): _STUCK leak. A pass called outside _advance (no-capacity re-offer, past confirm re-offer,
late give-up) ran out before the Ledger answered: the citizen got "le propongo subito un'alternativa"
and then nothing, because only _advance cleared _STUCK and started the background resume.

Case: the Ledger is quiet during the pass, then answers. _advance_alone is called directly (as the
no-capacity re-offer and the late give-up do). Must hold:
  (a) _STUCK is cleared for the message after the pass;
  (b) a background resume is scheduled for the need;
  (c) once the Ledger answers, the citizen really gets the alternative (an offer line).

Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_110_stuck_leak.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import LEDGER, Message, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "booking_centre"
os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 0.3
brain._RESUME_PAUSE = 0.05
brain._RESUME_WAIT = 3.0
brain.role_of = lambda: "booking_centre"

BC, HO, CZ = "agent:t110bc", "agent:t110ho", "citizen:t110-c1"
SEQ = [0]
RX = ("prescription prescription_number=RX1 patient=" + CZ + " procedure=VIS-FIS priority=B state=issued")
AGENDA = "agenda agenda_now=2026-11-23T08:00:00+00:00"
SLOTS = "slots page=1/1\n" + "\n".join(
    f"slot_id=S{i} doctor_id=asst-centro-h1 at=2026-11-2{3 + i}T09:00:00+00:00 day=x facility={HO} "
    "procedure=VIS-FIS free=1" for i in (1, 2, 3))


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m110-{SEQ[0]}", need_id=need,
                   body={"text": text}, sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))


class City:
    """The Ledger stays silent while quiet; wake() then answers every question asked so far."""

    def __init__(self, need):
        for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE", "_SENT",
                     "_HELD", "_STUCK", "_RESUMED", "_RESUMING", "_GAVE_UP", "_GATES"):
            g = getattr(brain, name, None)
            if hasattr(g, "clear"):
                g.clear()
        self.agent_id, self.address, self.need, self.inbound = BC, "", need, None
        self.rows, self.out, self.quiet = [], [], True

    def answer(self, head):
        text = {"agenda": AGENDA, "world": SLOTS, "prescription": RX}.get(head)
        if text and not any(r.sender == LEDGER and r.body.get("text") == text for r in self.rows):
            self.rows.append(msg(LEDGER, BC, text, self.need))

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER and not self.quiet:
            self.answer(text.split()[0])
        return "x"

    def wake(self):
        self.quiet = False
        for head in ("prescription", "agenda", "world"):
            self.answer(head)

    async def find(self, query):
        return [Peer(agent_id=HO, role="hospital", capabilities=("hospital.physiatry",))]

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {detail[:220]!r}"))
    if not ok:
        FAILS.append(name)


def offers(city):
    return [t for to, t in city.out if to == CZ and any(l.startswith("offer ") for l in t.splitlines())]


async def case(name, replacing):
    need = f"N-110-{name}"
    city = City(need)
    ask = msg(CZ, BC, "need.request area.district=6 lang=it prescription_number=RX1\nBuongiorno, vorrei prenotare.",
              need)
    city.rows.append(ask)
    city.inbound = ask
    if replacing:  # the no-capacity cancel already told the citizen an alternative is coming
        city.rows.append(msg(BC, CZ, "cancel offer_id=o-old reason=no_capacity\nQuella disponibilità non è più "
                             "valida: le propongo subito un'alternativa.", need))
    await asyncio.wait_for(
        brain._advance_alone(city, ask, list(city.rows), need, fixed=None, replacing=replacing), 20
    )
    check(f"{name}: no offer while the Ledger is quiet", not offers(city), " | ".join(offers(city)))
    check(f"{name}: _STUCK cleared after the pass", ask.msg_id not in brain._STUCK, repr(dict(brain._STUCK)))
    check(f"{name}: resume scheduled", need in brain._RESUMING, repr(list(brain._RESUMING)))
    city.wake()
    for _ in range(120):
        if offers(city):
            break
        await asyncio.sleep(0.05)
    check(f"{name}: the alternative is really sent once the Ledger answers", bool(offers(city)),
          " | ".join(t for to, t in city.out if to == CZ))
    for task in list(brain._RESUMING.values()):
        task.cancel()
    await asyncio.sleep(0)


async def main():
    await case("no_capacity_reoffer", True)
    await case("late_give_up_pass", False)
    print(f"\n{'OK' if not FAILS else 'FAILED'} test_110_stuck_leak ({len(FAILS)} failing)")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
