"""S17 (P1) renewal leg at the booking desk: the citizen comes back in the SAME case with the new
prescription number from the GP (live C3: the desk kept reading the old expired Ledger line and
only said "Sto lavorando alla sua richiesta. Messaggio 2 ricevuto", no lookup, no offer).

Fake City only. Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_b23_renewed_rx.py [brain.py]
Cases (each in the free-text form AND the ``data.response prescription_number=RX2`` form):
  1. instant Ledger: the desk sends ``prescription prescription_number=RX2``, offers name RX2, no
     second renewal request
  2. slow Ledger 8 s and 20 s on the renewal leg: a visible line within 3 s, then ONE offer message on RX2
  3. confirm of an RX2 offer: the confirm to the hospital carries RX2, never the expired RX1
FAST=1 skips the slow-Ledger cases. Exit 1 on any FAIL.
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message, Need

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1"
NOW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
EXP = "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=expired"
NEW = "prescription prescription_number=RX2 procedure=VIS-FIS priority=B state=issued"
RX = {"RX1": EXP, "RX2": NEW}
SLOTS = "slots page=1/1\n" + "\n".join(
    f"slot_id=S{i} doctor_id=D-h{i} at={(NOW + timedelta(hours=6 + i)).isoformat()} day=mon "
    f"facility={HO} procedure=VIS-FIS free=1"
    for i in range(1, 5)
)
FORMS = {
    "free text": "need.request lang=it prescription_number=RX2 Ecco la nuova ricetta rinnovata dal medico.",
    "data.response": "data.response prescription_number=RX2",
}
SEQ, NEED = [0], [0]
FAILS: list[str] = []


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    """One desk case. The Ledger answers after ``delay`` s; a late answer opens a Ledger turn."""

    def __init__(self, need, rows, delay=0.0):
        self.agent_id, self.address, self.need = BC, "", need
        self.rows, self.out, self.delay, self.inbound = list(rows), [], delay, None
        self.t0 = time.monotonic()

    def answer(self, text):
        head = text.split()[0] if text.split() else ""
        f = brain.fields_of(text)
        if head == "prescription":
            row = RX.get(f.get("prescription_number", ""))
            return [row] if row else []
        return {"agenda": [f"agenda agenda_now={NOW.isoformat()}"], "world": [SLOTS]}.get(head, [])

    async def say(self, to, text, *, status=None, need=None):
        self.out.append((round(time.monotonic() - self.t0, 1), to, text))
        self.rows.append(msg(BC, to, text, self.need))
        if to == LEDGER:
            if self.delay:
                asyncio.get_running_loop().create_task(self.late(text))
            else:
                self.rows += [msg(LEDGER, BC, a, self.need) for a in self.answer(text)]
        return "ok"

    async def late(self, text):
        await asyncio.sleep(self.delay)
        for a in self.answer(text):
            row = msg(LEDGER, BC, a, self.need)
            self.rows.append(row)
            asyncio.get_running_loop().create_task(turn(self, row))

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    def to(self, who):
        return [t for _, w, t in self.out if w == who]


async def turn(city, row):
    os.environ["CITY_ROLE"] = "booking_centre"
    city.inbound = row
    try:
        await brain.main(Need(summary=row.text, message=row), city)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise


def renewal_case(back_text, delay=0.0):
    """First leg done: RX1 expired on the Ledger, renewal asked. Then the citizen comes back."""

    NEED[0] += 1
    need = f"N-b23-{NEED[0]}"
    for store in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_GATES"):
        getattr(brain, store, {}).clear()
    rows = [
        msg(CZ, BC, "need.request lang=it prescription_number=RX1 Buongiorno, vorrei prenotare la visita.", need),
        msg(BC, LEDGER, "prescription prescription_number=RX1", need),
        msg(BC, LEDGER, "agenda", need),
        msg(BC, LEDGER, "world procedure=VIS-FIS page=1", need),
        msg(LEDGER, BC, f"agenda agenda_now={NOW.isoformat()}", need),
        msg(LEDGER, BC, EXP, need),
        msg(LEDGER, BC, SLOTS, need),
        msg(BC, CZ, brain._RENEW, need),
    ]
    back = msg(CZ, BC, back_text, need)
    city = City(need, [*rows, back], delay)
    return city, back


def offer_lines(city):
    return [ln for t in city.to(CZ) for ln in t.splitlines() if ln.startswith("offer") and "slot_id=" in ln]


def check(name, ok, city):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)
        for at, to, t in city.out:
            print(f"     {at:5.1f} > {to[-16:]} : {t[:150]!r}")


async def fast(form, text):
    city, back = renewal_case(text)
    await turn(city, back)
    looked = any(t.startswith("prescription prescription_number=RX2") for t in city.to(LEDGER))
    check(f"[{form}] new number RX2 looked up on the Ledger", looked, city)
    offers = offer_lines(city)
    check(f"[{form}] offers sent, all on RX2", bool(offers) and all("prescription_number=RX2" in ln for ln in offers), city)
    check(f"[{form}] no second renewal request", not any(
        t.startswith("data.request purpose=prescription_renewal") for t in city.to(CZ)), city)
    return city, offers


async def slow(form, text, delay):
    city, back = renewal_case(text, delay)
    await turn(city, back)
    end = time.monotonic() + delay * 2 + 10
    while time.monotonic() < end and not offer_lines(city):
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.0)
    first = next((at for at, w, _ in city.out if w == CZ), None)
    check(f"[{form}] slow Ledger {delay:.0f} s: first citizen line within 3 s (got {first})",
          first is not None and first <= 3.0, city)
    offers = offer_lines(city)
    check(f"[{form}] slow Ledger {delay:.0f} s: offers on RX2", bool(offers) and all(
        "prescription_number=RX2" in ln for ln in offers), city)
    sends = [t for t in city.to(CZ) if any(ln.startswith("offer") and "slot_id=" in ln for ln in t.splitlines())]
    check(f"[{form}] slow Ledger {delay:.0f} s: one offer message (got {len(sends)})", len(sends) == 1, city)
    check(f"[{form}] slow Ledger {delay:.0f} s: no second renewal request", not any(
        t.startswith("data.request purpose=prescription_renewal") for t in city.to(CZ)), city)


async def confirm_on_rx2():
    city, offers = await fast("confirm", FORMS["free text"])
    if not offers:
        check("confirm on RX2 offer carries RX2 (no offer to confirm)", False, city)
        return
    offer_id = brain.fields_of(offers[0])["offer_id"]
    conf = msg(CZ, BC, f"confirm offer_id={offer_id} consent_ref=cons-b23-booking", city.need)
    city.rows.append(conf)
    try:
        await asyncio.wait_for(turn(city, conf), 30)
    except asyncio.TimeoutError:
        pass
    to_ho = [t for t in city.to(HO) if t.startswith("confirm")]
    check("confirm to the hospital carries RX2, never the expired RX1",
          bool(to_ho) and "prescription_number=RX2" in to_ho[0] and "RX1" not in to_ho[0], city)


async def main():
    for form, text in FORMS.items():
        await fast(form, text)
    await confirm_on_rx2()
    for delay in () if os.environ.get("FAST") else (8.0, 20.0):
        for form, text in FORMS.items():
            await slow(form, text, delay)
    print("FAILED" if FAILS else "OK", len(FAILS))
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
