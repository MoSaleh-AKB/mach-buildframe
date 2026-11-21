"""Q3: gp reads the P1 prescription validity from the rule (days=/valid_days=/validity_days=/...,
default 2 agenda days) instead of a hard-coded 2, as the shared brain does.

Run: <city python> -I tests/gp/test_gp_p1_days.py [asst/_gp/agent.py]   (exit 0 = pass)
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
os.environ["CITY_ROLE"] = "gp"
spec = importlib.util.spec_from_file_location("brainq3", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
ME = "agent:t07/gp"


def expired(rule, agenda_days):
    b._RULES.clear()
    b._RULES["P1"] = rule
    real = timedelta(days=agenda_days) / b._AGENDA_X
    row = Message(type="chat", sender=ME, recipient=LEDGER, msg_id="p", need_id="N-q3",
                  body={"text": "prescribe prescription_number=RX-Q3 procedure=VIS-FIS"},
                  sent_at=(datetime.now(timezone.utc) - real).isoformat())
    return b._rx_expired([row], ME, {"prescription_number": "RX-Q3"})


CASES = [
    ("version=2|valid_days=1", 1.5, True),
    ("valid_days=3|version=2", 1.5, False),
    ("validity_days=1|version=2", 1.5, True),
    ("version=1", 1.5, False),  # no day count: the default 2 days
    ("version=1", 2.5, True),
]
fails = [f"P1 {r!r}, {d} days old: got {got}, want {w}" for r, d, w in CASES if (got := expired(r, d)) != w]
for f in fails:
    print("FAIL", f)
print(("OK" if not fails else f"FAIL {len(fails)}") + " test_gp_p1_days")
sys.exit(1 if fails else 0)
