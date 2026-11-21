"""Live 21 Nov 12:10: 41 citizens frozen before the pause stayed silent with the rescue resume live.

city.history() in city_sdk.http reads at most _HISTORY_PAGES (5) pages of 200 rows from the newest, so
after hours of traffic a 4 h old waiting message is not in the sweep's rows. This fake mailbox honours
that cap. The old case sits behind 1500 newer rows:
  (a) a plain sweep (deep read off) never reaches it;
  (b) with the deep read, the background sweep starts one deep read, and the next sweep resumes the
      case ("Bentornato" + offers);
  (c) the SDK page cap is restored after the deep read;
  (d) a second sweep right after does not start a second deep read.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_rescue_reach.py asst/_shared/agent.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["CITY_RESCUE_WARMUP"] = "0"
os.environ.setdefault("REGRESS_ROLES", "booking_centre")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import BC, LEDGER, Message, result

import city_sdk.http as sdk_http

CZ, NEED = "citizen:reach-1", "N-reach-1"
HOLD = "Sto verificando le disponibilità nel registro: le scrivo appena ho le date."


def row(sender, to, text, ago, need=NEED, mid=None):
    return Message(type="chat", sender=sender, recipient=to, msg_id=mid or f"old-{abs(hash((text, ago, need))) % 10**9}",
                   need_id=need, body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


class CappedView(_rh.View):
    async def history(self, peer=None, *, need_id=None):
        rows = await super().history(peer, need_id=need_id)
        if need_id is not None:
            return rows
        rows.sort(key=lambda r: r.sent_at or "")
        return rows[-200 * sdk_http._HISTORY_PAGES:]


async def sweep(brain, view, other):
    brain._RESCUE_LAST[:] = []
    await asyncio.wait_for(brain._rescue(view, "booking_centre", other, timer=True), 20)


async def main():
    brain = _rh.load(sys.argv[1], "booking_centre")
    w = _rh.World(brain, "booking_centre", delay=0.2, slots=_rh.hosp_slots(), rx={"RX1": _rh.RX1})
    w.rows += [
        row(CZ, BC, _rh.DESK_ASK, 13800),
        row(BC, CZ, HOLD, 14400),
        row(LEDGER, BC, _rh.RX1, 14300),
    ]
    # 1500 newer rows of answered cases (our reply is the last row of each): nothing to rescue there.
    for i in range(750):
        n = f"N-busy-{i}"
        w.rows.append(row(f"citizen:busy-{i}", BC, "grazie", 3000 - i, need=n, mid=f"b{i}a"))
        w.rows.append(row(BC, f"citizen:busy-{i}", "Prego, a presto.", 2999 - i, need=n, mid=f"b{i}b"))
    brain.role_of = lambda: "booking_centre"
    other = w.msg("citizen:other", BC, "ciao", "N-other")
    view = CappedView(w, other)
    pages0 = sdk_http._HISTORY_PAGES

    has = hasattr(brain, "_DEEP_PAGES")
    if has:
        brain._DEEP_PAGES = 0
    await sweep(brain, view, other)
    await w.settle(3)
    told = [tx for tx, _ in w.said_to(CZ)]
    result("RC1", "booking_centre", not told, f"deep read off: the old case gets {len(told)} line(s) (window too short)")

    if not has:
        result("RC2", "booking_centre", False, "no deep read in this brain: the old case is never reached")
        _rh.done()
        return
    brain._DEEP_PAGES = 20
    brain._DEEP_LAST[:] = []
    await sweep(brain, view, other)  # starts the deep read
    for _ in range(40):
        if brain._DEEP_TASK and brain._DEEP_TASK[0].done():
            break
        await asyncio.sleep(0.25)
    result("RC3", "booking_centre", sdk_http._HISTORY_PAGES == pages0 and len(brain._DEEP_ROWS) > 1000,
           f"deep read: {len(brain._DEEP_ROWS)} rows, SDK page cap restored to {sdk_http._HISTORY_PAGES}")
    first_task = brain._DEEP_TASK[0] if brain._DEEP_TASK else None
    await sweep(brain, view, other)  # uses the deep rows
    result("RC4", "booking_centre", brain._DEEP_TASK and brain._DEEP_TASK[0] is first_task,
           "a sweep right after does not start a second deep read")
    await w.settle(30)
    for _ in range(80):
        if not getattr(brain, "_RESUME_FLIGHT", {}):
            break
        await asyncio.sleep(0.5)
    await w.settle(3)
    hello = [tx for tx, _ in w.said_to(CZ) if tx.startswith("Bentornato")]
    offers = [o for o in _rh.offer_lines(w, CZ) if o.get("slot_id")]
    result("RC2", "booking_centre", len(hello) == 1 and len(offers) >= 1,
           f"deep read on: {len(hello)} Bentornato, {len(offers)} offer(s) to the old case")
    result("RC5", "booking_centre", not w.errors, f"errors={w.errors[:2]}")
    _rh.done()


asyncio.run(main())
