"""Live fix: a cross-ASST consent answered as data.response, and preference answers after offers.
Run: <city python> -I tests/test_urgent_consent_prefs.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, PEER, CZ = "agent:t/booking_centre", "agent:t/hospital", "agent:t/peer_hospital", "citizen:t/c1"
N = [0]


def row(sender, to, text, need, mtype="chat"):
    N[0] += 1
    return Message(type=mtype, sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows):
        self.rows, self.out = list(rows), []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(row(self.agent_id, to, text, self.rows[0].need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return list(self.rows)

    async def find(self, query):
        return []


def slot(i, day, d, hh, doc):
    return (f"slot_id=S{i} doctor_id={doc} at=2026-11-{d}T{hh}:00+00:00 day={day} "
            f"facility={HO} procedure=VIS-FIS free=1")


WORLD = "slots page=1/1\n" + "\n".join([
    slot(1, "mon", 23, "09:00", "D-h1"), slot(2, "tue", 24, "09:00", "D-h2"),
    slot(3, "wed", 25, "10:00", "D-h1"), slot(4, "thu", 26, "15:00", "D-h2"),
    slot(5, "fri", 27, "09:00", "D-h2"),
])
XA = ("offer offer_id=xa-1 slot_id=SX1 doctor_id=D-x1 facility=" + PEER + " procedure=VIS-FIS "
      "agenda_date=2026-11-24T09:00:00+00:00 expiry=2026-11-24T09:00:00+00:00 cross_asst=true")
LOCAL = ("offer offer_id=of-S1 slot_id=S1 doctor_id=D-h1 facility=" + HO + " procedure=VIS-FIS "
         "agenda_date=2026-11-23T09:00:00+00:00")


def base(need):
    return [row(CZ, BC, "Buongiorno, vorrei una visita fisiatrica prescription_number=RX1 lang=it", need),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=D state=issued", need),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00", need), row(LEDGER, BC, WORLD, need)]


async def consent():
    need = "n-xa"
    rows = base(need) + [
        row(BC, CZ, "Nei tempi richiesti ci sono queste disponibilità\n" + XA, need),
        row(BC, CZ, "data.request purpose=cross_asst fields=consent_ref\nmi serve il suo consenso", need)]
    fake = Fake(rows)
    m = row(CZ, BC, "data.response purpose=cross_asst consent_ref=c-1", need, "data.response")
    fake.rows.append(m)
    await brain.handle("booking_centre", fake, m, list(fake.rows))
    out = [t for to, t in fake.out if to == CZ]
    assert out, fake.out
    assert not any(t.strip() == "Grazie, ne tengo conto." for t in out), out
    assert any("offer_id=xa-1" in t and "confirm" in t and "consent_ref=c-1" in t for t in out), out
    assert not any(t.startswith("escalate.emergency") for t in out), out
    assert not [1 for to, _ in fake.out if to == PEER], fake.out  # nothing sent to the partner before confirm


async def prefs():
    need = "n-pref"
    rows = base(need) + [row(BC, CZ, "offer\n" + LOCAL + "\n" + LOCAL.replace("of-S1", "of-S2").replace("S1", "S2"), need)]
    fake = Fake(rows)
    m = row(CZ, BC, "data.response purpose=preferences_for_new_booking giovedì pomeriggio", need, "data.response")
    fake.rows.append(m)
    await brain.handle("booking_centre", fake, m, list(fake.rows))
    out = [t for to, t in fake.out if to == CZ]
    assert not any(t.strip() == "Grazie, ne tengo conto." for t in out), out
    new = next((t for t in out if "offer_id=" in t), "")
    assert new, out
    # the new set carries the wish: Thursday afternoon (S4) comes first
    assert [ln for ln in new.splitlines() if "offer_id=" in ln][0].find("slot_id=S4") > 0, new


asyncio.run(consent())
asyncio.run(prefs())
print("OK test_urgent_consent_prefs")
