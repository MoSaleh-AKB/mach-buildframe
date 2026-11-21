"""Review 2026-11-21 13:15 item 21: a second bad partner confirm after the refusal got silence, and the
impostor cancel named no offer_id. Every refusal now starts `cancel offer_id=<id> reason=...` and a
repeat gets a short repeat line. Cases: forged citizen (impostor path) and a consent that is no
cross_asst ref (P2 path), each confirm sent twice.

Run: <city python> -I tests/test_xa_second_bad_confirm.py asst/_shared/agent.py   (exit 0 = pass)
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for k, v in (("_ADVANCE_BUDGET", 0.6), ("_PULL_BUDGET", 0.8), ("_AGENT_BUDGET", 0.6), ("_CONSENT_WAIT", 0.3),
             ("_XA_BUDGET", 0.5), ("_XA_GRACE", 0.2), ("_VERIFY_BUDGET", 0.3), ("_PAGE_BUDGET", 0.2), ("_LLM_NET", False)):
    if hasattr(brain, k):
        setattr(brain, k, v)


async def _no_weather():
    return {}


brain._weather = _no_weather  # no network in tests
brain._RECEIPT_WAIT_FOR_COMMIT = True  # these cases test the strict mode (live default relays with no Ledger row)

HOSP, DESK = "agent:r8yprrs0e8be", "agent:y6hxsd766sys"
PARTNER, IMPOSTOR = "agent:bq7e62g3kkz3", "agent:zz9impostor"
CZ, VICTIM = "citizen:real-1", "citizen:victim-9"
PROC = "VIS-FIS"
N = [0]
FAILS: list[str] = []


def check(label, ok, out=()):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        for to, text in out:
            print("     >", to[-26:], "|", text[:150].replace("\n", " | "))


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"ih{N[0]}", need_id=need, body={"text": text}, recipient=to)


def reset():
    for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_VERIFIED", "_XA_REP",
                 "_REJECTED", "_ASKED", "_FILED", "_RESCUED", "_HELD", "_GATES", "_SENT"):
        table = getattr(brain, name, None)
        if isinstance(table, (dict, set)):
            table.clear()


# ---------------------------------------------------------------- facility (hospital): #3 and #5

NOW = "2026-11-23T08:00:00+00:00"


def world(reserve=False):
    base = f"facility={HOSP} procedure={PROC} free=1"
    return ("slots page=1/1\n"
            f"slot_id=S1 doctor_id=D-h1 at=2026-11-24T09:00:00+00:00 day=tue {base}"
            f"{' urgent_reserve=true' if reserve else ''}\n"
            f"slot_id=S2 doctor_id=D-h1 at=2026-11-25T09:00:00+00:00 day=wed {base}\n"
            f"slot_id=S3 doctor_id=D-h1 at=2026-11-26T09:00:00+00:00 day=thu {base}")


class Fac:
    """Hospital with an instant Ledger (agenda, world, prescription, hold, commit)."""

    address = ""

    def __init__(self, need, reserve=False, rx=None):
        self.agent_id, self.need, self.reserve, self.rx = HOSP, need, reserve, rx or {}
        self.rows, self.out, self.inbound = [], [], None

    def ledger(self, text):
        head, f = (text.split() or [""])[0], brain.fields_of(text)
        if head == "agenda":
            return [f"agenda agenda_now={NOW}"]
        if head == "world":
            return [world(self.reserve)]
        if head == "prescription":
            row = self.rx.get(f.get("prescription_number", ""))
            return [row] if row else []
        if head == "hold":
            return [f"held hold_id=h-{f.get('slot_id')} slot_id={f.get('slot_id')} fence=1"]
        if head == "commit":
            hid = f.get("hold_id", "")
            return [f"committed booking_id={hid} ledger_ref=c-1 hold_id={hid} slot_id={hid[2:]}"]
        return []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(HOSP, to, text, self.need))
        if to == LEDGER:
            for ans in self.ledger(text):
                self.rows.append(msg(LEDGER, HOSP, ans, self.need))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return list(self.rows)

    async def find(self, query):
        return []

    def writes(self):
        return [t for to, t in self.out if to == LEDGER and t.split()[0] in ("hold", "commit")]


async def turn(c, sender, text):
    m = msg(sender, c.agent_id, text, c.need)
    c.rows.append(m)
    c.inbound = m
    start = len(c.out)
    os.environ["CITY_ROLE"] = "hospital"
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=m), c), 20)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    return c.out[start:]


def first_offer(said):
    lines = [ln for _, t in said for ln in t.splitlines() if ln.startswith("offer ") and "slot_id=" in ln]
    return brain.fields_of(lines[0]) if lines else {}


async def twice(label, need, citizen, consent):
    reset()
    c = Fac(need)
    o = first_offer(await turn(c, PARTNER, f"need.request procedure={PROC} priority=B request_id=r-{need} citizen={CZ}"))
    check(f"{label}: partner got an offer", bool(o))
    if not o:
        return
    text = (f"confirm offer_id={o['offer_id']} slot_id={o['slot_id']} citizen={citizen} "
            f"consent_ref={consent.format(need=need)} facility={HOSP} prescription_number=RX-21")
    for n in (1, 2):
        said = await turn(c, PARTNER, text)
        to_partner = [t for to, t in said if to == PARTNER]
        check(f"{label}: confirm #{n} answered", bool(to_partner), said)
        check(f"{label}: confirm #{n} refusal names offer_id={o['offer_id']}",
              any(t.startswith("cancel ") and f"offer_id={o['offer_id']}" in t.split("\n")[0] for t in to_partner), said)
    check(f"{label}: no hold", not c.writes())


async def main():
    await twice("forged citizen", "n-21a", VICTIM, "cons-{need}-cross_asst")
    await twice("booking consent", "n-21b", CZ, "cons-{need}-booking")
    print(f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
