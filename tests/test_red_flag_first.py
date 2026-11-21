"""L8 G5 (desk, hospital, specialist, asst/_shared/agent.py): a citizen red flag gets the 112 line
at once, also when the history read is slow (8 s) and when a slow turn of the same need holds the
need lock (live: red flags waited minutes in that queue), and once only (twin turns). A calm text
gets no 112 line.
Run: <city python> -I tests/test_red_flag_first.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

ME = "agent:t/me"
DESK = "agent:y6hxsd766sys"
SEQ = [0]
FAILS = []

CALM = "Buongiorno, ho mal di schiena da qualche giorno, mi serve una visita"
BACK_EN = "Hello, my back has been hurting for days, I need a visit please"
CHEST = "Ho un forte dolore al petto che va al braccio sinistro e sudo freddo"

def msg(sender, to, text, need, mid=None):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=mid or f"h{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


class City:
    """One case: history may be slow, Ledger writes may be slow, the Ledger never answers."""

    def __init__(self, name, *, hist=0.0, say_slow=0.0, llm=None, sender=None):
        self.name = name
        self.need = "N-" + name
        self.cz = sender or "citizen:c-" + name
        self.rows = []
        self.out = []  # (t, to, text)
        self.hist = hist
        self.say_slow = say_slow
        self.llm = llm
        self.t0 = time.monotonic()

    async def say(self, to, text, *, status=None):
        if to == LEDGER and self.say_slow:
            await asyncio.sleep(self.say_slow)
        self.out.append((time.monotonic() - self.t0, to, text))
        self.rows.append(msg(ME, to, text, self.need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        if self.hist:
            await asyncio.sleep(self.hist)
        return list(self.rows)

    async def find(self, query):
        return []

    def lines(self, to=None):
        to = to or self.cz
        return [(t, x) for t, w, x in self.out if w == to]


class View:
    def __init__(self, city, inbound):
        self._c = city
        self.inbound = inbound
        self.agent_id = ME
        self.address = ""
        if city.llm is not None:
            self.llm = city.llm

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


async def turn(city, row, cap):
    city.t0 = time.monotonic()
    try:
        await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), View(city, row)), cap)
    except BaseException:  # noqa: BLE001 - OutOfScope, timeouts: only the lines matter here
        pass


async def run(city, text, cap=7.0, twice=False):
    row = msg(city.cz, ME, text, city.need)
    city.rows.append(row)
    if twice:
        await asyncio.gather(turn(city, row, cap), turn(city, row, cap))
    else:
        await turn(city, row, cap)
    return row


async def hang(messages):
    await asyncio.sleep(30)
    return {"content": "NO"}


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        if city is not None:
            for t, to, x in city.out:
                print(f"    {t:5.1f}s -> {to}: {x[:90]!r}")


def esc(city):
    return [(t, x) for t, x in city.lines() if x.split(" ", 1)[0].startswith("escalate.emergency")]


async def main():
    for role in ("booking_centre", "hospital", "specialist_care"):
        os.environ["CITY_ROLE"] = role
        city = City(role + "-slow", hist=8.0)
        await run(city, CHEST, cap=12.0)
        first = city.lines()[:1]
        check(bool(first) and first[0][1].startswith("escalate.emergency") and first[0][0] < 1.5,
              f"{role}: 112 line first and within 1.5 s with an 8 s history read "
              f"({(round(first[0][0], 1), first[0][1][:30]) if first else None})", city)
        check(len(esc(city)) == 1, f"{role}: one 112 line ({len(esc(city))})", city)
        # A slow earlier turn of the same need holds the need lock: the red flag does not wait.
        city = City(role + "-queue", hist=8.0)
        calm = msg(city.cz, ME, CALM, city.need)
        city.rows.append(calm)
        slow = asyncio.create_task(turn(city, calm, 12.0))
        await asyncio.sleep(0.3)
        red = msg(city.cz, ME, CHEST, city.need)
        city.rows.append(red)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), View(city, red)), 12.0)
        except BaseException:  # noqa: BLE001 - a turn stuck behind the lock: only the lines matter
            pass
        took = time.monotonic() - t0
        await slow
        check(len(esc(city)) == 1 and took < 1.5,
              f"{role}: red flag behind a slow turn of the same need, 112 at once ({took:.1f} s, {len(esc(city))})", city)
        city = City(role + "-twin", hist=0.5)
        await run(city, CHEST, cap=12.0, twice=True)
        check(len(esc(city)) == 1, f"{role}: twin turns, one 112 line ({len(esc(city))})", city)
        city = City(role + "-calm", hist=1.0)
        await run(city, CALM, cap=12.0)
        check(not esc(city), f"{role}: calm text, no 112 line", city)
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
