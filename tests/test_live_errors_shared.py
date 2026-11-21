"""A transport error (ConnectError, ReadError, RemoteProtocolError, an OSError) on a read or on a
send must never end a citizen turn with no line, and a slow Ledger must not leave the citizen with
the holding line only (live 000176 at the desk, 06:13: "Sto lavorando alla sua richiesta" and then
nothing). Desk, hospital and specialist.

"slow" = the Ledger answers 4 s later, the answer lands in history without a turn (like live), so
the 3 s holding line goes out first and the offers must follow in the same turn.

Run: <city python> -I tests/test_live_errors_shared.py asst/_shared/agent.py [-v]
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
from city_sdk.city import LEDGER, Message, Need, OutOfScope

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
VERBOSE = "-v" in sys.argv

CZ = "citizen:t07/c-le"
DESK = "agent:y6hxsd766sys"
HOSP = "agent:r8yprrs0e8be"
SPEC = "agent:dqfw9nh15dn4"
ME = {"booking_centre": DESK, "hospital": HOSP, "specialist_care": SPEC}
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
ERRS = {
    "ConnectError": lambda: httpx.ConnectError("All connection attempts failed"),
    "ReadError": lambda: httpx.ReadError("relay"),
    "RemoteProtocolError": lambda: httpx.RemoteProtocolError("Server disconnected without sending a response."),
    "OSError": lambda: ConnectionResetError(104, "Connection reset by peer"),
}
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"le{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    """read_fail: the first history read raises; say_fail: the first citizen send raises;
    ledger_delay: seconds before a Ledger answer lands in history (no turn for it)."""

    address = ""

    def __init__(self, me, read_fail=None, say_fail=None, ledger_delay=0.0):
        self.agent_id, self.read_fail, self.say_fail, self.delay = me, read_fail, say_fail, ledger_delay
        self.rows, self.out, self.inbound, self.reads, self.tasks = [], [], None, 0, []
        self.t0, self.first_line = 0.0, None

    def _answer(self, text):
        head = text.split()[0]
        fac = self.agent_id if self.agent_id != DESK else HOSP
        return {
            "agenda": f"agenda agenda_now={BASE.isoformat()}",
            "world": "slots page=1/1\n" + "\n".join(
                f"slot_id=S{i} doctor_id=D-h1 at={(BASE + timedelta(days=1, hours=i)).isoformat()} day=tue "
                f"facility={fac} procedure=VIS-CARD free=1" for i in range(1, 4)),
            "prescription": "prescription prescription_number=prescription_number-LE-1 "
                            "procedure=VIS-CARD priority=D state=issued",
        }.get(head)

    async def _late(self, text, need):
        await asyncio.sleep(self.delay)
        self.rows.append(msg(LEDGER, self.agent_id, text, need))

    async def say(self, to, text, *, status=None):
        need = self.inbound.need_id
        if to.startswith("citizen:") and self.say_fail is not None:
            fail, self.say_fail = self.say_fail, None
            raise fail()
        self.out.append((to, text))
        if to.startswith("citizen:") and self.first_line is None:
            self.first_line = time.monotonic() - self.t0
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            answer = self._answer(text)
            if answer and self.delay:
                self.tasks.append(asyncio.ensure_future(self._late(answer, need)))
            elif answer:
                self.rows.append(msg(LEDGER, self.agent_id, answer, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        self.reads += 1
        if self.read_fail is not None and self.reads == 1:
            raise self.read_fail()
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


STATE = ("_LOCKS", "_HELD", "_RESCUED", "_RESCUE_REFUSED", "_REJECTED", "_LAST_FAIL", "_LEDGER_SENDS", "_DUPS",
         "_CITIZEN_SENT", "_CITIZEN_RATE", "_CITIZEN_TOKENS", "_CANCELS_SENT", "_ONCE", "_GATES", "_SENT", "_REPS",
         "_SORRIED", "_VERIFIED", "_ASKED", "_FILED", "_REFUSAL_AT")


def reset(role):
    os.environ["CITY_ROLE"] = role
    for name in STATE:
        table = getattr(brain, name, None)
        if table is not None:
            table.clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]  # no rescue sweep in these turns


async def turn(role, text, need, **kw):
    reset(role)
    city = City(ME[role], **kw)
    row = msg(CZ, city.agent_id, text, need)
    city.rows.append(row)
    city.inbound = row
    city.t0 = time.monotonic()
    error = None
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=row), city), 45)
    except OutOfScope:
        pass
    except BaseException as exc:  # noqa: BLE001 - the bug is exactly an exception leaving the turn
        error = f"{type(exc).__name__}: {exc}"
    await asyncio.sleep(0.2)
    if role == "booking_centre" and kw.get("ledger_delay"):
        # A desk search turn ends after ~1 s; the offers (or the honest no-slot line) come from
        # the background resume once the late Ledger lines land. Wait up to 15 s for that real
        # answer before stopping the Ledger: no real answer in 15 s is still a FAIL (agreed with 84).
        stop = time.monotonic() + 15.0
        while not real(city) and time.monotonic() < stop:
            await asyncio.sleep(0.25)
    for t in city.tasks:
        t.cancel()
    return city, error


def real(city):
    return [t for to, t in city.out if to == CZ and not brain._is_hold_line(t)
            and not brain._is_hold_row(Message(type="chat", sender=city.agent_id, recipient=CZ, msg_id="x",
                                               need_id="x", conversation_id="x", body={"text": t},
                                               sent_at=""), city.agent_id)]


def show(city):
    for to, text in city.out:
        print("     ->", to, "|", text[:150].replace("\n", " | "))


class Recorder:
    """Unit check of _say: a dropped citizen line is reported as not sent."""

    address = ""

    def __init__(self):
        self.agent_id, self.out, self.inbound = DESK, [], None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "ok"


async def main():
    results = []

    def check(label, ok, city=None, error=None):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + label + (f"  [{error}]" if error else ""))
        if city is not None and (VERBOSE or not ok):
            show(city)

    book = "Buongiorno, vorrei prenotare la visita cardiologica prescription_number=prescription_number-LE-1"
    fac_text = "Buongiorno, sono arrivato per la visita, avete l'ascensore?"
    # 1. Desk, live 000176: holding line first (slow Ledger), then the offers in the same turn.
    for label, kw in (("slow Ledger", {}), ("slow Ledger + ConnectError first read",
                                            {"read_fail": ERRS["ConnectError"]})):
        city, error = await turn("booking_centre", book, f"N-desk-{label[:6]}-{SEQ[0]}", ledger_delay=4.0, **kw)
        told = [t for to, t in city.out if to == CZ]
        fast = city.first_line is not None and city.first_line <= 3.5
        check(f"desk {label}: a line within 3 s", error is None and bool(told) and fast, city, error)
        check(f"desk {label}: a real answer after the holding line", bool(real(city)), city, error)
    # 1b. Desk: a second message of the same need while the Ledger is slow. Its holding line is
    # the same text as the first one, so the guard drops it: the message must still get a line.
    reset("booking_centre")
    city = City(DESK, ledger_delay=6.0)
    need = f"N-desk-two-{SEQ[0]}"
    first = msg(CZ, DESK, "Buongiorno, vorrei prenotare una visita, ho mal di schiena da settimane", need)
    city.rows.append(first)
    city.inbound, city.t0 = first, time.monotonic()
    with_err = None
    try:
        await asyncio.wait_for(brain.main(Need(summary=first.text, message=first), city), 45)
        n_before = len(city.out)
        second = msg(CZ, DESK, "Buongiorno, vorrei prenotare una visita, ho mal di schiena da settimane", need)
        city.rows.append(second)
        city.inbound = second
        await asyncio.wait_for(brain.main(Need(summary=second.text, message=second), city), 45)
    except OutOfScope:
        n_before = len(city.out)
    except BaseException as exc:  # noqa: BLE001
        with_err, n_before = f"{type(exc).__name__}: {exc}", len(city.out)
    for t in city.tasks:
        t.cancel()
    told2 = [t for to, t in city.out[n_before:] if to == CZ]
    check("desk second message, slow Ledger: it gets a line", with_err is None and bool(told2), city, with_err)
    # 2. Every role: a transport error on the first read or on the first citizen send.
    for role in ("booking_centre", "hospital", "specialist_care"):
        text = book if role == "booking_centre" else fac_text
        for name, err in ERRS.items():
            for where in ("read", "say"):
                kw = {"read_fail": err} if where == "read" else {"say_fail": err}
                city, error = await turn(role, text, f"N-{role}-{name}-{where}-{SEQ[0]}", **kw)
                told = [t for to, t in city.out if to == CZ and not t.startswith(brain._CRASH_NOTE[:30])]
                check(f"{role} {name} on first {where}: citizen told (not the crash note)",
                      error is None and bool(told), city, error)
    # 3. _say: a line the spam / duplicate guard drops is reported as not sent (False).
    reset("booking_centre")
    rec = Recorder()
    rec.inbound = msg(CZ, DESK, "ciao", "N-say")
    first = await brain._say(rec, CZ, "Grazie, ne tengo conto.")
    second = await brain._say(rec, CZ, "Grazie, ne tengo conto.")
    check(f"_say dup: first True ({first}), dropped repeat False ({second}), one send ({len(rec.out)})",
          first is True and second is False and len(rec.out) == 1)
    tagged = "Ricevuto, la richiesta di annullamento." + brain._REFUSAL_TAG
    a = await brain._say(rec, CZ, tagged)
    b = await brain._say(rec, CZ, tagged.replace("Ricevuto", "Ok"))
    check(f"_say refusal burst: first True ({a}), dropped second False ({b})", a is True and b is False)
    # 4. S11: a first history read that raises a non-transport error: the citizen still gets a line.
    for role in ("booking_centre", "hospital", "specialist_care"):
        text = book if role == "booking_centre" else fac_text
        for name, err in (("ValueError", lambda: ValueError("bad json")), ("RuntimeError", lambda: RuntimeError("x")),
                          ("KeyError", lambda: KeyError("rows"))):
            city, error = await turn(role, text, f"N-{role}-{name}-s11-{SEQ[0]}", read_fail=err)
            await asyncio.sleep(3.0)
            told = [t for to, t in city.out if to == CZ]
            check(f"{role} S11 {name} on first read: citizen told ({len(told)})", bool(told), city, error)
    print(f"{sum(results)}/{len(results)} passed")
    print("OK test_live_errors_shared" if all(results) else "FAIL test_live_errors_shared")
    raise SystemExit(0 if all(results) else 1)


asyncio.run(main())
