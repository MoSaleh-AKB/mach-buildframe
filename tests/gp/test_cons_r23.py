"""R23 (L8) consultorio, family_counseling brain asst/_gp/agent.py, no LLM calls.
a) after a booking the CITIZEN says first it was cancelled ("la mia prenotazione è stata annullata.
   COME CANCELLATA??"). Bad: "Ho preso nota che non può venire" / "disdetta" / the dead receipt again.
   Good: sorry + fresh offers (or a new hold) without the cancelled slot.
b) two identical fault.cancel of the same booking_id 2 s apart: ONE offer message, no dead receipt.
Fails on origin/development 1d1e3d1c (cit + dup), passes with the fix.
Run: <city python> -I tests/gp/test_cons_r23.py asst/_gp/agent.py"""
import asyncio, os, sys
os.environ.setdefault("REGRESS_ROLES", "family_counseling")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _rh_world as _rh
from _rh_world import result
BAD_CONS = ("non può venire", "non puo venire", "disdetta")


async def cons_booked(label):
    b = _rh.load(sys.argv[1], "family_counseling")
    w = _rh.World(b, "family_counseling", delay=8.0, slots=_rh.cons_slots())
    cz, need = f"citizen:r23c-{label}", f"N-r23c-{label}"
    await w.send(cz, _rh.CONS_ASK, need)
    await w.settle(30)
    offs = [o for o in _rh.offer_lines(w, cz) if o.get("slot_id")]
    if not offs:
        result("R23", "family_counseling", False, f"{label}: no first offers (cannot test the cancel)")
        return None
    await w.send(cz, f"confirm offer_id={offs[0]['offer_id']} consent_ref=cons-{need}\nVa bene, confermo.", need)
    await w.settle(45)
    com = w.ops("commit")
    if not com:
        result("R23", "family_counseling", False, f"{label}: no commit in the first booking (cannot test the cancel)")
        return None
    return w, cz, need, offs[0]["slot_id"], _rh.fields(com[-1]).get("hold_id", "h-1")


async def cons_citizen_first():
    got = await cons_booked("cit")
    if not got:
        return
    w, cz, need, sid, bid = got
    w.taken.add(sid)  # the slot is gone (the city cancelled it)
    holds_before = len(w.ops("hold"))
    m, at = await w.send(cz, "Mi è arrivato un messaggio: la mia prenotazione è stata annullata. COME CANCELLATA?? "
                             "E adesso cosa faccio?", need)
    await w.settle(45)
    told = [tx for tx, _ in w.said_to(cz, since=at)]
    bad = [tx.split("\n")[-1][:70] for tx in told if any(k in tx.lower() for k in BAD_CONS)]
    dead = [tx for tx in told if tx.startswith("booking.receipt") and bid in tx]
    new = [o["slot_id"] for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id") and o["slot_id"] != sid]
    new_hold = [t for t in w.ops("hold")[holds_before:] if f"slot_id={sid}" not in t]
    ok = bool(told) and not bad and not dead and (bool(new) or bool(new_hold))
    result("R23", "family_counseling", ok, f"cit: citizen says the booking was cancelled: lines={len(told)}, "
           f"read as citizen cancel={bad or 'no'}, dead receipt={bool(dead)}, new offers={new or 'none'}, new hold={len(new_hold)}")


async def cons_dupcancel():
    got = await cons_booked("dup")
    if not got:
        return
    w, cz, need, sid, bid = got
    w.taken.discard(sid)
    m, at = await w.send("city:fault", f"fault.cancel booking_id={bid}", need, wait=False)
    await asyncio.sleep(2.0)
    await w.send("city:fault", f"fault.cancel booking_id={bid}", need, wait=False)
    await w.settle(50)
    told = w.said_to(cz, since=at)
    rounds = [tx for tx, _ in told if any(ln.startswith("offer ") for ln in tx.splitlines())]
    dead = [tx for tx, _ in told if tx.startswith("booking.receipt") and bid in tx]
    ok = bool(told) and len(rounds) <= 1 and not dead
    result("R23", "family_counseling", ok, f"dup: two fault.cancel of {bid} 2 s apart: offer messages={len(rounds)}, "
           f"receipt of the cancelled booking={bool(dead)}, citizen lines={len(told)}")


async def main():
    await cons_citizen_first()
    await cons_dupcancel()

_rh.run(main())
