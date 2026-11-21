"""B9 (export, L8): the desk asked for the prescription number 4 times (every empty
data.response got the same data.request) and never sent the citizen to the family doctor. After
one ask, a reply without a number gets the family-doctor line once, never the same ask again.
Slow Ledger (8 s writes); the turn cap is the live 10 s.
Run: <city python> -I tests/test_b9_rx_ask_once.py asst/_shared/agent.py"""
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


def asks(city):
    head = brain._ASK_RX.splitlines()[0]
    return [x for _, x in city.lines() if x.splitlines()[:1] == [head]]


async def main():
    for lang, first, empty, gp_word in (
        ("it", "Buongiorno, vorrei prenotare una visita", "data.response purpose=booking", "medico di base"),
        ("en", "Hello, I would like to book a visit please", "data.response purpose=booking", "family doctor"),
    ):
        city = City("b9-" + lang, say_slow=8.0)
        await run(city, first, cap=10.0)
        check(len(asks(city)) == 1, f"{lang}: first message, one ask for the number ({len(asks(city))})", city)
        for _ in range(3):
            await run(city, empty, cap=10.0)
        await run(city, "Ho mal di schiena. Vorrei prenotare visita fisiatrica, subito." if lang == "it"
                  else "My back hurts. I want a physiatry visit now.", cap=10.0)
        said = [x for _, x in city.lines()]
        gp = [x for x in said if gp_word in x]
        check(len(asks(city)) <= 2, f"{lang}: never the same ask 4 times ({len(asks(city))} data.request lines)", city)
        check(bool(gp), f"{lang}: the citizen is sent to the family doctor ({len(gp)})", city)
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
