"""R13 (#98): the third cancel cycle with no Ledger slots row in the chat must not crash.

_latest_slots(chat) is None when the world read never came back; _cycle_ladder must then
return False (the caller goes on to the next answer) instead of None.splitlines().

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_r13_ladder_no_slots.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC, CZ, NEED = "agent:t98bc", "citizen:t98-c1", "need-t98"
SEQ = [0]


def msg(sender, to, text):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=NEED, body={"text": text},
                   sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))


class City:
    agent_id, address, need, inbound = BC, "", NEED, None

    def __init__(self):
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))


def main():
    chat = [
        msg(CZ, BC, "Vorrei prenotare: prescription_number=RX1"),
        msg(BC, LEDGER, "prescription prescription_number=RX1"),
        msg(LEDGER, BC, f"prescription prescription_number=RX1 patient={CZ} procedure=VIS-FIS priority=P"),
        msg(BC, LEDGER, "agenda"),
        msg(LEDGER, BC, "agenda agenda_now=2026-11-23T08:00:00+00:00"),
        msg(CZ, BC, "No, annullo ancora."),
    ]
    city = City()
    try:
        got = asyncio.run(brain._cycle_ladder(city, chat[-1], chat, None))
    except AttributeError as exc:
        print("FAIL: crash with no slots row:", exc)
        sys.exit(1)
    if got is not False:
        print("FAIL: expected False with no slots row, got", got)
        sys.exit(1)
    print("PASS: no slots row -> _cycle_ladder returns False, no crash")


if __name__ == "__main__":
    main()
