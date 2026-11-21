"""L6 (citizen reports the cancel first) and L7 (two confirms, one booking), end to end.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_l6_double_confirm.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
RX = "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=B state=issued"


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


def slots():
    rows = []
    for i in range(1, 7):
        at = (BASE + timedelta(hours=7 + i)).isoformat()
        rows.append(f"slot_id=S{i} doctor_id=asst-centro-h1 at={at} day=mon facility={HO} procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


def offer(i):
    at = (BASE + timedelta(hours=7 + i)).isoformat()
    return (f"offer offer_id=of-S{i} slot_id=S{i} doctor_id=asst-centro-h1 facility={HO} procedure=VIS-FIS "
            f"agenda_date={at} expiry={at} prescription_number=prescription_number-N-0001")


class City:
    def __init__(self, rows):
        self.agent_id, self.address, self.rows, self.out, self.inbound = BC, "", list(rows), [], None

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, need))
        if to == LEDGER and text.split()[0] in ("agenda", "world"):
            body = f"agenda agenda_now={BASE.isoformat()}" if text.split()[0] == "agenda" else slots()
            self.rows.append(msg(LEDGER, BC, body, need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset():
    brain.role_of = lambda: "booking_centre"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT"):
        getattr(brain, name).clear()
    brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, row):
    city.rows.append(row)
    city.inbound = row
    view = SimpleNamespace(inbound=row, agent_id=BC, address="", find=city.find, history=city.history,
                           say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)


def base(need, offers=(1, 2)):
    return [
        msg(CZ, BC, "Vorrei una visita.", need, ago=300),
        msg(LEDGER, BC, RX, need, ago=290),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", need, ago=290),
        msg(LEDGER, BC, slots(), need, ago=290),
        msg(BC, CZ, "\n".join(offer(i) for i in offers), need, ago=250),
    ]


def booked(need):
    return [
        msg(CZ, BC, "confirm consent_ref=cons-N offer_id=of-S1", need, ago=200),
        msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=cons-N facility={HO}", need, ago=199),
        msg(LEDGER, BC, "held slot_id=S1 hold_id=bk-1", need, ago=198),
        msg(LEDGER, BC, "committed booking_id=bk-1 ledger_ref=lr-1", need, ago=197),
        msg(BC, CZ, "booking.receipt booking_id=bk-1 ledger_ref=lr-1\nconfermata", need, ago=196),
    ]


def to_cz(city):
    return [t for to, t in city.out if to == CZ]


def conf_out(city):
    return [t for to, t in city.out if to == HO and t.startswith("confirm")]


def verdict(label, ok, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok and city is not None:
        for to, text in city.out:
            print("    ->", to, text[:150].replace("\n", " | "))
    return ok


async def main():
    oks = []
    # L6a: the citizen reports the cancel before the desk heard of it: rebook now, one cancel line.
    reset()
    need = "N-61"
    city = City(base(need) + booked(need))
    await turn(city, msg(CZ, BC, "cancel booking_id=bk-1 reason=fault.cancel", need))
    out = to_cz(city)
    oks.append(verdict("L6a citizen-reported cancel rebooks (new offers, no old receipt)",
                       any("offer_id=of-S" in t for t in out) and not any(t.startswith("booking.receipt") for t in out), city))
    # L6b: the desk already cancelled and rebooked; the citizen asks again: new offers, never the dead receipt.
    reset()
    need = "N-62"
    rows = base(need) + booked(need) + [
        msg(BC, CZ, "cancel booking_id=bk-1 reason=facility_cancel", need, ago=100),
        msg(BC, CZ, offer(3) + "\n" + offer(4), need, ago=99),
    ]
    city = City(rows)
    await turn(city, msg(CZ, BC, "COME CANCELLATA?? la mia prenotazione è stata annullata", need))
    out = to_cz(city)
    oks.append(verdict("L6b second ask shows the new offers, not the dead receipt",
                       bool(out) and "of-S3" in out[-1] and not out[-1].startswith("booking.receipt"), city))
    oks.append(verdict("L6b _receipt_sent ignores a receipt we cancelled afterwards", brain._receipt_sent(rows, BC) == ""))
    reset()
    city = City(rows)
    await turn(city, msg(CZ, BC, "qual è la mia prenotazione?", need))
    oks.append(verdict("L6c 'la mia prenotazione' never re-shows a cancelled receipt",
                       not any(t.startswith("booking.receipt") and "bk-1" in t for t in to_cz(city)), city))
    # L7a: a confirm for a SECOND offer while the first is in flight at our own facility: one hold only.
    reset()
    need = "N-71"
    rows = base(need) + [
        msg(CZ, BC, "confirm consent_ref=cons-N offer_id=of-S1", need, ago=50),
        msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=cons-N facility={HO}", need, ago=49),
    ]
    city = City(rows)
    await turn(city, msg(CZ, BC, "confirm consent_ref=cons-N offer_id=of-S2", need))
    oks.append(verdict("L7a second offer's confirm is not forwarded", not conf_out(city) and len(to_cz(city)) == 1, city))
    # L7b: the same confirm twice: no second forward, the citizen is answered once.
    reset()
    city = City(rows)
    await turn(city, msg(CZ, BC, "confirm consent_ref=cons-N offer_id=of-S1", need))
    oks.append(verdict("L7b repeated confirm answered, not forwarded again", not conf_out(city) and len(to_cz(city)) == 1, city))
    # L7c: first confirm is forwarded; after the facility refuses it, a new confirm still goes out.
    reset()
    need = "N-73"
    city = City(base(need))
    await turn(city, msg(CZ, BC, "confirm consent_ref=cons-N offer_id=of-S1", need))
    oks.append(verdict("L7c first confirm forwarded once", len(conf_out(city)) == 1, city))
    reset()
    need = "N-74"
    rows = base(need) + [
        msg(CZ, BC, "confirm consent_ref=cons-N offer_id=of-S1", need, ago=60),
        msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} consent_ref=cons-N facility={HO}", need, ago=59),
        msg(HO, BC, "cancel offer_id=of-S1 reason=facility_cancelled", need, ago=30),
    ]
    city = City(rows)
    await turn(city, msg(CZ, BC, "confirm consent_ref=cons-N offer_id=of-S2", need))
    oks.append(verdict("L7c after a refusal the next confirm is forwarded", len(conf_out(city)) == 1, city))
    print("ALL OK" if all(oks) else "SOME FAILED")
    raise SystemExit(0 if all(oks) else 1)


asyncio.run(main())
