"""F01 (L8): after a fault.cancel of a receipted booking the desk is never silent. The citizen
always gets a cancel line in their own language, then new offers or an honest no-slot line.
Cases: a) fault.cancel that names no booking_id, b) no free slot left after the cancel,
c) the cancel path crashes (R22c: a NameError there left 0 lines). Each in en + it.
Fails on development 28c14a31 (all) and on 1d1e3d1c (crash cases).
Run: <city python> -I tests/test_cancel_never_silent.py asst/_shared/agent.py"""
import asyncio, os, sys
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import result, LIVE

IT_WORDS = ("Mi dispiace", "annullat", "disponibilit", "propongo", "prenotazione", "Buongiorno")


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
        result("F01", "booking_centre", None, f"{label}: no first offers (cannot test the cancel)")
        return None
    first = offs[0]
    yes = "Yes, I confirm." if lang == "en" else "Va bene, confermo."
    await w.send(cz, f"confirm offer_id={first['offer_id']} consent_ref=cons-{need}\n{yes}", need)
    await w.settle(30)
    if not [tx for tx, _ in w.said_to(cz) if tx.startswith("booking.receipt")]:
        result("F01", "booking_centre", None, f"{label}: first booking never receipted (cannot test the cancel)")
        return None
    return w, cz, need, first

def prose(told):
    return [ln for tx in told for ln in tx.splitlines()
            if ln.strip() and not ln.startswith(("offer ", "cancel ", "booking.", "data.request"))]


async def case(label, lang, text, empty, crash=False):
    got = await booked(f"ns-{label}", lang=lang)
    if not got:
        result("F01", "booking_centre", False, f"{label}: no receipted first booking")
        return
    w, cz, need, first = got
    if empty:
        w.taken.update(_rh.fields(s)["slot_id"] for s in w.slots)  # nothing free any more
    if crash:
        def boom(*_a, **_k):
            raise NameError("name '_cancel_told_after' is not defined")
        w.brain._cancel_told_after = boom  # the R22c crash, on purpose
    m, at = await w.send("city:fault", text, need)
    await w.settle(45)
    told = [tx for tx, _ in w.said_to(cz, since=at)]
    cancel = [tx for tx in told if "cancel" in tx.split("\n")[0] or "annullat" in tx or "cancelled" in tx]
    p = prose(told)
    wrong = [ln[:60] for ln in p if any(k in ln for k in IT_WORDS)] if lang == "en" else []
    stale = [o["slot_id"] for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id") in w.taken]
    ok = bool(cancel) and bool(p) and not wrong and not stale
    result("F01", "booking_centre", ok, f"{label}: lines after the cancel={len(told)}, cancel line={bool(cancel)}, "
           f"wrong language={wrong[:2] or 'none'}, taken slots offered={stale or 'none'}")


async def main():
    await case("noid-it", "it", "fault.cancel reason=facility_closed", False)
    await case("noid-en", "en", "fault.cancel reason=facility_closed", False)
    await case("empty-it", "it", "fault.cancel booking_id=bk-22-1", True)
    await case("empty-en", "en", "fault.cancel booking_id=bk-22-1", True)
    await case("crash-it", "it", "fault.cancel booking_id=bk-22-1", False, crash=True)
    await case("crash-en", "en", "fault.cancel booking_id=bk-22-1", False, crash=True)

_rh.run(main())
