"""R21 case c (L8): the CITIZEN says first that the booking was cancelled ("la mia prenotazione e
stata annullata"). Good desk: never re-shows the dead receipt; tells the citizen, offers new slots
without the cancelled one inside 40 s, and forwards the citizen's new confirm (rebook completes).
Also runs the city and hospital cancel cases (same path). Fails on train-0446: dead receipt re-shown.
Run: <city python> -I tests/test_citizen_told_cancel.py asst/_shared/agent.py"""
import asyncio, os, sys
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import result, LIVE, LEDGER


async def desk(label):
    b = _rh.load(sys.argv[1], "booking_centre")
    n = [0]

    async def hospital(w, to, text, need):
        if not text.startswith("confirm "):
            return
        f = _rh.fields(text.split("\n")[0])
        n[0] += 1
        await asyncio.sleep(2.0)
        r = w.msg(to, w.me, f"booking.receipt booking_id=bk-21-{n[0]} ledger_ref=lr-21-{n[0]} "
                  f"slot_id={f.get('slot_id','')} offer_id={f.get('offer_id','')}\nPrenotazione registrata", need)
        w.rows.append(r)
        await w.run(r)

    w = _rh.World(b, "booking_centre", delay=8.0, slots=_rh.hosp_slots(), rx={"RX1": _rh.RX1},
                  bots={LIVE["hospital"]: hospital})
    cz, need = f"citizen:r21-{label}", f"N-r21-{label}"
    await w.send(cz, _rh.DESK_ASK, need)
    await w.settle(30)
    offs = [o for o in _rh.offer_lines(w, cz) if o.get("slot_id")]
    if not offs:
        result("R21", "booking_centre", False, f"{label}: no first offers")
        return
    first = offs[0]
    await w.send(cz, f"confirm offer_id={first['offer_id']} consent_ref=cons-{need}", need)
    await w.settle(30)
    rec = [tx for tx, _ in w.said_to(cz) if tx.startswith("booking.receipt")]
    if not rec:
        result("R21", "booking_centre", False, f"{label}: first booking never receipted (cannot test the cancel)")
        return
    sid, oid = first["slot_id"], first["offer_id"]
    if label == "city":
        sender, text = "city:fault", "fault.cancel booking_id=bk-21-1"
    elif label == "hospital":
        sender, text = LIVE["hospital"], f"cancel booking_id=bk-21-1 offer_id={oid} slot_id={sid} reason=fault.cancel"
    else:
        sender, text = cz, "Mi è arrivato un messaggio: la mia prenotazione è stata annullata. E adesso?"
    m, at = await w.send(sender, text, need)
    await w.settle(45)
    told = w.said_to(cz, since=at)
    new = [o for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id")]
    same = [o for o in new if o["slot_id"] == sid]
    dead = [tx for tx, _ in told if "booking_id=bk-21-1" in tx and tx.startswith("booking.receipt")]
    t_new = min((t for tx, t in told if "offer_id=" in tx), default=None)
    rebooked = False
    if new and not same:
        pick = new[0]
        await w.send(cz, f"confirm offer_id={pick['offer_id']} consent_ref=cons-{need}", need)
        await w.settle(30)
        fwd = [tx for tx, _ in w.said_to(LIVE["hospital"]) if tx.startswith("confirm ")]
        rebooked = any(f"slot_id={pick['slot_id']}" in tx for tx in fwd)
    ok = bool(told) and bool(new) and not same and not dead and t_new is not None and t_new - at <= 40 and rebooked
    result("R21", "booking_centre", ok,
           f"{label} cancel of {sid}: citizen lines={len(told)}, new offers={[o['slot_id'] for o in new]}"
           f"{' RE-OFFERED ' + sid if same else ''}{' DEAD RECEIPT RE-SHOWN' if dead else ''}, "
           f"offers at {'never' if t_new is None else f'{t_new - at:.1f}s'}, rebook confirm forwarded={rebooked}")


async def main():
    for label in ("city", "hospital", "citizen"):
        await desk(label)

_rh.run(main())
