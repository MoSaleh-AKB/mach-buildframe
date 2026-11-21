"""Consultorio (S13, S14, S16): a Ledger refusal of our hold, commit or check-in is read and answered.

The Ledger names the refused operation as a field: "refused op=hold code=SLOT_TAKEN". The
consultorio read the operation only as a bare word, so the refusal was ignored: the citizen got
"Sto registrando..." and then "la ricevuta arriva da sola" (a false promise), a refused commit kept
its hold, the confirm said again stayed silent. A relay 4xx on the commit said had the same end.
After a refusal the citizen hears it once, with the other dates (or what is missing), the hold is
freed, nothing refused is sent again and a repeated confirm is answered.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_fc_refused_op.py asst/_shared/agent.py
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, CapError, Message, OutOfScope

os.environ["CITY_ROLE"] = "family_counseling"
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "asst", "_shared", "agent.py")
FC, CZ = "agent:t07/family_counseling", "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
FIRST = "Buongiorno, vorrei prenotare una visita ginecologica al consultorio. lang=it"
SEQ = [0]
FAILS: list[str] = []


def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    brain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(brain)
    brain.role_of = lambda: "family_counseling"
    brain._LLM_NET = False
    brain._wx_fetch = lambda: {}
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST.clear()
        brain._RESCUE_LAST.append(time.monotonic() + 9999)
    return brain


def msg(sender, to, text, need="N-0001"):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


def slots():
    rows = []
    for i in range(1, 5):
        at = (NOW + timedelta(days=1 + (i - 1) // 2)).replace(hour=9 + 2 * ((i - 1) % 2), minute=0, second=0, microsecond=0)
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[at.weekday()]
        rows.append(f"slot_id=S{i} doctor_id=D-c{1 + i % 2} at={at.isoformat()} day={day} facility={FC} procedure=CONS-GIN free=1")
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    """One case. The Ledger answers each op after 0.2 s; ``refuse`` answers an op with a refusal line,
    ``say_fail`` makes the relay raise a 4xx when we send that op; ``push`` opens a turn per Ledger line."""

    def __init__(self, brain, refuse=None, say_fail=None, push=False, fmt="op"):
        self.brain, self.refuse, self.say_fail, self.push, self.fmt = brain, dict(refuse or {}), dict(say_fail or {}), push, fmt
        self.agent_id, self.address = FC, ""
        self.rows: list[Message] = []
        self.out: list[tuple[float, str, str]] = []
        self.ledger: list[str] = []
        self.tasks: list[asyncio.Task] = []
        self.holds: dict[str, str] = {}
        self.committed: set[str] = set()
        self.errors: list[str] = []
        self.t0 = time.monotonic()

    def now(self):
        return time.monotonic() - self.t0

    async def say(self, to, text, need):
        if to == LEDGER:
            head = text.split()[0]
            if head in self.say_fail:
                self.ledger.append("RAISED " + text)
                raise CapError(self.say_fail[head], "relay refused")
            self.ledger.append(text)
            self.tasks.append(asyncio.create_task(self._answer(head, text, need)))
        else:
            self.out.append((self.now(), to, text))
        self.rows.append(msg(FC, to, text, need))
        return "ok"

    async def _answer(self, head, text, need):
        await asyncio.sleep(0.2)
        f = dict(w.split("=", 1) for w in text.split() if "=" in w)
        ref = " ".join(f"{k}={f[k]}" for k in ("slot_id", "hold_id", "booking_id") if k in f)
        code = self.refuse.pop(head, None)
        if code:
            body = f"refused op={head} code={code} {ref}" if self.fmt == "op" else f"refused {head} reason={code} {ref}"
        elif head == "agenda":
            body = f"agenda agenda_now={NOW.replace(hour=8, minute=0, second=0, microsecond=0).isoformat()}"
        elif head == "world":
            body = slots()
        elif head == "hold":
            hid = f"bk-{len(self.holds) + 1}"
            self.holds[hid] = f.get("slot_id", "")
            body = f"held hold_id={hid} slot_id={self.holds[hid]} fence=1 need_id={need}"
        elif head == "commit":
            hid = f.get("hold_id", "")
            self.committed.add(hid)
            body = f"committed booking_id={hid} ledger_ref=lr-{hid} slot_id={self.holds.get(hid, '')}"
        elif head == "cancel":
            body = f"cancelled {ref}"
        elif head == "checkin":
            body = f"checked_in booking_id={f.get('booking_id', '')}"
        else:
            body = f"{head} ok"
        row = msg(LEDGER, FC, body, need)
        self.rows.append(row)
        self.ledger.append("<= " + body.splitlines()[0])
        if self.push:
            self.tasks.append(asyncio.create_task(self.turn(row)))

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        try:
            await asyncio.wait_for(self.brain.main(SimpleNamespace(summary=row.text, is_emergency=False), View(self, row)), 45)
        except OutOfScope:
            pass
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{type(exc).__name__}: {exc}")

    async def citizen(self, text):
        """The citizen writes; returns (time it was sent, lines to the citizen after it)."""
        row = msg(CZ, FC, text)
        self.rows.append(row)
        t = self.now()
        self.tasks.append(asyncio.create_task(self.turn(row)))
        while any(not x.done() for x in self.tasks):
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        return t, [(s - t, x) for s, to, x in self.out if to == CZ and s >= t]

    def offer_ids(self):
        return [w.split("=", 1)[1] for s, to, x in self.out if to == CZ for w in x.split() if w.startswith("offer_id=of-")]


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def check(ok, label, city):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for t, to, text in city.out:
            print(f"      [{t:5.1f}s] -> {to[-14:]}: {text[:150]!r}")
        for line in city.ledger:
            if not line.startswith(("agenda", "world", "rules", "<= agenda", "<= slots", "<= rules")):
                print(f"      ledger {line[:120]!r}")
        for e in city.errors:
            print("      ERROR", e)


def offer_sets(told):
    return sum(1 for t, x in told if x.count("offer_id=of-") >= 2)


async def case(label, op, code, *, push=False, relay=False, fmt="op"):
    city = City(load(), refuse=None if relay else {op: code}, say_fail={op: code} if relay else None, push=push, fmt=fmt)
    await city.citizen(FIRST)
    first = city.offer_ids()[0]
    city.refuse = {} if relay else {op: code}
    _, told = await city.citizen(f"confirm offer_id={first} consent_ref=cons-N-0001-booking")
    sent = [x for x in city.ledger if x.startswith((op + " ", "RAISED " + op))]
    check(len(sent) == 1, f"{label}: the refused {op} is sent once ({len(sent)})", city)
    check(not any("booking.receipt" in x for t, x in told), f"{label}: no receipt", city)
    check(bool(told) and told[0][0] <= 3.0, f"{label}: the citizen hears it within 3 s", city)
    check(any(x.startswith(f"cancel offer_id={first} ") for t, x in told), f"{label}: the refused offer is withdrawn", city)
    check(not any("arriva qui da sola" in x or "Non serve riscrivere" in x for t, x in told),
          f"{label}: no promise of a receipt that cannot come", city)
    if op == "commit":
        check(any(x.startswith("cancel hold_id=bk-1") for x in city.ledger), f"{label}: the hold is freed", city)
    if code in ("CONSENT_MISSING", "PRESCRIPTION_EXPIRED"):
        check(any("consenso" in x or "ricetta" in x for t, x in told), f"{label}: says what is missing", city)
        check(sum(1 for x in city.ledger if x.startswith("world")) == 1, f"{label}: no new slot search", city)
    else:
        others = [o for o in city.offer_ids() if o != first]
        check(offer_sets(told) == 1 and bool(others), f"{label}: the other dates once ({offer_sets(told)} sets)", city)
        check(not any("non ho trovato un posto libero" in x for t, x in told), f"{label}: no contradictory no-slot line", city)
    n = len(city.ledger)
    _, again = await city.citizen(f"confirm offer_id={first} consent_ref=cons-N-0001-booking")
    check(bool(again) and again[0][0] <= 3.0, f"{label}: the confirm said again is answered", city)
    check(not [x for x in city.ledger[n:] if x.startswith(("hold ", "commit ", "RAISED"))], f"{label}: nothing sent again", city)
    check(not city.errors, f"{label}: no brain error", city)


async def checkin_refused(push=False):
    city = City(load(), push=push)
    await city.citizen(FIRST)
    _, told = await city.citizen(f"confirm offer_id={city.offer_ids()[0]} consent_ref=cons-N-0001-booking")
    bid = next(w.split("=", 1)[1] for t, x in told if x.startswith("booking.receipt") for w in x.split() if w.startswith("booking_id="))
    city.refuse = {"checkin": "NOT_FOUND"}
    _, told = await city.citizen(f"checkin booking_id={bid}")
    label = "checkin refused" + (" pushed" if push else "")
    check(bool(told) and told[0][0] <= 3.0 and "check-in" in told[0][1], f"{label}: told within 3 s {told[:1]}", city)
    check(not any("offer_id=" in x for t, x in told) and not city.errors, f"{label}: no new dates, no error", city)


async def main():
    await case("hold SLOT_TAKEN", "hold", "SLOT_TAKEN")
    await case("hold ZEBRA_QUOTA_77 pushed", "hold", "ZEBRA_QUOTA_77", push=True)
    await case("commit RATE_LIMITED pushed", "commit", "RATE_LIMITED", push=True)
    await case("commit HOLD_EXPIRED", "commit", "HOLD_EXPIRED")
    await case("commit NO_CAPACITY bare form pushed", "commit", "NO_CAPACITY", push=True, fmt="bare")
    await case("commit CONSENT_MISSING", "commit", "CONSENT_MISSING")
    await case("commit relay SLOT_TAKEN", "commit", "SLOT_TAKEN", relay=True)
    await case("commit relay FORBIDDEN pushed", "commit", "FORBIDDEN", relay=True, push=True)
    await checkin_refused()
    await checkin_refused(push=True)
    print("FAILED: " + " | ".join(FAILS) if FAILS else "OK test_fc_refused_op")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
