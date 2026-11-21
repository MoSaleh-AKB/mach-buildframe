"""#119: _renew waits for the Ledger answer to ITS prescription read. An old `refused` row earlier in
the case (another op, minutes ago) must not end the wait: on a slow Ledger (2 s) the renewal still
reads the old prescription and issues the new one (`prescribe`), never "tell me your symptoms".
Run: <city python> -I tests/test_renew_old_refusal.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 6.0

AG, CZ, NEED = "agent:t119/gp", "citizen:t119/c1", "N-t119"
N = [0]


def msg(sender, to, text):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=NEED, body={"text": text}, recipient=to)


class City:
    agent_id = AG
    inbound = None

    def __init__(self):
        self.rows = [msg(LEDGER, AG, "refused op=world reason=rate_limited")]
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text))
        if to == LEDGER and text.startswith("prescription "):
            async def late():
                await asyncio.sleep(2.0)
                self.rows.append(msg(LEDGER, AG, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=expired"))
            asyncio.get_running_loop().create_task(late())
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


async def main():
    city = City()
    m = msg(CZ, AG, "need.request prescription_number=RX1")
    city.rows.append(m)
    await brain._renew(city, m, list(city.rows), "RX1")
    led = [t for to, t in city.out if to == LEDGER]
    ok = any(t.startswith("prescribe ") for t in led)
    print(("OK" if ok else "FAIL") + f" renewal after an old refusal: ledger ops={[t.split()[0] for t in led]}, "
          f"citizen={[t[:50] for to, t in city.out if to == CZ]}")
    return ok

sys.exit(0 if asyncio.run(main()) else 1)
