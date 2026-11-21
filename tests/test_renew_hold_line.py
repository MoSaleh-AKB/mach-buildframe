"""R17 (L8): the citizen writes the renewed prescription number in free text after the desk asked
for a renewal. The Ledger answers 8 s late, so the desk looks RX2 up and waits. The first turn of
the need already sent the holding line, so the same line again was eaten by the duplicate guard
and the citizen heard nothing for 16.1 s. Good desk: a visible line within 3.5 s (never the same
holding line twice), then offers on RX2.
Run: <city python> -I tests/test_renew_hold_line.py asst/_shared/agent.py"""
import os, sys
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import result

OLD = "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=expired valid_until=2026-11-20T08:00:00+00:00"
NEW = "prescription prescription_number=RX2 procedure=VIS-FIS priority=B state=issued"


async def main():
    b = _rh.load(sys.argv[1], "booking_centre")
    w = _rh.World(b, "booking_centre", delay=8.0, slots=_rh.hosp_slots(), rx={"RX1": OLD, "RX2": NEW})
    cz, need = "citizen:r17-hold", "N-r17-hold"
    await w.send(cz, _rh.DESK_ASK, need)
    await w.settle(30)
    _, at = await w.send(cz, "Il medico me l'ha rinnovata, ecco il nuovo numero: prescription_number=RX2", need)
    await w.settle(45)
    first = w.first_reply(cz, at)
    said = [tx for tx, _ in w.said_to(cz)]
    repeats = len(said) - len(set(said))
    offers = [o for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id")]
    ok = first is not None and first <= 3.5 and repeats == 0 and bool(offers)
    result("R17h", "booking_centre", ok,
           f"renewed RX2 free text (Ledger 8 s): first line {'none' if first is None else f'{first:.1f}s'}, "
           f"repeated lines={repeats}, offers after={len(offers)}")

_rh.run(main())
