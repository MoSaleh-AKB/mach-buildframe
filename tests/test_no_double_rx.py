"""L8 C11: a person whose first booking is still committed comes back as a NEW journey (new citizen
id, same prescription_number). The desk must not book a second visit on that prescription.

Fake City only. Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_no_double_rx.py [brain.py]
Cases:
  1. same person (return id): told the visit is already booked, offered to move it, no offers
  2. other citizen id: privacy-safe refusal (no booking id, no data), no offers
  3. old booking cancelled: normal path (the prescription is looked up again)
Exit 1 on any FAIL.
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message, Need

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO = "agent:t07/booking_centre", "agent:t07/hospital"
OLD = "citizen:W01QQQ-04-000165"
RX = "N-W01QQQ-04-890904000165"
NOW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
SLOTS = "slots page=1/1\n" + "\n".join(
    f"slot_id=S{i} doctor_id=D-h{i} at={(NOW + timedelta(hours=2 + i)).isoformat()} day=mon "
    f"facility={HO} procedure=VIS-FIS free=1"
    for i in range(2, 5)
)
SEQ = [0]
FAILS: list[str] = []


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


def old_case(cancelled=False):
    n = "N-old"
    rows = [
        msg(OLD, BC, f"need.request lang=it prescription_number={RX} vorrei prenotare", n),
        msg(BC, OLD, "offer offer_id=of-S1 slot_id=S1 facility=" + HO, n),
        msg(OLD, BC, "confirm offer_id=of-S1 consent_ref=cons-1", n),
        msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={OLD} consent_ref=cons-1 prescription_number={RX}", n),
        msg(HO, BC, "booking.receipt booking_id=bk-2 ledger_ref=lr-3", n),
        msg(BC, OLD, "booking.receipt booking_id=bk-2 ledger_ref=lr-3\nLa sua prenotazione è confermata.", n),
    ]
    if cancelled:
        rows.append(msg(HO, BC, "cancel booking_id=bk-2 reason=facility_cancelled", n))
    return rows


class City:
    def __init__(self, rows):
        self.agent_id, self.address, self.inbound = BC, "", None
        self.rows, self.out = list(rows), []

    async def say(self, to, text, *, status=None, need=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, "N-new"))
        if to == LEDGER:
            head = text.split()[0] if text.split() else ""
            answer = {"agenda": f"agenda agenda_now={NOW.isoformat()}", "world": SLOTS,
                      "prescription": f"prescription prescription_number={RX} procedure=VIS-FIS priority=B state=issued",
                      "rules": "rules version=1"}.get(head)
            if answer:
                self.rows.append(msg(LEDGER, BC, answer, "N-new"))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]


async def run(who, cancelled=False, extra=()):
    for store in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_GATES", "_RX_SEEN", "_RX_CLEAR"):
        getattr(brain, store, {}).clear()
    back = msg(who, BC, f"need.request lang=it prescription_number={RX} Buongiorno, ho la ricetta, vorrei prenotare.", "N-new")
    pre = [msg(*row, "N-new") for row in extra]
    if extra:
        back = msg(who, BC, "confirm offer_id=of-S3 consent_ref=cons-9\nVa bene, confermo.", "N-new")
    city = City([*old_case(cancelled), *pre, back])
    os.environ["CITY_ROLE"] = "booking_centre"
    city.inbound = back
    try:
        await asyncio.wait_for(brain.main(Need(summary=back.text, message=back), city), 8)
    except asyncio.TimeoutError:
        pass
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    return city


def check(name, ok, city):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)
        for to, t in city.out:
            print(f"     > {to[-20:]} : {t[:150]!r}")


def offers(city, who):
    return [t for to, t in city.out if to == who and "offer_id=" in t]


async def main():
    same = "citizen:W01QQQ-04-0003-000165"
    city = await run(same)
    told = " ".join(t for to, t in city.out if to == same)
    check("same person: told already booked (bk-2), offered to move", "bk-2" in told and "spostar" in told, city)
    check("same person: no offers, no hold", not offers(city, same) and not any(to == HO for to, _ in city.out), city)
    other = "citizen:X99ZZZ-01-000777"
    city = await run(other)
    told = " ".join(t for to, t in city.out if to == other)
    check("other id: privacy refusal, no booking id", "privacy" in told and "bk-2" not in told, city)
    check("other id: no offers", not offers(city, other), city)
    back = "citizen:W01QQQ-04-0004-000165"  # one id per case: the desk never repeats a line to one citizen
    city = await run(back, cancelled=True)
    check("old booking cancelled: normal path offers again", bool(offers(city, back)), city)
    same = "citizen:W01QQQ-04-0005-000165"
    # Offers already in the new case (sent before the check existed, or by a rescue): a confirm of
    # one is not forwarded to the facility while the old booking is live.
    offer = f"offer offer_id=of-S3 slot_id=S3 facility={HO} procedure=VIS-FIS prescription_number={RX}"
    pre = [(same, BC, f"need.request lang=it prescription_number={RX} vorrei prenotare"),
           (LEDGER, BC, f"prescription prescription_number={RX} procedure=VIS-FIS priority=B state=issued"),
           (BC, same, offer)]
    city = await run(same, extra=pre)
    check("confirm on the same rx in the new case: nothing sent to the facility",
          not any(to == HO for to, _ in city.out) and any(to == same for to, _ in city.out), city)
    print("FAILED" if FAILS else "OK", len(FAILS))
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
