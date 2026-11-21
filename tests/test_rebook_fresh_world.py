"""N22 L6 desk failures that come back at L7 / L8 / Saturday (L6-LESSONS.md, written by the integrator
after L6 closed 03:10). From L6 on EVERY first booking still gets `fault.cancel`, so each of these hits
every citizen of the later levels too. Real booking first (Ledger 8 s, hospital bot receipts), then:
a) dupcancel: after the rebook is receipted, a LATE DUPLICATE `fault.cancel` of the OLD booking arrives
   (L7 duplicates). Good desk: the NEW booking stays; no cancel of it to the hospital or the citizen, no
   second rebook round. (84 handed: MoSaleh fix/urgent-l6-double; tl6bc-probe.py second.)
b) staleworld: two other slots are taken by other citizens between the first offers and the cancel.
   Good desk: the rebook offers come from a FRESH world read: never the dead slot, never a taken slot
   (-2 false offer risk; L6-LESSONS dropped #4).
c) english: an English citizen (lang=en) hears of the cancel in English, not Italian (dropped #10).
d) burst: right after the cancel the citizen writes three short lines ("ok", "grazie", "quando?").
   Good desk: the rebook offers still arrive inside 40 s (burst cap `_CITIZEN_BURST` dropped the offer
   while `_say` returned True: Sobhan #3, tl6bc-probe.py a_thanks).
Run: <city python> -I tests/test_rebook_fresh_world.py asst/_shared/agent.py
This test runs only staleworld + english on booking_centre (fails before the fix: S1 offered
while taken, cancel line in Italian to an English citizen)."""
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
        result("R22", "booking_centre", None, f"{label}: no first offers (cannot test the cancel)")
        return None
    first = offs[0]
    yes = "Yes, I confirm." if lang == "en" else "Va bene, confermo."
    await w.send(cz, f"confirm offer_id={first['offer_id']} consent_ref=cons-{need}\n{yes}", need)
    await w.settle(30)
    if not [tx for tx, _ in w.said_to(cz) if tx.startswith("booking.receipt")]:
        result("R22", "booking_centre", None, f"{label}: first booking never receipted (cannot test the cancel)")
        return None
    return w, cz, need, first


async def dupcancel():
    got = await booked("dup")
    if not got:
        return
    w, cz, need, first = got
    m, at = await w.send("city:fault", "fault.cancel booking_id=bk-22-1", need)
    await w.settle(45)
    new = [o for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id") and o["slot_id"] != first["slot_id"]]
    if not new:
        result("R22", "booking_centre", None, "dup: no rebook offers after the first cancel (R21 covers that)")
        return
    await w.send(cz, f"confirm offer_id={new[0]['offer_id']} consent_ref=cons-{need}\nVa bene, confermo.", need)
    await w.settle(30)
    rec2 = [tx for tx, _ in w.said_to(cz) if tx.startswith("booking.receipt") and "bk-22-2" in tx]
    if not rec2:
        result("R22", "booking_centre", None, "dup: rebook never receipted (cannot test the late duplicate)")
        return
    m, at2 = await w.send("city:fault", "fault.cancel booking_id=bk-22-1", need)
    await w.settle(30)
    to_h = [tx for tx, _ in w.said_to(LIVE["hospital"], since=at2) if tx.split()[:1] == ["cancel"]]
    to_c = [tx for tx, _ in w.said_to(cz, since=at2)]
    killed = [tx for tx in to_h + to_c if "bk-22-2" in tx and ("cancel" in tx.lower() or "annull" in tx.lower())]
    again = [o for o in _rh.offer_lines(w, cz, since=at2) if o.get("slot_id")]
    ok = not killed and not again and not to_h
    result("R22", "booking_centre", ok, f"dup: late duplicate fault.cancel of bk-22-1 after rebook bk-22-2: "
           f"cancel lines to hospital={len(to_h)}, new booking killed={bool(killed)}, second rebook offers={len(again)}")


async def staleworld():
    got = await booked("stale")
    if not got:
        return
    w, cz, need, first = got
    others = [_rh.fields(s)["slot_id"] for s in w.slots if _rh.fields(s)["slot_id"] != first["slot_id"]][:2]
    w.taken.update(others)  # taken by other citizens after our first offers
    w.taken.add(first["slot_id"])  # the cancelled slot stays closed (the facility cancelled it)
    m, at = await w.send("city:fault", "fault.cancel booking_id=bk-22-1", need)
    await w.settle(45)
    new = [o["slot_id"] for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id")]
    bad = [s for s in new if s in others or s == first["slot_id"]]
    ok = bool(new) and not bad
    result("R22", "booking_centre", ok, f"stale: taken after first offers {others}, dead {first['slot_id']}; "
           f"rebook offers {new or 'none'}{' FALSE OFFER ' + str(bad) if bad else ''}")


async def english():
    got = await booked("en", lang="en")
    if not got:
        return
    w, cz, need, first = got
    m, at = await w.send("city:fault", "fault.cancel booking_id=bk-22-1", need)
    await w.settle(45)
    told = [tx for tx, _ in w.said_to(cz, since=at)]
    prose = [ln for tx in told for ln in tx.splitlines()[1:] + [tx.splitlines()[0]]
             if ln.strip() and not ln.startswith(("offer ", "cancel ", "booking.", "data.request"))]
    it = [ln[:70] for ln in prose if any(k in ln for k in IT_WORDS)]
    ok = bool(told) and not it
    result("R22", "booking_centre", ok, f"en: lines after the cancel={len(told)}, Italian prose={it[:2] or 'none'}")


async def burst():
    got = await booked("burst")
    if not got:
        return
    w, cz, need, first = got
    m, at = await w.send("city:fault", "fault.cancel booking_id=bk-22-1", need, wait=False)
    for txt in ("ok", "grazie", "quando allora?"):
        await asyncio.sleep(0.4)
        await w.send(cz, txt, need, wait=False)
    await w.settle(50)
    t_off = min((t for tx, t in w.said_to(cz, since=at) if "offer_id=" in tx), default=None)
    new = [o["slot_id"] for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id") and o["slot_id"] != first["slot_id"]]
    ok = bool(new) and t_off is not None and t_off - at <= 40
    result("R22", "booking_centre", ok, f"burst: 3 citizen lines right after the cancel; rebook offers {new or 'none'} "
           f"at {'never' if t_off is None else f'{t_off - at:.1f}s'}")


async def main():
    pick = os.environ.get("R22_CASES", "staleworld,english").split(",")  # one case = a quick probe
    for name, fn in (("dupcancel", dupcancel), ("staleworld", staleworld), ("english", english), ("burst", burst)):
        if name in pick:
            await fn()

_rh.run(main())
