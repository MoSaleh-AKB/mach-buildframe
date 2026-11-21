"""FAC-S04: the citizen says no to sharing the report after the visit turn and must get an answer.

The visit turn already sent six lines to the citizen within the minute (wait line, check-in,
consent ask, report status, report, follow-up prescription), so the citizen burst guard dropped
the plain-text answer to "No, non voglio che il referto venga condiviso con nessuno." (silence).

Run: <city python> -I tests/test_report_consent_refusal.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "specialist_care"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

AG, CIT = "agent:dqfw9nh15dn4", "citizen:c1"
n = [0]


def msg(sender, to, text, need, kind="chat"):
    n[0] += 1
    return Message(type=kind, sender=sender, msg_id=f"t{n[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    def __init__(self, rows, inbound):
        self.agent_id, self.rows, self.out, self.address, self.inbound = AG, rows, [], "", inbound

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text, self.inbound.need_id))
        return "x"

    async def history(self, **kw):
        return list(self.rows)


def visit(need, lang=""):
    """A finished visit: report filed unshared (no answer in time), Ledger said reported."""
    tag = f" lang={lang}" if lang else ""
    rows = [
        msg(CIT, AG, f"checkin booking_id=B1 ledger_ref=c-2{tag}", need),
        msg(AG, LEDGER, f"checkin booking_id=B1 citizen={CIT}", need),
        msg(LEDGER, AG, f"checked_in booking_id=B1 citizen={CIT}", need),
        msg(AG, CIT, "checkin booking_id=B1\nIl suo check-in è registrato.", need),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", need),
        msg(AG, LEDGER, f"prescribe patient={CIT} procedure=VIS-CARD priority=P follow_up_of=B1 prescription_number=FU-B1", need),
        msg(AG, LEDGER, "report booking_id=B1 outcome_code=VISIT follow_up_prescription_number=FU-B1", need),
        msg(LEDGER, AG, "reported booking_id=B1 outcome_code=VISIT", need),
        msg(AG, CIT, "report booking_id=B1 outcome_code=VISIT\nLa visita è registrata e il referto è archiviato.", need),
        msg(AG, CIT, "prescription prescription_number=FU-B1 procedure=VIS-CARD priority=P follow_up_of=B1\nRicetta.", need),
    ]
    for i in range(6):  # the six citizen lines of the visit turn went out within the minute
        brain._citizen_sent(need, CIT, f"riga {i} del turno della visita")
    return rows


async def answer(rows, text, need, kind="need.request"):
    m = msg(CIT, AG, text, need, kind)
    rows.append(m)
    city = Fake(rows, m)
    await brain.handle("specialist_care", city, m, list(rows))
    return city.out


async def late(text, lang="", need=None):
    need = need or f"N-{text[:12]}-{lang}"
    return await answer(visit(need, lang), text, need)


def refusal_ok(out, *words):
    to_cit = [t for to, t in out if to == CIT]
    assert to_cit, f"silence: {out}"
    assert not [t for to, t in out if to == LEDGER or to.startswith("agent:")], out
    low = to_cit[-1].lower()
    for w in words:
        assert w in low, (w, to_cit[-1])
    return to_cit[-1]


async def main():
    said = refusal_ok(await late("No, non voglio che il referto venga condiviso con nessuno."), "nessuno", "cambia idea")
    assert said.startswith("report booking_id=B1 outcome_code=VISIT\n"), said
    refusal_ok(await late("I don't want the report shared", lang="en"), "anyone", "change your mind")
    refusal_ok(await late("No, keep it private", lang="en"), "anyone")
    refusal_ok(await late("No quiero que se comparta el informe con nadie."), "nadie")
    refusal_ok(await late("Non, je ne veux pas que le rapport soit partagé."), "personne")

    # Refusal in time (before the Ledger answered): the closing line says what happens next.
    need = "N-intime"
    rows = [
        msg(CIT, AG, "checkin booking_id=B1", need),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", need),
        msg(CIT, AG, "No, preferisco di no", need),
        msg(AG, LEDGER, "report booking_id=B1 outcome_code=VISIT", need),
    ]
    rep = msg(LEDGER, AG, "reported booking_id=B1", need)
    rows.append(rep)
    city = Fake(rows, rep)
    await brain._on_reported(city, rep, rows)
    said = [t for to, t in city.out if to == CIT]
    assert said and "senza condividerlo" in said[0].lower() and "cambia idea" in said[0].lower(), said

    # A refusal given before our ask counts: the report is filed unshared at once.
    early = [
        msg(CIT, AG, "checkin booking_id=B1 eccomi, ma il referto non condividetelo con nessuno", "N-early"),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", "N-early"),
    ]
    assert brain._consent_answer(early, AG, "B1") == "", brain._consent_answer(early, AG, "B1")
    plain = [
        msg(CIT, AG, "checkin booking_id=B1 eccomi, no, non ho portato altri documenti", "N-early2"),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", "N-early2"),
    ]
    assert brain._consent_answer(plain, AG, "B1") is None, brain._consent_answer(plain, AG, "B1")
    given = [
        msg(CIT, AG, "checkin booking_id=B1 consent purpose=report_sharing consent_ref=rs-1", "N-early3"),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", "N-early3"),
    ]
    assert brain._consent_answer(given, AG, "B1") == "rs-1"

    # A plain "no" to something else is not a report refusal: nothing re-filed, no refusal line.
    need = "N-shared"
    rows = [
        msg(CIT, AG, "checkin booking_id=B1", need),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", need),
        msg(CIT, AG, "consent purpose=report_sharing consent_ref=rs-1", need),
        msg(AG, LEDGER, "report booking_id=B1 outcome_code=VISIT consent_ref=rs-1", need),
        msg(LEDGER, AG, "reported booking_id=B1", need),
        msg(AG, CIT, "report booking_id=B1 outcome_code=VISIT\nLa visita è registrata e condivisa.", need),
        msg(AG, CIT, "Le serve altro?", need),
    ]
    out = await answer(rows, "No", need)
    assert not [t for to, t in out if to == LEDGER], out
    assert not [t for to, t in out if "nessuno" in t.lower()], out
    out = await answer([], "No", "N-none")
    assert not [t for to, t in out if to == LEDGER or "nessuno" in t.lower()], out
    print("OK test_report_consent_refusal")


asyncio.run(main())
