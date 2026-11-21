"""Q5: while a specialist report waits for the Ledger's answer to the follow-up prescribe, it reads
the case once per 2 s (before: every 0.5 s, up to 16 reads per report). Same 8 s budget.
Run: <city python> -I tests/test_report_poll_2s.py [asst/_shared/agent.py]   (exit 0 = pass)"""
import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import LEDGER, Message

os.environ.setdefault("CITY_ROLE", "specialist")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brainq5", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
SP = "agent:t07/specialist"


def msg(sender, to, text):
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"q5-{time.monotonic()}", need_id="N-q5",
                   body={"text": text})


class City:
    agent_id, address = SP, ""

    def __init__(self, answer_after):
        self.t0, self.after, self.reads = time.monotonic(), answer_after, 0

    async def history(self, peer=None, *, need_id=None):
        self.reads += 1
        if time.monotonic() - self.t0 >= self.after:
            return [msg(LEDGER, SP, "prescribed prescription_number=RX-F1")]
        return []


SENT = []


async def fake_send(city, message, chat, booking_id, citizen, consent_ref, key, parts):
    SENT.append(" ".join(parts))


async def main():
    b._send_report = fake_send
    city = City(6.0)
    m = msg("citizen:t07/c1", SP, "x")
    await b._report_after_rx(city, m, [m], "bk-1", "citizen:t07/c1", "cons-1", "k1",
                             ["report", "booking_id=bk-1", "follow_up_prescription_number=RX-F1"], "RX-F1")
    fails = []
    if city.reads > 4:
        fails.append(f"{city.reads} case reads while the report waited 6 s, want at most 4")
    if not SENT or "follow_up_prescription_number=RX-F1" not in SENT[0]:
        fails.append(f"report not filed with the follow-up number: {SENT}")
    for f in fails:
        print("FAIL", f)
    print(("OK" if not fails else f"FAIL {len(fails)}") + " test_report_poll_2s")
    sys.exit(1 if fails else 0)


asyncio.run(main())
