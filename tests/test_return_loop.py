"""Return loop (live citizens 91-100 back 7-9 times): a citizen who already got the waiting list
gets a different path on a new need for the same visit, then a clear "not possible", never the same
list; a new need for another visit (L8: two needs) is no return.
No doctor speaks English here: the desk offers the nearest Italian-speaking doctors at once, says so
honestly and adds the waiting-list line (it used to give the waiting list alone first).
Run: <city python> -I tests/test_return_loop.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO = "agent:t/booking_centre", "agent:t/hospital"
N = [0]


def row(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


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


# Only Italian-speaking doctors outside our table: nothing fits a lang=en citizen.
ITALIAN = "slots page=1/1\n" + "\n".join(
    f"slot_id=S{i} doctor_id=D-x{i} at=2026-11-2{2 + i}T09:00:00+00:00 day=mon facility={HO} "
    f"procedure=VIS-FIS free=1 languages=it" for i in (1, 2)
)
EMPTY = "slots page=1/1"


async def visit(citizen, need, priority, world, first="need.request lang=en prescription_number=RX1 I need a physiotherapy visit"):
    number = "RX2" if "RX2" in first else "RX1"
    procedure = "VIS-CARD" if number == "RX2" else "VIS-FIS"
    rows = [row(citizen, BC, first, need),
            row(LEDGER, BC, f"prescription prescription_number={number} procedure={procedure} priority={priority} "
                "state=issued", need),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00", need), row(LEDGER, BC, world, need)]
    fake = Fake(rows)
    await brain.handle("booking_centre", fake, rows[0], rows)
    return "\n".join(t for to, t in fake.out if to == citizen)


async def main():
    cz = "citizen:t/c100"
    first = await visit(cz, "n-1", "B", ITALIAN)
    assert "slot_id=S1" in first and "waiting_list=true" in first, first  # any doctor, and the list
    assert "cannot guarantee a doctor who speaks English" in first, first  # said honestly
    again = await visit(cz, "n-2", "B", ITALIAN)
    assert "slot_id=S1" in again, again
    assert "cannot guarantee a doctor who speaks English" in again, again
    empty = await visit(cz, "n-3", "B", EMPTY)
    assert "waiting_list=true" in empty and "slot_id=" not in empty, empty  # nothing free: the list
    last = await visit(cz, "n-4", "B", EMPTY)
    assert "not possible to book" in last and "waiting_list" not in last, last  # never the same list

    # L8: the same citizen's NEW need for a DIFFERENT visit is no return: never "not possible".
    card = await visit(cz, "n-4b", "B", EMPTY,
                       "need.request lang=en prescription_number=RX2 I need a heart check")
    assert "not possible to book" not in card and "waiting_list=true" in card, card

    # A new citizen gets the same honest offers on a first visit.
    other = await visit("citizen:t/c101", "n-5", "B", ITALIAN)
    assert "slot_id=S1" in other and "cannot guarantee a doctor who speaks English" in other, other

    # Priority P: the waiting list once, then real dates on the return.
    cp = "citizen:t/c102"
    p1 = await visit(cp, "n-6", "P", ITALIAN, "need.request prescription_number=RX1 visita fisiatrica")
    assert "waiting_list=true" in p1 and "slot_id=" not in p1, p1
    p2 = await visit(cp, "n-7", "P", ITALIAN, "need.request prescription_number=RX1 visita fisiatrica")
    assert "slot_id=S1" in p2, p2
    print("return loop: 8/8 ok")


asyncio.run(main())
