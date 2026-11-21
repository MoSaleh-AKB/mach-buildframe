"""#88: the ported _shared red-flag net in the gp brain keeps the gp guards (past event, somebody else,
a fear, an animal, a denial): ordinary citizens are not sent to 112; real red flags still are.
Run: <city python> -I tests/gp/test_gp_red_guards_shared_net.py asst/_gp/agent.py"""
import importlib.util
import os
import sys

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_gp/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

CALM = [
    "Anni fa ho avuto un ictus, ora ho mal di schiena",
    "Mio cugino è svenuto ieri, ora sta bene. Io vorrei una visita per la tosse",
    "Ho paura di avere un infarto, vorrei un controllo del cuore",
    "Il mio cane respira male, io ho bisogno di un certificato",
    "I don't have chest pain, only my knee hurts",
]
RED = [
    "Mio padre non risponde e non respira",
    "ho un dolore fortissimo al petto e al braccio sinistro",
    "I can't breathe and my lips are blue",
    "il mio cane mi ha morso e perdo sangue che non si ferma",
]
fails = 0
for role in ("gp", "family_counseling"):
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
