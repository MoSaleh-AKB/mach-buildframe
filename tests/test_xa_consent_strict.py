"""Cross-ASST confirm: only the citizen's own cross_asst consent lets data go to a partner ASST,
and every confirm without it gets a visible answer (red-team L8: rt_sat_bc_19, rt_sat_bc_21,
rt_consent_01, rt_consent_03).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_xa_consent_strict.py [agent.py]
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ.setdefault("CITY_ROLE", "booking_centre")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, PARTNER, CIT = "agent:t07/booking_centre", "agent:k3m9partner1", "citizen:t07/c1"
OFFER = (
    f"offer offer_id=of-PX1 slot_id=PX1 doctor_id=D-p1 facility={PARTNER} procedure=VIS-FIS "
    "agenda_date=2026-11-24T10:00:00+00:00 expiry=2026-11-24T10:00:00+00:00 priority=B "
    "prescription_number=RX-T07-1 cross_asst=true"
)
N = [0]


def row(sender, text, need, to=BC):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{N[0]}", need_id=need, body={"text": text})


class City:
    agent_id = BC

    def __init__(self, chat):
        self.chat, self.out, self.inbound = chat, [], None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.chat.append(row(BC, text, self.inbound.need_id, to=to))

    async def history(self, peer=None, *, need_id=None):
        return list(self.chat)


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE"):
        if hasattr(brain, name):
            getattr(brain, name).clear()


async def journey(need, before):
    """Our partner offer and the consent ask went out (as _xa_after sends them), then ``before``."""

    reset()
    chat = [row(CIT, "Devo prenotare la fisioterapia, prescription_number=RX-T07-1", need)]
    city = City(chat)
    city.inbound = chat[0]
    await brain._say(city, CIT, OFFER)
    await brain._say(city, CIT, brain._XA_CONSENT)
    for text in before:
        chat.append(row(CIT, text, need))
    return city


async def confirm(city, text):
    message = row(CIT, text, city.inbound.need_id)
    city.chat.append(message)
    city.inbound = message
    mark = len(city.out)
    await brain._on_confirm(city, message, list(city.chat), hold_self=False)
    return city.out[mark:]


def leaked(out):
    return [t for to, t in out if to == PARTNER and "citizen=" in t]


def told(out):
    return [t for to, t in out if to == CIT]


async def main():
    oks = []

    def check(label, ok, out):
        print(("PASS " if ok else "FAIL ") + label)
        if not ok:
            for to, text in out:
                print("    ->", to, "|", text[:160].replace("\n", " | "))
        oks.append(ok)

    # 1. a junk consent_ref in the confirm is no consent (base refused, the first fix forwarded it)
    city = await journey("N-xa-junk", [])
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=none")
    check("junk ref: nothing to the partner", not leaked(out), out)
    check("junk ref: the citizen gets an answer", len(told(out)) == 1, out)
    city = await journey("N-xa-junk2", ["consent consent_ref=ok"])
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-N-booking")
    check("junk ref in a consent message: nothing to the partner", not leaked(out), out)

    # 2. a booking consent in the confirm: no data out, and a visible answer every time
    city = await journey("N-xa-booking", [])
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-N-booking")
    check("booking ref: nothing to the partner", not leaked(out), out)
    check("booking ref: the citizen gets an answer", len(told(out)) == 1, out)
    out = await confirm(city, "confirm offer_id=of-PX1")
    check("confirm without ref: the citizen gets an answer", len(told(out)) == 1, out)
    city = await journey("N-xa-twice", [])
    first = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-N-booking")
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-N-booking")
    check("same confirm twice: both get an answer", len(told(first)) == 1 and len(told(out)) == 1, first + out)

    # 2b. consent withdrawn in words, then confirm with the old ref: asked again, visibly
    city = await journey("N-xa-revoked", [
        "consent purpose=cross_asst consent_ref=cons-x1-cross_asst",
        "Ci ho ripensato: revoco il consenso, non voglio che i miei dati vadano all'altra ASST.",
    ])
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-x1-cross_asst")
    check("withdrawn ref: nothing to the partner", not leaked(out), out)
    check("withdrawn ref: the citizen gets an answer", len(told(out)) == 1, out)

    # 3. a ref the citizen gave for another purpose never becomes the cross consent
    city = await journey("N-xa-purpose", ["consent purpose=booking consent_ref=cons-bk-1"])
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-bk-1")
    check("booking-purpose ref in the confirm: nothing to the partner", not leaked(out), out)
    check("booking-purpose ref in the confirm: the citizen gets an answer", len(told(out)) == 1, out)
    city = await journey("N-xa-purpose2", [
        "consent purpose=booking consent_ref=cons-bk-2",
        "consent consent_ref=cons-bk-2",
    ])
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-bk-2")
    check("booking-purpose ref sent again without purpose: nothing to the partner", not leaked(out), out)

    # control: the citizen's own cross_asst consent still books at the partner
    city = await journey("N-xa-ok", ["consent purpose=cross_asst consent_ref=cons-x2-cross_asst"])
    out = await confirm(city, "confirm offer_id=of-PX1 consent_ref=cons-x2-cross_asst")
    sent = [t for to, t in out if to == PARTNER]
    check("cross consent: confirm goes to the partner with that ref",
          len(sent) == 1 and "consent_ref=cons-x2-cross_asst" in sent[0], out)

    print(("OK" if all(oks) else "FAIL") + f" test_xa_consent_strict {sum(oks)}/{len(oks)}")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
