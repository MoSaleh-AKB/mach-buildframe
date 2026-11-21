"""Cancel/rebook loop: new slot after fault.cancel, declined slot never back, explicit stop closed, 3rd cycle to ladder.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/test_cancel_loop.py   (exit 1 on any FAIL)
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
spec = importlib.util.spec_from_file_location("brain", os.path.join(HERE, "..", "..", "asst", "booking_centre", "agent", "agent.py"))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC, CZ, FAC = "agent:t07/booking_centre", "citizen:t07/c1", "agent:t07/hospital"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAILS: list[str] = []
DOCS = ("asst-centro-h2", "asst-centro-h2", "asst-centro-h2")  # h2 speaks it only
NEED_RX = "need.request area={'district':_6} lang=%s prescription_number=prescription_number-N-0001"


def agenda_now():
    return (NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()


def slots_text(docs):
    base = (NOW + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[base.weekday()]
    rows = [f"slot_id=S{i} doctor_id={d} at={(base + timedelta(hours=3 * (i - 1))).isoformat()} day={day} "
            f"facility={FAC} procedure=VIS-FIS free=1" for i, d in enumerate(docs, start=1)]
    return "slots page=1/1\n" + "\n".join(rows)


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self, need, docs):
        self.agent_id, self.address, self.need, self.docs = BC, "", need, docs
        self.rows: list[Message] = []
        self.out: list[tuple[str, str]] = []
        self.tasks: list[asyncio.Task] = []

    async def say(self, to, text, *, status=None, need=None):
        if to == LEDGER:
            head = text.split()[0]
            if head in ("prescription", "agenda", "world"):
                self.tasks.append(asyncio.create_task(self._ledger(head, text)))
        else:
            self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, self.need))
        return "ok"

    async def _ledger(self, head, text):
        await asyncio.sleep(0.05)
        if head == "prescription":
            n = text.split("prescription_number=")[-1].split()[0]
            body = f"prescription prescription_number={n} procedure=VIS-FIS priority=B state=issued"
        elif head == "agenda":
            body = f"agenda agenda_now={agenda_now()}"
        else:
            body = slots_text(self.docs)
        self.rows.append(msg(LEDGER, self.agent_id, body, self.need))

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SORRIED"):
        getattr(brain, name, {}).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST.clear()
        brain._RESCUE_LAST.append(time.monotonic())
    sent = getattr(brain, "_SENT", None)
    if hasattr(sent, "clear"):
        sent.clear()
    if hasattr(brain, "_XA"):
        brain._XA.clear()


async def turn(city, row):
    city.rows.append(row)
    city.out_mark = len(city.out)
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), View(city, row)), 30)
    await asyncio.sleep(0.2)
    return " ".join(t for to, t in city.out[city.out_mark:] if to == CZ)


def check(ok, label, detail=""):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        if detail:
            print("      " + detail[:600])


async def main():
    import re
    def offers(t):
        return re.findall(r"offer_id=(of-\S+)", t)

    # 1. fault.cancel of S1 (doctor h1): the new offer is a different slot, other doctor first.
    reset()
    c = City("N-1", ("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"))
    first = await turn(c, msg(CZ, BC, "I need a physiatrist. " + NEED_RX % "en", "N-1"))
    check("offer_id=of-S1" in first, "1 setup: S1 offered", first)
    out = await turn(c, msg(LEDGER, BC, "fault.cancel offer_id=of-S1 slot_id=S1", "N-1"))
    new = out.split("cancel offer_id=of-S1", 1)[-1]
    check("of-S1" not in new and offers(new), "1 after fault.cancel the new offer is a different slot", out)

    # 2. A declined slot never comes back.
    reset()
    c = City("N-2", ("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"))
    await turn(c, msg(CZ, BC, "I need a physiatrist. " + NEED_RX % "en", "N-2"))
    r = await turn(c, msg(CZ, BC, "cancel offer_id=of-S1 reason=declined", "N-2"))
    r2 = await turn(c, msg(CZ, BC, "cancel offer_id=of-S2 reason=declined", "N-2"))
    check(r.strip() != "" and r2.strip() != "", "2 every reply is non-silent", r + " | " + r2)
    check("of-S1" not in r + r2 and "of-S2" not in r2, "2 declined slots are never offered again", r + " | " + r2)

    # 3. Explicit stop is honoured: closed kindly, no offers.
    reset()
    c = City("N-3", ("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"))
    await turn(c, msg(CZ, BC, "Mi serve una visita. " + NEED_RX % "it", "N-3"))
    r = await turn(c, msg(CZ, BC, "Non mi serve più la visita, lascia stare.", "N-3"))
    check(r.strip() != "" and "offer_id=" not in r and "annullo" in r.lower(), "3 explicit stop closes kindly with no offers", r)

    # 4. Third cancel cycle: ladder (widen/close), not the same offers.
    reset()
    c = City("N-4", ("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"))
    await turn(c, msg(CZ, BC, "I need a physiatrist. " + NEED_RX % "en", "N-4"))
    outs = []
    for oid in ("of-S1", "of-S2", "of-S3"):
        outs.append(await turn(c, msg(CZ, BC, f"cancel offer_id={oid} reason=declined", "N-4")))
    third = outs[2]
    check(third.strip() != "" and not any(o in third for o in ("of-S1", "of-S2", "of-S3")),
          "4 third cycle repeats no offer", third)
    check(any(w in third.lower() for w in ("widen", "allargare", "partner", "interpreter", "next step", "prossimo passo", "consent")),
          "4 third cycle goes to the ladder", third)

    # 5. Facility-side cancel of a BOOKED slot (receipt sent): citizen told AND a different slot offered in the same reply.
    reset()
    c = City("N-5", ("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"))
    await turn(c, msg(CZ, BC, "I need a physiatrist. " + NEED_RX % "en", "N-5"))
    c.rows.append(msg(CZ, BC, "confirm offer_id=of-S1 consent_ref=cons-1", "N-5"))
    c.rows.append(msg(BC, FAC, "confirm offer_id=of-S1 slot_id=S1 consent_ref=cons-1", "N-5"))
    c.rows.append(msg(BC, CZ, "booking.receipt booking_id=bk-1 slot_id=S1 offer_id=of-S1", "N-5"))
    out = await turn(c, msg(FAC, BC, "cancel booking_id=bk-1 slot_id=S1 reason=facility_cancelled", "N-5"))
    check("cancel booking_id=bk-1" in out or "cancel offer_id=of-S1" in out, "5 facility cancel is told to the citizen", out)
    tail = out.split("alternativ", 1)[-1] if "alternativ" in out else out
    check("offer_id=of-S2" in out and "offer_id=of-S1" not in out.split("offer_id=of-S2")[-1],
          "5 facility cancel re-offers a different slot in the same reply", out)
    print("FAIL" if FAILS else "OK", len(FAILS), "fail")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
