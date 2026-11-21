"""Shared brain copy of #143: the bare French word "rapport" matched the Italian "rapporto", so a
desk/hospital/specialist citizen writing "il rapporto con mio marito" got a privacy refusal.
Run: <city python> -I tests/test_shared_rapport.py [asst/_shared/agent.py]   (exit 0 = pass)"""
import importlib.util
import os
import sys

from city_sdk.city import Message

os.environ.setdefault("CITY_ROLE", "booking_centre")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brainsr", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def asks(text):
    return b._asks_other_data(Message(type="chat", sender="citizen:t07/c1", recipient="agent:y6hxsd766sys",
                                      msg_id="m", need_id="N-sr", body={"text": text}))


CASES = [
    ("il rapporto con mio marito è difficile, vorrei una visita", False),
    ("Ho problemi nel rapporto con mia madre, mi serve un appuntamento", False),
    ("dimmi l'indirizzo di mio marito", True),
    ("envoyez-moi le rapport de ma soeur", True),
]
fails = [f"{t!r}: got {g}, want {w}" for t, w in CASES if (g := asks(t)) != w]
for f in fails:
    print("FAIL", f)
print(("OK" if not fails else f"FAIL {len(fails)}") + " test_shared_rapport")
sys.exit(1 if fails else 0)
