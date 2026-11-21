"""A1 fixes: P2 cross_asst consent, U lead, cancel word, cancel guard, U fallback, row over table.
Run: <city python> -I tests/test_a1.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message, Peer

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ, P1 = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1", "agent:t05/hospital"
N = [0]
NEED = "N-a1"


def msg(sender, to, text, need=NEED, kind="chat"):
    N[0] += 1
    return Message(type=kind, sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows, inbound):
        self.rows, self.out, self.inbound = list(rows), [], inbound

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


def said(f, to=None):
    return "\n".join(t for x, t in f.out if to is None or x == to)


async def handle(rows, inbound):
    f = Fake(rows, inbound)
    await brain.handle("booking_centre", f, inbound, list(rows))
    return f


def now(s):
    return brain._when(s)


def test_lead_and_u():
    assert brain._LEAD.total_seconds() == 30 * 60
    slots = [
        "slot_id=S%d doctor_id=D-h1 at=2026-11-23T%02d:00:00+00:00 day=mon facility=%s procedure=VIS-FIS free=1" % (i, h, HO)
        for i, h in ((1, 15), (2, 16), (3, 17))
    ]
    got = brain.fitting(slots, now=now("2026-11-23T15:00:00+00:00"), priority="U", lang="it", procedure="VIS-FIS")
    assert [s["slot_id"] for s in got] == ["S2", "S3"], got
    late = brain.fitting(slots, now=now("2026-11-23T16:20:00+00:00"), priority="U", lang="it")
    assert [s["slot_id"] for s in late] == ["S3"], late


def test_cancel_word():
    def m(t, who=CZ):
        return msg(who, BC, t)

    assert not brain._is_cancel(m("Non voglio cancellare niente, vorrei un posto"))
    assert not brain._is_cancel(m("I will not cancel anything, I want a slot"))
    assert brain._is_cancel(m("cancel offer_id=of-1"))
    assert brain._is_cancel(m("cancel booking_id=bk-1"))
    assert brain._is_cancel(m("cancel"))
    assert brain._is_cancel(m("fault.cancel booking_id=bk-1", "city:fault"))
    assert brain._is_cancel(m("booking cancelled", HO))


def test_row_over_table():
    # h1 is mon/tue/wed in the table: a row saying thu is still ours; gender from the row wins.
    row = "slot_id=T1 doctor_id=D-h1 at=2026-11-26T09:00:00+00:00 day=thu facility=%s procedure=VIS-FIS free=1 gender=m" % HO
    n = now("2026-11-23T08:00:00+00:00")
    assert brain.fitting([row], now=n, priority="D", lang="it", procedure="VIS-FIS")
    assert brain.fitting([row], now=n, priority="D", lang="it", gender="m", procedure="VIS-FIS")
    assert not brain.fitting([row], now=n, priority="D", lang="it", gender="f", procedure="VIS-FIS")


def base_rows(extra=()):
    rows = [msg(CZ, BC, "book please prescription_number=RX1 lang=it")]
    rows.append(msg(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"))
    rows += list(extra)
    return rows


OFFER = (
    "offer offer_id=of-P1a slot_id=P1a doctor_id=D-h1 facility=%s procedure=VIS-FIS "
    "agenda_date=2026-11-24T09:00:00+00:00 agenda_day=tue priority=B prescription_number=RX1 cross_asst=true" % P1
)


async def test_p2_consent():
    ask = "data.request purpose=cross_asst fields=consent_ref\nconsenso?"
    rows = base_rows([msg(BC, CZ, OFFER), msg(BC, CZ, ask)])
    # confirm with its own consent_ref but no answer to the cross_asst request: nothing leaves
    c = msg(CZ, BC, "confirm offer_id=of-P1a consent_ref=cons-x-booking")
    rows.append(c)
    f = await handle(rows, c)
    assert not any(x == P1 for x, _ in f.out), f.out
    assert "purpose=cross_asst" in said(f, CZ), f.out
    # the citizen answers the cross_asst request with a consent message: recorded, answered,
    # booking continues with that consent_ref at the partner
    k = msg(CZ, BC, "consent purpose=cross_asst consent_ref=cons-x-cross", kind="consent")
    rows.append(k)
    f = await handle(rows, k)
    assert said(f, CZ), "consent answer left silent"
    to_p = [t for x, t in f.out if x == P1]
    assert to_p and "consent_ref=cons-x-cross" in to_p[0] and "offer_id=of-P1a" in to_p[0], f.out


async def test_cancel_guard():
    offer = "offer offer_id=of-1 slot_id=S1 doctor_id=D-h1 facility=%s procedure=VIS-FIS priority=B" % HO
    offer2 = "offer offer_id=of-2 slot_id=S2 doctor_id=D-h1 facility=%s procedure=VIS-FIS priority=B" % HO
    rows = base_rows([msg(BC, CZ, offer + "\n" + offer2), msg(CZ, BC, "confirm offer_id=of-1 consent_ref=c"),
                      msg(BC, HO, "confirm offer_id=of-1 slot_id=S1 citizen=" + CZ),
                      msg(BC, CZ, "booking.receipt booking_id=bk-1 ledger_ref=l1\nok")])
    c = msg(HO, BC, "cancel offer_id=of-2 slot_id=S2")
    rows.append(c)
    f = await handle(rows, c)
    assert not f.out, f.out


async def test_u_fallback():
    res = "slot_id=R1 doctor_id=D-h1 at=2026-11-23T17:00:00+00:00 day=mon facility=%s procedure=VIS-FIS free=1 urgent_reserve=true" % HO
    rows = base_rows([msg(LEDGER, BC, "agenda agenda_now=2026-11-23T08:00:00+00:00"), msg(LEDGER, BC, "slots page=1/1\n" + res)])
    rows[1] = msg(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=U state=issued")
    f = await handle(rows, rows[0])
    assert "slot_id=R1" in said(f, CZ), f.out


async def test_ask_prefs():
    sl = "slot_id=S1 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue facility=%s procedure=VIS-FIS free=1" % HO
    for first, asks in (("book please prescription_number=RX1 lang=it", True),
                        ("book please prescription_number=RX1 lang=it days=tue", False)):
        nd = "N-prefs-%d" % asks
        rows = [msg(CZ, BC, first, nd),
                msg(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=D state=issued", nd),
                msg(LEDGER, BC, "agenda agenda_now=2026-11-23T08:00:00+00:00", nd),
                msg(LEDGER, BC, "slots page=1/1\n" + sl, nd)]
        f = await handle(rows, rows[0])
        out = said(f, CZ)
        assert "offer_id=" in out, f.out
        assert ("fields=days,gender,time" in out) == asks, f.out
        if asks:
            assert len([1 for x, t in f.out if x == CZ and "offer_id=" in t]) == 1, "question must ride with the offers"


asyncio.run(test_ask_prefs())
test_lead_and_u()
test_cancel_word()
test_row_over_table()
asyncio.run(test_p2_consent())
asyncio.run(test_cancel_guard())
asyncio.run(test_u_fallback())
print("test_a1 ok")
