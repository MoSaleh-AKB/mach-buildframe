"""L8 G2 + G5 (gp and consultorio, asst/_gp/agent.py): a red flag gets the 112 line at once,
also when the history read is slow (8 s on a slow relay), and once only (twin turns of the same
message). The 112 line is skipped only when a 112 line already followed this message, not when
any other line of ours (a holding line) came after it.
Run: <city python> -I tests/gp/test_gp_red_flag_first.py asst/_gp/agent.py"""
import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "gp"
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
CONS_RED = "Non ce la faccio più, voglio farla finita stasera"

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
    for role, text in (("gp", CHEST), ("family_counseling", CONS_RED)):
        os.environ["CITY_ROLE"] = role
        # G5: slow history read, the 112 line goes first and fast.
        city = City(role + "-slow", hist=8.0)
        await run(city, text, cap=12.0)
        first = city.lines()[:1]
        check(bool(first) and first[0][1].startswith("escalate.emergency") and first[0][0] < 1.5,
              f"{role}: 112 line first and within 1.5 s with an 8 s history read "
              f"({(round(first[0][0], 1), first[0][1][:30]) if first else None})", city)
        check(len(esc(city)) == 1, f"{role}: one 112 line ({len(esc(city))})", city)
        # Twin turns of the same message: one 112 line.
        city = City(role + "-twin", hist=0.5)
        await run(city, text, cap=12.0, twice=True)
        check(len(esc(city)) == 1, f"{role}: twin turns, one 112 line ({len(esc(city))})", city)
        # Calm text with a slow read: no 112 line.
        city = City(role + "-calm", hist=2.0)
        await run(city, CALM, cap=12.0)
        check(not esc(city), f"{role}: calm text, no 112 line", city)
        # G2: our holding line after the red-flag message is no answer to it.
        city = City(role + "-held")
        row = msg(city.cz, ME, text, city.need)
        city.rows.append(row)
        city.rows.append(msg(ME, city.cz, "Sto verificando, le rispondo tra pochi secondi.", city.need))
        await brain._escalate(View(city, row), list(city.rows), row)
        check(len(esc(city)) == 1, f"{role}: holding line before, the 112 line still goes out ({len(esc(city))})", city)
        # A 112 line already after this message: nothing more.
        city = City(role + "-done")
        row = msg(city.cz, ME, text, city.need)
        city.rows.append(row)
        city.rows.append(msg(ME, city.cz, "escalate.emergency reason=red_flag\nChiami il 112.", city.need))
        await brain._escalate(View(city, row), list(city.rows), row)
        check(not esc(city), f"{role}: 112 already sent after this message, no second one", city)
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
