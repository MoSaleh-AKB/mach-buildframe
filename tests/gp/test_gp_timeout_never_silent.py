"""gp: a slow relay never leaves a symptom message without a reply (issue #55).

Live journey 890904000257 (gp 6245655): 'Sento il cuore che batte. Scusi, devo andare.' got no
reply. The SDK relay client gives up after 10 s and raises the transport's ReadTimeout (not a
CapError); on 6245655 it escaped the turn before any line to the citizen, and the relay re-pushed
the same silent turn. Fixed on brain-gp by f7231db and 1ff23e4; this test keeps it fixed.

Run: <city python> -I tests/test_gp_timeout_never_silent.py asst/_shared/agent.py
"""

import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace

import httpx
from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "gp"

CZ = "citizen:t07/c1"
GP = sorted(getattr(brain, "_MATES", ["agent:t07/gp"]))[0]
N = [0]
PALP = "Sento il cuore che batte. Scusi, devo andare."


def msg(sender, to, text, need, mid=None):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=mid or f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    """Relay fake: the first ``ledger_fails`` Ledger sends and ``read_fails`` history reads time out."""

    address = ""

    def __init__(self, first, ledger_fails=0, read_fails=0):
        self.agent_id, self.rows, self.inbound, self.out = GP, [first], first, []
        self.ledger_fails, self.read_fails = ledger_fails, read_fails

    async def say(self, to, text, *, status=None):
        if to == LEDGER and self.ledger_fails > 0:
            self.ledger_fails -= 1
            raise httpx.ReadTimeout("relay")
        self.out.append((to, text))
        self.rows.append(msg(GP, to, text, self.inbound.need_id))
        if to == LEDGER and text.startswith("prescribe"):
            num = next(w for w in text.split() if w.startswith("prescription_number="))
            self.rows.append(msg(LEDGER, GP, f"prescription {num} procedure=VIS-CARD priority=B state=issued",
                                 self.inbound.need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        if self.read_fails > 0:
            self.read_fails -= 1
            raise httpx.ReadTimeout("relay history")
        return list(self.rows)

    async def find(self, query):
        return []


async def push(city, m):
    """One relay push; a transport timeout is the relay's to retry (it re-pushes the same message)."""

    view = SimpleNamespace(inbound=m, agent_id=GP, address="", find=city.find, history=city.history, say=city.say)
    try:
        await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)
    except Exception as exc:
        return type(exc).__name__
    return ""


def to_cz(city):
    return [t for to, t in city.out if to == CZ]


def prescribes(city):
    return [t for to, t in city.out if to == LEDGER and t.startswith("prescribe")]


async def main():
    oks = []

    def check(ok, name, city):
        print(("PASS " if ok else "FAIL ") + name)
        oks.append(ok)
        if not ok:
            for to, t in city.out:
                print("    ", to[:16], t[:150].replace("\n", " | "))

    # 1. the prescribe send times out: the citizen hears something in this very push
    m = msg(CZ, GP, PALP, "N-1", mid="cz-1")
    city = City(m, ledger_fails=1)
    await push(city, m)
    check(bool(to_cz(city)), "prescribe send times out: citizen answered in the same push", city)
    check(any("112" in t for t in to_cz(city)), "palpitations: the waiting line keeps the 112 advice", city)
    # 2. the history read times out: still answered
    m = msg(CZ, GP, PALP, "N-2", mid="cz-2")
    city = City(m, read_fails=3)
    await push(city, m)
    check(bool(to_cz(city)), "history read times out: citizen answered", city)
    # 3. three timeouts, three re-pushes, then the relay works: one prescription, one number, no flood
    for fails in (1, 3):
        m = msg(CZ, GP, PALP, f"N-3{fails}", mid=f"cz-3{fails}")
        city = City(m, ledger_fails=fails)
        for _ in range(fails + 1):
            await push(city, m)
        told = {w for t in to_cz(city) for w in t.split() if w.startswith("prescription_number=")}
        check(len(prescribes(city)) == 1 and len(told) == 1 and len([t for t in to_cz(city) if not t.startswith("Ho passato il numero")]) <= 2,
              f"{fails} timeouts then OK: 1 prescribe, 1 number told, at most 2 citizen lines (the #140 hand-off confirmation line aside)", city)
        check(not any("corretto" in t.lower() or "seconda ricetta" in t for t in to_cz(city)),
              f"{fails} timeouts then OK: no false 'corrected' / 'second prescription'", city)
    # 4. English citizen, prescribe send times out: an English line
    m = msg(CZ, GP, "I feel my heart pounding. Sorry, I have to go.", "N-4", mid="cz-4")
    city = City(m, ledger_fails=1)
    await push(city, m)
    check(bool(to_cz(city)) and not any("Sto verificando" in t for t in to_cz(city)),
          "English citizen, send times out: answered in English", city)
    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_gp_timeout_never_silent")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
