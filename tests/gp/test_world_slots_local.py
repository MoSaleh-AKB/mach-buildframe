"""consultorio: the local Ledger's "world slots=N" answer counts as our world (#48).

Run: <city python> -I tests/gp/test_world_slots_local.py asst/_gp/agent.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("CITY_ROLE", "family_counseling")
spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_gp/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
from city_sdk.city import LEDGER, Message  # noqa: E402

ME = "agent:t/family_counseling"


def row(text):
    return Message(type="chat", sender=LEDGER, recipient=ME, msg_id="m1", need_id="n1", conversation_id="cv",
                   body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


fails = 0
for head in ("slots page=1/1", "world slots=2"):
    chat = [row(f"{head}\nslot_id=S1 facility={ME} free=1\nslot_id=S2 facility={ME} free=1")]
    got = (brain._world_slot(chat, ME, "S1"), brain._world_slot(chat, ME, "S9"))
    if got != (True, False):
        fails += 1
        print(f"FAIL {head!r}: {got}")
print(f"{'OK' if not fails else 'FAILED'} test_world_slots_local ({fails} failed)")
sys.exit(1 if fails else 0)
