"""gp L8 renewal: slow Ledger, still-valid number, fr/de/es, another person's number, desk asks.
Run: <city python> -I tests/test_gp_renew_l8.py asst/_shared/agent.py [bug1 bug1n bug2 bug3 bug4 bug5]
(exit 1 on any FAIL, ~40 s for all)

Local fake city only: one gp mailbox, a scripted Ledger that answers after DELAY seconds. The
answer lands in the case history; with push=True it also opens a gp turn (devmode).
Each case loads a FRESH brain module (no process state leaks between cases).
"""
import asyncio
import importlib.util
import re
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

PATH = sys.argv[1]
GP = "agent:jmqwr12vy2j6"
DESK = "agent:y6hxsd766sys"
CZ = "citizen:t07/c1"
OTHER = "citizen:t07/somebody-else"
SEQ = [0]
FAILS: list[str] = []
ITALIAN = re.compile(r"\b(?:la sua|ricetta|il suo|mi scriva|rinnovo|nel registro)\b")  # "registro" is Spanish too


def load():
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "gp"
    if hasattr(b, "_wx_fetch"):
        b._wx_fetch = lambda: {}
    return b


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self, delay=0.3, push=False, ledger=None, need="N-L8-1"):
        self.b = load()
        self.agent_id, self.address, self.need = GP, "", need
        self.delay, self.push = delay, push
        self.ledger = dict(ledger or {})  # number -> fields of the `prescription` answer
        self.rows: list[Message] = []
        self.out: list[tuple[float, str, str]] = []
        self.tasks: list[asyncio.Task] = []

    async def say(self, to, text, *, status=None, need=None):
        self.rows.append(msg(self.agent_id, to, text, need or self.need))
        self.out.append((time.monotonic(), to, text))
        if to == LEDGER:
            self.tasks.append(asyncio.create_task(self._ledger(text, need or self.need)))
        return "ok"

    async def _ledger(self, text, need):
        await asyncio.sleep(self.delay)
        head = text.split()[0]
        f = dict(w.split("=", 1) for w in text.split() if "=" in w)
        n = f.get("prescription_number", "")
        if head == "prescribe":
            self.ledger[n] = f"procedure={f.get('procedure')} priority={f.get('priority')} state=issued patient={f.get('patient')}"
            body = f"prescribed prescription_number={n} procedure={f.get('procedure')} priority={f.get('priority')}"
        elif head == "prescription":
            known = self.ledger.get(n)
            body = (f"prescription prescription_number={n} {known}" if known
                    else f"refused op=prescription prescription_number={n} code=NOT_FOUND")
        else:
            return
        row = msg(LEDGER, self.agent_id, body, need)
        self.rows.append(row)
        if self.push:
            await self.turn(row)

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        view = SimpleNamespace(agent_id=self.agent_id, address="", inbound=row,
                               say=lambda to, text, status=None: self.say(to, text, need=row.need_id),
                               history=self.history, find=self.find)
        await asyncio.wait_for(self.b.main(SimpleNamespace(summary="x", is_emergency=False), view), 45)

    async def write(self, sender, text, wait=True):
        row = msg(sender, self.agent_id, text, self.need)
        self.rows.append(row)
        t0 = time.monotonic()
        if wait:
            await self.turn(row)
        else:
            self.tasks.append(asyncio.create_task(self.turn(row)))
        return t0

    async def settle(self, secs):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            await asyncio.sleep(0.1)
        for t in self.tasks:
            if not t.done():
                t.cancel()

    def sent(self, to, t0=0.0):
        return [(t - t0, s) for t, x, s in self.out if t >= t0 and x == to]

    def prescribes(self):
        return [s for _, x, s in self.out if x == LEDGER and s.startswith("prescribe")]


def check(ok, label, city):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        t0 = city.out[0][0] if city.out else 0
        for t, to, text in city.out:
            print(f"      [{t - t0:5.1f}s] -> {to} : {text[:160]!r}")


def first_in(city, to, t0):
    rows = city.sent(to, t0)
    return rows[0][0] if rows else None


async def served(c, text="ho mal di schiena da una settimana"):
    d = c.delay
    c.delay = 0.05
    await c.write(CZ, text)
    await asyncio.sleep(0.3)
    c.delay = d
    return re.search(r"prescription_number=(\S+)", c.prescribes()[0]).group(1)


# 1. S17/S21: slow Ledger (5 s), our own `prescribed` row of the old number is in the case
async def bug1(nudge):
    c = City(delay=0.05)
    old = await served(c)
    c.ledger[old] = c.ledger[old].replace("state=issued", "state=expired")
    c.delay = 5.0
    t0 = await c.write(CZ, f"la ricetta prescription_number={old} è scaduta, me la rinnova?", wait=False)
    if nudge:
        await asyncio.sleep(3.0)
        await c.write(CZ, "allora?", wait=False)
    await c.settle(14.0)
    lab = f"bug1 slow Ledger 5 s{' + nudge' if nudge else ''}"
    t = first_in(c, CZ, t0)
    check(t is not None and t <= 3.0, f"{lab}: first reply within 3 s (got {t})", c)
    texts = [s for _, s in c.sent(CZ, t0)]
    check(not any("Non trovo questo numero" in s for s in texts), f"{lab}: never 'number not found'", c)
    rx = c.prescribes()[1:]
    check(len(rx) == 1 and f"prescription_number={old}" not in rx[0], f"{lab}: exactly one renewal, new number", c)
    if rx:
        new = re.search(r"prescription_number=(\S+)", rx[0]).group(1)
        check(any(f"prescription_number={new}" in s for s in texts), f"{lab}: citizen gets the new number", c)
    check(not any(s.startswith("prescription ") and f"prescription_number={old} " in s for s in texts),
          f"{lab}: the old number is never handed back as the answer", c)


