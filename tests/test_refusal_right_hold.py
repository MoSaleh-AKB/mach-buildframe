"""#95 (hospital): a Ledger refusal acts on the hold it NAMES, not on the case's last hold.
a) A late "refused commit hold_id=h1" after the desk moved the booking to S2 (held h2) must free
   h1 only: never "cancel hold_id=h2", never cancel offer of-S2 to the desk.
b) A refusal we cannot tie to a booker is never silent: the citizen of the case hears it.
Fails on development 6f7744ce (both cases), passes with the fix.
Run: <city python> -I -m pytest tests/test_refusal_right_hold.py -q
  or <city python> -I tests/test_refusal_right_hold.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.environ.get("BRAIN") or os.path.join(HERE, "..", "asst", "_shared", "agent.py")
HO, BC, CZ = "agent:t95/hospital", "agent:t95/booking_centre", "citizen:t95/c1"
N = [0]


def load(path):
    os.environ["CITY_ROLE"] = "hospital"
    spec = importlib.util.spec_from_file_location(f"brain95_{N[0]}", path)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "hospital"
    return b


def msg(sender, to, text, need="N-95"):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"r95-{N[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text})


class Fake:
    def __init__(self, rows):
        self.agent_id, self.address = HO, ""
        self.rows, self.out = list(rows), []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(HO, to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


async def run(b, rows, inbound):
    f = Fake(rows + [inbound])
    await b.handle("hospital", f, inbound, list(f.rows))
    return f.out


def case_late_refusal_keeps_good_hold(b):
    rows = [
        msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=cons-95 facility={HO}"),
        msg(HO, LEDGER, f"hold slot_id=S1 citizen={CZ} consent_ref=cons-95"),
        msg(LEDGER, HO, "held hold_id=h1 slot_id=S1 fence=1"),
        msg(HO, LEDGER, f"commit hold_id=h1 consent_ref=cons-95 citizen={CZ} fence=1"),
        msg(BC, HO, f"confirm offer_id=of-S2 slot_id=S2 citizen={CZ} consent_ref=cons-95 facility={HO}"),
        msg(HO, LEDGER, f"hold slot_id=S2 citizen={CZ} consent_ref=cons-95"),
        msg(LEDGER, HO, "held hold_id=h2 slot_id=S2 fence=2"),
    ]
    out = asyncio.run(run(b, rows, msg(LEDGER, HO, "refused commit hold_id=h1 code=NO_CAPACITY")))
    assert not any(to == LEDGER and "hold_id=h2" in t.split() for to, t in out), f"good hold h2 cancelled: {out}"
    assert not any(to == BC and "offer_id=of-S2" in t.split()[:2] for to, t in out), f"desk told S2 is gone: {out}"
    assert any(to == LEDGER and t == "cancel hold_id=h1" for to, t in out), f"refused hold h1 not freed: {out}"


def case_unknown_booker_not_silent(b):
    rows = [msg(CZ, HO, "Buongiorno, vorrei prenotare la visita.")]
    out = asyncio.run(run(b, rows, msg(LEDGER, HO, "refused hold code=SLOT_TAKEN")))
    assert any(to == CZ for to, _ in out), f"silent on a refusal with no booker: {out}"


def test_late_refusal_keeps_good_hold():
    case_late_refusal_keeps_good_hold(load(BRAIN))


def test_unknown_booker_not_silent():
    case_unknown_booker_not_silent(load(BRAIN))


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else BRAIN
    case_late_refusal_keeps_good_hold(load(path))
    case_unknown_booker_not_silent(load(path))
    print("test_refusal_right_hold: passed")
