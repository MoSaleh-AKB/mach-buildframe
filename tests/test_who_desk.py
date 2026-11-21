"""SI-19 (!56): the booking desk answers "who are you / are you a robot / a person" honestly, act line
first, then carries on (asks the prescription, or says the options already sent stay valid).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_who_desk.py [asst/_shared/agent.py]
"""


import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import LEDGER, Message, Need, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 0.6
brain._XA_BUDGET = 0.5
brain._XA_GRACE = 0.2
brain.role_of = lambda: "booking_centre"

BC, HO, CZ = "agent:t07bc", "agent:t07ho", "citizen:t07-c1"
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need, body={"text": text},
                   sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))


class City:
    def __init__(self, need):
        for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE", "_SENT",
                     "_HELD", "_XA_NEEDS"):
            g = getattr(brain, name, None)
            if hasattr(g, "clear"):
                g.clear()
        self.agent_id, self.address, self.need, self.inbound = BC, "", need, None
        self.rows, self.out = [], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER:
            head = text.split()[0]
            if head == "agenda":
                self.rows.append(msg(LEDGER, BC, "agenda agenda_now=2026-11-23T08:00:00+00:00", self.need))
            elif head == "world":
                self.rows.append(msg(LEDGER, BC, "slots page=1/1\n" + "\n".join(
                    f"slot_id=S{i} doctor_id=asst-centro-h1 at=2026-11-2{3 + i}T09:00:00+00:00 day=x facility={HO} "
                    "procedure=VIS-FIS free=1" for i in (1, 2, 3)), self.need))
            elif head == "prescription" and "prescription_number=RX1" in text.split():
                self.rows.append(msg(LEDGER, BC, "prescription prescription_number=RX1 patient=" + CZ
                                     + " procedure=VIS-FIS priority=B state=issued", self.need))
        return "x"

    async def find(self, query):
        return [Peer(agent_id=HO, role="hospital", capabilities=("hospital.physiatry",))]

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


async def turn(city, text):
    row = msg(CZ, BC, text, city.need)
    city.rows.append(row)
    city.inbound = row
    start = len(city.out)
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=row), city), 20)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    return [t for to, t in city.out[start:] if to == CZ]



FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {detail[:220]!r}"))
    if not ok:
        FAILS.append(name)


async def main():
    city = City("N-who1")
    got = " | ".join(await turn(city, "Perché dovrei scrivere a voi invece del CUP? C'è una persona dietro o è un robot?"))
    check("it: says automatic service", "servizio automatico" in got, got)
    check("it: CUP stays", "CUP" in got, got)
    check("it: asks prescription", "prescription_number" in got, got)
    city = City("N-who2")
    got = " | ".join(await turn(city, "Are you a real person?"))
    check("en: english answer", "automated service" in got and "servizio" not in got, got)
    city = City("N-who3")
    first = await turn(city, "Buongiorno, devo prenotare prescription_number=RX1")
    check("offers first", any(l.startswith("offer ") for t in first for l in t.splitlines()), " | ".join(first))
    got = " | ".join(await turn(city, "ma sei un robot?"))
    check("with offers: who + still valid", "servizio automatico" in got and "restano valide" in got, got)
    check("with offers: offers not replayed", not any(l.startswith("offer ") for l in got.splitlines()), got)
    city = City("N-who4")
    got = " | ".join(await turn(city, "Buongiorno, devo prenotare prescription_number=RX1"))
    check("normal booking unchanged", "servizio automatico" not in got, got)
    print("FAILED" if FAILS else "all passed", len(FAILS))
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