# 2. S12: a still-valid number + a cancelled booking, no renewal words
async def bug2():
    for text in ("la mia prenotazione è stata annullata prescription_number=RX-OLD-1",
                 "my booking was cancelled prescription_number=RX-OLD-1, what now?"):
        c = City(need="N-L8-2", ledger={"RX-OLD-1": f"procedure=VIS-ORT priority=D state=issued patient={CZ}"})
        t0 = await c.write(CZ, text)
        await c.settle(0.6)
        lab = f"bug2 valid number {text[:30]!r}"
        check(not c.prescribes(), f"{lab}: no second prescription", c)
        check(any("prescription_number=RX-OLD-1" in s for _, s in c.sent(CZ, t0)), f"{lab}: reply carries the valid number", c)
    # P1 guard: the bare renewal need still renews (the Ledger may still say issued)
    c = City(need="N-L8-2b", ledger={"RX-OLD-2": f"procedure=VIS-ORT priority=D state=issued patient={CZ}"})
    await c.write(CZ, "need.request prescription_number=RX-OLD-2")
    await c.settle(0.6)
    check(len(c.prescribes()) == 1, "bug2 guard: bare need.request prescription_number=X still renews", c)


# 3. renewal in es / fr / de
async def bug3():
    for text in ("Hola, mi receta prescription_number=RX-77-AB ha caducado, ¿me la puede renovar?",
                 "Bonjour, mon ordonnance RX-77-AB a expiré, pouvez-vous la renouveler ?",
                 "Guten Tag, mein Rezept RX-77-AB ist abgelaufen. Können Sie es erneuern?"):
        c = City(push=True, need="N-L8-3", ledger={"RX-77-AB": "procedure=VIS-ORTO priority=B state=expired"})
        t0 = await c.write(CZ, text)
        await c.settle(0.8)
        lab = f"bug3 {text[:24]!r}"
        check(len(c.prescribes()) == 1, f"{lab}: one renewal", c)
        bad = [s for _, s in c.sent(CZ, t0) if ITALIAN.search(s.split("\n", 1)[-1])]
        check(not bad, f"{lab}: no Italian reply", c)


# 4. another person's number: turn + push + next turn say NOT_YOURS once, never silence
async def bug4():
    c = City(push=True, need="N-L8-4", ledger={"RX-55-OT": f"procedure=VIS-CARD priority=B state=expired patient={OTHER}"})
    a = await c.write(CZ, "Mi rinnova la ricetta prescription_number=RX-55-OT? è scaduta")
    await c.settle(1.0)
    b = await c.write(CZ, "è di mia madre, non può scrivere lei. Me la rinnovi a me che poi gliela porto")
    await c.settle(1.0)
    texts = [s for _, s in c.sent(CZ)]
    n = sum("a nome di un'altra persona" in s for s in texts)
    check(n == 1, f"bug4 NOT_YOURS said once (got {n})", c)
    check(first_in(c, CZ, a) is not None and first_in(c, CZ, b) is not None, "bug4 every citizen message answered", c)
    check(not c.prescribes() and OTHER not in " ".join(texts), "bug4 no renewal, no leak", c)


# 5. our desk asks the gp to renew for its citizen
async def bug5():
    for push in (False, True):
        c = City(push=push, need="N-L8-5", ledger={"RX-77-AB": f"procedure=VIS-ORTO priority=B state=expired patient={CZ}"})
        t0 = await c.write(DESK, f"need.request purpose=prescription_renewal prescription_number=RX-77-AB citizen={CZ}\n"
                                 "Il cittadino ha la ricetta scaduta (PRESCRIPTION_EXPIRED): potete rinnovarla?")
        await c.settle(1.0)
        lab = f"bug5 desk asks renewal (push={push})"
        desk = [s for _, s in c.sent(DESK, t0)]
        check(not any("no_capacity" in s for s in desk), f"{lab}: no 'cancel reason=no_capacity'", c)
        rx = c.prescribes()
        check(len(rx) == 1 and f"patient={CZ}" in rx[0] and "procedure=VIS-ORTO" in rx[0], f"{lab}: one renewal for the citizen", c)
        if rx:
            new = re.search(r"prescription_number=(\S+)", rx[0]).group(1)
            check(sum(f"prescription_number={new}" in s for s in desk) == 1, f"{lab}: desk gets the new number once", c)


async def run():
    cases = {"bug1": lambda: bug1(False), "bug1n": lambda: bug1(True), "bug2": bug2, "bug3": bug3,
             "bug4": bug4, "bug5": bug5}
    for name, fn in cases.items():
        if len(sys.argv) < 3 or name in sys.argv[2:]:
            await fn()
    print(f"\n{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(run())
