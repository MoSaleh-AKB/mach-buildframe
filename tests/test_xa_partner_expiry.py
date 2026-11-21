"""S14 at the booking desk: a partner offer keeps the partner's own expiry, and an expired one is dropped.

Before: the relayed line got expiry=<slot date> (the partner's expiry was rewritten), so an offer the
partner had already let expire reached the citizen as valid (false offer).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_xa_partner_expiry.py [asst/_shared/agent.py]
"""

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, P1 = "agent:t07/booking_centre", "agent:t05/hospital"
NOW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
SLOT = (NOW + timedelta(days=1)).replace(hour=16).isoformat()
FAILS = []


def line(offer_id, slot_id, expiry):
    return (f"offer offer_id={offer_id} slot_id={slot_id} doctor_id=D-p1 facility={P1} procedure=VIS-FIS "
            f"agenda_date={SLOT} agenda_day=tue expiry={expiry} priority=B request_id=R1")


def check(ok, label, got):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        print(f"      got: {got!r}")


expired = (NOW - timedelta(days=1)).isoformat()
soon = (NOW + timedelta(hours=6)).isoformat()
text = "Ecco.\n" + "\n".join([line("of-old", "PX1", expired), line("of-ok", "PX2", soon), line("of-none", "PX3", "x")])
row = Message(type="chat", sender=P1, recipient=BC, msg_id="m1", need_id="N-1", conversation_id="cv-N-1",
              body={"text": text}, sent_at=NOW.isoformat())
offers = brain._xa_offers([row], P1, now=NOW, priority="B", procedure="VIS-FIS", lang="it", rid="R1",
                          taken=set(), taken_ids=set())
ids = [o["offer_id"] for o in offers]
check("of-old" not in ids, "an offer past the partner's expiry is not relayed", ids)
check("of-ok" in ids, "an offer inside its expiry is relayed", ids)
check("of-none" in ids, "an offer without a readable expiry keeps the slot date as before", ids)
lines = {o["offer_id"]: brain._xa_line(o, "RX1", "B") for o in offers}
check(f"expiry={soon}" in lines.get("of-ok", ""), "the relayed line keeps the partner's expiry", lines.get("of-ok"))
check("expiry=" not in lines.get("of-none", ""), "no partner expiry: no expiry on the line (!177, only what the partner said)", lines.get("of-none"))
print(f"\n{'OK' if not FAILS else 'FAIL'} test_xa_partner_expiry ({len(FAILS)} failed)")
sys.exit(1 if FAILS else 0)
