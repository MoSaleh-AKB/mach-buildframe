"""B7 (export, 17 failed): "Messaggio N ricevuto" went to a citizen who never wrote to the desk
(a GP hand-off), and N counted messages that were not to us. The status line counts only the
citizen's own messages to us, and a hand-off proxy gets no message count at all.
Run: <city python> -I tests/test_b7_message_count.py asst/_shared/agent.py"""
import importlib.util
import os
import sys
from datetime import datetime, timezone

from city_sdk.city import Message

os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

ME = "agent:y6hxsd766sys"
GP = "agent:jmqwr12vy2j6"
CZ = "citizen:c-b7"
FAILS = []


def msg(sender, to, text, mid):
    return Message(type="chat", sender=sender, recipient=to, msg_id=mid, need_id="N-b7", conversation_id="cv-b7",
                   body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


def check(ok, label, line):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        print("    ", repr(line[:160]))


hand = msg(GP, ME, f"hand-off citizen={CZ} prescription_number=RX-1 lang=it\nVisita fisiatrica per mal di schiena.", "g1")
proxy = brain._handoff_proxy(hand, ME)
check(proxy is not None, "hand-off proxy built", "")
line = brain._never_silent([hand], ME, proxy)
check("ricevuto" not in line and "received" not in line, "hand-off, citizen never wrote: no 'Messaggio N ricevuto'", line)
to_gp = msg(CZ, GP, "Dottore, ho mal di schiena da giorni", "c0")
first = msg(CZ, ME, "Buongiorno, aspetto la visita", "c1")
line = brain._never_silent([to_gp, hand, first], ME, first)
check("Messaggio 2" not in line and "Messaggio 3" not in line, "a message to the gp is not counted", line)
second = msg(CZ, ME, "Ci sono novità?", "c2")
line = brain._never_silent([to_gp, hand, first, second], ME, second)
check("Messaggio 2 ricevuto" in line, "two citizen messages to us: count 2", line)
line = brain._never_silent([to_gp, hand], ME, first)
check("Messaggio 1 ricevuto" in line, "history misses the current citizen row: still counted", line)
print(f"{len(FAILS)} FAIL")
sys.exit(1 if FAILS else 0)
