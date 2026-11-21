"""p08: violence happening now gets 112; ordinary text with 'me pega / mă bate' does not.
Run: <city python> -I tests/test_red_now_p08.py <brain> [role]"""
import importlib.util
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
role = sys.argv[2] if len(sys.argv) > 2 else ("gp" if "_gp" in sys.argv[1] else "booking_centre")
os.environ["CITY_ROLE"] = role
spec = importlib.util.spec_from_file_location("b", sys.argv[1])
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
b.role_of = lambda: role
from city_sdk.city import Message  # noqa: E402

RED = ["mio padre non smette, mi sta picchiando", "never mind, my husband is hitting me",
       "he is hitting me with a belt", "mio marito mi sta picchiando adesso"]
BENIGN = ["ele me pega na escola", "a mãe me pega", "mă bate inima", "me pega la mano",
          "cosa fare se mi sta picchiando il vicino", "vorrei prenotare una visita al ginocchio"]
bad = 0
for t in RED + BENIGN:
    m = Message(type="chat", sender="citizen:t/c1", recipient="agent:t/x", msg_id="m", need_id="n",
                conversation_id="cv", body={"text": t}, sent_at=datetime.now(timezone.utc).isoformat())
    hit = bool(b._emergency(m))
    want = t in RED
    bad += hit != want
    print(f"{'OK  ' if hit == want else 'BAD '} {'112' if hit else '---'} want={'112' if want else '---'}  {t}")

print(f"{'OK' if not bad else 'FAILED'} test_red_now_p08 ({bad} failed)")
sys.exit(1 if bad else 0)
