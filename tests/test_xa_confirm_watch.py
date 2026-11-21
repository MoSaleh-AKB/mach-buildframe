"""FINAL-AUDIT: the citizen confirmed a partner ASST offer and was promised the other options if the
partner stays silent. When the watch cannot act safely
1. both history reads fail (transport ReadError twice), or
2. the need lock stays busy longer than the wait,
it never offers alternatives or touches a facility/Ledger (double-booking risk), but the citizen
gets exactly ONE honest holding line ("has not answered yet, I will write"), never silence.
Normal path (readable, lock free, partner silent): alternatives as before.
Run: <city python> -I tests/test_xa_confirm_watch.py asst/_shared/agent.py
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
brain._PULL_PAUSE = 0.05

BC, CZ, PARTNER = "agent:y6hxsd766sys", "citizen:t/xw", "agent:k3m9partner1"
FAILS: list[str] = []
N = [0]


class ReadError(OSError):
    """Stands for httpx.ReadError (a dropped connection is an OSError for _read_failed)."""


def check(ok, what, out):
    print(("PASS " if ok else "FAIL ") + what + ("" if ok else f" -> {out}"))
    if not ok:
        FAILS.append(what)


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, fail_reads):
        self.out: list[tuple[str, str]] = []
        self.fail_reads = fail_reads
        self.inbound = None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        if self.fail_reads > 0:
            self.fail_reads -= 1
            raise ReadError("connection dropped")
        m = self.inbound
        ours = Message(type="chat", sender=BC, recipient=PARTNER, msg_id=f"{m.msg_id}-fw", need_id=m.need_id,
                       body={"text": "confirm offer_id=of-PX1 slot_id=PX1 consent_ref=cons-x"})
        return [m, ours]

    async def find(self, query):
        return []


def confirm(need):
    N[0] += 1
    return Message(type="chat", sender=CZ, recipient=BC, msg_id=f"w{N[0]}", need_id=need,
                   body={"text": "confirm offer_id=of-PX1 consent_ref=cons-x"})


OFFER = {"offer_id": "of-PX1", "slot_id": "PX1", "facility": PARTNER}


def honest(fake):
    return [t for to, t in fake.out if to == CZ and ("non ha ancora risposto" in t or "has not answered yet" in t)]


def only_honest(fake):
    """Exactly one line, to the citizen, the honest one: no offers, no slot ids, nothing elsewhere."""
    return (
        len(fake.out) == 1 and fake.out[0][0] == CZ and len(honest(fake)) == 1
        and "slot_id" not in fake.out[0][1] and "offer" not in fake.out[0][1].lower()
        and fake.out[0][1].endswith(brain._HOLD_TAG)
    )


def silent_line(fake):
    return any(to == CZ and ("non ha risposto" in t or "did not answer" in t) for to, t in fake.out)


async def main():
    # 1. two failed reads
    fake = Fake(fail_reads=9)
    m = confirm("n-xw1")
    fake.inbound = m
    await asyncio.wait_for(brain._xa_confirm_watch(fake, m, OFFER, 0.0), 20)
    check(only_honest(fake), "two failed history reads: exactly one honest line, no alternatives, nothing to facility", fake.out)

    # 2. the need lock is held by a stuck turn longer than the wait
    brain._CITIZEN_TURN_SAVE = brain._CITIZEN_TURN
    brain._CITIZEN_TURN = 0.3
    fake = Fake(fail_reads=0)
    m = confirm("n-xw2")
    fake.inbound = m
    lock = brain._need_lock(m.need_id)
    await lock.acquire()
    try:
        await asyncio.wait_for(brain._xa_confirm_watch(fake, m, OFFER, 0.0), 20)
    finally:
        lock.release()
        brain._CITIZEN_TURN = brain._CITIZEN_TURN_SAVE
    check(only_honest(fake), "need lock busy: exactly one honest line, no alternatives, nothing to facility", fake.out)

    # 3. the line is once per confirm: a second watch run for the same confirm adds nothing
    fake.out.clear()
    m3 = fake.inbound
    brain._CITIZEN_TURN = 0.3
    lock = brain._need_lock(m3.need_id)
    await lock.acquire()
    try:
        await asyncio.wait_for(brain._xa_confirm_watch(fake, m3, OFFER, 0.0), 20)
    finally:
        lock.release()
        brain._CITIZEN_TURN = brain._CITIZEN_TURN_SAVE
    check(not fake.out, "repeat watch for the same confirm: no second honest line", fake.out)

    # 4. normal path unchanged: readable, lock free, partner silent -> the silent line + alternatives path
    fake = Fake(fail_reads=0)
    m = confirm("n-xw4")
    fake.inbound = m
    await asyncio.wait_for(brain._xa_confirm_watch(fake, m, OFFER, 0.0), 20)
    check(silent_line(fake) and not honest(fake), "normal path: the silent line, no honest holding line", fake.out)
    print("FAILED: " + "; ".join(FAILS) if FAILS else "OK test_xa_confirm_watch")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
