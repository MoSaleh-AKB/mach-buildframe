"""False check-in / false cancel phrases from MR !18 (live hospital 1.0.3).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_checkin_phrases.py
"""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("brain", os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

NOT_ARRIVED = [
    "Non posso venire per la visita di domani",
    "Sono in ritardo, non sono ancora arrivato",
    "Non sono ancora arrivata, sono in ritardo",
    "Domani sono qui alle 9",
    "I'm late, I am not here yet",
]
ARRIVED = ["Sono qui", "Eccomi", "Sono arrivato", "Sono arrivata, faccio il check-in", "I'm here", "Sono presente"]
NOT_CANCEL = ["Grazie per la risposta!", "sono davanti al cancello", "Grazie mille per la risposta"]
CANCEL = ["Vorrei annullare la visita", "Devo disdire", "Non posso venire per la visita di domani",
          "please cancel my booking", "Vorrei spostare l'appuntamento"]


def arrival(text):
    return bool(brain._ARRIVAL_RE.search(text)) and not brain._NOT_YET_RE.search(text) \
        and not brain._CANCEL_RE.search(text)


for t in NOT_ARRIVED:
    assert not arrival(t), f"false check-in: {t!r}"
for t in ARRIVED:
    assert arrival(t), f"missed arrival: {t!r}"
for t in NOT_CANCEL:
    assert not brain._CANCEL_RE.search(t), f"false cancel: {t!r}"
for t in CANCEL:
    assert brain._CANCEL_RE.search(t), f"missed cancel: {t!r}"
print("test_checkin_phrases OK")
