"""Consultorio: a citizen's direct confirm is always served or answered (issue #54).

Live journey 890904000256 (consultorio 5911b27, 10 msgs, 2 cancels): the citizen's confirm of our
offer got no reply. On 5911b27 every confirm is silent when the Ledger takes 8 s or more (fixed on
brain-gp by c6b1228). On brain-gp a confirm of an offer the citizen had cancelled earlier (a change
of mind) was answered with a fresh search, "Buongiorno, ho trovato...", or "quella data non è più
disponibile" while the slot was still free: the citizen's own cancel counted as a withdrawn slot.

Run: <city python> -I tests/gp/test_fc_confirm_never_silent.py asst/_gp/agent.py
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message, OutOfScope

os.environ["CITY_ROLE"] = "family_counseling"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "family_counseling"

FC = "agent:t07/family_counseling"
NOW = datetime.now(timezone.utc)
SLOTP = "W01QQQ-asst-centro-c1-2026112"  # live slot ids are long; offer_id = of-<slot_id>
FIRST = "Buongiorno, vorrei prenotare una visita ginecologica al consultorio. lang=it"
PROMISE = ("non serve riscrivere", "no need to write again", "arriva qui da sola", "comes here by itself")
SEQ = [0]
FAILS = []


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


def slots():
    rows = []
    for i in range(1, 9):
        at = (NOW + timedelta(days=1 + (i - 1) // 2)).replace(hour=9 + 2 * ((i - 1) % 2), minute=0, second=0, microsecond=0)
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[at.weekday()]
        rows.append(f"slot_id={SLOTP}S{i} doctor_id=D-c{1 + i % 2} at={at.isoformat()} day={day} facility={FC} "
                    "procedure=CONS-GIN free=1")
    return "slots page=1/1\n" + "\n".join(rows)


class City:
    """One case. Ledger answers go to history only (the push never opens a turn)."""

    def __init__(self, need, hold, commit):
        self.need, self.citizen = need, f"citizen:t07/{need}"
        self.delays = {"hold": hold, "commit": commit}
        self.rows, self.out, self.ledger = [], [], []
        self.turns, self.errors = [], []
        self.t0 = time.monotonic()
        self.n = 0
        self.taken = set()

    def now(self):
        return time.monotonic() - self.t0

    async def say(self, to, text, need):
        self.rows.append(msg(FC, to, text, need))
        if to == LEDGER:
            self.ledger.append((self.now(), text))
            asyncio.get_running_loop().create_task(self._answer(text))
        else:
            self.out.append((self.now(), text))
        return "ok"

    async def _answer(self, text):
        head = text.split()[0]
        delay = self.delays.get(head, 0.1)
        if delay is None:
            return  # this Ledger never answers
        await asyncio.sleep(delay)
        f = dict(w.split("=", 1) for w in text.split() if "=" in w)
        if head == "agenda":
            body = f"agenda agenda_now={NOW.replace(hour=8, minute=0, second=0, microsecond=0).isoformat()}"
        elif head == "world":
            body = slots()
        elif head == "hold" and f.get("slot_id", "") in self.taken:
            body = f"refused op=hold code=SLOT_TAKEN slot_id={f.get('slot_id', '')}"
        elif head == "hold":
            self.n += 1
            body = f"held hold_id=bk-{self.n} slot_id={f.get('slot_id', '')} fence=1 need_id={self.need}"
        elif head == "commit":
            hid = f.get("hold_id", "")
            body = f"committed booking_id={hid} ledger_ref=lr-{hid} slot_id=S1"
            self.ledger.append((self.now(), "<= committed"))
        else:
            body = f"{head} ok"
        self.rows.append(msg(LEDGER, FC, body, self.need))

    def says(self, text):
        row = msg(self.citizen, FC, text, self.need)
        self.rows.append(row)
        self.turns.append(asyncio.get_running_loop().create_task(self._turn(row)))
        return self.now()

    async def _turn(self, row):
        try:
            await asyncio.wait_for(brain.main(SimpleNamespace(summary=row.text, is_emergency=False), View(self, row)), 45)
        except OutOfScope:
            pass
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{type(exc).__name__}: {exc}")

    async def settle(self):
        while any(not t.done() for t in self.turns):
            await asyncio.sleep(0.1)

    def offer(self):
        for _, text in self.out:
            for w in text.split():
                if w.startswith("offer_id=of-"):
                    return w.split("=", 1)[1].rstrip(".,;")
        return ""

    def receipts(self):
        return [(t, x) for t, x in self.out if "booking.receipt" in x.split()]

    def commits(self):
        return [x for _, x in self.ledger if x.startswith("commit ")]


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, FC, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self._c.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []



def check(ok, label, city):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for t, x in city.out:
            print(f"      [{t:5.1f}s] -> citizen: {x[:120]!r}")
        for t, x in city.ledger:
            print(f"      [{t:5.1f}s] ledger {x[:90]!r}")
        for e in city.errors:
            print("      ERROR", e)


def ids(city, since=0):
    found = []
    for _, text in city.out[since:]:
        for w in text.split():
            if w.startswith("offer_id=of-"):
                found.append(w.split("=", 1)[1].rstrip(".,;"))
    return found


async def step(city, text):
    n0 = len(city.out)
    t0 = city.says(text)
    await city.settle()
    await asyncio.sleep(0.3)
    return t0, [x for _, x in city.out[n0:]], n0


def gone_words(lines):
    return [x for x in lines if "non è più disponibile" in x or x.startswith("Buongiorno")]


async def slow_after_refusal(delay):
    name = f"slow{int(delay)}"
    city = City(f"N-{name}", 0.1, 0.1)
    await step(city, FIRST)
    first = ids(city)[0]
    _, _, n0 = await step(city, f"cancel offer_id={first} reason=other non mi va bene questo orario")
    _, _, n1 = await step(city, "va bene un altro giorno, per esempio giovedì o venerdì")
    fresh = [i for i in ids(city, n1) if i != first] or [i for i in ids(city, n0) if i != first]
    if not fresh:
        check(False, f"{name}: new offers after the refusal", city)
        return
    city.delays.update(hold=delay, commit=delay)
    t0, _, _ = await step(city, f"confirm consent_ref=cons-{city.need}-booking offer_id={fresh[0]}")
    told = [t for t, _ in city.out if t >= t0]
    check(bool(told) and told[0] - t0 <= 3.5, f"{name}: confirm after a refusal, Ledger {delay:.0f} s: a line within 3.5 s", city)
    check(len(city.commits()) == 1 and len(city.receipts()) == 1, f"{name}: one commit and one receipt", city)


async def changed_mind(taken=False):
    name = "taken" if taken else "mind"
    city = City(f"N-{name}", 0.1, 0.1)
    await step(city, FIRST)
    first = ids(city)[0]
    _, _, n0 = await step(city, f"cancel offer_id={first} reason=other non mi va bene questo orario")
    for i in ids(city, n0):
        if i != first:
            await step(city, f"cancel offer_id={i} reason=other nemmeno questo")
            break
    if taken:
        city.taken.add(first[3:])
    _, got, _ = await step(city, f"confirm consent_ref=cons-{city.need}-booking offer_id={first}")
    if taken:
        check(bool(got) and not city.receipts(), "changed mind, slot taken at the Ledger: answered, no receipt", city)
        check(any("non è più" in x for x in got) and any("offer_id=of-" in x and first not in x for x in got),
              "changed mind, slot taken: told the date is gone + other dates", city)
    else:
        check(len(city.commits()) == 1 and len(city.receipts()) == 1, "changed mind: the confirmed slot is booked (1 commit, 1 receipt)", city)
        check(not gone_words(got), "changed mind: no 'non è più disponibile' / fresh search for a free slot", city)
        _, again, _ = await step(city, f"confirm consent_ref=cons-{city.need}-booking offer_id={first}")
        check(len(city.commits()) == 1 and any("booking.receipt" in x for x in again),
              "duplicate confirm after the receipt: receipt again, no second commit", city)


async def facility_withdrew():
    city = City("N-fac", 0.1, 0.1)
    await step(city, FIRST)
    first = ids(city)[0]
    # the consultorio itself withdrew the slot (as after a refused hold): it stays gone
    city.rows.append(msg(FC, city.citizen, f"cancel offer_id={first} reason=slot_unavailable", city.need))
    _, got, _ = await step(city, f"confirm consent_ref=cons-{city.need}-booking offer_id={first}")
    check(bool(got) and not city.receipts() and not any(x.startswith("hold ") and first[3:] in x for _, x in city.ledger),
          "slot withdrawn by the consultorio: answered, no hold on it, no receipt", city)


async def main():
    await asyncio.gather(slow_after_refusal(0.1), slow_after_refusal(8.0), slow_after_refusal(14.0), slow_after_refusal(20.0))
    await changed_mind()
    await changed_mind(taken=True)
    await facility_withdrew()
    print("FAILED: " + " | ".join(FAILS) if FAILS else "OK test_fc_confirm_never_silent")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
