"""#92: an expired prescription on a slow Ledger (8 s). The desk asks for the renewal ONCE: never the
`data.request ... prescription_renewal` line and "Per prenotare mi serve una ricetta nuova" in the
same second (two renewal lines for one Ledger answer).
Run: <city python> -I tests/test_renew_once_slow.py asst/_shared/agent.py"""
import os, sys
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import result

OLD = "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=expired valid_until=2026-11-20T08:00:00+00:00"


async def main():
    b = _rh.load(sys.argv[1], "booking_centre")
    w = _rh.World(b, "booking_centre", delay=8.0, slots=_rh.hosp_slots(), rx={"RX1": OLD})
    cz, need = "citizen:r92-once", "N-r92-once"
    await w.send(cz, _rh.DESK_ASK, need)
    await w.settle(40)
    said = w.said_to(cz)
    renew = [(tx, at) for tx, at in said if tx in (b._RENEW, b._RENEW_AGAIN, b._EN_TEXT.get(b._RENEW_AGAIN))]
    close = [(a, c) for (_, a), (_, c) in zip(renew, renew[1:]) if c - a < 5.0]
    ok = len(renew) >= 1 and not close
    result("R92", "booking_centre", ok,
           f"expired RX1 (Ledger 8 s): renewal lines={len(renew)} at "
           f"{[round(at, 1) for _, at in renew]}, pairs under 5 s={len(close)}")

_rh.run(main())
