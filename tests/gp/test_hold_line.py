"""L7 S14 / S21: a slow relay, Ledger or model must not leave a citizen in silence for more than 3 s.
The gp and the consultorio say one short holding line by about 3 s when the turn has told the citizen nothing yet;
never a second line when the real answer is on time, never to an agent or the Ledger, never before
an emergency reply.
Armour train: the gp + consultorio brain is asst/_gp/agent.py (desk and facility roles run asst/_shared).
Run: <city python> -I tests/test_hold_line.py asst/_gp/agent.py"""
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

BACK = "Buongiorno, ho mal di schiena da qualche giorno, mi serve una visita"
BACK_EN = "Hello, my back has been hurting for days, I need a visit please"
RENEW = "Buongiorno, la mia ricetta RX-77-AB è scaduta, me la può rinnovare?"
LONG = "Buongiorno dottore, da tre giorni ho un mal di schiena che non mi lascia dormire, vorrei proprio una visita"
LONGRED = "Buongiorno, da stamattina mio padre ha la bocca storta e non riesce a muovere bene il braccio destro, cosa facciamo"
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
                print(f"      [{t:5.1f}s] {to[-14:]:>14} : {x[:110]!r}")


def const(name):
    return getattr(brain, name, None)


def loc(text, row):
    if text is None:
        return None
    hold_text = const("_hold_text")
    return hold_text(row, text, []) if hold_text else brain._loc(row, text)


def first(city):
    got = city.lines()
    return got[0] if got else (99.0, "")


def held(city, cases):
    """Number of holding lines this case sent to the citizen."""

    notes = {loc(c, city.rows[0]) for c in cases if c}
    return sum(1 for _, x in city.lines() if x in notes)


HOLDS = ("_R5_WAIT_NOTE", "_RENEW_SLOW", "_HOLD_NOTE_CONS", "_BOOKING_WAIT", "_HOLD_CONFIRM", "_HOLD_CHECKIN", "_HOLD_GOT")


async def expect_hold(city, text, want, *, cap=7.0, en=False, or_rx=False):
    row = await run(city, text, cap)
    t, line = first(city)
    note = loc(const(want), row)
    if en and const(want) is not None:
        note = brain._EN_TEXT.get(const(want)) or (const("_HOLD_EN") or {}).get(const(want))
    # #148: a slow Ledger prescribe no longer delays the prescription past _HOLD_AFTER: the real
    # answer by 3 s is better than the holding line.
    rx_first = or_rx and line is not None and line.startswith("prescription ")
    check(note is not None and t < 3.0 and (line == note or rx_first),
          f"{os.environ['CITY_ROLE']} {city.name}: {want} by 3 s (first line at {t:.1f} s)", city)
    count = sum(1 for _, x in city.lines() if x == note)
    check(count <= 1, f"{os.environ['CITY_ROLE']} {city.name}: holding line once ({count})", city)
    return row


