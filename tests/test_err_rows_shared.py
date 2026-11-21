"""L8 error rows of the shared brain (desk, hospital, specialist): no path may end in silence.

1. A Ledger refusal with no op= ("refused slot_id=S1 code=RATE_LIMITED") is the answer to the write
   we wait for, in the turn (_await_line) and in the background follower (_follow). A refusal that
   names another slot is not.
2. A crash in an AGENT turn: the partner agent gets one short line (never a cancel).
3. A history read error that is not a transport error (a parse error, a bad page): the citizen
   still gets a line, the turn does not crash.
4. The rescue sweep after a citizen confirm must not change that turn's "citizen was told" flag:
   a rescue that answered another citizen cancelled the confirm's 3 s holding line.

Run: <city python> -I tests/test_err_rows_shared.py asst/_shared/agent.py [-v]
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timezone

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message, Need, OutOfScope  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
VERBOSE = "-v" in sys.argv

CZ = "citizen:t07/c-er"
CZ2 = "citizen:t07/c-er2"
DESK = "agent:y6hxsd766sys"
HOSP = "agent:r8yprrs0e8be"
PARTNER = "agent:q4partner01x"
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"er{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    address = ""

    def __init__(self, me, read_error=None):
        self.agent_id, self.read_error = me, read_error
        self.rows, self.out, self.inbound, self.reads = [], [], None, 0

    async def say(self, to, text, *, status=None):
        need = self.inbound.need_id if self.inbound is not None else ""
        self.out.append((to, text, need))
        self.rows.append(msg(self.agent_id, to, text, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        self.reads += 1
        if self.read_error is not None and self.reads == 1:
            raise self.read_error
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


STATE = ("_LOCKS", "_HELD", "_RESCUED", "_RESCUE_REFUSED", "_REJECTED", "_LAST_FAIL", "_LEDGER_SENDS", "_DUPS",
         "_CITIZEN_SENT", "_CITIZEN_RATE", "_CITIZEN_TOKENS", "_CANCELS_SENT", "_ONCE", "_GATES", "_SENT", "_REPS",
         "_SORRIED", "_VERIFIED", "_ASKED", "_FILED", "_REFUSAL_AT", "_FOLLOWING", "_RESCUE_TRIED")


def reset(role, sweep=False):
    os.environ["CITY_ROLE"] = role
    for name in STATE:
        table = getattr(brain, name, None)
        if table is not None:
            table.clear()
    brain._RESCUE_LAST[:] = [] if sweep else [time.monotonic()]


async def run_turn(city, row):
    city.rows.append(row)
    city.inbound = row
    error = None
    try:
        await asyncio.wait_for(brain.main(Need(summary=row.text, message=row), city), 45)
    except OutOfScope:
        pass
    except BaseException as exc:  # noqa: BLE001 - an exception leaving the turn is the bug
        error = f"{type(exc).__name__}: {exc}"
    return error


def show(city):
    for to, text, need in city.out:
        print("     ->", to, f"[{need}]", "|", text[:140].replace("\n", " | "))


async def main():
    results = []

    def check(label, ok, city=None, error=None):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + label + (f"  [{error}]" if error else ""))
        if city is not None and (VERBOSE or not ok):
            show(city)

    # 1a. _await_line: a bare refusal (no op=) of our slot ends the wait at once.
    reset("hospital")
    city = City(HOSP)
    need = "N-er-await"
    city.inbound = msg(CZ, HOSP, "x", need)
    bare = msg(LEDGER, HOSP, "refused slot_id=S1 code=RATE_LIMITED", need)
    city.rows.append(bare)
    t0 = time.monotonic()
    try:
        got = await brain._await_line(city, need, [], "held", "slot_id=S1", op="hold", budget=3.0)
    except Exception as exc:  # noqa: BLE001
        got = exc
    took = time.monotonic() - t0
    check(f"_await_line: bare refusal of S1 matched ({took:.1f}s)",
          isinstance(got, tuple) and got[0].msg_id == bare.msg_id and took < 1.0)
    # 1b. _follow: a bare refusal (no op=) that lands later runs the step (answer, not silence).
    # Real follower pause: a late refusal reaches the step within 3 s.
    for label, token, text, want in (
        ("hold, slot_id named", "slot_id=S1", "refused slot_id=S1 code=RATE_LIMITED", True),
        ("commit, hold_id named", "booking_id=H1", "refused hold_id=H1 code=HOLD_EXPIRED", True),
        ("commit RATE_LIMITED, hold_id named", "booking_id=H2", "refused hold_id=H2 code=RATE_LIMITED", True),
        ("no id at all", "slot_id=S1", "refused code=RATE_LIMITED", True),
        ("another slot", "slot_id=S1", "refused slot_id=S9 code=RATE_LIMITED", False),
    ):
        reset("hospital")
        city = City(HOSP)
        need = f"N-er-follow-{SEQ[0]}"
        city.inbound = msg(CZ, HOSP, "x", need)
        hits = []

        async def then(line, rows, hits=hits):
            hits.append(line.text)

        head = "committed" if token.startswith("booking_id") else "held"
        brain._follow(city, need, [], head, token, "commit" if head == "committed" else "hold", then)
        await asyncio.sleep(0.25)
        city.rows.append(msg(LEDGER, HOSP, text, need))
        await asyncio.sleep(2.5)
        for task in list(brain._FOLLOWING.values()):
            task.cancel()
        check(f"_follow {label}: {'matched' if want else 'not matched'} ({hits})", bool(hits) == want)

    # 2. A crash in an agent turn: the partner agent gets a line, and it is not a cancel.
    saved_handle = brain.handle

    async def boom(role, city, message, case):
        raise RuntimeError("boom in handle")

    for role, me in (("hospital", HOSP), ("specialist_care", "agent:dqfw9nh15dn4"), ("booking_centre", DESK)):
        reset(role)
        brain.handle = boom
        city = City(me)
        row = msg(PARTNER, me, "confirm offer_id=OF-1 consent_ref=cons-N-x-booking", f"N-er-crash-{role}")
        error = await run_turn(city, row)
        brain.handle = saved_handle
        to_partner = [t for to, t, _ in city.out if to == PARTNER]
        check(f"{role}: crash in an agent turn -> partner told, no cancel",
              bool(to_partner) and not any(t.split()[0] == "cancel" for t in to_partner), city, error)

    # 3. A non-transport read error on the first history read: the citizen still gets a line.
    for role, me in (("hospital", HOSP), ("specialist_care", "agent:dqfw9nh15dn4"), ("booking_centre", DESK)):
        reset(role)
        city = City(me, read_error=ValueError("Expecting value: line 1 column 1 (char 0)"))
        row = msg(CZ, me, "Buongiorno, avete il parcheggio per i pazienti?", f"N-er-read-{role}")
        error = await run_turn(city, row)
        told = [t for to, t, _ in city.out if to == CZ]
        check(f"{role}: ValueError on the first read -> citizen told, turn not crashed",
              error is None and bool(told), city, error)

    # 4. Desk confirm forwarded to the facility (nothing told yet) + a rescue that answers another
    # citizen: the confirm's 3 s holding line must still go out, with the confirm's own need_id.
    reset("booking_centre", sweep=True)
    city = City(DESK)
    live_need, other_need = "N-er-live", "N-er-other"
    other = msg(CZ2, DESK, "Buongiorno, vorrei sapere a che punto è la mia richiesta", other_need)
    city.rows.append(other)
    confirm = msg(CZ, DESK, "confirm offer_id=OF-9 consent_ref=cons-N-er-live-booking", live_need)
    saved = (brain.handle, brain._rescue_picks, brain._confirm_fallback)

    async def fake_handle(role, c, message, case):
        if message.msg_id == other.msg_id:
            await brain._say(c, message.sender, "Ecco a che punto è la sua richiesta: la sto seguendo io.")
        # the live confirm: forwarded to the facility, nothing said to the citizen yet

    async def no_fallback(c, chat, message, role):
        return False

    brain.handle = fake_handle
    brain._rescue_picks = lambda rows, me, current, now: [(30.0, other, [other])]
    brain._confirm_fallback = no_fallback
    try:
        error = await run_turn(city, confirm)
        await asyncio.sleep(brain._HOLD_AFTER + 1.0)
    finally:
        brain.handle, brain._rescue_picks, brain._confirm_fallback = saved
    rescued = [t for to, t, _ in city.out if to == CZ2]
    hold = [(t, n) for to, t, n in city.out if to == CZ]
    check(f"desk confirm + rescue of another case: rescue answered ({len(rescued)}), "
          f"confirm holding line still out ({len(hold)})",
          error is None and bool(rescued) and bool(hold) and all(n == live_need for _, n in hold), city, error)

    print(f"{sum(results)}/{len(results)} passed")
    print("OK test_err_rows_shared" if all(results) else "FAIL test_err_rows_shared")
    raise SystemExit(0 if all(results) else 1)


asyncio.run(main())
