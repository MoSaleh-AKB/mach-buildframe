"""Booking 2.15.0 failed deploy conformance "chat.only: structured send" (21 Nov 12:49).

The "faults" stage marks a facility down and back; _resume_down_case (#60) then sends a background
status line to the waiting citizen, and a citizen body carries status=wip. Landing inside the
chat.only window it fails the deploy. Inside the boot warmup (conformance time) the resume must stay
silent; after the warmup it still sends its one line.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_chat_only_boot.py asst/_shared/agent.py
"""
import asyncio
import os
import sys
import time

os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import BC, result

CZ, NEED = "citizen:down-1", "N-down-1"


async def main():
    brain = _rh.load(sys.argv[1], "booking_centre")
    brain.role_of = lambda: "booking_centre"
    w = _rh.World(brain, "booking_centre", delay=0.0, slots=_rh.hosp_slots(), rx={"RX1": _rh.RX1})
    confirm = w.msg(CZ, BC, "confirm offer_id=of-X consent_ref=cons-N-down-1-booking", NEED)
    w.rows.append(confirm)
    view = _rh.View(w, confirm)

    brain._RESCUE_WARMUP = 300.0
    brain._BOOT = time.monotonic()  # fresh container: conformance time
    await brain._resume_down_case(view, confirm, "of-X")
    await w.settle(1)
    said = [tx for tx, _ in w.said_to(CZ)]
    result("CB1", "booking_centre", not said, f"inside the boot warmup: {len(said)} line(s) to the citizen (want 0)")

    brain._BOOT = time.monotonic() - 1000.0  # long after boot: live traffic
    await brain._resume_down_case(view, confirm, "of-X")
    await w.settle(1)
    said = [tx for tx, _ in w.said_to(CZ)]
    result("CB2", "booking_centre", len(said) == 1, f"after the warmup: {len(said)} line(s) to the citizen (want 1)")
    _rh.done()


asyncio.run(main())
