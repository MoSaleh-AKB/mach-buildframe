"""#87: "never had chest pain this bad" / "mai avuto un dolore al petto così" is a red flag, not a denial;
real "never had chest pain" denials stay calm.
Run: <city python> -I tests/test_red_never_this_bad.py asst/_shared/agent.py"""
import importlib.util
import sys

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

RED = [
    "I've never had chest pain this bad",
    "Mai avuto un dolore al petto così",
    "I have never had chest pain like this before",
    "non ho mai avuto un dolore al petto così forte",
]
CALM = [
    "I've never had chest pain, I need a knee visit",
    "never had chest pain, just a cough",
    "mai avuto dolore al petto, vorrei una visita per la schiena",
    "non ho mai avuto problemi al petto",
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
