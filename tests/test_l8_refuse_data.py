"""L8 hostile citizen at the desk who REFUSES the data we ask (no prescription number, no consent,
no tax code): an honest answer with the reason and an alternative, never "working on it", never the
same offers replayed, never a booking, never the same line twice.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_l8_refuse_data.py [asst/_shared/agent.py]
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


def offers_in(texts):
    return any(line.startswith("offer ") for t in texts for line in t.splitlines())


WORKING = ("Sto lavorando", "I am working")


async def no_rx(name, turns, reason_words):
    city = City(f"N-{name}")
    seen = []
    for i, text in enumerate(turns):
        got = await turn(city, text)
        check(f"{name} t{i} answered", bool(got), text)
        if i:
            check(f"{name} t{i} honest (no 'working on it')", not any(w in g for g in got for w in WORKING), " | ".join(got))
            check(f"{name} t{i} says why + what to do", any(re.search(reason_words, g, re.I) for g in got), " | ".join(got))
        seen += got
    check(f"{name} never the same line twice", len(seen) == len(set(seen)), " | ".join(seen))
    check(f"{name} nothing sent to agents", not any(to.startswith("agent:") for to, _ in city.out))


async def after_offers(name, refusals, reason_words, lang="it"):
    city = City(f"N-{name}")
    first = await turn(city, "Buongiorno, devo prenotare prescription_number=RX1" + (" lang=en" if lang == "en" else ""))
    check(f"{name} offers first", offers_in(first), " | ".join(first))
    seen = list(first)
    for i, text in enumerate(refusals):
        got = await turn(city, text)
        check(f"{name} r{i} answered", bool(got), text)
        check(f"{name} r{i} offers not replayed", not offers_in(got), " | ".join(got))
        check(f"{name} r{i} says why + what to do", any(re.search(reason_words, g, re.I) for g in got), " | ".join(got))
        seen += got
    check(f"{name} never the same line twice", len(seen) == len(set(seen)), " | ".join(seen))
    check(f"{name} no confirm forwarded", not any(to.startswith("agent:") and "confirm" in t.split() for to, t in city.out))


async def mixed():
    city = City("N-mixed")
    got = await turn(city, "ecco la ricetta prescription_number=RX1, ma il codice fiscale non ve lo do")
    check("mixed: a normal request with a refusal still gets offers", offers_in(got), " | ".join(got))


import re  # noqa: E402

RX_WHY = r"ricetta|prescription"
CONS_WHY = r"consens|consent"
TAX_WHY = r"codice fiscale|tax code"


async def main():
    await no_rx("refuse_rx_it", ["voglio una visita ma non vi do niente", "non vi do il numero della ricetta, non vi do niente",
                                 "ho detto che non vi do niente!"], RX_WHY)
    await no_rx("refuse_rx_typo", ["voglio prenotare una visita", "non vi do nienet, nemmeno la ricetta"], RX_WHY)
    await no_rx("refuse_rx_en", ["I need a visit", "I won't give you anything, no number, nothing"], RX_WHY)
    await no_rx("refuse_rx_es", ["necesito una visita", "no le doy mis datos, nada"], RX_WHY)
    await no_rx("angry_no_data_en", ["you are useless idiots, book me now or I will sue you all", "answer me!!!"], RX_WHY)
    await no_rx("angry_no_data_it", ["siete degli incompetenti, prenotatemi subito", "rispondetemi!!!", "allora???"], RX_WHY)
    await after_offers("refuse_consent_it", ["non do il consenso, non voglio dare il consenso a nessuno", "no, niente consenso"],
                       CONS_WHY)
    await after_offers("refuse_consent_typo", ["non do il consenzo"], CONS_WHY)
    await after_offers("refuse_consent_en", ["I will not give my consent to anyone"], CONS_WHY, lang="en")
    await after_offers("refuse_consent_fr", ["je ne donne pas mon consentement"], CONS_WHY)
    await after_offers("refuse_tax_it", ["non vi do il codice fiscale ne altri dati"], TAX_WHY)
    await mixed()
    print(f"\n{'OK' if not FAILS else 'FAILED'} test_l8_refuse_data ({len(FAILS)} failing)")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
