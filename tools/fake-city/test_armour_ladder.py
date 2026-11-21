"""Booking desk armour: no-fit ladder, instant re-offer after a cancel, attempt-aware path.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/test_armour_ladder.py   (exit 1 on any FAIL)
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
    # 1. English speaker, no English slot: honest nearest slot + interpreter note, never a false match.
    reset()
    c = City("N-1", DOCS)
    r1 = await turn(c, msg(CZ, BC, "I need a physiatrist. " + NEED_RX % "en", "N-1"))
    check("offer_id=of-S1" in r1 or "offer_id=of-" in r1, "1 English speaker with no English slot still gets a slot offer", r1)
    check("interpreter" in r1.lower() and "not confirmed" in r1.lower() or "cannot guarantee" in r1.lower(),
          "1 the note says plainly the doctor is not a match and offers an interpreter", r1)
    check("waiting_list" not in r1.split("offer_id=of-")[0], "1 not a waiting list only", r1)

    # 3. Returning citizen: each further turn takes a different path, no repeat.
    texts = [r1]
    for i in range(2, 6):
        texts.append(await turn(c, msg(CZ, BC, f"any news? ({i})", "N-1")))
    body = [t.strip() for t in texts[1:] if t.strip()]
    check(len(body) >= 2 and len(set(body)) == len(body), "3 returning turns never repeat the same reply", " | ".join(texts[1:]))
    check(any("widen" in t.lower() or "other days" in t.lower() or "altri giorni" in t.lower() for t in body),
          "3 a later attempt asks once to widen the window", " | ".join(body))
    check(sum(("widen" in t.lower()) for t in body) <= 1, "3 widen is asked only once", " | ".join(body))

    # 2. Cancel of a held offer: new offer in the SAME reply as the cancel notice.
    reset()
    c = City("N-2", ("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"))
    first = await turn(c, msg(CZ, BC, "I need a physiatrist. " + NEED_RX % "en", "N-2"))
    check("offer_id=of-" in first, "2 setup: first offers sent", first)
    cancel = await turn(c, msg(LEDGER, BC, "fault.cancel offer_id=of-S1 slot_id=S1", "N-2"))
    check("cancel offer_id=of-S1" in cancel and "offer_id=of-S" in cancel.split("cancel offer_id=of-S1", 1)[1],
          "2 a cancelled offer is answered with a cancel notice AND a fresh offer at once", cancel)
    print("FAIL" if FAILS else "OK", len(FAILS), "fail")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
