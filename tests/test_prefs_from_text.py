"""Booking desk: a preference the citizen writes in free text must reach the search.

Live (21 Nov, 1031 journeys): 624 cancels reason=lang in 98 journeys, 363 past, 48 day, 35 gender.
A need.request carried ``lang=tr`` while the text said "Need a doctor, English speaking", and the
desk searched by the field; a refusal "I need a doctor who speaks English. cancel offer_id=X
reason=lang" did not change the next offers; "medico donna" and "solo di mattina" only re-ordered
a list that still began with the wrong doctors.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_prefs_from_text.py asst/_shared/agent.py

Fake city: one desk, a synchronous Ledger, our doctors h1 (it,en, woman), h2 (it, man), s2 (it, woman);
the world rows carry gender= (the desk never invents a gender).

 a. lang=tr + "English speaking": only English-speaking doctors (h1) are offered.
 b. After "cancel offer_id=X reason=lang" + "I need a doctor who speaks English": the next offers are
    English doctors only, never the cancelled slot.
 c. "medico donna": only women doctors.
 d. "solo di mattina" / "preferisco le mattine": no afternoon slot is offered first (nor at all
    while a morning slot exists).
 e. No doctor matches: the closest alternative, an honest line saying so, and the waiting list.
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC, HO = "agent:t07/booking_centre", "agent:t07/hospital"
NOW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FAILS: list[str] = []
SEQ = [0]

# slot id -> (doctor, day offset, hour, gender). Offsets start at 1 (tomorrow), all inside window D.
SLOTS = {
    "A1": ("asst-centro-h2", 1, 9, "m"),
    "A2": ("asst-centro-h2", 1, 15, "m"),
    "A3": ("asst-centro-h2", 2, 9, "m"),
    "S1": ("asst-centro-s2", 2, 15, "f"),
    "E1": ("asst-centro-h1", 2, 11, "f"),
    "E2": ("asst-centro-h1", 3, 15, "f"),
    "E3": ("asst-centro-h1", 3, 9, "f"),
}


def slot_lines(ids=None):
    rows = []
    for sid, (doc, off, hour, gender) in SLOTS.items():
        if ids is not None and sid not in ids:
            continue
        at = NOW + timedelta(days=off, hours=hour - 8)
        rows.append(f"slot_id={sid} doctor_id={doc} at={at.isoformat()} day={DAYS[at.weekday()]} "
                    f"facility={HO} procedure=VIS-FIS free=1 gender={gender}")
    return rows


def fields(text):
    return dict(t.split("=", 1) for t in (text or "").split() if "=" in t)


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"f{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


def offer_ids(text):
    return [fields(ln)["slot_id"] for ln in text.splitlines()
            if ln.startswith("offer ") and "offer_id=of-" in ln and "slot_id=" in ln]


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT", "_HELD", "_LEADS",
                 "_SEEN_GENDER", "_SEEN_LANGS"):
        table = getattr(brain, name, None)
        if hasattr(table, "clear"):
            table.clear()
    brain._RESCUE_LAST[:] = [time.monotonic() + 3600]
    brain._WX.update(at=time.monotonic(), ttl=1e9, data={})


def check(ok, label):
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        FAILS.append(label)


class Fast:
    """A synchronous Ledger for one-turn checks."""

    def __init__(self, cz, need, rows, slots):
        self.cz, self.need, self.slots = cz, need, slots
        self.rows, self.out = list(rows), []

    async def say(self, to, text):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER:
            head = text.split()[0]
            body = {"agenda": f"agenda agenda_now={NOW.isoformat()}",
                    "world": "slots page=1/1\n" + "\n".join(self.slots),
                    "prescription": "prescription prescription_number=RX-1 procedure=VIS-FIS priority=D state=issued"}.get(head)
            if body:
                self.rows.append(msg(LEDGER, BC, body, self.need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    def last_text(self):
        got = [t for to, t in self.out if to == self.cz and "offer_id=of-" in t]
        return got[-1] if got else ""

    def offers(self):
        return offer_ids(self.last_text())

    def says(self):
        return " ".join(t for to, t in self.out if to == self.cz)


async def one_turn(city, row):
    city.rows.append(row)
    view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=city.find, history=city.history,
                           say=lambda to, text, *, status=None: city.say(to, text))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 30)
    return city


async def ask(tag, text, slots=None):
    reset()
    cz, need = f"citizen:t07/{tag}", f"N-{tag}"
    city = Fast(cz, need, [], slots or slot_lines())
    return await one_turn(city, msg(cz, BC, text, need))


async def after_refusal(tag, first, refusal, offered, slots=None):
    """The desk already offered ``offered`` slots; the citizen refuses the first with ``refusal``."""
    reset()
    cz, need = f"citizen:t07/{tag}", f"N-{tag}"
    lines = slots or slot_lines()
    by_id = {fields(ln)["slot_id"]: fields(ln) for ln in lines}
    offer = "\n".join(
        f"offer offer_id=of-{s} slot_id={s} doctor_id={by_id[s]['doctor_id']} facility={HO} "
        f"procedure=VIS-FIS agenda_date={by_id[s]['at']} prescription_number=RX-1" for s in offered)
    rows = [
        msg(cz, BC, first, need),
        msg(BC, LEDGER, "prescription prescription_number=RX-1", need),
        msg(LEDGER, BC, "prescription prescription_number=RX-1 procedure=VIS-FIS priority=D state=issued", need),
        msg(LEDGER, BC, f"agenda agenda_now={NOW.isoformat()}", need),
        msg(LEDGER, BC, "slots page=1/1\n" + "\n".join(lines), need),
        msg(BC, cz, offer, need),
    ]
    city = Fast(cz, need, rows, lines)
    return await one_turn(city, msg(cz, BC, refusal.replace("{offer}", f"of-{offered[0]}"), need))


def speaks_en(ids):
    return all(SLOTS[i][0].endswith("-h1") for i in ids)


async def t_a():
    city = await ask("a", "My back hurts. Need a doctor, English speaking. Book it. Fast. "
                          "need.request area={'district':_6} lang=tr prescription_number=RX-1 service_type=hospital.physiatry")
    got = city.offers()
    print(f"      offers {got}")
    check(bool(got) and speaks_en(got), f"(a) lang=tr + 'English speaking': English doctors only ({got})")


async def t_b():
    city = await after_refusal(
        "b", "Vorrei prenotare la visita. need.request lang=it prescription_number=RX-1",
        "I need a doctor who speaks English. cancel offer_id={offer} reason=lang", ["A1", "A2", "S1"])
    got = city.offers()
    print(f"      offers {got}")
    check(bool(got) and speaks_en(got) and "A1" not in got,
          f"(b) after cancel reason=lang + English: English doctors only, not the cancelled slot ({got})")
    # The refusal names no language: the refused doctor is not offered again, an English-free alternative may stay.
    city = await after_refusal(
        "b2", "Vorrei prenotare la visita. need.request lang=tr prescription_number=RX-1 doctor_language=en",
        "No thanks, this doctor does not speak my language. cancel offer_id={offer} reason=lang", ["A1", "A2", "E1"])
    got = city.offers()
    print(f"      offers {got}")
    check(bool(got) and not any(SLOTS[i][0].endswith("-h2") for i in got),
          f"(b2) cancel reason=lang with no language named: the refused doctor is not offered again ({got})")


async def t_c():
    for tag, text in (("c1", "Vorrei una visita, preferisco un medico donna. need.request lang=it prescription_number=RX-1"),
                      ("c2", "Mi serve una dottoressa. need.request lang=it prescription_number=RX-1")):
        city = await ask(tag, text)
        got = city.offers()
        print(f"      offers {got}")
        check(bool(got) and all(SLOTS[i][3] == "f" for i in got), f"(c) '{text[:30]}': women doctors only ({got})")
    city = await after_refusal(
        "c3", "Vorrei prenotare la visita. need.request lang=it prescription_number=RX-1",
        "Preferisco un medico donna. cancel offer_id={offer} reason=gender", ["A1", "A2", "E1"])
    got = city.offers()
    check(bool(got) and all(SLOTS[i][3] == "f" for i in got), f"(c) cancel reason=gender + 'medico donna': women only ({got})")


async def t_d():
    for tag, text in (("d1", "Vorrei la visita, solo di mattina. need.request lang=it prescription_number=RX-1"),
                      ("d2", "Vorrei la visita, preferisco le mattine. need.request lang=it prescription_number=RX-1")):
        city = await ask(tag, text)
        got = city.offers()
        print(f"      offers {got}")
        check(bool(got) and SLOTS[got[0]][2] < 13 and all(SLOTS[i][2] < 13 for i in got),
              f"(d) '{text[22:50]}': no afternoon slot while a morning slot exists ({got})")
    city = await after_refusal(
        "d3", "Vorrei prenotare la visita. need.request lang=it prescription_number=RX-1",
        "Posso solo la mattina. cancel offer_id={offer} reason=day", ["A2", "S1", "E2"])
    got = city.offers()
    check(bool(got) and SLOTS[got[0]][2] < 13, f"(d) cancel reason=day + 'solo la mattina': a morning slot first ({got})")


async def t_e():
    # No woman doctor free at all.
    men = [ln for ln in slot_lines() if "gender=m" in ln]
    city = await ask("e1", "Vorrei una visita con un medico donna. need.request lang=it prescription_number=RX-1", men)
    said = city.says().lower()
    got = city.offers()
    print(f"      offers {got}")
    check(bool(got), f"(e) no woman doctor free: the closest alternative is still offered ({got})")
    check("donn" in said or "dottoressa" in said or "woman" in said or "female" in said,
          "(e) no woman doctor free: an honest line says so")
    check("waiting_list=true" in city.last_text(), "(e) no woman doctor free: the waiting list rides along")
    # No English speaker free.
    it_only = [ln for ln in slot_lines() if "-h1" not in ln]
    city = await ask("e2", "Need a doctor, English speaking. need.request lang=tr prescription_number=RX-1", it_only)
    said = city.says().lower()
    got = city.offers()
    check(bool(got) and "english" in said and "waiting_list=true" in city.last_text(),
          f"(e) no English speaker free: closest slots, an honest line and the waiting list ({got})")
    # No morning slot free.
    pm = [ln for ln in slot_lines() if SLOTS[fields(ln)['slot_id']][2] >= 13]
    city = await ask("e3", "Vorrei la visita, solo di mattina. need.request lang=it prescription_number=RX-1", pm)
    said = city.says().lower()
    got = city.offers()
    check(bool(got) and ("mattin" in said or "morning" in said),
          f"(e) no morning slot free: closest slots and an honest line about the morning ({got})")


async def t_f():
    # Other languages, and the citizen's own "I do not speak English" is not a doctor wish.
    for tag, text in (("f1", "Quiero una cita, un médico que hable inglés. need.request lang=es prescription_number=RX-1"),
                      ("f2", "Je voudrais un médecin qui parle anglais. need.request lang=fr prescription_number=RX-1"),
                      ("f3", "Vorrei un medico che parli inglese. need.request lang=ro prescription_number=RX-1")):
        got = (await ask(tag, text)).offers()
        check(bool(got) and speaks_en(got), f"(f) '{text[:34]}': English doctors only ({got})")
    got = (await ask("f4", "Non parlo inglese, vorrei una visita. need.request lang=it prescription_number=RX-1")).offers()
    check(bool(got) and not speaks_en(got), f"(f) 'non parlo inglese' is not an English-doctor wish ({got})")
    # Only non-English doctors are free; the citizen refused the closest alternative for language:
    # no second round of the same kind, the waiting list answers.
    it_only = [ln for ln in slot_lines() if "-h1" not in ln]
    city = await after_refusal(
        "f5", "Need a doctor, English speaking. need.request lang=tr prescription_number=RX-1",
        "I need a doctor who speaks English. cancel offer_id={offer} reason=lang", ["A1", "A2", "A3"], it_only)
    sent = [t for to, t in city.out if to == city.cz]
    print(f"      offers {city.offers()}  replies {len(sent)}: {' // '.join(sent)[:700]}")
    check(bool(sent) and not city.offers(), "(f) refused the alternative for language: no second round of non-English doctors")
    check(bool(sent) and any("waiting_list=true" in t or "lista d'attesa" in t or "waiting list" in t for t in sent),
          "(f) ... and the waiting list is offered, never silence")


async def main():
    await t_a()
    await t_b()
    await t_c()
    await t_d()
    await t_e()
    await t_f()
    print("ALL OK" if not FAILS else "FAILED: " + "; ".join(FAILS))
    raise SystemExit(1 if FAILS else 0)


asyncio.run(main())
