"""R27 (#112): the second rebook path (the citizen tells us of the fault.cancel before the
facility or the city does) had no crash guard, unlike the cancel path from an agent. A crash in
_cancel_trigger there must be caught in that path (log care.cancel.crashed), not escape the desk
to the turn-level net (care.turn.crashed), and the citizen gets _CRASH_NOTE exactly once.
Fails on development 9da62a6c (the crash escapes _desk), passes with the guard.
Run: <city python> -I tests/test_112_rebook_crash_guard.py asst/_shared/agent.py"""
import asyncio, logging, os, sys
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import result, LIVE


async def booked(label, lang="it"):
    """World + citizen with one receipted booking (bk-22-1). Returns (w, cz, need, first) or None."""
    b = _rh.load(sys.argv[1], "booking_centre")
    n = [0]

    async def hospital(w, to, text, need):
        if not text.startswith("confirm "):
            return
        f = _rh.fields(text.split("\n")[0])
        n[0] += 1
        await asyncio.sleep(2.0)
        r = w.msg(to, w.me, f"booking.receipt booking_id=bk-22-{n[0]} ledger_ref=lr-22-{n[0]} "
                  f"slot_id={f.get('slot_id', '')} offer_id={f.get('offer_id', '')}\nPrenotazione registrata", need)
        w.rows.append(r)
        await w.run(r)

    w = _rh.World(b, "booking_centre", delay=8.0, slots=_rh.hosp_slots(), rx={"RX1": _rh.RX1},
                  bots={LIVE["hospital"]: hospital})
    cz, need = f"citizen:r22-{label}", f"N-r22-{label}"
    ask = _rh.DESK_ASK if lang == "it" else _rh.DESK_ASK.replace("lang=it", "lang=en").replace(
        "text=vorrei_prenotare_la_visita", "Hello, I have my prescription and I would like to book the visit please.")
    await w.send(cz, ask, need)
    await w.settle(30)
    offs = [o for o in _rh.offer_lines(w, cz) if o.get("slot_id")]
    if not offs:
        result("R27", "booking_centre", None, f"{label}: no first offers (cannot test the cancel)")
        return None
    first = offs[0]
    yes = "Yes, I confirm." if lang == "en" else "Va bene, confermo."
    await w.send(cz, f"confirm offer_id={first['offer_id']} consent_ref=cons-{need}\n{yes}", need)
    await w.settle(30)
    if not [tx for tx, _ in w.said_to(cz) if tx.startswith("booking.receipt")]:
        result("R27", "booking_centre", None, f"{label}: first booking never receipted (cannot test the cancel)")
        return None
    return w, cz, need, first


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.names = []

    def emit(self, record):
        self.names.append(record.getMessage())


async def main():
    got = await booked("r27-rebook")
    if not got:
        result("R27", "booking_centre", False, "no receipted first booking")
        return
    w, cz, need, first = got
    hits, grab = [], _Grab()
    w.brain._LOG.addHandler(grab)

    async def boom(*_a, **_k):
        hits.append(1)
        raise NameError("name '_r27_missing' is not defined")
    w.brain._cancel_trigger = boom  # the crash, on purpose, in the second rebook path
    m, at = await w.send(cz, "Ho ricevuto un fault.cancel per la mia visita, cosa succede adesso?", need)
    await w.settle(20)
    w.brain._LOG.removeHandler(grab)
    told = [tx for tx, _ in w.said_to(cz, since=at)]
    notes = [tx for tx in told if tx == w.brain._CRASH_NOTE]
    local = "care.cancel.crashed" in grab.names
    escaped = "care.turn.crashed" in grab.names
    if not hits:
        result("R27", "booking_centre", None, "the second rebook path was not reached (cannot test)")
        return
    ok = local and not escaped and len(notes) == 1 and len(told) == 1
    result("R27", "booking_centre", ok, f"guarded in path={local}, escaped to turn net={escaped}, "
           f"crash notes={len(notes)}, lines to citizen={len(told)}")

_rh.run(main())
