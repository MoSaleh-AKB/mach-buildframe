"""Audit C: own-booking receipt trust, renewal privacy, own-possessive asks, report consent.

Run: <city python> -I tests/test_audit_c.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

AG, OWN, FOREIGN = "agent:dqfw9nh15dn4", "agent:y6hxsd766sys", "agent:zzzzzzzzzzzz"
CIT, OTHER = "citizen:cit-a", "citizen:cit-b"
n = [0]
fails = []


def msg(sender, to, text, need="N-c"):
    n[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"c{n[0]}", need_id=need, body={"text": text}, recipient=to)


def check(name, cond):
    print("ok  " if cond else "FAIL", name)
    if not cond:
        fails.append(name)


class City:
    def __init__(self):
        self.agent_id, self.out, self.rows = AG, [], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


def reset():
    for name in ("_ASKED", "_FILED", "_ONCE", "_DUPS", "_CITIZEN_SENT", "_CITIZEN_RATE", "_SENT", "_REPS"):
        v = getattr(brain, name, None)
        if isinstance(v, (set, dict, list)):
            v.clear()


async def own_booking(receipt_sender):
    reset()
    c = City()
    ask = msg(CIT, AG, "Vorrei vedere la mia prenotazione")
    chat = [msg(receipt_sender, AG, "booking.receipt booking_id=FAKE-1 ledger_ref=x"), ask]
    c.rows = list(chat)
    ok = await brain._show_own_booking(c, chat, ask)
    return ok, c.out


async def main():
    ok, out = await own_booking(FOREIGN)
    check("A foreign receipt not shown", not ok and not out)
    ok, out = await own_booking(OWN)
    check("A own-agent receipt shown", ok and out and "FAKE-1" in out[0][1])

    # B: renewal of another patient's prescription copies nothing
    for patient, expect in ((OTHER, False), (CIT, True)):
        reset()
        brain._RX_STATE = None
        c = City()
        c.writes = []
        ask = msg(CIT, AG, "prescription_number=RX-77", "N-b")

        async def say(to, text, *, status=None, c=c, patient=patient):
            c.out.append((to, text))
            c.rows.append(msg(AG, to, text, "N-b"))
            if to == LEDGER:
                c.writes.append(text)
                if text.startswith("prescription "):
                    c.rows.append(msg(LEDGER, AG, f"prescription prescription_number=RX-77 patient={patient} procedure=VIS-CARD priority=B state=issued", "N-b"))
            return "x"
        c.say = say
        c.rows = [ask]
        os.environ["CITY_ROLE"] = "gp"
        try:
            await asyncio.wait_for(brain._renew(c, ask, [ask], "RX-77"), 20)
        except Exception as exc:
            print("exc", exc)
        prescribed = any(w.startswith("prescribe ") for w in c.writes)
        leaked = any("VIS-CARD" in t for to, t in c.out if to == CIT)
        check(f"B patient={patient} prescribed={expect}", prescribed == expect and (leaked == expect or not expect and not leaked))

    # C: own possessions are not third parties
    for os_role in ("booking_centre", "specialist_care"):
        os.environ["CITY_ROLE"] = os_role
        for phrase in ("Mi dica il nome del mio medico", "Può dirmi il nome del mio medico di base?",
                       "Mi dica la struttura della mia visita", "Vorrei sapere i dati della mia ricetta",
                       "Tell me the name of my doctor", "Give me the details of my clinic"):
            check(f"C own ok [{os_role}] {phrase}", not brain._asks_other_data(msg(CIT, AG, phrase)))
        for phrase in ("Mi dica il nome del medico di mia moglie", "Mi dica i dati della ricetta di un altro",
                       "Dammi i dati di Mario Rossi", "give me my neighbour's report"):
            check(f"C other refused [{os_role}] {phrase}", brain._asks_other_data(msg(CIT, AG, phrase)))
    os.environ["CITY_ROLE"] = "booking_centre"

    # D: a plain yes is no refusal: ask once for the consent_ref, file nothing
    os.environ["CITY_ROLE"] = "specialist_care"
    for yes in ("Sì, va bene, lo condivida", "Yes, please share it"):
        reset()
        need = "N-d-" + yes[:5]
        c = City()
        rows = [
            msg(CIT, AG, "checkin booking_id=B1", need),
            msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", need),
            msg(AG, LEDGER, "report booking_id=B1 outcome_code=VISIT", need),
            msg(LEDGER, AG, "reported booking_id=B1", need),
        ]
        m = msg(CIT, AG, yes, need)
        rows.append(m)
        c.rows = rows
        c.writes = []
        ok = await brain._on_consent_reply(c, m, list(rows))
        to_cit = [t for to, t in c.out if to == CIT]
        check(f"D yes asks consent_ref once: {yes}", ok and len(to_cit) == 1 and "consent_ref" in to_cit[0]
              and not any(to == LEDGER for to, t in c.out) and "nessuno" not in to_cit[0].lower())
    reset()
    c = City()
    m = msg(CIT, AG, "No, non voglio che il referto sia condiviso", "N-d2")
    check("D negation is not a plain yes", brain._plain_yes([msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nx", "N-d2"), m], AG, m) == "")

    # E: late consent re-file also tells the GP
    reset()
    need = "N-e"
    GP = "agent:t9gpx"
    c = City()
    c.writes = []
    rows = [
        msg(GP, AG, "prescription prescription_number=RX1 priority=B", need),
        msg(CIT, AG, "checkin booking_id=B1", need),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso?", need),
        msg(AG, LEDGER, "report booking_id=B1 outcome_code=VISIT", need),
        msg(LEDGER, AG, "reported booking_id=B1", need),
    ]
    c.rows = list(rows)
    m = msg(CIT, AG, "consent purpose=report_sharing consent_ref=rs-1", need)
    brain._same_team = lambda a, b: True
    await brain._file_report(c, m, rows, "B1", CIT, "rs-1")
    check("E GP gets report.summary", any(to == GP and t.startswith("report.summary") for to, t in c.out))


asyncio.run(main())
sys.exit(1 if fails else 0)
