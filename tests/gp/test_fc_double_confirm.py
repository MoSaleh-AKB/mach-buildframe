"""Consultorio S15: a second confirm for ANOTHER date never makes a second booking, and is answered.

The citizen confirms a second offer after the receipt, at the same moment as the first one (the
case history lags behind, as on the live relay) or while a slow Ledger (8 s) is still on the first
hold. One hold, one commit, one receipt; the second confirm gets a line within 3 s that names the
booking which stands and says how to move it. Kept working: a repeated confirm of the same offer
(the receipt again), a move request, an L6 fault.cancel with its rebook, the check-in.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_fc_double_confirm.py asst/_shared/agent.py
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message, OutOfScope

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
os.environ["CITY_ROLE"] = "family_counseling"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "family_counseling"

FC, CZ = "agent:t07/family_counseling", "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FIRST = "Buongiorno, vorrei prenotare una visita ginecologica al consultorio. lang=it"
FAILS: list[str] = []


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


def slot_rows():
    rows = []
    for i in range(1, 5):
        at = (NOW + timedelta(days=1 + (i - 1) // 2)).replace(hour=9 + 2 * ((i - 1) % 2), minute=0, second=0, microsecond=0)
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[at.weekday()]
        rows.append(f"slot_id=S{i} doctor_id=D-c{i % 2 + 1} at={at.isoformat()} day={day} facility={FC} "
                    "procedure=CONS-GIN free=1")
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    """One case, a scripted Ledger with per-op delays, history that may lag (``lag`` s), live pushes."""

    def __init__(self, delays=None, lag=0.0, push=True):
        self.need = "N-0001"
        self.agent_id, self.address = FC, ""
        self.delays = {"hold": 0.2, "commit": 0.2, "cancel": 0.2, "checkin": 0.2}
        self.delays.update(delays or {})
        self.lag, self.push = lag, push
        self.rows, self.out, self.ledger_in, self.tasks = [], [], [], []
        self.holds, self.committed, self.cancelled, self.n = {}, {}, set(), 0
        self.errors, self.sent_t, self.t0 = [], {}, time.monotonic()

    def now(self):
        return time.monotonic() - self.t0

    async def say(self, to, text, *, status=None, need=None):
        if to == LEDGER:
            self.ledger_in.append(text)
            self.tasks.append(asyncio.create_task(self._ledger(text.split()[0], text)))
        else:
            self.out.append((self.now(), to, text))
        self.rows.append(msg(self.agent_id, to, text, self.need))
        return "ok"

    async def _ledger(self, head, text):
        await asyncio.sleep(self.delays.get(head, 0.1))
        f = dict(w.split("=", 1) for w in text.split() if "=" in w)
        body = {"agenda": f"agenda agenda_now={NOW.replace(hour=8, minute=0, second=0, microsecond=0).isoformat()}",
                "world": slot_rows()}.get(head)
        if head == "hold":
            self.n += 1
            hid = f"bk-{self.n}"
            self.holds[hid] = f.get("slot_id", "")
            body = f"held hold_id={hid} slot_id={self.holds[hid]} fence=1 need_id={self.need}"
        elif head == "commit":
            hid = f.get("hold_id", "")
            self.committed[hid] = self.holds.get(hid, "")
            body = f"committed booking_id={hid} ledger_ref=lr-{hid} slot_id={self.holds.get(hid, '')}"
        elif head == "cancel":
            hid = f.get("booking_id") or f.get("hold_id", "")
            self.cancelled.add(hid)
            body = f"cancelled booking_id={hid}"
        elif head == "checkin":
            body = f"checked_in booking_id={f.get('booking_id', '')}"
        elif body is None:
            body = f"{head} ok"
        row = msg(LEDGER, self.agent_id, body, self.need)
        self.rows.append(row)
        if self.push and head in ("hold", "commit", "cancel", "checkin"):
            self.tasks.append(asyncio.create_task(self.turn(row)))

    async def history(self, peer=None, *, need_id=None):
        snap = list(self.rows)
        if self.lag:
            await asyncio.sleep(self.lag)
        return [r for r in snap if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        view = SimpleNamespace(inbound=row, agent_id=self.agent_id, address="", find=self.find, history=self.history,
                               say=lambda to, text, *, status=None: self.say(to, text, status=status))
        try:
            await asyncio.wait_for(brain.main(SimpleNamespace(summary=row.text or "x", is_emergency=False), view), 45)
        except OutOfScope:
            pass
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{type(exc).__name__}: {exc}")

    def citizen(self, text, sender=CZ):
        row = msg(sender, self.agent_id, text, self.need)
        self.sent_t[row.msg_id] = self.now()
        self.rows.append(row)
        self.tasks.append(asyncio.create_task(self.turn(row)))
        return row

    def city_says(self, sender, text):
        row = msg(sender, self.agent_id, text, self.need)
        self.rows.append(row)
        self.tasks.append(asyncio.create_task(self.turn(row)))
        return row

    async def settle(self):
        while any(not t.done() for t in self.tasks):
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)

    def to_cz(self, after=0.0):
        return [(t, x) for t, to, x in self.out if to == CZ and t >= after]

    def offers(self):
        return [w.split("=", 1)[1] for _, x in self.to_cz() for line in x.splitlines() if line.startswith("offer ")
                for w in line.split() if w.startswith("offer_id=of-")]

    def active(self):
        return {b: s for b, s in self.committed.items() if b not in self.cancelled}

    def dump(self):
        for t, to, text in self.out:
            print(f"      [{t:5.1f}s] -> {to[-12:]}: {text[:170]!r}")
        for text in self.ledger_in:
            print(f"      ledger {text[:120]!r}")
        for e in self.errors:
            print("      ERROR", e)


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE", "_RESCUED",
                 "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_COMMITS", "_CONS_HELD", "_R5_TURNS", "_LAST_FAIL",
                 "_FILED", "_TURN_TOLD", "_SENT", "_CONS_CANCELLED", "_CONS_CLAIMS"):
        obj = getattr(brain, name, None)
        if hasattr(obj, "clear"):
            obj.clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]
    brain._EN_CASES.discard(CZ)


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        if city is not None:
            city.dump()
    return ok


def conf(oid):
    return f"confirm offer_id={oid} consent_ref=cons-N-0001-booking"


def day_of(city, oid):
    """The date of an offer as the citizen reads it (dd/mm)."""

    for _, x in city.to_cz():
        for line in x.splitlines():
            if f"offer_id={oid} " in line + " ":
                at = dict(w.split("=", 1) for w in line.split() if "=" in w).get("agenda_date", "")
                return datetime.fromisoformat(at).strftime("%d/%m") if at else ""
    return ""


def answered_in(city, row, within=3.0):
    t0 = city.sent_t[row.msg_id]
    return [x for t, x in city.to_cz() if t0 <= t <= t0 + within]


async def start(delays=None, lag=0.0, push=True):
    reset()
    city = City(delays=delays, lag=lag, push=push)
    city.citizen(FIRST)
    await city.settle()
    return city, city.offers()


def second_ok(city, name, row2, first_oid):
    """One booking, one receipt, and the second confirm names the standing booking within 3 s."""

    holds = [x for x in city.ledger_in if x.startswith("hold ")]
    commits = [x for x in city.ledger_in if x.startswith("commit ")]
    receipts = {w for _, x in city.to_cz() for w in x.split() if w.startswith("booking_id=") and "booking.receipt" in x}
    check(len(city.active()) == 1 and len(holds) == 1 and len(commits) == 1,
          f"{name}: one booking (active={city.active()}, holds={len(holds)}, commits={len(commits)})", city)
    check(len(receipts) == 1, f"{name}: one receipt ({receipts})", city)
    fast = answered_in(city, row2)
    named = [x for x in fast if day_of(city, first_oid) in x and ("sposta" in x.lower() or "move" in x.lower())]
    check(bool(named), f"{name}: second confirm told within 3 s which booking stands and how to move it", city)
    check(not city.errors, f"{name}: no brain error {city.errors}")


async def main():
    # 1. D: the second offer after the receipt.
    city, ids = await start()
    city.citizen(conf(ids[0]))
    await city.settle()
    r2 = city.citizen(conf(ids[1]))
    await city.settle()
    second_ok(city, "after receipt", r2, ids[0])

    # 2. C: both confirms at once, the history lags 0.4 s (neither turn sees the other's hold).
    city, ids = await start(lag=0.4)
    city.citizen(conf(ids[0]))
    await asyncio.sleep(0.05)
    r2 = city.citizen(conf(ids[1]))
    await city.settle()
    second_ok(city, "at once, lagging history", r2, ids[0])

    # 3. G: the second offer while a slow Ledger (8 s) is on the first booking.
    city, ids = await start(delays={"hold": 8.0, "commit": 8.0})
    city.citizen(conf(ids[0]))
    await asyncio.sleep(9.0)
    r2 = city.citizen(conf(ids[1]))
    await city.settle()
    second_ok(city, "mid booking, slow ledger", r2, ids[0])

    # 4. The same offer again while the slow Ledger works: never silent, one booking.
    city, ids = await start(delays={"hold": 8.0, "commit": 8.0})
    city.citizen(conf(ids[0]))
    await asyncio.sleep(9.0)
    r2 = city.citizen(conf(ids[0]))
    await city.settle()
    check(len(city.active()) == 1 and sum(x.startswith("hold ") for x in city.ledger_in) == 1,
          f"same offer mid booking: one booking ({city.active()})", city)
    check(bool(answered_in(city, r2, 3.5)), "same offer mid booking: holding line within 3.5 s", city)

    # 5. Kept: the same offer after the receipt gets the receipt again, no second hold.
    city, ids = await start()
    city.citizen(conf(ids[0]))
    await city.settle()
    r2 = city.citizen(conf(ids[0]))
    await city.settle()
    again = answered_in(city, r2)
    check(len(city.ledger_in) and sum(x.startswith("hold ") for x in city.ledger_in) == 1
          and any("booking.receipt booking_id=bk-1" in x for x in again), "same offer after receipt: receipt again", city)

    # 6. Kept: a move request cancels, offers again, and the new confirm is booked.
    city, ids = await start()
    city.citizen(conf(ids[0]))
    await city.settle()
    city.citizen("Vorrei spostare la prenotazione a un altro giorno")
    await city.settle()
    fresh = city.offers()[len(ids):]
    city.citizen(conf(next((o for o in fresh if o != ids[0]), "")))
    await city.settle()
    check("bk-1" in city.cancelled and len(city.active()) == 1 and list(city.active().values())[0] != city.holds["bk-1"],
          f"move: old cancelled, new booked (active={city.active()}, cancelled={city.cancelled})", city)

    # 7. Kept: L6 fault.cancel after the receipt, rebook offers, the new confirm is booked.
    city, ids = await start()
    city.citizen(conf(ids[0]))
    await city.settle()
    city.cancelled.add("bk-1")
    city.city_says("city:fault", "fault.cancel booking_id=bk-1")
    await city.settle()
    fresh = city.offers()[len(ids):]
    if check(bool(fresh), f"fault.cancel: new offers sent ({fresh})", city):
        city.citizen(conf(fresh[0]))
        await city.settle()
        check(len(city.active()) == 1 and "bk-2" in city.active(),
              f"fault.cancel: the rebook confirm is booked (active={city.active()})", city)

    # 8. Kept: check-in with the receipt code.
    city, ids = await start()
    city.citizen(conf(ids[0]))
    await city.settle()
    city.citizen("checkin booking_id=bk-1")
    await city.settle()
    check(any(x.startswith("checkin booking_id=bk-1") for x in city.ledger_in), "check-in reaches the Ledger", city)

    print("FAILED: " + " | ".join(FAILS) if FAILS else "OK test_fc_double_confirm")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
