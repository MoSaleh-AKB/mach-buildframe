"""Hold life: a facility whose Ledger answers `held` and `committed` later than the confirm turn AND
later than the short follow still books (commit + receipt) while the hold lives.
Run: <city python> -I tests/test_hold_life.py asst/_shared/agent.py   (exit 1 on FAIL)
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, CapError, Message, Need

ROLE = "hospital"
spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_shared/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for name, value in (("_ADVANCE_BUDGET", 0.4), ("_PAGE_BUDGET", 0.2), ("_CONSENT_WAIT", 0.3), ("_CONFIRM_BUDGET", 0.5),
                    ("_FOLLOW_FOR", 1.0), ("_HOLD_FOLLOW_FOR", 25.0), ("_FOLLOW_PAUSE", 0.2)):
    setattr(brain, name, value)

ME = f"agent:t07/{ROLE}"
BC = "agent:t07/booking_centre"
CZ = "citizen:t07/c1"
NOW = "2026-11-23T08:00:00+00:00"
PROC = "VIS-FIS"
N, NEED = [0], [600]


def new_need():
    NEED[0] += 1
    return f"N-HL-{NEED[0]}"


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"hl{N[0]}", need_id=need, body={"text": text}, recipient=to)


def world():
    base = f"facility={ME} procedure={PROC} free=1"
    return ("slots page=1/1\n"
            f"slot_id=S2 doctor_id=D-h1 at=2026-11-25T09:00:00+00:00 day=wed {base}")


class City:
    """Fake city: hold and commit answers arrive ``delay`` seconds late, as a task (the live Ledger)."""

    def __init__(self, first, rows, delay):
        self.agent_id, self.inbound, self.address = ME, first, ""
        self.rows, self.out, self.delay = [*rows, first], [], delay
        self.rx = {"RX1": f"prescription prescription_number=RX1 procedure={PROC} priority=B state=issued"}
        for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_FOLLOWING"):
            getattr(brain, name, {}).clear()

    def _ledger_answer(self, text):
        head = text.split()[0] if text.split() else ""
        f = brain.fields_of(text)
        if head == "prescription":
            row = self.rx.get(f.get("prescription_number", ""))
            return [row] if row else []
        if head == "agenda":
            return [f"agenda agenda_now={NOW}"]
        if head == "world":
            return [world()]
        if head == "hold":
            return [f"held hold_id=h-{f.get('slot_id')} slot_id={f.get('slot_id')} fence=1"]
        if head == "commit":
            hid = f.get("hold_id", "")
            return [f"committed booking_id={hid} ledger_ref=c-1 hold_id={hid} slot_id={hid[2:]}"]
        return []

    async def _late(self, text, delay):
        await asyncio.sleep(delay)
        for ans in self._ledger_answer(text):
            self.rows.append(msg(LEDGER, ME, ans, self.inbound.need_id))

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(ME, to, text, self.inbound.need_id))
        if to == LEDGER:
            if text.split()[0] in ("hold", "commit"):
                asyncio.get_running_loop().create_task(self._late(text, self.delay))
            else:
                for ans in self._ledger_answer(text):
                    self.rows.append(msg(LEDGER, ME, ans, self.inbound.need_id))
        return "x"

    async def find(self, query):
        raise CapError("UPSTREAM_TIMEOUT", "index")

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    def to(self, who):
        return [t for w, t in self.out if w == who]


async def drive(city):
    os.environ["CITY_ROLE"] = ROLE
    try:
        await brain.main(Need(summary=city.inbound.text, message=city.inbound), city)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    finally:
        os.environ.pop("CITY_ROLE", None)
    return city


FAILS = []


def check(name, ok, c):
    print(("PASS " if ok else "FAIL ") + f"[{ROLE}] {name}")
    if not ok:
        FAILS.append(name)
        for to, t in c.out:
            print(f"     > {to[-26:]} : {t[:160]!r}")


async def late_case(delay, label):
    need = new_need()
    text = (f"confirm offer_id=of-S2 slot_id=S2 prescription_number=RX1 priority=B citizen={CZ} "
            f"consent_ref=cons-{need}-booking facility={ME}")
    pre = [msg(ME, LEDGER, f"world procedure={PROC} facility={ME} page=1", need), msg(LEDGER, ME, world(), need)]
    c = City(msg(BC, ME, text, need), pre, delay)
    await drive(c)
    for _ in range(int((delay * 2 + 8) * 5)):  # the turn is over: only the background follow can finish it
        if any("booking.receipt" in t for _, t in c.out):
            break
        await asyncio.sleep(0.2)
    commit = any(t.startswith("commit") for t in c.to(LEDGER))
    receipt = any("booking.receipt" in t for _, t in c.out)
    check(f"{label}: late held ({delay:.0f} s, past the 1 s follow) is committed and receipted", commit and receipt, c)


async def main():
    await late_case(3.0, "slow Ledger")
    await late_case(6.0, "very slow Ledger")
    print(f"{len(FAILS)} FAIL")  # magic_pass.py reads "0 FAIL" as clean
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
