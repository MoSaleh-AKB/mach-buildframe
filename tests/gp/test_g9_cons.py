"""L8 consultorio (family_counseling, brain asst/_gp/agent.py), no LLM calls.

a) A sender that only looks like the city (City:ledger, City:fault, "city:fault ") cancels
   nothing: no line to the citizen, no cancel at the Ledger, no new hold. city:fault still rebooks.
b) An agent that is no party to the booking sends "cancel": no rebook for the citizen (the old
   booking would stay at the Ledger as a ghost). Our desk that booked at the consultorio cancels:
   the slot is freed at the Ledger, the desk is acked, the citizen hears nothing.
c) The citizen turn is cancelled (deploy drain) while the Ledger is slow: the citizen gets the
   short wait line, and the cancel is still raised.
d) Our desk confirms at the consultorio and the Ledger is slow: the desk hears a hold line inside
   its turn, never a cancel.

Run: <city python> -I tests/gp/test_g9_cons.py asst/_gp/agent.py
"""
import asyncio
import os
import sys

os.environ.setdefault("REGRESS_ROLES", "family_counseling")
os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _rh_world as _rh  # noqa: E402
from _rh_world import result  # noqa: E402

BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "..", "asst", "_gp", "agent.py")
BC = _rh.BC
DESK_ASK = "need.request service_type=family_counseling procedure=CONS-GIN priority=D request_id=rq-1"


def world(delay=0.2):
    b = _rh.load(BRAIN, "family_counseling")
    return _rh.World(b, "family_counseling", delay=delay, slots=_rh.cons_slots())


async def citizen_booked(label):
    w = world()
    cz, need = f"citizen:g9-{label}", f"N-g9-{label}"
    await w.send(cz, _rh.CONS_ASK, need)
    await w.settle(20)
    offs = [o for o in _rh.offer_lines(w, cz) if o.get("slot_id")]
    if not offs:
        return None
    await w.send(cz, f"confirm offer_id={offs[0]['offer_id']} consent_ref=cons-{need}\nVa bene, confermo.", need)
    await w.settle(20)
    com = w.ops("commit")
    if not com:
        return None
    return w, cz, need, _rh.fields(com[-1]).get("hold_id", "h-1")


