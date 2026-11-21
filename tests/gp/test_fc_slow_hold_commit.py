"""S21 consultorio: a slow Ledger (8-20 s) whose held / committed answers never open a turn.
The citizen's confirm turn must commit and send the receipt itself, a nudge during the open hold
must not leave a late committed line without a receipt, and no wait line may promise a receipt
that nothing will send ("non serve riscrivere").
Run: <city python> -I tests/test_fc_slow_hold_commit.py asst/_shared/agent.py"""
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
    for i in range(1, 5):
        at = (NOW + timedelta(days=1 + (i - 1) // 2)).replace(hour=9 + 2 * ((i - 1) % 2), minute=0, second=0, microsecond=0)
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[at.weekday()]
        rows.append(f"slot_id=S{i} doctor_id=D-c{1 + i % 2} at={at.isoformat()} day={day} facility={FC} "
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


async def case(name, hold, commit, *, twin=False, nudge=None):
    city = City(f"N-{name}", 0.1, 0.1)
    city.says(FIRST)
    await city.settle()
    offer = city.offer()
    if not offer:
        check(False, f"{name}: offers delivered", city)
        return
    city.delays.update(hold=hold, commit=commit)
    t_conf = city.says(f"confirm offer_id={offer} consent_ref=cons-{city.need}-booking")
    if twin:
        await asyncio.sleep(1.0)
        city.says(f"confirm offer_id={offer} consent_ref=cons-{city.need}-booking")
    if nudge is not None:
        await asyncio.sleep(nudge)
        city.says("ci siete? è confermato?")
    await city.settle()
    await asyncio.sleep(0.3)
    receipts, commits = city.receipts(), city.commits()
    told = [t for t, _ in city.out if t >= t_conf]
    if commit is None or hold is None:
        last = city.out[-1][1].lower() if city.out else ""
        check(not receipts, f"{name}: no receipt without a committed line", city)
        check("riscriva" in last, f"{name}: the citizen is asked to write again", city)
    else:
        committed = [t for t, x in city.ledger if x == "<= committed"]
        check(len(commits) == 1, f"{name}: one commit reaches the Ledger ({len(commits)})", city)
        check(len(receipts) == 1, f"{name}: one receipt reaches the citizen ({len(receipts)})", city)
        check(all(committed and t >= committed[0] for t, _ in receipts), f"{name}: no receipt before commit", city)
    check(bool(told) and told[0] - t_conf <= 3.5, f"{name}: a line within 3.5 s of the confirm", city)
    promised = [x for _, x in city.out if any(p in x.lower() for p in PROMISE)]
    check(not promised, f"{name}: no wait line promises a receipt that nothing sends", city)
    check(not city.errors, f"{name}: no brain errors", city)


async def main():
    await asyncio.gather(
        case("slow8", 8.0, 8.0),
        case("twin8", 8.0, 8.0, twin=True),
        case("nudge8", 8.0, 8.0, nudge=12.0),
        case("slow14", 14.0, 14.0),
        case("slow20", 20.0, 20.0),
        case("silent", None, None),
    )
    print("FAILED: " + " | ".join(FAILS) if FAILS else "OK test_fc_slow_hold_commit")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
