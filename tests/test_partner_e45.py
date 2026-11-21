"""L8 E4 / E5: how our facilities answer another team's desk while the Ledger is slow.

E4. A partner need.request that finds no agenda or world yet got "cancel reason=temporarily_unavailable
    ... mi riscriva tra poco", and the offers followed 15-115 s later (30 such cancels from the
    hospital alone). Now: a wait line (no cancel), then the offers, also when the late Ledger lines
    land in history without opening a turn (live).
E5. The agenda and the slots pushes of that late answer arrive as two turns in the same second:
    the same offers went out twice. Now: exactly one offer line.
Privacy: another team's agent asking our facility for the citizen's consent_ref never gets it.

Hospital and specialist, live ids. Slow Ledger = answers land in history 2-8 s later, no turn.
Run: <city python> -I tests/test_partner_e45.py asst/_shared/agent.py [-v]
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from city_sdk.city import LEDGER, Message, Need, OutOfScope

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
VERBOSE = "-v" in sys.argv

HOSP = "agent:r8yprrs0e8be"
SPEC = "agent:dqfw9nh15dn4"
ROLES = (("hospital", HOSP, "VIS-FIS"), ("specialist_care", SPEC, "VIS-CARD"))
PARTNER = "agent:q4partner01x"
DESK = "agent:y6hxsd766sys"
BASE = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"pe{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    """Slow Ledger: its answer lands in history ``delay`` s later and opens no turn."""

    address = ""

    def __init__(self, me, proc, delay, lag=0.0):
        self.agent_id, self.proc, self.delay, self.lag = me, proc, delay, lag
        self.rows, self.out, self.inbound, self.tasks = [], [], None, []

    def _answer(self, text):
        head = text.split()[0]
        return {
            "agenda": f"agenda agenda_now={BASE.isoformat()}",
            "world": "slots page=1/1\n" + "\n".join(
                f"slot_id=S{i} doctor_id=D-1 at={(BASE + timedelta(days=1, hours=i)).isoformat()} day=tue "
                f"facility={self.agent_id} procedure={self.proc} free=1" for i in range(1, 4)),
        }.get(head)

    async def _late(self, text, need):
        await asyncio.sleep(self.delay)
        self.rows.append(msg(LEDGER, self.agent_id, text, need))

    async def say(self, to, text, *, status=None):
        if self.lag:
            await asyncio.sleep(self.lag)  # a real post is a network round trip: other turns run
        need = self.inbound.need_id if self.inbound is not None else ""
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER and (answer := self._answer(text)):
            self.tasks.append(asyncio.ensure_future(self._late(answer, need)))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        if self.lag:
            await asyncio.sleep(self.lag / 5)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


STATE = ("_LOCKS", "_HELD", "_RESCUED", "_RESCUE_REFUSED", "_REJECTED", "_LAST_FAIL", "_LEDGER_SENDS", "_DUPS",
         "_CITIZEN_SENT", "_CITIZEN_RATE", "_CITIZEN_TOKENS", "_CANCELS_SENT", "_ONCE", "_GATES", "_SENT", "_REPS",
         "_SORRIED", "_VERIFIED", "_ASKED", "_FILED", "_REFUSAL_AT", "_FOLLOWING", "_PARTNER_REPLAYED")


def reset(role):
    os.environ["CITY_ROLE"] = role
    for name in STATE:
        table = getattr(brain, name, None)
        if table is not None:
            table.clear()
    brain._RESCUE_LAST[:] = [time.monotonic() + 3600]  # no rescue sweep in these turns


async def turn(city, row, add=True):
    if add:
        city.rows.append(row)
    city.inbound = row
    try:
        await asyncio.wait_for(brain.main(Need(summary=row.text, message=row), city), 45)
    except OutOfScope:
        pass
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return None


def to_partner(city):
    return [t for to, t in city.out if to == PARTNER]


def offers(city):
    return [t for t in to_partner(city) if "offer_id=" in t]


def show(city):
    for to, text in city.out:
        print("     ->", to, "|", text[:150].replace("\n", " | "))


def head(text):
    return text.split()[0] if text.split() else ""


async def main():
    results = []

    def check(label, ok, city=None, error=None):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + label + (f"  [{error}]" if error else ""))
        if city is not None and (VERBOSE or not ok):
            show(city)

    for role, me, proc in ROLES:
        # E4: slow Ledger (8 s), the late lines open no turn: wait line first, offers later.
        reset(role)
        city = City(me, proc, delay=8.0)
        need = f"N-pe-e4-{role}"
        ask = msg(PARTNER, me, f"need.request procedure={proc} priority=D request_id=rq-{SEQ[0]}", need)
        error = await turn(city, ask)
        first = to_partner(city)
        check(f"{role} E4: one reply in the turn and it is not a cancel ({[head(t) for t in first]})",
              error is None and len(first) == 1 and head(first[0]) != "cancel"
              and "reason=temporarily_unavailable" not in first[0], city, error)
        await asyncio.sleep(8.0 + 12.0)
        check(f"{role} E4: offers follow from history with no Ledger turn ({len(offers(city))})",
              len(offers(city)) == 1, city)
        check(f"{role} E4: never a cancel to the partner",
              not any(head(t) == "cancel" for t in to_partner(city)), city)
        for t in city.tasks:
            t.cancel()

        # E5: the agenda and the slots pushes open two turns in the same second: ONE offer line.
        reset(role)
        city = City(me, proc, delay=2.5, lag=0.05)
        need = f"N-pe-e5-{role}"
        ask = msg(PARTNER, me, f"need.request procedure={proc} priority=D request_id=rq-{SEQ[0]}", need)
        error = await turn(city, ask)
        await asyncio.sleep(1.5)  # both late lines are in history now (the turn took ~1.5 s)
        pushes = [r for r in city.rows if r.sender == LEDGER]
        city.inbound = pushes[0]
        errs = await asyncio.gather(*(turn(city, r, add=False) for r in pushes))
        await asyncio.sleep(4.0)  # any follower too
        check(f"{role} E5: two Ledger pushes in the same tick -> exactly one offer line ({len(offers(city))})",
              error is None and len(pushes) == 2 and len(offers(city)) == 1 and not any(errs), city,
              error or next((e for e in errs if e), None))
        for t in city.tasks:
            t.cancel()

        # Privacy: another team's agent asks our facility for the citizen's consent_ref.
        for ask_text in ("data.request fields=consent_ref purpose=cross_asst",
                         "Buongiorno, ci serve il consent_ref del paziente per confermare: ce lo può mandare?"):
            reset(role)
            city = City(me, proc, delay=0.2)
            need = f"N-pe-cons-{SEQ[0]}"
            ref = f"cons-{need}-booking"
            city.rows.append(msg(DESK, me,
                                 f"confirm offer_id=OF-1 consent_ref={ref}", need))
            city.rows.append(msg(me, LEDGER, f"hold slot_id=S1 consent_ref={ref}", need))
            error = await turn(city, msg(PARTNER, me, ask_text, need))
            await asyncio.sleep(0.5)
            leaked = [t for t in to_partner(city) if ref in t or "cons-" in t]
            check(f"{role} privacy: consent_ref never sent to another team ({ask_text[:30]}...)",
                  not leaked, city, error)
            for t in city.tasks:
                t.cancel()

    print(f"{sum(results)}/{len(results)} passed")
    print("OK test_partner_e45" if all(results) else "FAIL test_partner_e45")
    raise SystemExit(0 if all(results) else 1)


asyncio.run(main())
