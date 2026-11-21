"""AUDIT-L8: one facility refusal must give the citizen exactly ONE new offer set.

Two fixes meet on this path: 54e70fb6 (the desk rebooks from a fresh world after the hospital's
"cancel offer_id=... reason=NO_CAPACITY") and #45 (the hospital's structured cancel is followed by
its own other dates, sent to the desk as offer lines). If both reach the citizen, the citizen gets
two offer sets for one refusal. Cases:
  nocap   - the hospital sends the cancel, then its alternatives (#45 shape)
  cancel  - the hospital sends only the cancel (54e70fb6 shape)
Each: exactly one new offer set to the citizen after the refusal, never the refused slot.
Run: <city python> -I tests/test_one_refusal_one_set.py asst/_shared/agent.py
"""
import asyncio
import os
import sys

os.environ.setdefault("REGRESS_ROLES", "booking_centre")
os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh  # noqa: E402
from _rh_world import result, LIVE  # noqa: E402


async def case(label, alternatives):
    b = _rh.load(sys.argv[1], "booking_centre")
    refused = {}

    async def hospital(w, to, text, need):
        if not text.startswith("confirm "):
            return
        f = _rh.fields(text.split("\n")[0])
        sid, oid = f.get("slot_id", ""), f.get("offer_id", "")
        await asyncio.sleep(1.0)
        w.taken.add(sid)
        r = w.msg(to, w.me, f"cancel offer_id={oid} reason=NO_CAPACITY\nMi dispiace, quella disponibilità "
                  "non è più valida: le propongo subito un'alternativa.", need)
        refused["at"], refused["slot"] = w.now(), sid
        w.rows.append(r)
        await w.run(r)
        if alternatives:
            # #45: the facility's own other dates follow the structured cancel, as offer lines.
            free = [ln for ln in _rh.hosp_slots() if _rh.fields(ln)["slot_id"] not in w.taken]
            lines = [
                f"offer offer_id=of-{_rh.fields(ln)['slot_id']} {ln.replace(' free=1', '')}" for ln in free[:2]
            ]
            r = w.msg(to, w.me, "\n".join(lines + ["Altre date libere presso di noi."]), need)
            w.rows.append(r)
            await w.run(r)

    w = _rh.World(b, "booking_centre", delay=2.0, slots=_rh.hosp_slots(), rx={"RX1": _rh.RX1},
                  bots={LIVE["hospital"]: hospital})
    cz, need = f"citizen:one-{label}", f"N-one-{label}"
    await w.send(cz, _rh.DESK_ASK, need)
    await w.settle(30)
    offs = [o for o in _rh.offer_lines(w, cz) if o.get("slot_id")]
    if not offs:
        result("ONE-SET", "booking_centre", None, f"{label}: no first offers (cannot test the refusal)")
        return
    first = offs[0]
    await w.send(cz, f"confirm offer_id={first['offer_id']} consent_ref=cons-{need}\nVa bene, confermo.", need)
    await w.settle(60)
    at = refused.get("at")
    if at is None:
        result("ONE-SET", "booking_centre", None, f"{label}: the hospital bot never refused")
        return
    sets = [
        tx for tx, _ in w.said_to(cz, since=at)
        if any(ln.startswith("offer ") and "slot_id=" in ln for ln in tx.splitlines())
    ]
    new = [o["slot_id"] for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id")]
    bad = [s for s in new if s == first["slot_id"]]
    ok = len(sets) == 1 and not bad
    result("ONE-SET", "booking_centre", ok,
           f"{label}: {len(sets)} offer set(s) after one refusal of {first['slot_id']}, slots {new or 'none'}"
           + (f" REFUSED SLOT AGAIN {bad}" if bad else "") + (f", errors={w.errors[:1]}" if w.errors else ""))


async def main():
    await case("nocap", True)
    await case("cancel", False)

_rh.run(main())
