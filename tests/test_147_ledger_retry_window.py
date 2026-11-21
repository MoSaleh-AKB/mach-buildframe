"""#147: a Ledger 4xx blocks the same send only for _REJECT_TTL (90 s); a facility 4xx still blocks
it for good (#60). A gateway 408 reaches the brain as POLICY_VIOLATION (the SDK maps an unknown
status so), so a timed-out check-in must be re-sendable. A TOO_EARLY / TOO_LATE check-in refusal
with no minutes is no "your booking is valid, stay" grace.
Run: <city python> -I tests/test_147_ledger_retry_window.py [asst/_shared/agent.py]   (exit 0 = pass)"""
import asyncio
import importlib.util
import os
import sys
import time
from types import SimpleNamespace

from city_sdk.city import LEDGER, CapError, Message

os.environ.setdefault("CITY_ROLE", "booking_centre")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain147", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
FAILS = []
FAC = "agent:t07/hospital"


class City:
    """Fake relay: the first send to each recipient raises POLICY_VIOLATION, then sends go out."""

    def __init__(self, need):
        self.inbound = SimpleNamespace(need_id=need, msg_id=f"m-{need}")
        self.tries, self.out = {}, []

    async def say(self, to, text, *, status=None):
        self.tries[to] = self.tries.get(to, 0) + 1
        if self.tries[to] == 1:
            raise CapError("POLICY_VIOLATION", "408 request timeout")
        self.out.append((to, text))
        return "ok"


def age(need, to, secs):
    for k in [k for k in b._REJECTED if k[0] == need and k[1] == to]:
        b._REJECTED[k] = time.monotonic() - secs


async def main():
    c = City("N-147")
    checkin = "checkin booking_id=bk-1 citizen=citizen:t07/c1"
    await b._say(c, LEDGER, checkin)
    if await b._say(c, LEDGER, checkin):
        FAILS.append("Ledger checkin re-sent at once after a refusal (want: blocked inside 90 s)")
    age("N-147", LEDGER, b._REJECT_TTL + 1)
    if not await b._say(c, LEDGER, checkin) or (LEDGER, checkin) not in c.out:
        FAILS.append("Ledger checkin refused once is never re-sent, even after 90 s")
    cancel = "cancel offer_id=of-1 reason=CONSENT_MISSING"
    await b._say(c, FAC, cancel)
    age("N-147", FAC, b._REJECT_TTL + 1)
    if await b._say(c, FAC, cancel):
        FAILS.append("a facility 4xx was retried (#60: never)")

    def reply(text):
        return Message(type="chat", sender=LEDGER, msg_id="r1", need_id="N-147", body={"text": text}, recipient=FAC)
    if b._window_grace(reply("refused booking_id=bk-1 code=TOO_EARLY")):
        FAILS.append("TOO_EARLY with no minutes told as 'booking valid, stay'")
    if not b._window_grace(reply("refused booking_id=bk-1 code=TOO_LATE minutes=10")):
        FAILS.append("TOO_LATE by 10 min lost its grace")
    if b._window_grace(reply("refused booking_id=bk-1 code=TOO_EARLY minutes=240")):
        FAILS.append("TOO_EARLY by 4 h got the grace")
    for f in FAILS:
        print("FAIL", f)
    print(("OK" if not FAILS else f"FAIL {len(FAILS)}") + " test_147_ledger_retry_window")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