async def lookalike():
    for sender, text in (("City:ledger", "cancel booking_id={b} reason=fault"),
                         ("City:fault", "fault.cancel booking_id={b}"),
                         ("city:fault ", "fault.cancel booking_id={b}"),
                         ("city:ledger​", "cancelled booking_id={b}")):
        got = await citizen_booked("lk")
        if not got:
            result("G9a", "family_counseling", False, f"{sender!r}: no first booking (cannot test)")
            continue
        w, cz, need, bid = got
        holds, cancels = len(w.ops("hold")), len(w.ops("cancel"))
        _, at = await w.send(sender, text.format(b=bid), need)
        await w.settle(20)
        told = [tx[:60] for tx, _ in w.said_to(cz, since=at)]
        ok = not told and len(w.ops("hold")) == holds and len(w.ops("cancel")) == cancels
        result("G9a", "family_counseling", ok, f"look-alike {sender!r} cancel: citizen lines={told or 'none'}, "
               f"new holds={len(w.ops('hold')) - holds}, Ledger cancels={len(w.ops('cancel')) - cancels}")
    got = await citizen_booked("real")
    if got:
        w, cz, need, bid = got
        w.taken.discard(w.holds.get(bid, ""))
        _, at = await w.send("city:fault", f"fault.cancel booking_id={bid}", need)
        await w.settle(20)
        new = [o for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id")]
        result("G9a", "family_counseling", bool(new), f"real city:fault fault.cancel still rebooks: new offers={len(new)}")


async def stranger_cancel():
    for sender in ("agent:zz9stranger", "agent:t05/booking_centre"):
        got = await citizen_booked("st")
        if not got:
            result("G9b", "family_counseling", False, f"{sender}: no first booking (cannot test)")
            continue
        w, cz, need, bid = got
        holds = len(w.ops("hold"))
        _, at = await w.send(sender, f"cancel booking_id={bid} reason=facility_cancelled", need)
        await w.settle(20)
        told = [tx[:60] for tx, _ in w.said_to(cz, since=at)]
        ok = not told and len(w.ops("hold")) == holds
        result("G9b", "family_counseling", ok, f"{sender} (no party) cancels the citizen's booking: citizen lines="
               f"{told or 'none'}, new holds={len(w.ops('hold')) - holds}")


async def desk_booking(slow=0.0):
    w = world()
    cz, need = "citizen:g9-desk", "N-g9-desk"
    await w.send(BC, DESK_ASK, need)
    await w.settle(20)
    offs = [o for o in _rh.offer_lines(w, BC) if o.get("slot_id")]
    if not offs:
        return w, need, cz, None, 0.0
    o = offs[0]
    if slow:
        w.delay = slow  # the Ledger turns slow before the confirm
    _, at = await w.send(BC, f"confirm offer_id={o['offer_id']} slot_id={o['slot_id']} citizen={cz} "
                             f"consent_ref=cons-{need} facility={_rh.FC}", need)
    return w, need, cz, o, at


async def desk_cancel():
    w, need, cz, o, _ = await desk_booking()
    await w.settle(20)
    rec = [tx for tx, _ in w.said_to(BC) if tx.startswith("booking.receipt")]
    if not o or not rec:
        result("G9b", "family_counseling", False, "our desk: no booking at the consultorio (cannot test)")
        return
    bid = _rh.fields(rec[-1].splitlines()[0]).get("booking_id", "")
    holds = len(w.ops("hold"))
    _, at = await w.send(BC, f"cancel booking_id={bid} reason=citizen_request", need)
    await w.settle(20)
    freed = [t for t in w.ops("cancel") if bid in t]
    acked = [tx for tx, _ in w.said_to(BC, since=at) if bid in tx]
    told = [tx[:60] for tx, _ in w.said_to(cz, since=at)]
    new_offers = [x for x in _rh.offer_lines(w, BC, since=at)]
    ok = bool(freed) and bool(acked) and not told and not new_offers and len(w.ops("hold")) == holds
    result("G9b", "family_counseling", ok, f"our desk cancels its consultorio booking: Ledger cancel={bool(freed)}, "
           f"desk acked={bool(acked)}, citizen lines={told or 'none'}, rebook offers={len(new_offers)}")


async def drain_cancel():
    w = world(delay=8.0)
    cz, need = "citizen:g9-drain", "N-g9-drain"
    m = w.msg(cz, w.me, _rh.CONS_ASK, need)
    w.rows.append(m)
    task = asyncio.get_running_loop().create_task(
        w.brain.main(_rh.Need(summary=m.text, message=m), _rh.View(w, m)))
    await asyncio.sleep(1.0)
    task.cancel()
    raised = False
    try:
        await task
    except asyncio.CancelledError:
        raised = True
    except Exception:
        pass
    told = [tx[:60] for tx, _ in w.said_to(cz)]
    for t in list(w.tasks):
        t.cancel()
    result("ROW5", "family_counseling", raised and bool(told),
           f"citizen turn cancelled at 1 s (deploy drain): cancel re-raised={raised}, citizen lines={told or 'none'}")


async def desk_slow():
    w, need, cz, o, at = await desk_booking(slow=8.0)
    if not o:
        result("ROW6", "family_counseling", False, "our desk: no offers (cannot test)")
        return
    fast = [tx for tx, t in w.said_to(BC, since=at) if t - at <= 3.0]
    bad = [tx[:60] for tx in fast if tx.split()[:1] == ["cancel"]]
    result("ROW6", "family_counseling", bool(fast) and not bad,
           f"our desk confirms, Ledger 8 s: lines to the desk within 3 s={len(fast)}, cancels={bad or 'none'}")
    for t in list(w.tasks):
        t.cancel()


async def main():
    await lookalike()
    await stranger_cancel()
    await desk_cancel()
    await drain_cancel()
    await desk_slow()

_rh.run(main())
