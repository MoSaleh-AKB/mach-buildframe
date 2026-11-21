"""#141: in the gp brain one denial covers the whole "X or Y" list of its clause.

"I don't have chest pain or shortness of breath, only my knee hurts" went to 112 after #88. Real signs after
a cut (but, and, a comma) or with an uncertain "not sure" still escalate.

Run: <city python> -I tests/gp/test_gp_denial_list.py asst/_gp/agent.py
"""
import importlib.util, sys
from city_sdk.city import Message
spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_gp/agent.py"); b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
def hit(t):
    return bool(b._emergency(Message(type="chat", sender="citizen:t07/c1", msg_id="m1", need_id="n1", body={"text": t}, recipient="agent:t07/gp"), wide=True))
NEG = ["I don't have chest pain or shortness of breath, only my knee hurts", "Non ho dolore al petto o difficoltà a respirare, solo mal di schiena",
       "I have no chest pain or breathing problems, just a sore back", "senza dolore al petto né fiato corto, ho male al ginocchio",
       "I don't have chest pain, only my knee hurts"]
POS = ["I don't have chest pain but I can't breathe", "I don't have a fever and I have crushing chest pain", "I'm not sure if it is chest pain or a heart attack",
       "Non ho febbre, ma ho un forte dolore al petto", "ho un dolore al petto fortissimo adesso", "non riesco a respirare", "I have chest pain or pressure right now",
       "my son swallowed a button battery", "Mio padre non risponde e non respira"]
bad = [f"FALSE ALARM {t}" for t in NEG if hit(t)] + [f"MISS {t}" for t in POS if not hit(t)]
for x in bad: print("FAIL", x)
print("OK test_gp_denial_list" if not bad else f"FAIL {len(bad)} test_gp_denial_list"); sys.exit(1 if bad else 0)
