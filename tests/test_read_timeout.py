"""A relay history read that fails (the SDK's ReadTimeout after its retry, a plain timeout, a
dropped connection) must not end the turn in silence (live 000118, 000103 at the desk).

The citizen still gets a visible line within 3 s and the turn goes on with the inbound text, an
agent sender still gets its answer, and a red flag still escalates. "first" = only the turn's
first read fails (a hiccup), "all" = every history read fails (relay down for reads).

Run: <city python> -I tests/test_read_timeout.py asst/_shared/agent.py [-v]
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

CZ = "citizen:t07/c-rt"
GP = "agent:t07/gp"
DESK = "agent:t07/booking_centre"
ME = {"booking_centre": DESK, "hospital": "agent:t07/hospital", "specialist_care": "agent:t07/specialist"}
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
FAILS = {
    "ReadTimeout": lambda: httpx.ReadTimeout("relay history"),
    "TimeoutError": lambda: TimeoutError("relay history"),
    "OSError": lambda: ConnectionResetError(104, "Connection reset by peer"),
    "ReadError": lambda: httpx.ReadError("relay history"),
}
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"rt{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    """Sends always work and the Ledger answers at once; history reads fail as told."""

    address = ""

    def __init__(self, me, fail, mode):
        self.agent_id, self.fail, self.mode = me, fail, mode
        self.rows, self.out, self.inbound, self.reads = [], [], None, 0
        self.t0, self.first_line = 0.0, None

    async def say(self, to, text, *, status=None):
        need = self.inbound.need_id
        self.out.append((to, text))
        if to.startswith("citizen:") and self.first_line is None:
            self.first_line = time.monotonic() - self.t0
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            head = text.split()[0]
            fac = self.agent_id if not self.agent_id.endswith("booking_centre") else ME["hospital"]
            answer = {
                "agenda": f"agenda agenda_now={BASE.isoformat()}",
                "world": "slots page=1/1\n" + "\n".join(
                    f"slot_id=S{i} doctor_id=D-h1 at={(BASE + timedelta(days=1, hours=i)).isoformat()} day=tue "
                    f"facility={fac} procedure=VIS-CARD free=1" for i in range(1, 4)),
                "prescription": "prescription prescription_number=prescription_number-RT-1 "
                                "procedure=VIS-CARD priority=D state=issued",
            }.get(head)
            if answer:
                self.rows.append(msg(LEDGER, self.agent_id, answer, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        self.reads += 1
        if self.mode == "all" or self.reads == 1:
            raise self.fail()
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


STATE = ("_LOCKS", "_HELD", "_RESCUED", "_RESCUE_REFUSED", "_REJECTED", "_LAST_FAIL", "_LEDGER_SENDS", "_DUPS",
         "_CITIZEN_SENT", "_CITIZEN_RATE", "_CANCELS_SENT", "_ONCE", "_GATES", "_SENT", "_REPS", "_SORRIED",
         "_VERIFIED", "_ASKED", "_FILED")


def reset(role):
    os.environ["CITY_ROLE"] = role
    for name in STATE:
        table = getattr(brain, name, None)
        if table is not None:
            table.clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST[:] = [time.monotonic()]  # no rescue sweep in these turns


async def turn(role, fail, mode, sender, text, need):
    reset(role)
    city = City(ME[role], FAILS[fail], mode)
    row = msg(sender, city.agent_id, text, need)
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
    return city, error


def show(city):
    for to, text in city.out:
        print("     ->", to, "|", text[:150].replace("\n", " | "))


async def main():
    results = []

    def check(label, ok, city, error):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + label + (f"  [{error}]" if error else ""))
        if VERBOSE or not ok:
            show(city)

    for role in ("booking_centre", "hospital", "specialist_care"):
        for fail in FAILS:
            for mode in ("first", "all"):
                tag = f"{role} {fail} {mode}:"
                # 1. Citizen: a visible line within 3 s, and the turn goes on with the inbound text.
                if role == "booking_centre" and mode == "first":
                    text = "Buongiorno, vorrei prenotare la visita cardiologica prescription_number=prescription_number-RT-1"
                elif role == "booking_centre":
                    # Every read failing hides the Ledger answers too: that is the slow-Ledger case
                    # (holding line, slowledger.py). Here the desk must still read the text and ask.
                    text = "Buongiorno, vorrei prenotare una visita cardiologica"
                else:
                    text = "Buongiorno, sono arrivato per la visita, avete l'ascensore?"
                city, error = await turn(role, fail, mode, CZ, text, f"N-{role}-{fail}-{mode}-c")
                told = [t for to, t in city.out if to == CZ]
                fast = city.first_line is not None and city.first_line <= 3.0
                check(f"{tag} citizen told in {city.first_line or 0:.2f} s", error is None and bool(told) and fast,
                      city, error)
                if role == "booking_centre" and mode == "first":
                    used = any(to == LEDGER and "prescription_number-RT-1" in t for to, t in city.out)
                    check(f"{tag} the inbound prescription number reached the Ledger", used, city, error)
                # 2. Agent sender: still gets an answer.
                need_text = "need.request service_type=visit procedure=VIS-CARD priority=D request_id=r-rt"
                sender = GP if role == "booking_centre" else DESK
                city, error = await turn(role, fail, mode, sender, need_text, f"N-{role}-{fail}-{mode}-a")
                answered = [t for to, t in city.out if to == sender]
                check(f"{tag} agent answered", error is None and bool(answered), city, error)
                # 3. Red flag: still escalates (112), within 3 s.
                city, error = await turn(role, fail, mode, CZ, "Ho un dolore fortissimo al petto e non riesco a respirare",
                                         f"N-{role}-{fail}-{mode}-r")
                esc = [t for to, t in city.out if to == CZ and "112" in t]
                fast = city.first_line is not None and city.first_line <= 3.0
                check(f"{tag} red flag escalated", error is None and bool(esc) and fast, city, error)
    print(f"{sum(results)}/{len(results)} passed")
    print("OK test_read_timeout" if all(results) else "FAIL test_read_timeout")
    raise SystemExit(0 if all(results) else 1)


asyncio.run(main())
