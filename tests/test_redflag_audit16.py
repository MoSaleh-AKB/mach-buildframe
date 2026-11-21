"""Audit #16 (21 Nov): red flags every brain missed, plus benign look-alikes that must stay quiet.
Run: <city python> -I tests/test_redflag_audit16.py asst/_shared/agent.py   (also works with asst/_gp/agent.py)"""
import importlib.util
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import Message  # noqa: E402

RED = [
    "my child is not breathing", "he is not breathing", "she stopped breathing",
    "mio padre è incosciente", "è incosciente da due minuti", "il bambino è cianotico", "ha le labbra blu",
    "sangue dal naso che non si ferma da un'ora", "my nose won't stop bleeding",
    "non riesco a muovere metà del corpo", "I can't move one side of my body",
]
BENIGN = [
    "he is breathing normally now, I only need a check-up", "la struttura non risponde più al telefono",
    "non ho mai avuto un infarto, è solo un controllo", "I had a heart attack 10 years ago, yearly check-up",
    "ho il naso chiuso da due giorni", "vorrei prenotare una visita di controllo",
]
# Pre-existing on live gp 139af01 too (one phone call, not a missed 112): _gp line ~293
# "non risponde più" without a person, and "non ho mai avuto un infarto". Remove once fixed.
GP_KNOWN_FP = set()


def load(role):
    os.environ["CITY_ROLE"] = role
    spec = importlib.util.spec_from_file_location(f"b_{role}", sys.argv[1])
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    if hasattr(b, "role_of"):
        b.role_of = lambda: role
    return b


def msg(text):
    return Message(type="chat", sender="citizen:t/c1", recipient="agent:t/x", msg_id="m1", need_id="n1",
                   conversation_id="cv", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


fails = 0
roles = ["gp", "family_counseling"] if "_gp" in sys.argv[1] else ["booking_centre", "hospital", "specialist"]
for role in roles:
    b = load(role)
    for t in RED:
        if not b._emergency(msg(t)):
            fails += 1
            print(f"FAIL {role} missed: {t}")
    for t in BENIGN:
        if role in ("gp", "family_counseling") and t in GP_KNOWN_FP:
            continue
        if b._emergency(msg(t)):
            fails += 1
            print(f"FAIL {role} false alarm: {t}")
print(f"{'OK' if not fails else 'FAILED'} test_redflag_audit16 ({fails} failed)")
sys.exit(1 if fails else 0)
