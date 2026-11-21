"""Live 21 Nov: the brain-gp hand-off carried no citizen= and no lang=, so the desk could not act on
it (_handoff_proxy needs citizen=) and the citizen waited 13-15 min until they wrote to the desk.

1. The brain-gp hand-off line names the citizen (and lang= when not Italian); a hand-off for an
   agent sender is never taken as a citizen request by the desk.
2. The desk (shared brain) reads that line as the citizen's request and sends the offers to the
   citizen under the GP case, seconds after the prescription.
3. The city refuses the desk's post under the GP case: no crash, and the citizen who then writes
   to the desk gets the offers as today.

Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/gp/test_gp_handoff_citizen.py \
     [asst/_gp/agent.py] [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")

from city_sdk.city import LEDGER, CapError, Message

HERE = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if a.endswith(".py")]
GP_PATH = args[0] if args else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
DESK_PATH = args[1] if len(args) > 1 else os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py")

GP = "agent:jmqwr12vy2j6"  # live ids: both in the default team list of each brain
DESK = "agent:y6hxsd766sys"
CZ = "citizen:t-hand"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAILS: list[str] = []


def load(name, path, role):
    os.environ["CITY_ROLE"] = role
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.role_of = lambda: role
    return mod


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"h{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


def check(ok, label, out=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for row in out or []:
            print("      ", repr(row)[:150])


class GpCity:
    def __init__(self):
        self.agent_id, self.address, self.inbound = GP, "", None
        self.out: list[tuple[str, str]] = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "ok"

    async def find(self, query):
        return [SimpleNamespace(agent_id=DESK, role="booking_centre")]

    async def history(self, peer=None, *, need_id=None):
        return []


async def gp_line(gp, sender, lang):
    city = GpCity()
    m = msg(sender, GP, "Ho mal di schiena da una settimana.", "N-GP1")
    await gp._hand_to_desk(city, [m], m, "RX-77", "B", lang)
    return next((t for to, t in city.out if to == DESK), "")


def agenda_now():
    return (NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()


def slots_text():
    base = (NOW + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[base.weekday()]
    rows = [
        f"slot_id=S{i} doctor_id=asst-centro-h{i} at={(base + timedelta(hours=2 * i)).isoformat()} day={day} "
        f"facility=agent:r8yprrs0e8be procedure=VIS-FIS free=1"
        for i in (1, 2, 3)
    ]
    return "slots page=1/1\n" + "\n".join(rows)


class DeskCity:
    """The desk's city: a quick Ledger; refuse=True refuses posts to the citizen under the GP case."""

    def __init__(self, refuse_need=""):
        self.agent_id, self.address, self.inbound = DESK, "", None
        self.rows: list[Message] = []
        self.out: list[tuple[float, str, str, str]] = []
        self.refuse_need = refuse_need
        self.refused = 0
        self.t0 = time.monotonic()
        self.tasks: list[asyncio.Task] = []

    async def say(self, to, text, *, status=None, need=None):
        need = need or (self.inbound.need_id if self.inbound else "")
        if to.startswith("citizen:") and need == self.refuse_need:
            self.refused += 1
            raise CapError("FORBIDDEN", "not a party of this need")
        if to == LEDGER:
            self.tasks.append(asyncio.create_task(self._ledger(text, need)))
        else:
            self.out.append((time.monotonic() - self.t0, to, need, text))
        self.rows.append(msg(DESK, to, text, need))
        return "ok"

    async def _ledger(self, text, need):
        await asyncio.sleep(0.3)
        head = text.split()[0]
        if head == "prescription":
            number = text.split("prescription_number=")[-1].split()[0]
            body = f"prescription prescription_number={number} procedure=VIS-FIS priority=B state=issued"
        elif head == "agenda":
            body = f"agenda agenda_now={agenda_now()}"
        elif head == "world":
            body = slots_text()
        else:
            return
        self.rows.append(msg(LEDGER, DESK, body, need))

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


async def desk_turn(desk, city, row, limit=30.0):
    city.rows.append(row)
    city.inbound = row
    try:
        await asyncio.wait_for(desk.main(SimpleNamespace(summary="x", is_emergency=False), View(city, row)), limit)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ not in ("OutOfScope",):
            check(False, f"desk turn raised {type(exc).__name__}: {exc}")


