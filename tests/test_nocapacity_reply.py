"""J07 (mixed test, L8): the citizen confirms, the desk forwards the confirm to our hospital, the
hospital's commit at the Ledger is refused NO_CAPACITY (or the hold SLOT_TAKEN) and the hospital
answers the desk "cancel offer_id=<id> reason=NO_CAPACITY". Live the desk stayed SILENT: no line to
the citizen, no new offer, the citizen had to write "Ci siete?" a minute later.

Good desk: within 3 s of the facility's refusal the citizen hears that this slot is gone, then gets
the next free slots from a FRESH world read (never the refused slot, never a slot taken meanwhile).
Run: <city python> -I tests/test_nocapacity_reply.py asst/_shared/agent.py
NOCAP_CASES=nocap,taken (default both)."""
import asyncio, os, sys
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import result, LIVE


async def case(label, code):
    b = _rh.load(sys.argv[1], "booking_centre")
    refused = {}

    async def hospital(w, to, text, need):
        if not text.startswith("confirm "):
            return
        f = _rh.fields(text.split("\n")[0])
        sid, oid = f.get("slot_id", ""), f.get("offer_id", "")
        await asyncio.sleep(1.5)  # J07: the hospital's slow-Ledger line first (no act word)
        r = w.msg(to, w.me, "Prenotazione in corso: il registro è lento a rispondere e non ho ancora la "
                  "ricevuta. Rimandatemi la stessa conferma tra un minuto e riprendo da lì.", need)
        w.rows.append(r)
        await w.run(r)
        await asyncio.sleep(6.0)  # hold, commit refused at the Ledger
        w.taken.add(sid)  # the slot really is gone (another citizen got it)
        r = w.msg(to, w.me, f"cancel offer_id={oid} reason={code}\nMi dispiace, quella disponibilità non "
                  "è più valida: le propongo subito un'alternativa.", need)
        refused["at"], refused["slot"] = w.now(), sid
        w.rows.append(r)
        await w.run(r)

    w = _rh.World(b, "booking_centre", delay=8.0, slots=_rh.hosp_slots(), rx={"RX1": _rh.RX1},
                  bots={LIVE["hospital"]: hospital})
    cz, need = f"citizen:nocap-{label}", f"N-nocap-{label}"
    await w.send(cz, _rh.DESK_ASK, need)
    await w.settle(30)
    offs = [o for o in _rh.offer_lines(w, cz) if o.get("slot_id")]
    if not offs:
        result("NOCAP", "booking_centre", None, f"{label}: no first offers (cannot test the refusal)")
        return
    first = offs[0]
    other = next((o["slot_id"] for o in offs[1:] if o["slot_id"] != first["slot_id"]), "")
    if other:
        w.taken.add(other)  # taken by another citizen between the first offers and the refusal
    await w.send(cz, f"confirm offer_id={first['offer_id']} consent_ref=cons-{need}\nVa bene, confermo.", need)
    await w.settle(60)
    at = refused.get("at")
    if at is None:
        result("NOCAP", "booking_centre", None, f"{label}: the hospital bot never refused")
        return
    told = w.said_to(cz, since=at)
    t_first = min((t for _, t in told), default=None)
    gone_line = any(f"offer_id={first['offer_id']}" in tx and ("cancel" in tx.split("\n")[0]) for tx, _ in told)
    new = [o["slot_id"] for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id")]
    bad = [s for s in new if s in (first["slot_id"], other)]
    retry = [tx for tx, t in w.said_to(LIVE["hospital"], since=at)
             if tx.startswith("confirm ") and f"slot_id={first['slot_id']}" in tx.split()]
    fast = t_first is not None and t_first - at <= 3.0
    ok = fast and gone_line and bool(new) and not bad and not retry
    result("NOCAP", "booking_centre", ok,
           f"{label}: {code} for {first['slot_id']}; first line after "
           f"{'never' if t_first is None else f'{t_first - at:.1f}s'}, slot-gone line={gone_line}, "
           f"new offers {new or 'none'}{' FALSE OFFER ' + str(bad) if bad else ''}, retries={len(retry)}"
           + (f", errors={w.errors[:1]}" if w.errors else ""))


async def main():
    pick = os.environ.get("NOCAP_CASES", "nocap,taken").split(",")
    if "nocap" in pick:
        await case("nocap", "NO_CAPACITY")
    if "taken" in pick:
        await case("taken", "SLOT_TAKEN")

_rh.run(main())
