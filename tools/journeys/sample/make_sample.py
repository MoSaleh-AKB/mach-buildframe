#!/usr/bin/env python3
"""Write sample/synthetic-dump.json: SYNTHETIC journeys shaped like /v1/team/journeys(+detail).

Used to test analyze.py before the first real citizens. Agent ids are this team's card ids.
"""
import json
from pathlib import Path

BC, GP, HO, SP, FC = ("agent:y6hxsd766sys", "agent:jmqwr12vy2j6", "agent:r8yprrs0e8be",
                      "agent:dqfw9nh15dn4", "agent:pv3a0t3tyqdq")
OTHER = "agent:otherteam/booking"


def msg(i, t, frm, to, typ, text):
    return {"msg_id": f"m{i}", "sent_at": f"2026-11-20T21:{t:02d}:00Z", "type": typ, "from": frm, "to": to,
            "body": {"text": text}}


def journey(n, state, minute, messages, rows=(), ratings=(), review=None):
    nid = f"need:W01QQQ-synth-{n:03d}"
    d = {"journey": {"need_id": nid, "wave_id": "W01QQQ", "citizen": f"citizen:asst-centro-{n:03d}", "level": 1,
                     "state": state, "updated_at": f"2026-11-20T21:{minute:02d}:30Z"},
         "messages": messages,
         "verdict": {"record_id": f"rec-synth-{n:03d}", "time": f"2026-11-20T21:{minute:02d}:40Z",
                     "rubric": "rubric.v1", "rule_versions": [], "facts": [],
                     "answer_set": {"size": 3, "pref_size": 1, "effective_size": 2},
                     "rows": list(rows)},
         "ratings": list(ratings),
         "review": review}
    return nid, d


def rev(n, agents):
    return {"record_id": f"rev-synth-{n:03d}", "fault": None, "agents": agents}


def ra(aid, p=0, c=0, d=0, **notes):
    return {"agent_id": aid, "proactivity": p, "collaboration": c, "domain": d, "notes": notes, "evidence": {}}


def main():
    c = lambda n: f"citizen:asst-centro-{n:03d}"
    J = [
        journey(1, "failed", 5, [msg(1, 1, c(1), BC, "need", "Ho la ricetta per una ecografia, quando posso?"),
                                 msg(2, 2, BC, c(1), "data.request", "purpose=preferences")],
                rows=[{"row": "booking.none", "rater": c(1), "subject": BC, "score": -1, "reason": "no_booking"}],
                review=rev(1, [ra(BC, p=-1, proactivity="Il centro ha chiesto preferenze e poi nessuna offerta, il cittadino e' rimasto senza risposta.")])),
        journey(2, "failed", 9, [msg(1, 6, c(2), BC, "need", "Serve una visita cardiologica"),
                                 msg(2, 8, BC, c(2), "info", "nessuno slot")],
                rows=[{"row": "booking.no_alt", "rater": c(2), "subject": BC, "score": -1, "reason": "no_booking"}]),
        journey(3, "failed", 14, [msg(1, 11, c(3), FC, "need", "Vorrei un colloquio al consultorio"),
                                  msg(2, 12, FC, c(3), "data.request", "purpose=prescription prescription_number?")],
                rows=[{"row": "consultorio.rx", "rater": c(3), "subject": FC, "score": -1, "reason": "prescription_demanded"}],
                review=rev(3, [ra(FC, p=-1, proactivity="Ha chiesto la ricetta al consultorio, risposta brusca con solo token.")])),
        journey(4, "done", 20, [msg(1, 15, c(4), GP, "need", "Ho mal di schiena da settimane"),
                                msg(2, 15, GP, c(4), "prescription", "procedure=RX_SPINE priority=D")],
                ratings=[{"record_id": "rat-4", "rater": c(4), "subject": GP, "score": 2, "reason": "prescription_ok"}],
                review=rev(4, [ra(GP, p=1, c=1, proactivity="Ha spiegato i passi successivi.")])),
        journey(5, "done", 26, [msg(1, 21, c(5), GP, "need", "Ho una tosse forte"),
                                msg(2, 25, GP, c(5), "prescription", "procedure=RX_CHEST priority=U")],
                rows=[{"row": "gp.priority", "rater": c(5), "subject": GP, "score": -1, "reason": "wrong_priority"}],
                review=rev(5, [ra(GP, p=-1, proactivity="Risposta arrivata in ritardo, quattro minuti dopo.")])),
        journey(6, "done", 33, [msg(1, 28, c(6), SP, "checkin", "booking_id=b6"),
                                msg(2, 30, SP, c(6), "info", "visita fatta")],
                rows=[{"row": "spec.report", "rater": c(6), "subject": SP, "score": -1, "reason": "report_missing"}]),
        journey(7, "failed", 38, [msg(1, 34, c(7), OTHER, "need", "prenotazione"),
                                  msg(2, 35, OTHER, c(7), "info", "...")],
                rows=[{"row": "booking.none", "rater": c(7), "subject": OTHER, "score": -1, "reason": "no_booking"}]),
        journey(8, "done", 44, [msg(1, 40, c(8), BC, "need", "ricetta per risonanza"),
                                msg(2, 42, BC, c(8), "booking.receipt", "booking_id=b8")],
                rows=[{"row": "booking.S_pref", "rater": c(8), "subject": BC, "score": 2, "reason": "s_pref"}],
                review=rev(8, [ra(BC, p=-1, proactivity="Corretto ma passivo: ha fatto solo quanto richiesto, nessun promemoria.")])),
        journey(9, "open", 50, [msg(1, 49, c(9), HO, "checkin", "booking_id=b9")]),
    ]
    for _, d in J:
        if not d["review"] and d["journey"]["state"] == "open":
            d["verdict"] = None
    dump = {"list": {"rubric": "rubric.v1", "journeys": [d["journey"] for _, d in J]},
            "details": {nid: d for nid, d in J},
            "assignment": {"roles": []},
            "pulled_at": "2026-11-20T21:55:00Z", "synthetic": True}
    out = Path(__file__).with_name("synthetic-dump.json")
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=1))
    print(out)


if __name__ == "__main__":
    main()
