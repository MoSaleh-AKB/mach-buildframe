"""Rescue acceptance: late Ledger answers, a burst of citizen messages, a LOST Ledger page.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/reask.py asst/_shared/agent.py [A B C] [--fast]

Real constants by default (about 2 minutes). --fast shrinks the turn and the waits for a quick check.
An agent cannot act outside a turn, so the sweep needs inbound traffic: a background "other citizens
write every 3 s" stream stands in for the live mailbox.
  A  Ledger world reply arrives past the 40 s turn (history only, no turn opened): offers reach the
     citizen by the next sweep, without a new citizen message
  B  4 citizen messages of one need in the same second: every one is answered or covered, none silent
  C  Ledger world reply LOST (never comes for the first ask): the sweep asks again and the offers come
"""

import asyncio
import importlib.util
import os
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv.append("--noop")
path = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py"))
only = {a.upper() for a in sys.argv[1:] if a.upper() in ("A", "B", "C")}
FAST = "--fast" in sys.argv

# Reuse the slow-Ledger fake city (its module runs main() on import, so load its helpers by source).
src = open(os.path.join(HERE, "slowledger.py"), encoding="utf-8").read().split("async def scenario_a")[0]
src = src.replace("path = next(", "path = %r\n_unused = next(" % path)
ns: dict = {"__file__": os.path.join(HERE, "slowledger.py"), "__name__": "slow_helpers"}
sys.argv = [sys.argv[0], path]
exec(compile(src, "slowledger_helpers", "exec"), ns)
brain, SlowCity, msg, run_turn, offers, check, reset = (ns[k] for k in ("brain", "SlowCity", "msg", "run_turn", "offers", "check", "reset"))
BC, CZ, NEED_RX, FAILS, _View = ns["BC"], ns["CZ"], ns["NEED_RX"], ns["FAILS"], ns["_View"]

if FAST:
    brain._CITIZEN_TURN = 8.0
    brain._ADVANCE_BUDGET = 3.0
    brain._RESCUE_MIN_AGE = 4.0
    brain._RESCUE_EVERY = 3.0
    brain._REASK_AFTER = 2.0
SCALE = 0.2 if FAST else 1.0


class LossyCity(SlowCity):
    """Drops the first `lost` world asks: the Ledger never answers them."""

    def __init__(self, *a, lost=1, **k):
        super().__init__(*a, **k)
        self.lost = lost

    async def say(self, to, text, *, status=None, need=None):
        if to == ns["LEDGER"] and text.split()[0] == "world" and self.lost > 0:
            self.lost -= 1
            self.rows.append(msg(self.agent_id, to, text, need or self.need))
            return "ok"
        return await super().say(to, text, status=status, need=need)


async def traffic(city, stop, every=3.0):
    """Other citizens keep writing (other needs): each inbound is a turn and so a sweep clock."""
    i = 0
    while not stop.is_set():
        await asyncio.sleep(every * SCALE)
        i += 1
        row = msg("citizen:t07/c%d" % (i + 10), BC, "ci siete?", f"N-other-{i}")
        city.rows.append(row)
        try:
            await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), _View(city, row)), 60)
        except Exception as exc:  # noqa: BLE001
            print("      traffic turn raised", type(exc).__name__, exc)


async def drive(city, first, until):
    stop = asyncio.Event()
    side = asyncio.create_task(traffic(city, stop))
    city.rows.append(first)
    city.reset_clock()
    turn = asyncio.create_task(brain.main(SimpleNamespace(summary="x", is_emergency=False), _View(city, first)))
    while time.monotonic() - city.t0 < until and not (offers(city) and turn.done()):
        await asyncio.sleep(0.25)
    stop.set()
    await side
    if not turn.done():
        turn.cancel()
    for task in city.tasks:
        task.cancel()


def need_offers(city, need):
    return [(t, x) for t, to, x in city.out if to == CZ and "offer_id=of-" in x]


async def scenario_a():
    reset()
    brain._RESCUE_LAST[:] = [time.monotonic()]
    need = "N-A"
    city = SlowCity(need, delays=(0.3, 0.3, 41.0 * SCALE))
    first = msg(CZ, BC, "Mi fa male il ginocchio. Vorrei una visita. " + NEED_RX, need)
    await drive(city, first, 75.0 * SCALE)
    got = offers(city)
    ok = bool(got) and got[0][0] <= 60.0 * SCALE + 12
    check(ok, "A late Ledger world (41 s): offers by the sweep, no new citizen message"
          + (f" (first offer at {got[0][0]:.1f}s)" if got else ""), city)
    check(len(got) <= 1, "A one offer message", city)


async def scenario_b():
    reset()
    need = "N-B2"
    city = SlowCity(need, delays=(0.4, 0.4, 0.4))
    rows = [msg(CZ, BC, t + " " + NEED_RX, need) for t in ("Vorrei una visita.", "Mi fa male la schiena.", "Anche il ginocchio.", "Ci siete?")]
    city.rows.extend(rows)
    city.reset_clock()
    res = await asyncio.gather(
        *[asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), _View(city, r)), 60) for r in rows],
        return_exceptions=True,
    )
    for task in city.tasks:
        task.cancel()
    errs = [r for r in res if isinstance(r, Exception)]
    silent = [i + 1 for i, r in enumerate(rows) if not any(
        row.sender == BC and row.recipient == CZ for row in city.rows[city.rows.index(r):])]
    check(not errs, f"B no turn crashed ({errs[:1]})", city)
    check(not silent, f"B 4 messages in one second: none silent (silent: {silent}, replies to citizen: "
          f"{sum(1 for t, to, x in city.out if to == CZ)})", city)
    check(bool(offers(city)), "B offers delivered", city)


async def scenario_c():
    reset()
    brain._RESCUE_LAST[:] = [time.monotonic()]
    need = "N-C2"
    city = LossyCity(need, delays=(0.3, 0.3, 0.3), lost=1)
    first = msg(CZ, BC, "Ho mal di schiena. Vorrei una visita. " + NEED_RX, need)
    await drive(city, first, 90.0 * SCALE)
    got = offers(city)
    check(bool(got) and got[0][0] <= 60.0 * SCALE + 12,
          "C Ledger world reply LOST: offers via re-ask" + (f" (first offer at {got[0][0]:.1f}s)" if got else ""), city)
    asks = sum(1 for r in city.rows if r.sender == BC and r.recipient == ns["LEDGER"] and r.text.startswith("world"))
    check(2 <= asks <= 1 + brain._REASK_MAX, f"C re-ask capped ({asks} world asks)", city)


async def main():
    if not only or "A" in only:
        await scenario_a()
    if not only or "B" in only:
        await scenario_b()
    if not only or "C" in only:
        await scenario_c()
    print("FAILED: " + "; ".join(FAILS) if FAILS else "OK reask")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
