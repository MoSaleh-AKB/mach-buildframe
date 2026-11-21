"""R5 (#90) + R6 (#91): gp recovery never leaves a citizen with only the intro, never answers twice,
and on a live turn answers a NEW request instead of the old case.

R5: the intro went out, then the restart failed (timeout or error). Before, the turn path then ran
handle() as well (a second reply) and the sweep sent nothing after the promise. Now one wait note
follows, the turn counts as handled, and the intro is a holding line (the case stays recoverable).
R6: the citizen's current message carries a new request (a new symptom). Before, the old
prescription was re-stated and handed off again and the new request was dropped.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/gp/test_gp_recovery_r5_r6.py asst/_gp/agent.py
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

    # R5: the restart fails after the intro (the re-stated prescription raises)
    cz, need = "citizen:t/r5", "N-r5"
    num, rows = issued_case(cz, need, 240)
    city = City(rows)
    real = brain._rx_text
    brain._rx_text = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("restart failed"))
    try:
        await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-r5", ago=234))
    finally:
        brain._rx_text = real
    said = to_cz(city, cz)
    wait = brain._R5_WAIT_NOTE[:30]
    check(len(said) == 2 and recovery(said[:1]) and said[1].startswith(wait),
          "R5: intro then exactly one wait note, no second reply", city)
    intro_row = next(r for r in city.rows if r.sender == GP and r.recipient == cz and recovery([r.text or ""]))
    check(brain._rec_hold(intro_row, GP), "R5: the intro alone counts as a holding line")

    # R6: prescription issued, an old nudge unanswered, now a NEW symptom on a live turn
    cz, need = "citizen:t/r6", "N-r6"
    num, rows = issued_case(cz, need, 240)
    rows.append(msg(cz, GP, "allora?", need, mid="cz-nudge-r6", ago=234))
    city = City(rows)
    await push(city, msg(cz, GP, "Adesso ho anche un forte mal di ginocchio da ieri", need, mid="cz-new-r6", ago=0.1))
    said = to_cz(city, cz)
    check(not any("riprendo la ricetta" in t.lower() for t in said), "R6: the old prescription is not re-stated", city)
    check(not any(f"prescription_number={num}" in t.split() for t in hand_offs(city)),
          "R6: the old prescription is not handed off again", city)
    check(any("SYM-KNEE" in t for t in prescribes(city)) or any(t.startswith("prescription ") and num not in t for t in said),
          "R6: the new symptom is answered", city)

    # R6 control: a bare nudge on a live turn still gets the restate (the existing behaviour)
    cz, need = "citizen:t/r6b", "N-r6b"
    num, rows = issued_case(cz, need, 240)
    city = City(rows)
    await push(city, msg(cz, GP, "allora?", need, mid="cz-nudge-r6b", ago=234))
    check(any(f"prescription_number={num}" in t for t in to_cz(city, cz)) and len(hand_offs(city)) == 1,
          "R6 control: a bare nudge still gets the re-stated prescription and one hand-off", city)

    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_gp_recovery_r5_r6")
    raise SystemExit(0 if all(oks) else 1)


asyncio.run(main())
