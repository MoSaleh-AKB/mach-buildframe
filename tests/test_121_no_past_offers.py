"""#121 (live N-2026-11-02-8909040002720008): the desk offered 2026-12-23 14:30 slots at agenda 12:37
and the citizen, answering at 15:23, cancelled them all with reason=past; at agenda 12-24 02:48 it
re-sent the same 12-23 slots ("Ecco i posti ancora liberi"). Now:
(a) the offer pass never offers a slot within _MIN_LEAD (9 agenda hours) of agenda now;
(b) a re-send of our open offers drops the lines that are past or within that lead.
Run: <city python> -I tests/test_121_no_past_offers.py [asst/_shared/agent.py]   (exit 0 = pass)"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message

os.environ.setdefault("CITY_ROLE", "booking_centre")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain121", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
b.role_of = lambda: "booking_centre"
BC, CZ, FAC = "agent:y6hxsd766sys", "citizen:W01QQQ-04-0008-000272", "agent:r8yprrs0e8be"
fails = []


def world_line(doc, at):
    sid = f"W01QQQ-asst-centro-{doc}-{at.strftime('%Y%m%d-%H%M')}"
    return (f"slot_id={sid} doctor_id=asst-centro-{doc} facility={FAC} procedure=VIS-FIS "
            f"at={at.isoformat()} free=1")


# (a) offer pass at agenda 2026-12-23 07:00, priority D: page 1 had 14:30 and 16:00 slots of that day.
now = datetime(2026, 12, 23, 7, 0, tzinfo=timezone.utc)
lines = [world_line(d, datetime(2026, 12, 23, h, m, tzinfo=timezone.utc)) for d in ("h1", "h2") for h, m in ((14, 30), (16, 0))]
lines.append(world_line("h1", datetime(2026, 12, 23, 17, 0, tzinfo=timezone.utc)))  # 10 h ahead
picked = [s["slot_id"][-13:] for s in b.fitting(lines, now=now, priority="D", lang=None, gender=None, procedure="VIS-FIS")]
if picked != ["20261223-1700"]:
    fails.append(f"offer pass at agenda 07:00 picked {picked}: want only the slot 9 h or more ahead (17:00)")

# (b) the live re-send: our 12-23 offers still open, agenda now 2026-12-24 02:48, citizen asks which offer_id.
rows = []


def row(sender, to, text, sent_at=None):
    rows.append(Message(type="chat", sender=sender, recipient=to, msg_id=f"r{len(rows)}", need_id="N-121",
                        body={"text": text}, sent_at=sent_at or datetime.now(timezone.utc).isoformat()))


offers = "\n".join(
    f"offer offer_id=of-W01QQQ-asst-centro-{d}-20261223-{t} slot_id=W01QQQ-asst-centro-{d}-20261223-{t} "
    f"doctor_id=asst-centro-{d} facility={FAC} procedure=VIS-FIS agenda_date=2026-12-23T{t[:2]}:{t[2:]}:00.000Z "
    "agenda_day=wed priority=D" for d in ("h1", "h2") for t in ("1430", "1600"))
row(BC, CZ, "Buongiorno, ho trovato alcune disponibilità:\n" + offers)
row(LEDGER, BC, "agenda agenda_now=2026-12-24T02:48:00Z")
left = b._open_offer_lines(rows, BC)
if left:
    fails.append(f"re-send at agenda 12-24 02:48 still lists {len(left)} offers of 12-23")

for f in fails:
    print("FAIL", f)
print(("OK" if not fails else f"FAIL {len(fails)}") + " test_121_no_past_offers")
sys.exit(1 if fails else 0)
