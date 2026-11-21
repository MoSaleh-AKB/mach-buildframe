"""Booking centre: citizen preferences in free words (days, parts of the day, gender, clock times).

Run: <city python> -I tools/fake-city/test_bc_prefs.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

N = [0]


def chat(*texts):
    rows = []
    for text in texts:
        N[0] += 1
        rows.append(Message(type="chat", sender="citizen:t/c1", msg_id=f"m{N[0]}", need_id="n1",
                            body={"text": text}, recipient="agent:t/booking_centre"))
    return rows


def at(day, hhmm, doctor="D-h1"):
    h = int(hhmm[:2])
    return {"slot_id": f"S{day}{hhmm}", "doctor_id": doctor, "day": day,
            "at": f"2026-11-2{ {'mon': 3, 'tue': 4, 'wed': 5, 'thu': 6}[day] }T{hhmm}:00+00:00"}


def window(*t):
    return brain._window(chat(*t))


def days(*t):
    return brain._days(chat(*t))


assert window("preferisco la mattina") == (0, 780)
assert window("Meglio il pomeriggio per favore") == (780, 1440)
assert window("posso solo dopo le 17") == (1020, 1440)
assert window("dopo le 5") == (1020, 1440)
assert window("prima delle 10") == (0, 600)
assert window("dalle 9 alle 11") == (540, 660)
assert window("after 5pm") == (1020, 1440)
assert window("time=morning") == (0, 780)
assert window("non di mattina, meglio il pomeriggio") == (780, 1440)
assert window("la ricetta e' di 3 giorni fa, dopo 3 giorni") is None
assert window("ciao, mi serve una visita") is None
assert days("posso venire giovedi o venerdì") == {"thu", "fri"}
assert days("martedì mattina") == {"tue"}
assert days("non il lunedì") == {"tue", "wed", "thu", "fri", "sat", "sun"}
assert days("book on Wednesday please") == {"wed"}
assert days("days=mon,wed lang=it") == {"mon", "wed"}
assert days("la visita e' per mia madre") == frozenset()
assert brain._gender_words(chat("vorrei una dottoressa")) == "f"
assert brain._gender_words(chat("female doctor please")) == "f"
assert brain._gender_words(chat("medico uomo")) == "m"
assert brain._gender_words(chat("non voglio una dottoressa")) is None
assert brain._gender_words(chat("ok")) is None

morning, late, thu = at("mon", "09:00"), at("mon", "17:00"), at("thu", "09:00")
w = (0, 780)
assert brain._prefers(morning, None, frozenset(), w)
assert not brain._prefers(late, None, frozenset(), w)
assert brain._prefers(thu, None, frozenset({"thu"}), w)
assert not brain._prefers(morning, None, frozenset({"thu"}), w)
assert brain._prefers(late, None, frozenset(), None)
nod = dict(morning)
del nod["day"]
assert brain._prefers(nod, None, frozenset({"mon"}), w)  # weekday from the date


# end to end: the favourite slot is in the one offer message and comes first
BC, HO, CZ = "agent:t/booking_centre", "agent:t/hospital", "citizen:t/c1"


NEED = ["n9"]


def row(sender, to, text, need=None):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need or NEED[0], body={"text": text}, recipient=to)


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows):
        self.rows, self.out = list(rows), []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(row(BC, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


def line(i, day, hhmm, doc):
    d = {"mon": 23, "tue": 24, "wed": 25, "thu": 26}[day]
    return f"slot_id=S{i} doctor_id={doc} at=2026-11-{d}T{hhmm}:00+00:00 day={day} facility={HO} procedure=VIS-FIS free=1"


async def e2e(first, expect_first):
    NEED[0] = f"n{N[0]}"
    slots = "slots page=1/1\n" + "\n".join([
        line(1, "mon", "09:00", "D-h1"), line(2, "mon", "10:00", "D-h1"), line(3, "tue", "09:00", "D-h2"),
        line(4, "tue", "10:00", "D-h2"), line(5, "wed", "09:00", "D-h1"), line(6, "wed", "10:00", "D-h2"),
        line(7, "thu", "09:00", "D-h2"), line(8, "thu", "10:00", "D-h2"), line(9, "thu", "17:00", "D-h2"),
        line(10, "wed", "16:30", "D-h2"),
    ])
    rows = [row(CZ, BC, first),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=D state=issued"),
            row(LEDGER, BC, "agenda agenda_now=2026-11-23T07:00:00+00:00"), row(LEDGER, BC, slots)]
    fake = Fake(rows)
    await brain.handle("booking_centre", fake, rows[0], rows)
    offers = [t for to, t in fake.out if to == CZ and "slot_id=" in t]
    assert offers, (fake.out, fake.rows[-3:])
    ids = [tok.split("=")[1] for tok in offers[0].split() if tok.startswith("slot_id=")]
    assert ids[0] in expect_first, (first, ids, fake.out)


asyncio.run(e2e("serve visita prescription_number=RX1 lang=it, solo dopo le 16", {"S9", "S10"}))
asyncio.run(e2e("visita prescription_number=RX1 lang=it, giovedi pomeriggio", {"S9"}))


async def expired():
    NEED[0] = f"n{N[0]}"
    slots = "slots page=1/1\n" + line(1, "mon", "09:00", "D-h1")
    rx = "prescription prescription_number=RX1 procedure=VIS-FIS priority=D state=issued valid_until=2026-11-22T00:00:00+00:00"
    rows = [row(CZ, BC, "visita prescription_number=RX1 lang=it"), row(LEDGER, BC, rx),
            row(LEDGER, BC, "agenda agenda_now=2026-11-23T07:00:00+00:00"), row(LEDGER, BC, slots)]
    fake = Fake(rows)
    await brain.handle("booking_centre", fake, rows[0], rows)
    assert fake.out and not any("slot_id=" in t for _, t in fake.out), fake.out
    assert any(to == CZ for to, _ in fake.out), fake.out


asyncio.run(expired())
print("--- bc prefs: all passed")
