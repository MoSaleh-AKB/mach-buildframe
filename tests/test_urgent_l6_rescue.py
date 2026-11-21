"""L6 urgent rescue: a promised alternative is kept, the rescue sees hold-only cases, no cancel replay loop.

(a) a facility cancel with an empty world: the citizen gets a line after the cancel line;
(b) a case whose last row is our own "Sto lavorando" line is picked by the rescue sweep;
(c) three cancels in one second after an empty set: one reply, no "Messaggio 4 ricevuto", no old offers.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_urgent_l6_rescue.py asst/_shared/agent.py
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
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", os.path.abspath(path))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ = "agent:t07/booking_centre", "agent:t07/hospital", "citizen:t07/c1"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
SEQ = [0]
RX = "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=B state=issued"
HOLD_LINE = ("Sto lavorando alla sua richiesta. Messaggio 2 ricevuto: mi dica se vuole qualcosa di diverso "
             "(giorno, orario, lingua del medico) e agisco subito.​")


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


class City:
    """A fake city whose world is empty: no slot at all."""

    def __init__(self, me, rows):
        self.agent_id, self.address, self.rows, self.out, self.inbound = me, "", list(rows), [], None

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        if to != LEDGER:
            self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER:
            head = text.split()[0]
            body = {"agenda": f"agenda agenda_now={BASE.isoformat()}", "world": "slots page=1/1"}.get(head)
            if body:
                self.rows.append(msg(LEDGER, self.agent_id, body, need))
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
    for name in ("_REFUSAL_AT", "_PENDING_SEARCH", "_RESCUE_TRIED"):
        if hasattr(brain, name):
            getattr(brain, name).clear()
    brain._RESCUE_LAST[:] = [time.monotonic()]


async def turn(city, row):
    city.rows.append(row)
    city.inbound = row
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find,
                           history=city.history, say=lambda to, text, *, status=None: city.say(to, text, need=row.need_id))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 40)


def empty_case(need, age=60):
    at = (BASE + timedelta(hours=30)).isoformat()
    return [
        msg(CZ, BC, "Vorrei una visita.", need, ago=age + 30),
        msg(LEDGER, BC, RX, need, ago=age + 20),
        msg(LEDGER, BC, f"agenda agenda_now={BASE.isoformat()}", need, ago=age + 20),
        msg(LEDGER, BC, "slots page=1/1", need, ago=age + 20),
        msg(BC, CZ, f"offer offer_id=wl-{need}-alt waiting_list=true\nlista d'attesa", need, ago=age + 10),
        msg(BC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=asst-centro-h1 facility={HO} procedure=VIS-FIS "
            f"agenda_date={at} expiry={at} prescription_number=prescription_number-N-0001", need, ago=age),
    ]


async def main():
    oks = []

    def verdict(label, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + label + (f" {detail}" if detail else ""))
        oks.append(ok)

    # (a) facility cancel, empty world: a line after the cancel line
    reset()
    city = City(BC, empty_case("N-A"))
    await turn(city, msg(HO, BC, "cancel offer_id=of-S1 reason=facility_cancelled", "N-A"))
    lines = [t for to, t in city.out if to == CZ]
    after = [t for t in lines if not t.startswith("cancel ")]
    verdict("(a) facility cancel, empty world: citizen gets a follow-up line", len(lines) >= 2 and bool(after),
            f"({len(lines)} lines)")

    # (b) rescue sees a case whose last row is our own holding line
    reset()
    rows = [msg(CZ, BC, "Vorrei una visita.", "N-B", ago=3010), msg(BC, CZ, HOLD_LINE, "N-B", ago=3000)]
    current = msg(CZ, BC, "altro", "N-OTHER")
    picks = brain._rescue_picks(rows, BC, current, datetime.now(timezone.utc))
    verdict("(b) hold-only case 3000 s old is picked by rescue", len(picks) == 1 and picks[0][1].need_id == "N-B",
            f"({len(picks)} picks)")

    # (c) three cancels in one second after an empty set
    reset()
    city = City(BC, empty_case("N-C"))
    replies = []
    for oid in ("wl-N-C-alt", "wl-N-C-alt", "wl-N-C"):
        before = len(city.out)
        await turn(city, msg(CZ, BC, f"cancel offer_id={oid} reason=citizen_declined", "N-C"))
        replies += [t for to, t in city.out[before:] if to == CZ]
    # #125: the one reply may point in prose at the still-open out-of-window offer (confirm + consent_ref);
    # what must never come back is an offer card line, a slot_id, or the holding counter.
    bad = [t for t in replies if "Messaggio" in t or "Message 4" in t or "slot_id=" in t
           or any(ln.startswith("offer ") for ln in t.splitlines())]
    verdict("(c) three cancels in one second: one reply, no replay", len(replies) == 1 and not bad,
            f"({len(replies)} replies)")
    if not oks[-1]:
        for t in replies:
            print("   ", t[:160].replace("\n", " | "))

    print("ALL OK" if all(oks) else "SOME FAILED")
    raise SystemExit(0 if all(oks) else 1)


asyncio.run(main())
