"""#60 facility down (L7): a 4xx from a facility is never retried, not even after 90 s, and the
citizens who waited on it are resumed ONCE when it is back (the city's order or its first reply).

Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_60_facility_down_resume.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import LEDGER, CapError, Message, Need, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 0.6
brain._XA_BUDGET = 0.5
brain._XA_GRACE = 0.2
brain.role_of = lambda: "booking_centre"

BC, HO, CZ = "agent:t07bc", "agent:t07ho", "citizen:t07-c1"
SEQ = [0]
SHIFT = [0.0]  # seconds added to time.monotonic (the 90 s of the old retry)
_real_monotonic = time.monotonic
brain.time.monotonic = lambda: _real_monotonic() + SHIFT[0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need, body={"text": text},
                   sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))


class City:
    def __init__(self, need):
        for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE", "_SENT",
                     "_HELD", "_XA_NEEDS", "_REPS", "_DOWN_WAIT"):
            g = getattr(brain, name, None)
            if hasattr(g, "clear"):
                g.clear()
        self.agent_id, self.address, self.need, self.inbound = BC, "", need, None
        self.rows, self.out = [], []
        self.down = True

    async def say(self, to, text, *, status=None):
        if to == HO and self.down:
            self.out.append(("refused:" + to, text))
            raise CapError("TARGET_NOT_VISIBLE", "facility down")
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER:
            head = text.split()[0]
            if head == "agenda":
                self.rows.append(msg(LEDGER, BC, "agenda agenda_now=2026-11-23T08:00:00+00:00", self.need))
            elif head == "world":
                self.rows.append(msg(LEDGER, BC, "slots page=1/1\n" + "\n".join(
                    f"slot_id=S{i} doctor_id=asst-centro-h1 at=2026-11-2{3 + i}T09:00:00+00:00 day=x facility={HO} "
                    "procedure=VIS-FIS free=1" for i in (1, 2, 3)), self.need))
            elif head == "prescription" and "prescription_number=RX1" in text.split():
                self.rows.append(msg(LEDGER, BC, "prescription prescription_number=RX1 patient=" + CZ
                                     + " procedure=VIS-FIS priority=B state=issued", self.need))
        return "x"

    async def find(self, query):
        return [Peer(agent_id=HO, role="hospital", capabilities=("hospital.physiatry",))]

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


async def turn(city, sender, text):
    row = msg(sender, BC, text, city.need)
    city.rows.append(row)
    city.inbound = row
    start = len(city.out)
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=row), city), 20)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    await asyncio.sleep(0.3)  # background resumes
    return city.out[start:]


FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {str(detail)[:260]!r}"))
    if not ok:
        FAILS.append(name)


BACK = ("di nuovo raggiungibile", "can be reached again")


def resumed(out):
    return [t for to, t in out if to == CZ and any(b in t for b in BACK)]


def to_ho(out):
    return [t for to, t in out if to.endswith(HO)]


async def no_retry():
    """The same send refused with a 4xx is not sent again 100 s later (old TTL: 90 s)."""

    SHIFT[0] = 0.0
    city = City("N-60-retry")
    city.inbound = msg(CZ, BC, "confirm offer_id=o1", city.need)
    text = "confirm offer_id=o1 slot_id=S1 citizen=" + CZ + " facility=" + HO
    first = await brain._say(city, HO, text)
    check("retry: first send refused", first is False and len(city.out) == 1, city.out)
    SHIFT[0] = 100.0
    again = await brain._say(city, HO, text)
    check("retry: 4xx not retried after 100 s", again is False and len(city.out) == 1, city.out)
    SHIFT[0] = 0.0


async def flow(name, back):
    city = City(f"N-60-{name}")
    first = await turn(city, CZ, "need.request area.district=6 lang=it prescription_number=RX1\nBuongiorno, vorrei prenotare.")
    offers = [ln for to, t in first if to == CZ for ln in t.splitlines() if ln.startswith("offer ")]
    check(f"{name}: offers first", bool(offers), first)
    oid = next((ln.split("offer_id=")[1].split()[0] for ln in offers if "slot_id=" in ln), "")
    got = await turn(city, CZ, f"confirm offer_id={oid} consent_ref=CR1")
    check(f"{name}: confirm tried once at the down facility", len(to_ho(got)) == 1, got)
    check(f"{name}: citizen told the facility is unreachable",
          any("non è raggiungibile" in t or "unreachable" in t for to, t in got if to == CZ), got)
    check(f"{name}: no resume while still down", not resumed(got), got)
    SHIFT[0] = 100.0
    again = await turn(city, CZ, "allora? ci siete ancora?")
    check(f"{name}: no retry of the 4xx confirm after 100 s", not to_ho(again), again)
    city.down = False
    got = await back(city)
    lines = resumed(got)
    check(f"{name}: waiting citizen resumed when the facility is back", len(lines) == 1, got)
    more = await back(city)
    check(f"{name}: resumed once only", not resumed(more), more)
    SHIFT[0] = 0.0


async def order_back(city):
    return await turn(city, "city:ops", f"facility.up facility={HO}")


async def reply_back(city):
    return await turn(city, HO, "status ok\nSiamo di nuovo operativi.")


async def main():
    await no_retry()
    await flow("order", order_back)
    await flow("reply", reply_back)
    print(f"\n{'OK' if not FAILS else 'FAILED'} test_60_facility_down_resume ({len(FAILS)} failing)")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
