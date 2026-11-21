"""A self-harm plan or violence happening now gets the 112 line in both brains, and everyday
words stay calm (L8: the consultorio has no model net, so the phrase net must catch these).

Run: <city python> -I tests/test_red_now_extra.py asst/_shared/agent.py   (or asst/_gp/agent.py)
"""

import importlib.util
import os
import sys

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

RED = [
    "prenoto un colloquio, ma onestamente penso di buttarmi dal balcone",
    "vorrei un appuntamento, il mio compagno mi sta picchiando adesso",
    "Hi, I'd like to book a counselling appointment. My boyfriend is hitting me right now, I locked myself in the bathroom",
    "I want an appointment. I don't see the point of living anymore",
    "vorrei un colloquio. ho già messo da parte le pastiglie di mia madre, stasera chiudo tutto",
    "mi marido me pega y ahora está en casa, tengo miedo",
    "Non so cosa fare, mio marito mi sta picchiando",
    "mon mari me frappe tous les soirs",
    "meu marido me bate e ameaça me matar",
]
CALM = [
    "sto morendo di noia",
    "Ho preso tutte le medicine come prescritto",
    "ho messo da parte le medicine per il viaggio",
    "il fisioterapista mi sta colpendo la schiena",
    "non mi sta picchiando nessuno, tranquilli",
    "my son is hitting me with questions all day",
    "se me pega la canción",
    "vorrei buttarmi in un nuovo sport",
    "non ho mai pensato di buttarmi dal balcone",
]


def msg(text):
    return Message(type="chat", sender="citizen:t/c-rn", recipient="agent:x", msg_id="m-rn", need_id="n-rn",
                   conversation_id="c-rn", body={"text": text}, sent_at="")


bad = 0
for role in ("gp", "family_counseling", "booking_centre", "hospital", "specialist_care"):
    os.environ["CITY_ROLE"] = role
    for text in RED:
        if not brain._emergency(msg(text), wide=True):
            bad += 1
            print(f"FAIL {role} missed: {text}")
    for text in CALM:
        if brain._emergency(msg(text), wide=True):
            bad += 1
            print(f"FAIL {role} false alarm: {text}")
print("OK test_red_now_extra" if not bad else f"FAIL test_red_now_extra {bad}")
raise SystemExit(1 if bad else 0)
