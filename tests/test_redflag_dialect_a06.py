"""L8 (A06): dialect and broken-Italian chest pain / breathing escalate on both brains; calm texts stay calm.
Run: <city python> -I tests/test_redflag_dialect_a06.py asst/_shared/agent.py   (and asst/_gp/agent.py)"""
import importlib.util
import sys

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
RED = ["c'ho 'na fitta ar petto che nun me fa respirà", "io avere dolore forte petto e braccio sinistra",
       "me fa male er petto forte forte", "io no respira bene, dolore petto"]
CALM = ["nessun dolore al petto, solo mal di schiena", "vorrei una visita al ginocchio", "respiro bene, grazie"]
bad = 0
for i, t in enumerate(RED + CALM):
    m = Message(type="chat", sender="citizen:t/c1", msg_id=f"m{i}", need_id="n1", body={"text": t}, recipient="agent:t/x")
    if bool(b._emergency(m, wide=True)) != (t in RED):
        bad += 1
        print("FAIL", t)
print(f"{bad} FAIL" if bad else "OK 0")
sys.exit(1 if bad else 0)