def offers_to_citizen(city):
    return [x for x in city.out if x[1] == CZ and "offer_id=of-" in x[3]]


def reset(desk):
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_ONCE", "_RESCUED", "_GATES", "_LOCKS",
                 "_RESUMED", "_RESUMING", "_GAVE_UP", "_HELD", "_STUCK"):
        getattr(desk, name, {}).clear()
    if hasattr(desk, "_RESCUE_LAST"):
        desk._RESCUE_LAST.clear()
        desk._RESCUE_LAST.append(time.monotonic())
    sent = getattr(desk, "_SENT", None)
    if hasattr(sent, "clear"):
        sent.clear()


async def main():
    gp = load("brain_gp", GP_PATH, "gp")
    it_line = await gp_line(gp, CZ, "it")
    en_line = await gp_line(gp, CZ, "en")
    agent_line = await gp_line(gp, "agent:dqfw9nh15dn4", "en")
    head_it, head_en = it_line.splitlines()[0] if it_line else "", en_line.splitlines()[0] if en_line else ""
    check(f"citizen={CZ}" in head_it and "lang=" not in head_it, f"gp hand-off names the citizen ({head_it!r})")
    check(f"citizen={CZ}" in head_en and "lang=en" in head_en, f"gp hand-off carries lang=en ({head_en!r})")
    check("procedure=" not in it_line, "no procedure= token in the hand-off")

    desk = load("brain_desk", DESK_PATH, "booking_centre")
    desk._ADVANCE_BUDGET = 2.0
    hand = msg(GP, DESK, en_line, "N-GP2")
    proxy = desk._handoff_proxy(hand, DESK)
    check(proxy is not None and proxy.sender == CZ and "lang=en" in (proxy.text or ""),
          "the desk reads the gp hand-off as the citizen's request")
    check(not agent_line or desk._handoff_proxy(msg(GP, DESK, agent_line, "N-GP9"), DESK) is None,
          "a hand-off for an agent sender is never taken as a citizen request")

    # 2. the desk offers under the GP case, seconds after the prescription
    reset(desk)
    city = DeskCity()
    await desk_turn(desk, city, msg(GP, DESK, en_line, "N-GP2"))
    t_end = time.monotonic() + 8
    while not offers_to_citizen(city) and time.monotonic() < t_end:
        await asyncio.sleep(0.25)
    got = offers_to_citizen(city)
    check(len(got) == 1 and got[0][2] == "N-GP2" and got[0][0] < 10,
          f"desk offers to the citizen under the GP case ({[round(x[0], 1) for x in got]})", city.out)
    check(not [x for x in city.out if x[1] == GP and "offer_id=" in x[3]], "no offers go back to the GP", city.out)

    # 3. the city refuses that post: no crash, no loop, and the citizen's own message works as today
    reset(desk)
    city = DeskCity(refuse_need="N-GP3")
    await desk_turn(desk, city, msg(GP, DESK, it_line, "N-GP3"))
    await asyncio.sleep(1.0)
    check(city.refused >= 1, f"city refused the post under the GP case ({city.refused} tries)")
    tries = city.refused
    await asyncio.sleep(3.0)
    check(city.refused - tries <= 2, f"no retry loop after the refusal ({city.refused - tries} more)")
    await desk_turn(desk, city, msg(CZ, DESK, "Buongiorno, ho la ricetta prescription_number=RX-77", "N-DESK3"))
    t_end = time.monotonic() + 8
    while not offers_to_citizen(city) and time.monotonic() < t_end:
        await asyncio.sleep(0.25)
    got = offers_to_citizen(city)
    check(len(got) == 1 and got[0][2] == "N-DESK3", "after a refusal the citizen's own message gets the offers", city.out)
    for c in (city,):
        for task in c.tasks:
            task.cancel()
    for task in list(getattr(desk, "_RESUMING", {}).values()):
        task.cancel()
    print("FAILED: " + "; ".join(FAILS) if FAILS else "OK test_gp_handoff_citizen")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
