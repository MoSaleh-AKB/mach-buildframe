"""#86: a denial only covers its own clause. "Mio padre non risponde e non respira" is an emergency even
though a "non" comes first; real denials stay calm.
Run: <city python> -I tests/test_red_neg_clause.py asst/_shared/agent.py"""
import importlib.util
import sys

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

RED = [
    "Mio padre non risponde e non respira",
    "mia madre non parla e non respira bene",
    "He's not responding and not breathing",
]
CALM = [
    "non ho dolore al petto e non ho febbre, vorrei una visita per il ginocchio",
    "no chest pain and no fever, just a sore knee",
    "non ho difficoltà a respirare, ho solo mal di schiena",
    "I'm not short of breath and not dizzy, I need a check-up",
]
fails = 0
for i, text in enumerate(RED + CALM):
    m = Message(type="chat", sender="citizen:t/c1", msg_id=f"m{i}", need_id="n1", body={"text": text}, recipient="agent:t/booking_centre")
    got = bool(brain._emergency(m, wide=True))
    want = text in RED
    if got != want:
        fails += 1
        print(f"FAIL want={want} got={got}: {text}")
print(f"{fails} FAIL" if fails else "OK 0")
sys.exit(1 if fails else 0)
