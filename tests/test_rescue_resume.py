"""Live 21 Nov (41 open journeys): a case frozen before the city pause is resumed by a background task.

The rescue sweep has a 3 s budget per pick, a Ledger agenda/world round trip takes 8-20 s, so the
advance was cancelled before the offers and its fallback was the holding line the citizen already
had (dropped by the spam guard): the citizen heard nothing, every sweep.

Case: our last desk row is "Sto verificando le disponibilita..." sent 4 h ago, the citizen wrote 3 h 50
ago, the Ledger answers agenda/world after 10 s. One sweep must:
  (a) return to the triggering turn within 3 s;
  (b) send one new "Bentornato" line at once (the spam guard has never seen it);
  (c) send offers within ~40 s, from a FRESH world (never the pre-freeze slot S9);
  (d) start only ONE resume task per need even with 3 sweeps in a row.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_rescue_resume.py asst/_shared/agent.py
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ["CITY_RESCUE_WARMUP"] = "0"
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import BC, LEDGER, Message, result

CZ, NEED = "citizen:resume-1", "N-resume-1"
HOLD = "Sto verificando le disponibilità nel registro: le scrivo appena ho le date."


def row(sender, to, text, ago):
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"old-{abs(hash((text, ago))) % 10**8}",
                   need_id=NEED, body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


async def main():
    brain = _rh.load(sys.argv[1], "booking_centre")
    w = _rh.World(brain, "booking_centre", delay=10.0, slots=_rh.hosp_slots(), rx={"RX1": _rh.RX1})
    ask = row(CZ, BC, _rh.DESK_ASK, 13800)  # citizen wrote 3 h 50 ago
    w.rows += [
        ask,
        row(BC, CZ, HOLD, 14400),  # our last line to the citizen: 4 h ago
        row(LEDGER, BC, _rh.RX1, 14300),
        row(LEDGER, BC, "agenda agenda_now=2026-11-23T04:00:00+00:00", 14300),
        row(LEDGER, BC, "slots page=1/1\n" + _rh.slot("S9", "2026-11-23T05:00:00+00:00", "mon", _rh.HO), 14300),
    ]
    brain.role_of = lambda: "booking_centre"
    other = w.msg("citizen:other", BC, "ciao", "N-other")
    view = _rh.View(w, other)

    t0 = time.perf_counter()
    brain._RESCUE_LAST[:] = []
    await asyncio.wait_for(brain._rescue(view, "booking_centre", other), 20)
    took = time.perf_counter() - t0
    result("RR1", "booking_centre", took < 3.0, f"triggering turn returned in {took:.2f}s (limit 3 s)")

    await asyncio.sleep(1.0)
    hello = [tx for tx, _ in w.said_to(CZ) if tx.startswith("Bentornato")]
    result("RR2", "booking_centre", len(hello) == 1, f"{len(hello)} Bentornato line(s) within 1 s of the sweep")

    for _ in range(2):  # two more sweeps while the resume runs
        await asyncio.sleep(0.5)
        brain._RESCUE_LAST[:] = []
        await asyncio.wait_for(brain._rescue(view, "booking_centre", other), 20)
    await w.settle(45)
    for _ in range(100):
        if not getattr(brain, "_RESUME_FLIGHT", {}):
            break
        await asyncio.sleep(0.5)
    await w.settle(5)

    hello = [tx for tx, _ in w.said_to(CZ) if tx.startswith("Bentornato")]
    offers = [o for o in _rh.offer_lines(w, CZ) if o.get("slot_id")]
    first = min((at for tx, at in w.said_to(CZ) if "offer_id=" in tx), default=None)
    sweep_at = t0 - w.t0
    late = None if first is None else first - sweep_at
    result("RR3", "booking_centre", len(offers) >= 1 and late is not None and late <= 40.0,
           f"{len(offers)} offer(s), first {late if late is None else round(late, 1)} s after the sweep")
    result("RR4", "booking_centre", all(o.get("slot_id") != "S9" for o in offers),
           "no offer from the pre-freeze world (S9)")
    result("RR5", "booking_centre", len(hello) == 1 and len(w.ops("world")) == 1 and len(w.ops("agenda")) == 1,
           f"3 sweeps: {len(hello)} Bentornato, {len(w.ops('world'))} world read(s), {len(w.ops('agenda'))} agenda read(s)")
    result("RR6", "booking_centre", not w.errors, f"errors={w.errors[:2]}")
    _rh.done()


asyncio.run(main())
