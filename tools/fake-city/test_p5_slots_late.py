"""Port p5: late Ledger answer replay, "which slot_id?" question, agenda time moved on.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/test_p5_slots_late.py [brain.py]   (exit 1 on any FAIL)
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "booking_centre", "agent", "agent.py")
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

ME, CZ = "agent:t07/booking_centre", "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
FAILS: list[str] = []
SEQ = [0]


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def msg(sender, to, text, need="n1", age=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id="cv", body={"text": text},
                   sent_at=(NOW - timedelta(seconds=age)).isoformat())


cur = msg(CZ, ME, "other", need="other")
# 1. late Ledger answer: replayed once, only when old enough and an answer to us
case = [msg(CZ, ME, "need", age=300), msg(ME, LEDGER, "hold slot_id=S1", age=290),
        msg(LEDGER, ME, "held hold_id=H1 slot_id=S1", age=100)]
check("late held replayed", brain._late_pick(case, ME, cur, NOW) is not None)
rows = [*case]
check("late held in sweep picks", len(brain._rescue_picks(rows, ME, cur, NOW)) == 1)
fresh = [*case[:2], msg(LEDGER, ME, "held hold_id=H1 slot_id=S1", age=2)]
check("fresh answer not replayed", brain._late_pick(fresh, ME, cur, NOW) is None)
done = [*case, msg(ME, CZ, "booking.receipt booking_id=B1", age=50)]
check("receipted case not replayed", brain._late_pick(done, ME, cur, NOW) is None)
foreign = [case[0], case[2]]
check("not an answer to us", brain._late_pick(foreign, ME, cur, NOW) is None)

# 2. which slot_id? with the offers still open
check("slot question matches", all(brain._SLOT_Q.search(t) for t in
      ("Which slot_id?", "quale slot devo scrivere", "cuál cita", "quel créneau")))
check("plain text no match", brain._SLOT_Q.search("Thanks, see you") is None)
offers = msg(ME, CZ, "offer offer_id=O1 slot_id=S1 at=x\noffer offer_id=O2 slot_id=S2 at=y", age=200)
lost = msg(LEDGER, ME, "cancel slot_id=S1 reason=taken", age=150)
lines = brain._open_offer_lines([offers, lost], ME)
check("cancelled slot left out", len(lines) == 1 and "S2" in lines[0])
no = msg(CZ, ME, "No thanks", age=100)
check("citizen no keeps offers", len(brain._open_offer_lines([offers, no], ME)) == 2)

# 3. agenda time moves on with real time
ag = msg(LEDGER, ME, "agenda agenda_now=" + (NOW + timedelta(days=1)).replace(microsecond=0).isoformat(), age=120)
moved = brain._agenda_now([ag])
stored = brain._when((NOW + timedelta(days=1)).replace(microsecond=0).isoformat())
check("agenda moved ~2 agenda hours", moved is not None and 1.9 * 3600 <= (moved - stored).total_seconds() <= 2.2 * 3600)

print(f"{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
