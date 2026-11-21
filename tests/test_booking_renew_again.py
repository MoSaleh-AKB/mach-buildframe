"""S17: the citizen writes again after the desk asked for the prescription renewal.

Within 120 s the duplicate guard drops a second identical renewal text, and the citizen used to
get the false "Sto lavorando alla sua richiesta". The desk must answer with a different short
line that names the gp and asks for the new prescription_number (English when the citizen writes
English), and still book on the new number afterwards. One process, one need, the guard live.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_booking_renew_again.py [asst/_shared/agent.py]
Exit 1 on any FAIL.
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET, brain._PAGE_BUDGET, brain._CONSENT_WAIT = 0.4, 0.2, 0.3

BC = "agent:t07/booking_centre"
HO = "agent:t07/hospital"
CZ = "citizen:t07/c1"
NOW = "2026-11-23T08:00:00+00:00"
RX = {
    "RX1": "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=expired",
    "RX2": "prescription prescription_number=RX2 procedure=VIS-FIS priority=B state=issued",
}
SLOTS = (
    "slots page=1/1\n"
    f"slot_id=S1 doctor_id=D-h1 at=2026-11-23T14:00:00+00:00 day=mon facility={HO} procedure=VIS-FIS free=1\n"
    f"slot_id=S2 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue facility={HO} procedure=VIS-FIS free=1"
)
N = [0]
NEED = [700]
FAILS: list[str] = []


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    """Fake city with a synchronous Ledger; rows persist across turns (one live process, one need)."""

    def __init__(self, need):
        self.agent_id = BC
        self.need = need
        self.rows: list[Message] = []
        self.out: list[tuple[str, str]] = []
        self.inbound = None

    def _answer(self, text):
        head = text.split()[0] if text.split() else ""
        f = brain.fields_of(text)
        if head == "prescription":
            row = RX.get(f.get("prescription_number", ""))
            return [row] if row else []
        if head == "agenda":
            return [f"agenda agenda_now={NOW}"]
        if head == "world":
            return [SLOTS]
        return []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER:
            for ans in self._answer(text):
                self.rows.append(msg(LEDGER, BC, ans, self.need))
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def turn(self, text):
        m = msg(CZ, BC, text, self.need)
        self.rows.append(m)
        self.inbound = m
        self.out = []
        os.environ["CITY_ROLE"] = "booking_centre"
        try:
            try:
                await brain.main(Need(summary=text, message=m), self)
            except Exception as exc:
                if type(exc).__name__ != "OutOfScope":
                    raise
        finally:
            os.environ.pop("CITY_ROLE", None)
        return [t for w, t in self.out if w == CZ]


def check(name, ok, said):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)
        for t in said:
            print(f"     > {t[:170]!r}")


WORKING = ("sto lavorando", "i am working on your request")


async def journey(first, second, third, en):
    city = City(f"N-{NEED[0] + 1:04d}")
    NEED[0] += 1
    tag = f"[{'en' if en else 'it'}: {second[:20]!r}]"
    t1 = await city.turn(first)
    check(f"{tag} t1 expired RX1 -> renewal asked", any("prescription_renewal" in t for t in t1), t1)
    t2 = await city.turn(second)
    check(f"{tag} t2 repeat -> a visible line", bool(t2), t2)
    check(f"{tag} t2 repeat -> no false 'working on your request'",
          not any(w in t.lower() for t in t2 for w in WORKING), t2)
    gp = ("family doctor", "gp") if en else ("medico di base",)
    check(f"{tag} t2 repeat -> names the gp and asks for the new prescription_number",
          any("prescription_number" in t and any(g in t.lower() for g in gp) for t in t2), t2)
    check(f"{tag} t2 repeat -> {'English' if en else 'Italian'} line",
          any(("prescription" in t and "need" in t) if en else ("ricetta" in t) for t in t2), t2)
    t3 = await city.turn(third)
    check(f"{tag} t3 new number -> offers on RX2",
          any(ln.startswith("offer") and "prescription_number=RX2" in ln for t in t3 for ln in t.splitlines()), t3)


async def main():
    await journey("prenota prescription_number=RX1 lang=it", "non ho capito, cosa devo fare?",
                  "data.response prescription_number=RX2", en=False)
    await journey("prenota prescription_number=RX1 lang=it", "prenota prescription_number=RX1",
                  "ecco la nuova ricetta prescription_number=RX2", en=False)
    await journey("please book prescription_number=RX1 lang=en", "I don't understand, what do I need to do?",
                  "data.response prescription_number=RX2", en=True)
    print(f"RESULT {'FAIL' if FAILS else 'PASS'} {len(FAILS)} failed")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
