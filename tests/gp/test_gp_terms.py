"""SI-13: technical words from a report or a referral map to the protocol code and never escalate.
Run: <city python> -I tests/test_gp_terms.py asst/_shared/agent.py"""
import importlib.util
import sys

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

CASES = {
    "ho una lombosciatalgia da una settimana": "SYM-BACK",
    "il medico dice che ho un'ernia del disco": "SYM-BACK",
    "mi fa male il menisco": "SYM-KNEE",
    "ho rotto il legamento crociato giocando a calcio": "SYM-KNEE",
    "mi fa male la rotula": "SYM-KNEE",
    "ho il cardiopalmo la sera": "SYM-PALPITATIONS",
    "mi serve un ECG di controllo": "SYM-FOLLOWUP",
    "devo rifare l'ecocardiogramma": "SYM-FOLLOWUP",
    "mi serve un holter": "SYM-FOLLOWUP",
    "vorrei una visita cardiologica": "SYM-FOLLOWUP",
}
bad = []
for text, code in CASES.items():
    m = Message(type="chat", sender="citizen:t/c1", msg_id="m1", need_id="n1", body={"text": text}, recipient="agent:t/gp")
    got = brain._symptom(m)
    if got != code or brain._emergency(m, wide=True):
        bad.append(f"{text} -> {got}")
print("\n".join(bad) or f"OK test_gp_terms {len(CASES)}")
sys.exit(1 if bad else 0)