async def gp():
    os.environ["CITY_ROLE"] = "gp"
    slow_hist = City("gp-hist", hist=6.0)
    slow_say = City("gp-say", say_slow=8.0)
    renew = City("gp-renew")
    llm = City("gp-llm", llm=hang)
    en = City("gp-en", hist=6.0)
    await asyncio.gather(
        expect_hold(slow_hist, BACK, "_R5_WAIT_NOTE"),
        expect_hold(slow_say, BACK, "_R5_WAIT_NOTE", cap=10.0, or_rx=True),
        expect_hold(renew, RENEW, "_RENEW_SLOW"),
        expect_hold(llm, LONG, "_R5_WAIT_NOTE", cap=8.0),
        expect_hold(en, BACK_EN, "_R5_WAIT_NOTE", en=True),
    )
    for c in (slow_hist, slow_say, llm):
        rx = [x for _, x in c.lines() if x.startswith("prescription prescription_number=")]
        check(len(rx) == 1, f"gp {c.name}: the prescription still follows, once ({len(rx)})", c)
    slow = brain._loc(msg(renew.cz, ME, RENEW, renew.need), brain._RENEW_SLOW)
    still = brain._loc(msg(renew.cz, ME, RENEW, renew.need), brain._RENEW_STILL)
    n = [x for _, x in renew.lines() if x in (slow, still)]
    check(len(n) == 1, f"gp renewal: the slow-lookup line is not doubled ({len(n)})", renew)

    # On time: the real answer goes out at once, no holding line after it.
    fast = City("gp-fast")
    dup = City("gp-dup", hist=6.0)
    red_llm = City("gp-red-llm", llm=hang)
    red_hist = City("gp-red-hist", hist=6.0)
    agent = City("gp-agent", hist=6.0, sender=DESK)
    await asyncio.gather(
        run(fast, BACK, 4.0),
        run(dup, BACK, 7.0, twice=True),
        run(red_llm, LONGRED, 7.0),
        run(red_hist, CHEST, 9.0),
        run(agent, "need.request procedure=VIS-FIS priority=B request_id=xa-11111111", 7.0),
    )
    fast_lines = [x for x in fast.lines() if not x[1].startswith("Ho passato il numero")]  # #140: the desk hand-off confirmation is its own line
    check(len(fast_lines) == 1, f"gp fast: one line only ({len(fast_lines)})", fast)
    n = held(dup, [brain._R5_WAIT_NOTE])
    check(n == 1, f"gp same message twice, slow relay: one holding line ({n})", dup)
    for c in (red_llm, red_hist):
        lines = c.lines()
        check(bool(lines) and "112" in lines[0][1] and held(c, [brain._R5_WAIT_NOTE]) == 0,
              f"gp {c.name}: the emergency reply comes first and alone", c)
    check(bool(red_hist.lines()) and red_hist.lines()[0][0] < 1.0,
          f"gp slow relay + red flag: escalated at once ({first(red_hist)[0]:.1f} s)", red_hist)
    bad = [x for _, to, x in agent.out if any(x == loc(const(h), agent.rows[0]) for h in HOLDS if const(h))]
    check(not bad, f"gp agent sender: no holding line ({len(bad)})", agent)


async def consultorio():
    os.environ["CITY_ROLE"] = "family_counseling"
    await asyncio.gather(
        expect_hold(City("fc-checkin", hist=6.0), "checkin booking_id=bk-1", "_HOLD_CHECKIN"),
        expect_hold(City("fc-first", hist=6.0), "Buongiorno, vorrei prenotare una visita ginecologica al consultorio.",
                    "_HOLD_NOTE_CONS"),
        expect_hold(City("fc-confirm", hist=6.0), "confirm offer_id=of-1 consent_ref=cons-1", "_BOOKING_WAIT"),
    )
    # S25: a red flag with the relay history hanging: the 112 line at once, never a booking line.
    red = City("fc-red", hist=9.0)
    await run(red, "Sono incinta di 10 settimane, ho perdite di sangue abbondanti e un dolore fortissimo alla pancia", 10.0)
    lines = red.lines()
    booking = [x for _, x in lines if x in {loc(brain._HOLD_NOTE_CONS, red.rows[0]), loc(brain._BOOKING_WAIT, red.rows[0]),
                                           loc(brain._R5_WAIT_NOTE, red.rows[0])}]
    check(bool(lines) and lines[0][0] < 1.0 and "112" in lines[0][1] and not booking,
          f"family_counseling red flag, history hangs: 112 at once and no holding line ({first(red)[0]:.1f} s, {len(booking)})", red)
    # A red flag that comes with the booking code ("sono arrivata"): handle() decides, so the 112 line
    # comes and, where handle() checks in after it, the check-in still reaches the Ledger.
    arrived = City("fc-red-ref")
    calls = []
    real = brain.handle

    async def spy(*args, **kwargs):
        calls.append(1)
        return await real(*args, **kwargs)

    brain.handle = spy
    try:
        await run(arrived, "booking_id=bk-77 sono arrivata al consultorio, ho un forte dolore al petto", 6.0)
    finally:
        brain.handle = real
    lines = arrived.lines()
    check(bool(lines) and lines[0][0] < 3.0 and "112" in lines[0][1],
          f"family_counseling red flag + booking_id: 112 line by 3 s ({first(arrived)[0]:.1f} s)", arrived)
    check(bool(calls), "family_counseling red flag + booking_id: handle() runs (no early 112 path)", arrived)
    if hasattr(brain, "_cons_arrival_code"):
        ck = [x for _, to, x in arrived.out if to == LEDGER and x.startswith("checkin ")]
        check(bool(ck), f"family_counseling red flag + booking_id: still checked in ({len(ck)})", arrived)


async def main():
    await gp()
    await consultorio()
    print(f"\n{'OK test_hold_line' if not FAILS else 'FAILED ' + str(len(FAILS))}")
    for f in FAILS:
        print("  " + f)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
