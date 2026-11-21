"""Port of !50 (the safe part): SI-16 wish echo, SI-19 info question, audit 7 consent cancel, Javad 1.
Run: <city python> -I tests/test_desk_backlog.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message, Peer

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ, P1 = "agent:y6hxsd766sys", "agent:r8yprrs0e8be", "citizen:t07/c1", "agent:t05/hospital"
N = [0]
NEED = "N-bcfix"


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


def base_rows(extra=(), first="book please prescription_number=RX1 lang=it"):
    rows = [msg(CZ, BC, first)]
    rows.append(msg(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"))
    return rows + list(extra)


def test_si16_wish_echo():
    t = brain._wish_echo("cerco il posto più vicino e prima di marzo, anche sabato")
    assert "più vicino" in t and "sabato" in t and "lista d'attesa" in t, t
    assert brain._wish_echo("book please prescription_number=RX1") == ""


async def test_si19_info_question():
    c = msg(CZ, BC, "ma perché voi invece del CUP? c'è una persona dietro?")
    f = await handle([c], c)
    out = said(f, CZ)
    assert "persona" in out and "Non invento" in out and "data.request" not in out, f.out


async def test_audit7_consent_cancel_asks_again():
    offer = "offer offer_id=of-1 slot_id=S1 doctor_id=D-h1 facility=%s procedure=VIS-FIS priority=B" % HO
    rows = base_rows([msg(BC, CZ, offer)])
    c = msg(LEDGER, BC, "cancel offer_id=of-1 reason=CONSENT_MISSING")
    rows.append(c)
    f = await handle(rows, c)
    out = said(f, CZ)
    assert "consent_ref" in out and "slot_id=" not in out, f.out


def test_javad1_only_my_asst():
    rows = [msg(CZ, BC, "va bene ma solo nella mia ASST")]
    assert brain._xa_refused(rows)
    assert brain._xa_refused([msg(CZ, BC, "revoco il consenso")])
    assert not brain._xa_refused([msg(CZ, BC, "confermo il primo")])

for t in (test_si16_wish_echo, test_javad1_only_my_asst):
    t()
for t in (test_audit7_consent_cancel_asks_again,):
    asyncio.run(t())
print("test_desk_backlog ok")
