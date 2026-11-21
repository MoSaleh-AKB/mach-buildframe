"""Swallowed button battery / magnet and sudden breathlessness are red flags in both brains.

Live-fail check (X100_A11, L8_v1_05, L3_v1_12): "la bambina ha inghiottito una batteria", "ha mangiato
una pila piatta", "batteria a bottone ingoiata dal bimbo" were missed (a button battery burns the
oesophagus in hours). The breathlessness phrases of the old branch sobhan-redflag-breath-battery are
already caught on development and stay here as a guard. Everyday battery talk must not alarm.

Run: <city python> -I tests/test_redflag_button_battery.py [brain.py]
"""

import importlib.util
import os
import sys

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
BRAINS = sys.argv[1:] or [os.path.join(HERE, "..", "asst", d, "agent.py") for d in ("_shared", "_gp")]
POS = [
    "mi è mancato il respiro all'improvviso", "Mi e' mancato il fiato salendo le scale e ora non respiro bene",
    "mi manca improvvisamente il respiro", "mi manca l'aria da stamattina",
    "mio figlio ha ingoiato una pila a bottone", "la bambina ha inghiottito una batteria", "ha mangiato una pila piatta",
    "ho ingoiato una calamita", "batteria a bottone ingoiata dal bimbo", "my son swallowed a button battery",
    "il bambino ha ingerito due pile", "credo che abbia inghiottito la calamita del frigo", "pile ingoiate dal bambino un'ora fa",
]
NEG = [
    "mi manca il mio medico di prima", "mi è mancato il tempo per venire", "ho comprato una pila nuova per il telecomando",
    "la batteria del telefono è scarica", "ho mangiato e poi ho cambiato la pila del telecomando", "ho comprato una pila piatta",
    "la batteria a bottone dell'orologio è finita", "ha mangiato una mela", "le pile ingoiate dal cane? no, era un giocattolo",
    "non mi manca il respiro, sto bene", "vorrei prenotare una visita di controllo",
]


def main():
    bad = []
    for path in BRAINS:
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location("brain", path)
        brain = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(brain)

        def hit(text):
            m = Message(type="chat", sender="citizen:t07/c1", msg_id="m1", need_id="n1", body={"text": text}, recipient="agent:t07/gp")
            return bool(brain._emergency(m, wide=True))

        name = os.path.basename(os.path.dirname(path)) or path
        bad += [f"{name} MISS {t!r}" for t in POS if not hit(t)]
        bad += [f"{name} FALSE ALARM {t!r}" for t in NEG if hit(t)]
    for line in bad:
        print("FAIL", line)
    print("OK test_redflag_button_battery" if not bad else f"FAIL {len(bad)} test_redflag_button_battery")
    sys.exit(1 if bad else 0)


main()
