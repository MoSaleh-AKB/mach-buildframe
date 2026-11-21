"""#137: the shared brain (booking_centre, hospital, specialist) keeps the gp guards of #88: a past event,
somebody else's past sign, a fear, an animal are not a 112; the same signs NOW, in the citizen, still are.
Run: <city python> -I tests/test_shared_red_guards.py asst/_shared/agent.py"""
import importlib.util
import os
import sys

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

CALM = [
    "Anni fa ho avuto un ictus, ora ho mal di schiena",
    "Mio cugino è svenuto ieri, oggi sta bene, io ho mal di testa",
    "Ho paura di avere un infarto ma sto bene, vorrei un controllo",
    "Il mio cane respira male, io ho bisogno di una visita per il ginocchio",
]
RED = [
    "Mio padre non risponde e non respira",
    "ho un dolore fortissimo al petto e al braccio sinistro",
    "I can't breathe and my lips are blue",
    "il mio cane mi ha morso e perdo sangue che non si ferma",
    "mio cugino è svenuto adesso e non si sveglia",
    "sto avendo un infarto",
    "ho avuto un ictus stamattina, non muovo il braccio",
]
fails = 0
for role in ("booking_centre", "hospital", "specialist"):
    os.environ["CITY_ROLE"] = role
    for i, text in enumerate(CALM + RED):
        m = Message(type="chat", sender="citizen:t/c1", msg_id=f"m{i}", need_id="n1", body={"text": text}, recipient=f"agent:t/{role}")
        got = bool(brain._emergency(m, wide=True))
        want = text in RED
        if got != want:
            fails += 1
            print(f"FAIL {role} want={want} got={got}: {text}")
print(f"{fails} FAIL" if fails else "OK 0")
sys.exit(1 if fails else 0)
