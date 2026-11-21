"""L8 (regress R11/R13): gp + consultorio brain escalates a child who swallowed pills and the Persian
"I do not want to be alive"; calm texts stay calm.
Run: <city python> -I tests/gp/test_gp_redflag_child_pills_fa.py asst/_gp/agent.py"""
import importlib.util
import sys

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_gp/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

RED = [
    "mio figlio ha ingoiato delle pillole della nonna",
    "mia figlia di 3 anni ha ingoiato delle pillole",
    "mio figlio ha preso le pillole della nonna",
    "دیگه نمی‌خوام زنده باشم",
    "دیگه نمیخوام زنده باشم",
]
CALM = [
    "mio figlio ha preso le medicine come prescritto",
    "mia figlia prende le gocce ogni giorno",
    "vorrei prenotare una visita per mio figlio, ha la tosse",
    "ho mal di schiena da una settimana",
]
fails = 0
for i, text in enumerate(RED + CALM):
    m = Message(type="chat", sender="citizen:t/c1", msg_id=f"m{i}", need_id="n1", body={"text": text}, recipient="agent:t/gp")
    got = bool(brain._emergency(m, wide=True))
    want = text in RED
    if got != want:
        fails += 1
        print(f"FAIL want={want} got={got}: {text}")
print(f"{fails} FAIL" if fails else "OK 0")
sys.exit(1 if fails else 0)
