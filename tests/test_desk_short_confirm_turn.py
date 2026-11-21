"""#139: a desk citizen confirm turn must return fast while the Ledger is slow.

The platform serves one turn at a time per agent. The desk confirm used to wait the Ledger
prescription re-check (P1 rule in force) for up to 20 s inside the turn, so citizens queued. Now the
turn returns within _DESK_CONFIRM_WAIT with ONE holding line; the same confirm step finishes in a
background task: the confirm goes to the facility exactly once, and the citizen is never silent.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_desk_short_confirm_turn.py [brain.py]
Exit 1 on any FAIL. On origin/development the turn takes about LEDGER_DELAY s and this test FAILs.
"""
import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain139", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False
brain._RESCUE_WARMUP = 1e9

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c139"
NEED = "N-139-a"
LEDGER_DELAY = 6.0  # the live Ledger answers in 8-20 s
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
N = [0]


def msg(sender, to, text, ago=0.0):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"s139-{N[0]}", need_id=NEED,
                   conversation_id="cv-139", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


class SlowLedgerCity:
    agent_id = BC
    address = ""

    def __init__(self, rows, inbound):
        self.rows, self.out, self.inbound = list(rows), [], inbound
        self.timers = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text))
        if to == LEDGER and text.startswith("prescription"):
            async def later():
                await asyncio.sleep(LEDGER_DELAY)
                self.rows.append(msg(LEDGER, BC, "prescription prescription_number=RX139 "
                                     "procedure=VIS-FIS priority=B state=issued"))
            self.timers.append(asyncio.get_running_loop().create_task(later()))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def rows():
    at = (BASE + timedelta(hours=8)).isoformat()
    return [
        msg(CZ, BC, "vorrei una visita prescription_number=RX139", 90),
        msg(LEDGER, BC, "prescription prescription_number=RX139 procedure=VIS-FIS priority=B state=issued", 80),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", 80),
        msg(LEDGER, BC, "rules\nrule_id=P1 scope=prescription max_age_days=30", 80),
        msg(BC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=D1 facility={HO} procedure=VIS-FIS "
            f"agenda_date={at} expiry={at} prescription_number=RX139", 30),
    ]


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE", "_HELD",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        getattr(brain, name, {}).clear()
    brain._RESCUE_LAST[:] = [time.monotonic()]


async def run(resend=False):
    reset()
    inbound = msg(CZ, BC, "confirm offer_id=of-S1 consent_ref=cons-N-139-a-booking")
    city = SlowLedgerCity(rows() + [inbound], inbound)
    t0 = time.monotonic()
    await brain.main(SimpleNamespace(summary="x", is_emergency=False), city)
    took = time.monotonic() - t0
    in_turn = list(city.out)
    if resend:
        # The citizen sends the same confirm again while the background work still waits the Ledger.
        again = msg(CZ, BC, "confirm offer_id=of-S1 consent_ref=cons-N-139-a-booking")
        city.rows.append(again)
        city.inbound = again
        t1 = time.monotonic()
        await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), city), 6.0)
        city.resend_took = time.monotonic() - t1
    # The Ledger answers at LEDGER_DELAY: the background task must finish the same confirm.
    await asyncio.sleep(LEDGER_DELAY + 1.5)
    return city, took, in_turn


FAILS = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {info}" if not ok else ""))
    if not ok:
        FAILS.append(name)


def main():
    city, took, in_turn = asyncio.run(run())
    city2, _, _ = asyncio.run(run(resend=True))
    sent = lambda: [t for to, t in city.out if to == HO and t.startswith("confirm")]
    told = lambda out: [t for to, t in out if to == CZ]
    check("confirm turn returns under 3 s (slow Ledger)", took < 3.0, f"took {took:.1f} s")
    check("the citizen is told in the turn (never silent)", len(told(in_turn)) == 1, str(told(in_turn)))
    check("the confirm reaches the facility exactly once", len(sent()) == 1, str(city.out))
    check("no second holding line from the background", len(told(city.out)) <= 1, str(told(city.out)))
    fw2 = [t for to, t in city2.out if to == HO and t.startswith("confirm")]
    check("resend while the background runs: exactly one forward", len(fw2) == 1, str(city2.out))
    check("resend returns at once (no queued turn)", city2.resend_took < 1.5, f"{city2.resend_took:.1f} s")
    check("resend gets at most one extra line, no receipt twice", len(told(city2.out)) <= 2
          and sum(1 for t in told(city2.out) if "receipt" in t.lower() or "ricevuta" in t.lower()) <= 1,
          str(told(city2.out)))
    for t in city.timers + city2.timers:
        t.cancel()
    print("ALL OK" if not FAILS else "SOME FAILED")
    sys.exit(1 if FAILS else 0)


main()
