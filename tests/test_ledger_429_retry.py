"""#93: one Ledger HTTP 429 is a rate limit, not a refusal. The same send must go out on the
next try (429 then 200 -> the booking goes on) and the Ledger is not left marked dead.
A real 4xx refusal (400) still blocks the same send for _REJECT_TTL.
Run: <city python> -I tests/test_ledger_429_retry.py [asst/_shared/agent.py]"""
import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace

from city_sdk.city import LEDGER, CapError

os.environ.setdefault("CITY_ROLE", "booking_centre")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
n = [0]


def load():
    n[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain93_{n[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    return b


class City:
    """Fake relay: the first ``fails`` Ledger sends raise ``code``, then every send goes out."""

    def __init__(self, need, code, fails=1):
        self.inbound = SimpleNamespace(need_id=need, msg_id=f"m-{need}")
        self.code, self.fails, self.tries, self.out = code, fails, 0, []

    async def say(self, to, text, *, status=None):
        if to == LEDGER:
            self.tries += 1
            if self.tries <= self.fails:
                raise CapError(self.code, "relay")
        self.out.append((to, text))
        return "ok"


async def main():
    hold = "hold slot_id=S1 citizen=citizen:t07/c1"

    # 429 then 200: the second try goes out, nothing is remembered as refused, Ledger alive.
    b = load()
    c = City("N-429", "RATE_LIMITED")
    assert await b._say(c, LEDGER, hold) is False, "the 429 send cannot count as sent"
    assert not any(k[0] == "N-429" for k in b._REJECTED), f"429 recorded as a refusal: {dict(b._REJECTED)}"
    assert await b._say(c, LEDGER, hold) is True, "after a 429 the same hold was blocked"
    assert c.tries == 2 and c.out == [(LEDGER, hold)], (c.tries, c.out)
    assert not b._ledger_dead("N-429"), "Ledger still marked dead after a good send"

    # A real refusal (400) is still not sent again inside _REJECT_TTL.
    b = load()
    c = City("N-400", "SCHEMA_INVALID")
    assert await b._say(c, LEDGER, hold) is False
    assert any(k[0] == "N-400" for k in b._REJECTED), "a 400 must be remembered as refused"
    assert await b._say(c, LEDGER, hold) is False, "a 400 refused send went out again"
    assert c.tries == 1, f"400 send retried: {c.tries}"
    print("OK test_ledger_429_retry")


asyncio.run(main())
