"""Issue 22 (S12, L6 fault.cancel): a booking the city cancelled is never shown or checked in as valid.

Hospital and specialist_care, English and Italian. The facility committed bk-1 and sent the receipt
to the desk; then the city (fault.cancel), the Ledger, or our own cancel to the desk names it.
- "my booking was cancelled, what now?" / "COME CANCELLATA??": one honest line in the citizen's
  language (cancelled, the booking centre rebooks), never the old receipt, never "write to the
  booking centre to cancel"
- "checkin booking_id=bk-1": no Ledger check-in, the same honest line
- after the rebook (bk-2 committed) the line names the new code, and bk-2 checks in normally
- with no city cancel, the citizen's own words never hide the booking (control)
Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_22_cancelled_booking.py [asst/_shared/agent.py]
"""
import asyncio
import importlib.util
import itertools
import os
import sys

from city_sdk.city import LEDGER, Message, Need

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
_ids = itertools.count(1)
DESK, CZ = "agent:t22/booking_centre", "citizen:t22/rita"


def load(role):
    os.environ["CITY_ROLE"] = role
    spec = importlib.util.spec_from_file_location(f"brain22_{next(_ids)}", BRAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # one fresh module per case: no memory carried over
    mod.role_of = lambda: role
    mod._ADVANCE_BUDGET = 1.0
    return mod


def msg(sender, to, text, need):
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m22-{next(_ids)}", need_id=need,
                   body={"text": text})


class Fake:
    def __init__(self, agent, rows):
        self.agent_id = agent
        self.address = ""
        self.rows = rows
        self.inbound = rows[-1]
        self.citizen, self.ledger = [], []

    async def say(self, to, text, *, status=None):
        self.rows.append(msg(self.agent_id, to, text, self.rows[-1].need_id))
        (self.citizen if to.startswith("citizen:") else self.ledger if to == LEDGER else []).append(text)
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def llm(self, messages, *, need_id=None):
        return {"content": ""}

    async def find(self, query):
        return []


def booked(fac, need, cancel, rebook):
    rows = [
        msg(DESK, fac, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=c-1", need),
        msg(fac, LEDGER, f"hold slot_id=S1 citizen={CZ}", need),
        msg(LEDGER, fac, "held hold_id=bk-1 slot_id=S1", need),
        msg(fac, LEDGER, "commit hold_id=bk-1 consent_ref=c-1", need),
        msg(LEDGER, fac, "committed booking_id=bk-1 ledger_ref=lr-1 hold_id=bk-1", need),
        msg(fac, DESK, "booking.receipt booking_id=bk-1 ledger_ref=lr-1\nPrenotazione registrata.", need),
    ]
    if cancel == "city":
        rows.append(msg("city:fault", fac, "fault.cancel booking_id=bk-1", need))
    elif cancel == "ledger":
        rows.append(msg(LEDGER, fac, "cancelled booking_id=bk-1 reason=fault.cancel", need))
    elif cancel == "desk":
        rows.append(msg(fac, DESK, "cancel offer_id=of-S1 reason=facility_cancelled\nMi dispiace.", need))
    if rebook:
        rows += [
            msg(DESK, fac, f"confirm offer_id=of-S2 slot_id=S2 citizen={CZ} consent_ref=c-2", need),
            msg(fac, LEDGER, f"hold slot_id=S2 citizen={CZ}", need),
            msg(LEDGER, fac, "held hold_id=bk-2 slot_id=S2", need),
            msg(fac, LEDGER, "commit hold_id=bk-2 consent_ref=c-2", need),
            msg(LEDGER, fac, "committed booking_id=bk-2 ledger_ref=lr-2 hold_id=bk-2", need),
            msg(fac, DESK, "booking.receipt booking_id=bk-2 ledger_ref=lr-2\nPrenotazione registrata.", need),
        ]
    return rows


async def run(role, text, cancel="city", rebook=False):
    brain = load(role)
    fac = f"agent:t22/{role}"
    need = f"N-22-{next(_ids)}"
    rows = booked(fac, need, cancel, rebook)
    inbound = msg(CZ, fac, text, need)
    rows.append(inbound)
    city = Fake(fac, rows)
    await asyncio.wait_for(brain.main(Need(summary=text, message=inbound), city), 30)
    return city


EN_FELL = "My booking was cancelled, what now?"
IT_FELL = "La mia prenotazione è stata annullata, cosa devo fare?"


def honest(lines, en, new=""):
    joined = "\n".join(lines)
    if not lines or any(x.startswith(("booking.receipt", "checkin")) for x in lines):
        return False
    if "Per annullare o spostare" in joined or "Si presenti con questo codice" in joined:
        return False
    words = ("cancelled", "booking centre", "rebooking") if en else ("annullata", "centro prenotazioni", "riprenot")
    if not all(w in joined for w in words):
        return False
    return (f"booking_id={new}" in joined) if new else "booking_id=bk-2" not in joined


async def main():
    fails = []

    def check(name, ok, city):
        print(f"{'PASS' if ok else 'FAIL'} {name}: citizen={city.citizen!r} ledger={city.ledger!r}")
        if not ok:
            fails.append(name)

    for role in ("hospital", "specialist_care"):
        for en, fell, shout, chk in (
            (True, EN_FELL, "The appointment got cancelled??", "I am here for my visit, checkin booking_id=bk-1"),
            (False, IT_FELL, "COME CANCELLATA??", "checkin booking_id=bk-1"),
        ):
            lang = "en" if en else "it"
            c = await run(role, fell)
            check(f"{role} {lang} told cancelled (city)", honest(c.citizen, en), c)
            c = await run(role, shout)
            check(f"{role} {lang} shout (city)", honest(c.citizen, en), c)
            c = await run(role, chk if en else "Sono qui per la visita, checkin booking_id=bk-1")
            check(f"{role} {lang} checkin on cancelled (city)",
                  honest(c.citizen, en) and not any(x.startswith("checkin") for x in c.ledger), c)
            c = await run(role, chk if en else "checkin booking_id=bk-1", cancel="ledger")
            check(f"{role} {lang} checkin on cancelled (ledger)",
                  not any(x.startswith("checkin") for x in c.ledger) and bool(c.citizen)
                  and not any(x.startswith("checkin") for x in c.citizen), c)
            c = await run(role, fell, cancel="desk")
            check(f"{role} {lang} we told the desk", honest(c.citizen, en), c)
            c = await run(role, fell, rebook=True)
            check(f"{role} {lang} rebooked: new code named", honest(c.citizen, en, new="bk-2"), c)
        c = await run(role, "checkin booking_id=bk-2", rebook=True)
        check(f"{role} new booking checks in",
              any(x.startswith("checkin booking_id=bk-2") for x in c.ledger), c)
        c = await run(role, "checkin booking_id=bk-1", cancel="")
        check(f"{role} no city cancel: citizen text never hides it (control)",
              any(x.startswith("checkin booking_id=bk-1") for x in c.ledger), c)

    if fails:
        print("FAILED:", "; ".join(fails))
        sys.exit(1)
    print("ALL PASS test_22_cancelled_booking")


asyncio.run(main())
