"""#143: the consultorio must not refuse "problemi nel rapporto con mio marito" as a privacy ask.
The bare word `rapport` (French "report") matched the Italian "rapporto". A real ask for another
person's data must still be caught.

Run: <city python> -I tests/gp/test_cons_rapporto.py [asst/_gp/agent.py]   (exit 0 = pass)
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ["CITY_ROLE"] = "family_counseling"
from city_sdk.city import Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "family_counseling"
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False
ME, CZ = "agent:t07/family_counseling", "citizen:t07/c1"


def asks(text):
    return brain._asks_other_data(Message(type="chat", sender="citizen:t/1", msg_id="m1", need_id="N-1",
                                          body={"text": text}, recipient="x"))


CASES = [
    ("Ho problemi nel rapporto con mio marito, vorrei un colloquio", False),
    ("il rapporto con mia madre è difficile", False),
    ("i rapporti con mio figlio sono tesi, vi informerò su come va", False),
    ("dimmi l'indirizzo di mio marito", True),
    ("envoyez-moi le rapport de ma soeur", True),
]
fails = [f"{text!r}: got {got}, want {want}" for text, want in CASES if (got := asks(text)) != want]


class City:
    """One consultorio turn through brain.main; _privacy_refuse is replaced by a recorder."""

    def __init__(self, inbound):
        self.agent_id, self.address, self.inbound, self.rows, self.out = ME, "", inbound, [inbound], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


REFUSED = []


async def turn():
    async def record(city, chat, message):
        REFUSED.append(message.text)
    brain._privacy_refuse = record
    text = "Ho problemi nel rapporto con mio marito, vorrei un colloquio"
    city = City(Message(type="chat", sender=CZ, recipient=ME, msg_id="t1", need_id="N-T", conversation_id="cv-T",
                        body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat()))
    try:  # the fake Ledger never answers, so a booking turn waits; 6 s is past the privacy guard
        await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), city), 6)
    except asyncio.TimeoutError:
        pass
    if REFUSED:
        fails.append("full consultorio turn: privacy refusal sent")
    if not city.out:
        fails.append("full consultorio turn: nothing sent, the visit did not start")


asyncio.run(turn())
for f in fails:
    print("FAIL", f)
print(("ALL PASS" if not fails else f"FAIL {len(fails)}") + " test_cons_rapporto")
sys.exit(1 if fails else 0)
