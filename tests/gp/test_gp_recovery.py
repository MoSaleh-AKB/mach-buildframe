"""gp / consultorio: a STALE citizen case (old wave, last message unanswered, or our last line is from
before the 10:00 reopen) is taken over by a fresh "recovery" persona inside the same agent.

The persona says ONE new intro line, then: a prescription already issued is re-stated (number,
procedure, priority) and handed to the booking desk again; no prescription yet runs the normal
symptom-to-prescription path; a red flag in the old messages gets the 112 line first. A fresh case
is left alone. The recovery also runs from a background sweep (no message needed), at most once
per need per 10 minutes.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/gp/test_gp_recovery.py asst/_gp/agent.py
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "gp"
brain._REC_MAX_AGE = 36 * 3600.0  # these fixtures are hours old; the 2 h horizon (#138) is in test_gp_recovery_drain

MATES = sorted(brain._MATES)
DESK = brain._our_desk()  # #140: the hand-off goes to the known desk id first
GP = next(a for a in MATES if a != DESK)
N = [0]


def when(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def msg(sender, to, text, need, mid=None, ago=0.0):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=mid or f"m{N[0]}", need_id=need, body={"text": text},
                   recipient=to, sent_at=when(ago))


class City:
    """Relay fake: a mailbox with sent_at stamps, a Ledger that answers every prescribe, one desk."""

    address = ""

    def __init__(self, rows):
        self.agent_id, self.rows, self.out, self.inbound = GP, list(rows), [], None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        need = self.inbound.need_id if self.inbound is not None else ""
        self.rows.append(msg(GP, to, text, need))
        if to == LEDGER and text.startswith("prescribe"):
            num = next(w for w in text.split() if w.startswith("prescription_number="))
            self.rows.append(msg(LEDGER, GP, f"prescription {num} procedure=VIS-X priority=B state=issued", need))
        if to == LEDGER and text.startswith("prescription prescription_number=") and getattr(self, "lookup", ""):
            self.rows.append(msg(LEDGER, GP, self.lookup, need))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return [SimpleNamespace(agent_id=DESK, role="booking_centre")]


async def push(city, m):
    view = SimpleNamespace(inbound=m, agent_id=GP, address="", find=city.find, history=city.history, say=city.say)
    city.inbound = m
    if m not in city.rows:
        city.rows.append(m)
    try:
        await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 30)
    except Exception as exc:
        return type(exc).__name__
    return ""


def to_cz(city, cz):
    return [t for to, t in city.out if to == cz]


def hand_offs(city):
    return [t for to, t in city.out if to == DESK and t.startswith("hand-off")]


def prescribes(city):
    return [t for to, t in city.out if to == LEDGER and t.startswith("prescribe")]


def recovery(lines):
    return [t for t in lines if "servizio di recupero" in t or "recovery service" in t]


def issued_case(cz, need, age):
    """Citizen wrote about back pain `age` min ago and got prescription + hand-off."""
    num = brain._rx(need, "cz-first-" + need)
    return num, [
        msg(cz, GP, "Ho mal di schiena da due giorni", need, mid="cz-first-" + need, ago=age),
        msg(GP, LEDGER, f"prescribe patient={cz} procedure=VIS-ORTO priority=B symptom_code=SYM-BACK "
            f"prescription_number={num}", need, ago=age - 0.1),
        msg(LEDGER, GP, f"prescription prescription_number={num} procedure=VIS-ORTO priority=B state=issued",
            need, ago=age - 0.1),
        msg(GP, cz, f"prescription prescription_number={num} procedure=VIS-ORTO priority=B\nLe ho emesso la ricetta.",
            need, ago=age - 0.2),
        msg(GP, DESK, f"hand-off prescription_number={num} priority=B citizen={cz}\nBuongiorno.", need, ago=age - 0.3),
    ]


async def main():
    oks = []

    def check(ok, name, city=None):
        print(("PASS " if ok else "FAIL ") + name)
        oks.append(ok)
        if not ok and city is not None:
            for to, t in city.out:
                print("    ", to[:16], t[:140].replace("\n", " | "))

    # (i) stale: prescription issued 4 h ago, citizen asked "allora?" 3.9 h ago, nobody answered
    cz, need = "citizen:t/i", "N-i"
    num, rows = issued_case(cz, need, 240)
    nudge = msg(cz, GP, "allora?", need, mid="cz-nudge-i", ago=234)
    city = City(rows)
    await push(city, nudge)
    said = to_cz(city, cz)
    check(len(recovery(said)) == 1, "(i) stale + prescription: one recovery intro line", city)
    check(any(f"prescription_number={num}" in t and "VIS-ORTO" in t and "priority=B" in t for t in said),
          "(i) prescription number, procedure and priority re-stated", city)
    check(len(hand_offs(city)) == 1 and f"prescription_number={num}" in hand_offs(city)[0]
          and f"citizen={cz}" in hand_offs(city)[0], "(i) one new hand-off to the desk with citizen=", city)
    check(not prescribes(city), "(i) no second prescribe", city)
    check(bool(said) and bool(recovery(said[:1])), "(i) the intro is the first line", city)
    # (i-en) English citizen: English intro and lang= in the hand-off
    cz, need = "citizen:t/ie", "N-ie"
    num, rows = issued_case(cz, need, 300)
    rows[0] = msg(cz, GP, "I have back pain for two days, please help", need, mid="cz-first-" + need, ago=300)
    city = City(rows)
    await push(city, msg(cz, GP, "hello? any news?", need, mid="cz-nudge-ie", ago=290))
    check(any("recovery service" in t for t in to_cz(city, cz)), "(i-en) English intro line", city)
    check(any("lang=en" in t for t in hand_offs(city)), "(i-en) hand-off carries lang=en", city)

    # (ii) stale, no prescription yet: the normal path runs now
    cz, need = "citizen:t/ii", "N-ii"
    first = msg(cz, GP, "Ho mal di schiena da giorni", need, mid="cz-first-ii", ago=200)
    city = City([first])
    await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-ii", ago=190))
    said = to_cz(city, cz)
    check(len(recovery(said)) == 1, "(ii) stale + no prescription: recovery intro line", city)
    check(len(prescribes(city)) == 1 and "SYM-BACK" in prescribes(city)[0], "(ii) a prescription is issued", city)
    check(any(t.startswith("prescription ") and "prescription_number=" in t for t in said),
          "(ii) the citizen gets the prescription", city)

    # (iii) not stale: no recovery
    cz, need = "citizen:t/iii", "N-iii"
    city = City([])
    await push(city, msg(cz, GP, "Ho mal di schiena", need, mid="cz-first-iii", ago=0.2))
    check(not recovery(to_cz(city, cz)) and len(prescribes(city)) == 1, "(iii-a) fresh first message: no recovery", city)
    cz, need = "citizen:t/iiib", "N-iiib"
    num, rows = issued_case(cz, need, 5)
    city = City(rows)
    await push(city, msg(cz, GP, "grazie", need, mid="cz-thx", ago=0.1))
    check(not recovery(to_cz(city, cz)) and not hand_offs(city), "(iii-b) answered case, fresh thanks: no recovery", city)

    # (iv) a plain "grazie" / "ok" after the hand-off, even 20 min old and unanswered: never a recovery
    for k, word in enumerate(("grazie", "ok", "Ok, grazie!", "thanks")):
        cz, need = f"citizen:t/iv{k}", f"N-iv{k}"
        num, rows = issued_case(cz, need, 120)
        city = City(rows)
        await push(city, msg(cz, GP, word, need, mid=f"cz-thx-iv{k}", ago=20))
        check(not recovery(to_cz(city, cz)) and not hand_offs(city), f"(iv) old unanswered {word!r} after hand-off: no recovery", city)
    # (iv-b) a hand-off sent AFTER the old message already answered it; a booking receipt closes the case
    cz, need = "citizen:t/ivb", "N-ivb"
    first = msg(cz, GP, "Ho mal di schiena da giorni", need, mid="cz-first-ivb", ago=300)
    after = msg(GP, DESK, "hand-off prescription_number=prescription_number-X1 priority=B citizen=" + cz, need, ago=200)
    city = City([first, after])
    await push(city, msg(cz, GP, "novità?", need, mid="cz-n-ivb", ago=0.1))
    check(not recovery(to_cz(city, cz)), "(iv-b) hand-off already sent after the old message: no recovery", city)
    cz, need = "citizen:t/ivc", "N-ivc"
    num, rows = issued_case(cz, need, 240)
    rows.append(msg(GP, cz, "booking.receipt booking_id=bk-1\nPrenotato.", need, ago=100))
    rows.append(msg(cz, GP, "dove devo andare?", need, mid="cz-n-ivc", ago=90))
    city = City(rows)
    await push(city, rows[-1])
    check(not recovery(to_cz(city, cz)), "(iv-c) case with a booking receipt: no recovery", city)

    # (v) red flag in the old, unanswered message: the 112 line first, no recovery chatter
    cz, need = "citizen:t/v", "N-v"
    old = msg(cz, GP, "ho forte dolore al petto e non riesco a respirare", need, mid="cz-first-v", ago=300)
    city = City([old])
    await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-v", ago=290))
    said = to_cz(city, cz)
    check(bool(said) and "112" in said[0] and not recovery(said[:1]), "(v) red flag in old message: 112 first", city)
    check(not prescribes(city), "(v) red flag: no prescription", city)

    # (vi) once per need per 10 minutes
    cz, need = "citizen:t/vi", "N-vi"
    num, rows = issued_case(cz, need, 240)
    city = City(rows)
    await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-vi1", ago=234))
    await push(city, msg(cz, GP, "??", need, mid="cz-nudge-vi2", ago=0.05))
    check(len(recovery(to_cz(city, cz))) == 1 and len(hand_offs(city)) == 1,
          "(vi) a second turn inside 10 min: no second recovery", city)

    # (viii) P1 in force: a prescription older than 2 agenda days (48 real min) is renewed, not re-stated
    cz, need = "citizen:t/viii", "N-viii"
    num, rows = issued_case(cz, need, 240)
    brain._RULES = {"P1": "valid_days=2"}
    city = City(rows)
    city.lookup = f"prescription prescription_number={num} procedure=VIS-ORTO priority=B state=expired patient={cz}"
    await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-viii", ago=234))
    pres = [t for t in prescribes(city) if f"prescription_number={num}" not in t]
    check(len(recovery(to_cz(city, cz))) == 1 and len(pres) == 1 and "VIS-ORTO" in pres[0],
          "(viii) P1 + old prescription: renewed through the normal path", city)
    check(not any(f"prescription_number={num}" in t and "riprendo la ricetta" in t.lower() for t in to_cz(city, cz)),
          "(viii) P1 + old prescription: not re-stated", city)
    # (viii-b) P1 in force but the prescription is young (30 real min = 1 agenda day): re-stated
    cz, need = "citizen:t/viiib", "N-viiib"
    num, rows = issued_case(cz, need, 30)
    city = City(rows)
    await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-viiib", ago=15))
    check(len(recovery(to_cz(city, cz))) == 1 and len(hand_offs(city)) == 1 and not prescribes(city),
          "(viii-b) P1 + young prescription: re-stated and handed off", city)
    brain._RULES = {}

    # (ix) kill switch
    os.environ["CITY_GP_RECOVERY"] = "0"
    cz, need = "citizen:t/ix", "N-ix"
    num, rows = issued_case(cz, need, 240)
    city = City(rows)
    await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-ix", ago=234))
    check(not recovery(to_cz(city, cz)), "(ix) CITY_GP_RECOVERY=0: no recovery", city)
    del os.environ["CITY_GP_RECOVERY"]

    # (vii) a background sweep recovers a stale case with no message arriving for it
    cz, need = "citizen:t/vii", "N-vii"
    num, rows = issued_case(cz, need, 400)
    rows.append(msg(cz, GP, "ho bisogno di una risposta", need, mid="cz-wait-vii", ago=380))
    other = "citizen:t/other"
    city = City(rows)
    brain._REC_SWEEP_EVERY = 0.05
    brain._REC_FIRST_MIN = 0.0
    for t in brain._REC_TASK:
        t.cancel()
    brain._REC_TASK[:] = []  # a fresh timer that reads the short interval
    await push(city, msg(other, GP, "Ho mal di schiena", "N-other", mid="cz-other", ago=0.1))
    await asyncio.sleep(1.0)
    check(len(recovery(to_cz(city, cz))) == 1 and len(hand_offs(city)) >= 1,
          "(vii) sweep: stale case recovered without a message", city)
    check(not recovery(to_cz(city, other)), "(vii) sweep: the fresh citizen is left alone", city)

    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_gp_recovery")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
