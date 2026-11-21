"""gp, L8 E04: two own needs for two different visits in one message ("palpitazioni e mal di schiena")
get one prescription each, the heart visit first; neither need is dropped. Two needs for the same
visit, a relative's sign, typed protocol tokens and red flags keep their old path.
Run: <city python> -I tests/gp/test_gp_two_needs.py asst/_gp/agent.py"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

os.environ.setdefault("CITY_ROLE", "gp")
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for k, v in (("_ADVANCE_BUDGET", 0.6), ("_PULL_BUDGET", 0.8), ("_AGENT_BUDGET", 0.6), ("_CONSENT_WAIT", 0.3),
             ("_XA_BUDGET", 0.5), ("_XA_GRACE", 0.2), ("_VERIFY_BUDGET", 0.3), ("_LLM_NET", False)):
    if hasattr(brain, k):
        setattr(brain, k, v)

GP, ME = "agent:jmqwr12vy2j6", "citizen:t-two"
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    def __init__(self, need):
        self.role, self.agent_id, self.need, self.address = "gp", GP, need, ""
        self.rows, self.out, self.inbound = [], [], None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(GP, to, text, self.need))
        return "x"

    async def find(self, q):
        return []

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if peer is None or peer in (r.sender, r.recipient)]


async def turn(c, text):
    m = msg(ME, GP, text, c.need)
    c.rows.append(m)
    c.inbound = m
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=m), c), 10)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise


def rx_of(c):
    return [t for to, t in c.out if to == LEDGER and t.startswith("prescribe ")]


TWO = [
    ("it", "palpitazioni e mal di schiena", "seconda ricetta"),
    ("it", "Buongiorno, ho le palpitazioni e anche mal di schiena da una settimana", "seconda ricetta"),
    ("en", "I have palpitations and back pain", "second prescription"),
    ("es", "Tengo palpitaciones y dolor de espalda", "segunda receta"),
]
# One prescription, as before: same visit, a relative's sign, typed tokens, a past sign.
ONE = [
    ("Ho mal di schiena e male al ginocchio", "SYM-BACK"),
    ("Mio marito ha palpitazioni e io ho mal di schiena", "SYM-BACK"),
    ("Ho mal di schiena. SYM-PALPITATIONS", "SYM-BACK"),
    ("Ho mal di schiena; l'anno scorso avevo palpitazioni", "SYM-BACK"),
]
RED = ["Ho palpitazioni e dolore al petto adesso, e mal di schiena"]


async def main():
    bad = []
    for lang, text, lead in TWO:
        N[0] += 1
        c = City(f"n{N[0]}")
        await turn(c, text)
        rx = rx_of(c)
        said = " ".join(t for to, t in c.out if to == ME)
        if (
            len(rx) != 2 or "symptom_code=SYM-PALPITATIONS" not in rx[0] or "procedure=VIS-CARD" not in rx[0]
            or "symptom_code=SYM-BACK" not in rx[1] or "procedure=VIS-FIS" not in rx[1]
            or any(f"patient={ME}" not in w for w in rx)
        ):
            bad.append(("two: want VIS-CARD then VIS-FIS", text, rx))
            continue
        numbers = [brain.fields_of(w)["prescription_number"] for w in rx]
        if numbers[0] == numbers[1] or not all(n in said for n in numbers) or lead not in said:
            bad.append(("two: citizen does not get both numbers", text, said[:200]))
        # The same message again in the case: no third prescribe, still an answer.
        before = len(c.out)
        await turn(c, text)
        if len(rx_of(c)) != 2 or not any(to == ME for to, _ in c.out[before:]):
            bad.append(("two: repeat writes again or goes silent", text, rx_of(c)))
    for text, code in ONE:
        N[0] += 1
        c = City(f"n{N[0]}")
        await turn(c, text)
        rx = rx_of(c)
        if len(rx) != 1 or (code and f"symptom_code={code}" not in rx[0]):
            bad.append(("one: want exactly one prescribe", text, rx))
    for text in RED:
        N[0] += 1
        c = City(f"n{N[0]}")
        await turn(c, text)
        said = " ".join(t for to, t in c.out if to == ME)
        if "escalate.emergency" not in said or rx_of(c):
            bad.append(("red: no escalation", text, said[:160]))
    for row in bad:
        print("FAIL", *row)
    total = len(TWO) + len(ONE) + len(RED)
    print("OK", total - len({r[1] for r in bad}), "/", total)
    sys.exit(1 if bad else 0)


asyncio.run(main())
