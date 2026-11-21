"""gp + family_counseling: a dropped relay connection never ends a citizen turn without a line.

A non-timeout transport error (httpx ConnectError, ReadError, RemoteProtocolError) on the turn's
first history read or on a send to the citizen must still give the citizen a visible line, and a
red flag must still reach them as the escalation (112). A send that a guard drops (the same cancel
line twice) must not be reported as sent.

Run: <city python> -I tests/gp/test_live_errors_gp.py asst/_gp/agent.py [-v]
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
    HERE, "..", "..", "asst", "_gp", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
VERBOSE = "-v" in sys.argv

CZ = "citizen:t07/c-le"
ME = {"gp": "agent:t07/gp", "family_counseling": "agent:t07/family_counseling"}
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
FAILS = {
    "ConnectError": lambda: httpx.ConnectError("relay down"),
    "ReadError": lambda: httpx.ReadError("relay reset"),
    "RemoteProtocolError": lambda: httpx.RemoteProtocolError("relay closed the stream"),
}
TEXTS = {
    "gp": "Buongiorno dottore, ho mal di schiena da tre giorni",
    "family_counseling": "Buongiorno, vorrei prenotare una visita ginecologica al consultorio",
}
RED = "Ho un dolore fortissimo al petto e non riesco a respirare"
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"le{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    """The Ledger answers at once; the first history read or the first citizen send fails as told."""

    address = ""

    def __init__(self, me, fail, where):
        self.agent_id, self.fail, self.where = me, fail, where
        self.rows, self.out, self.inbound = [], [], None
        self.reads = self.cz_sends = 0

    async def say(self, to, text, *, status=None):
        need = self.inbound.need_id
        if to.startswith("citizen:"):
            self.cz_sends += 1
            if self.where == "say" and self.cz_sends == 1:
                raise self.fail()
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            head = text.split()[0]
            answer = {
                "agenda": f"agenda agenda_now={BASE.isoformat()}",
                "world": "slots page=1/1\n" + "\n".join(
                    f"slot_id=S{i} doctor_id=D-h1 at={(BASE + timedelta(days=1, hours=i)).isoformat()} day=tue "
                    f"facility={self.agent_id} procedure=VIS-CARD free=1" for i in range(1, 4)),
                "prescription": "prescription prescription_number=prescription_number-LE-1 "
                                "procedure=VIS-CARD priority=D state=issued",
            }.get(head)
            if answer:
                self.rows.append(msg(LEDGER, self.agent_id, answer, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        self.reads += 1
        if self.where == "read" and self.reads == 1:
            raise self.fail()
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


STATE = ("_LOCKS", "_HELD", "_RESCUED", "_RESCUE_REFUSED", "_REJECTED", "_LAST_FAIL", "_LEDGER_SENDS", "_DUPS",
         "_CITIZEN_SENT", "_CITIZEN_RATE", "_CANCELS_SENT", "_ONCE", "_GATES", "_SENT", "_REPS", "_SORRIED",
         "_VERIFIED", "_ASKED", "_FILED", "_R5_TURNS", "_CONS_HELD", "_COMMITS")


def reset(role):
    os.environ["CITY_ROLE"] = role
    for name in STATE:
        table = getattr(brain, name, None)
        if table is not None:
            table.clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]  # no rescue sweep in these turns


async def turn(role, fail, where, text, need):
    reset(role)
    city = City(ME[role], FAILS[fail], where)
    row = msg(CZ, city.agent_id, text, need)
    city.rows.append(row)
    city.inbound = row
    error = None
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=row), city), 45)
    except OutOfScope:
        pass
    except BaseException as exc:  # noqa: BLE001 - the bug is exactly an exception leaving the turn
        error = f"{type(exc).__name__}: {exc}"
    return city, error


def show(city):
    for to, text in city.out:
        print("     ->", to, "|", text[:150].replace("\n", " | "))


async def main():
    results = []

    def check(label, ok, city=None, error=None):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + label + (f"  [{error}]" if error else ""))
        if city is not None and (VERBOSE or not ok):
            show(city)

    for role in ("gp", "family_counseling"):
        for fail in FAILS:
            for where in ("read", "say"):
                tag = f"{role} {fail} on the first {where}:"
                city, error = await turn(role, fail, where, TEXTS[role], f"N-{role}-{fail}-{where}-c")
                told = [t for to, t in city.out if to == CZ]
                check(f"{tag} citizen told", error is None and bool(told), city, error)
                city, error = await turn(role, fail, where, RED, f"N-{role}-{fail}-{where}-r")
                esc = [t for to, t in city.out if to == CZ and "escalate.emergency" in t and "112" in t]
                check(f"{tag} red flag escalated", error is None and bool(esc), city, error)

    # A guard drops the line: _say must not report it as sent.
    for role in ("gp", "family_counseling"):
        reset(role)
        row = msg(CZ, ME[role], "va bene", f"N-{role}-guard")
        city = City(ME[role], FAILS["ConnectError"], "none")
        city.rows.append(row)
        city.inbound = row
        line = "cancel offer_id=of-1 reason=facility_cancelled\nMi dispiace, quella data non è più valida."
        first = await brain._say(city, CZ, line)
        second = await brain._say(city, CZ, line)
        sent = [t for to, t in city.out if to == CZ]
        check(f"{role} duplicate cancel line: first sent, second reported as not sent",
              first is True and second is False and len(sent) == 1, city)

    # S11: a first history read that raises a non-transport error: the citizen still gets a line.
    for role in ("gp", "family_counseling"):
        for name, err in (("ValueError", lambda: ValueError("bad json")), ("RuntimeError", lambda: RuntimeError("x")),
                          ("KeyError", lambda: KeyError("rows"))):
            FAILS[name] = err
            city, error = await turn(role, name, "read", TEXTS[role], f"N-{role}-{name}-s11")
            await asyncio.sleep(3.0)
            told = [t for to, t in city.out if to == CZ]
            check(f"{role} S11 {name} on first read: citizen told ({len(told)})", bool(told), city, error)
            del FAILS[name]

    # S13: a Ledger refusal with no op= ("refused hold_id=H1 code=RATE_LIMITED") ends the commit wait.
    reset("gp")
    city = City(ME["gp"], FAILS["ConnectError"], "none")
    city.inbound = msg(CZ, ME["gp"], "x", "N-gp-bare")
    bare = msg(LEDGER, ME["gp"], "refused hold_id=H1 code=RATE_LIMITED", "N-gp-bare")
    city.rows.append(bare)
    t0 = time.monotonic()
    try:
        got = await brain._await_line(city, "N-gp-bare", [], "committed", "booking_id=H1", op="commit", budget=3.0)
    except Exception as exc:  # noqa: BLE001
        got = exc
    check(f"gp _await_line: bare commit refusal matched ({time.monotonic() - t0:.1f}s)",
          isinstance(got, tuple) and got[0].msg_id == bare.msg_id)
    print(f"{sum(results)}/{len(results)} passed")
    print("OK test_live_errors_gp" if all(results) else "FAIL test_live_errors_gp")
    raise SystemExit(0 if all(results) else 1)


asyncio.run(main())
