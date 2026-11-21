"""Red-flag corpus gate (L8): every RED item escalates in every live role, through brain.main on a fake City.

Run: <city python> -I tests/test_redflag_corpus.py asst/_shared/agent.py [roles,csv] [plain|mid]
Corpus: tests/redflag_corpus.json (RECONSTRUCTION: test_gp_redflags RED/CALM at brain-gp f7231db, the tia/pb
probe lists, the escalated_on_red_flag personas, zero-width variants, the L8 rf tester's 56 + 10 cases;
MoSaleh's rf/run.py 91 phrases were not found). Escalated = a line to the citizen that starts escalate.emergency.
FAILS on any missed RED item, and on a CALM false alarm that is not in KNOWN_FALSE_ALARMS (stories, other
people, past events, a reference naming the sign, hyperbole: pre-existing, tracked separately).
"""
import asyncio, importlib.util, json, os, sys
from pathlib import Path

from city_sdk.city import LEDGER, Message, Need

ROLES = (sys.argv[2] if len(sys.argv) > 2 else "gp,family_counseling,booking_centre,hospital,specialist_care").split(",")
CTX = sys.argv[3] if len(sys.argv) > 3 else "plain"
os.environ["CITY_ROLE"] = ROLES[0]
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for k, v in (("_ADVANCE_BUDGET", 0.6), ("_PULL_BUDGET", 0.8), ("_AGENT_BUDGET", 0.6), ("_CONSENT_WAIT", 0.3),
             ("_XA_BUDGET", 0.5), ("_XA_GRACE", 0.2), ("_VERIFY_BUDGET", 0.3), ("_LLM_NET", False)):
    if hasattr(brain, k):
        setattr(brain, k, v)
# + CALM-040 ('sto soffocando dal caldo'): MR !97's _concept_life, pre-existing on sina-root-fixes.
KNOWN_FALSE_ALARMS = set(['test_gp_redflags@f7231db:CALM-040', 'X/tia/rf.py:F-087', 'X/tia/rf.py:F-088', 'X/tia/rf.py:F-089', 'X/tia/rf.py:F-105', 'l8rf-c01', 'l8rf-c02', 'l8rf-c04', 'l8rf-c09', 'l8rf-c10', 'test_gp_redflags@f7231db:CALM-041', 'test_gp_redflags@f7231db:CALM-042', 'test_gp_redflags@f7231db:CALM-043', 'test_gp_redflags@f7231db:CALM-044', 'test_gp_redflags@f7231db:CALM-045', 'test_gp_redflags@f7231db:CALM-046', 'test_gp_redflags@f7231db:CALM-047', 'test_gp_redflags@f7231db:CALM-048', 'test_gp_redflags@f7231db:CALM-053', 'test_gp_redflags@f7231db:CALM-056', 'test_gp_redflags@f7231db:CALM-057'])

LIVE = {"gp": "agent:jmqwr12vy2j6", "booking_centre": "agent:y6hxsd766sys", "hospital": "agent:r8yprrs0e8be",
        "specialist_care": "agent:dqfw9nh15dn4", "family_counseling": "agent:pv3a0t3tyqdq"}
PROC = {"hospital": "VIS-FIS", "specialist_care": "VIS-CARD", "family_counseling": "CONS-GIN", "booking_centre": "VIS-FIS", "gp": "VIS-FIS"}
OPEN = {"gp": "Buongiorno, ho mal di schiena da tre giorni.", "family_counseling": "Buongiorno, vorrei una visita ginecologica.",
        "booking_centre": "Buongiorno, vorrei prenotare la visita, prescription_number=RX-OPEN-1.",
        "hospital": "checkin booking_id=bk-open-1", "specialist_care": "checkin booking_id=bk-open-1"}
REF = {"gp": "prescription_number=RX-OPEN-1", "family_counseling": "prescription_number=RX-OPEN-1",
       "booking_centre": "prescription_number=RX-OPEN-1", "hospital": "checkin booking_id=bk-open-1",
       "specialist_care": "checkin booking_id=bk-open-1"}
N = [0]


def msg(sender, to, text, need, mtype="chat"):
    N[0] += 1
    return Message(type=mtype, sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    def __init__(self, role, need, cz):
        self.role, self.agent_id, self.need, self.address, self.cz = role, LIVE[role], need, "", cz
        self.rows, self.out, self.inbound, self.n = [], [], None, 0
        self.bk, self.holds = {"bk-open-1": cz, "bk-1": cz}, {}

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, self.need))
        if to == LEDGER:
            ans = self.ledger(text)
            if ans:
                self.rows.append(msg(LEDGER, self.agent_id, ans, self.need))
        return "x"

    def ledger(self, text):
        w = text.split()
        op, f = w[0], dict(t.split("=", 1) for t in w[1:] if "=" in t)
        if op == "agenda":
            return "agenda agenda_now=2026-11-23T08:00:00+00:00"
        if op == "world":
            proc = f.get("procedure", PROC[self.role])
            fac = LIVE["hospital"] if proc == "VIS-FIS" else LIVE["specialist_care"] if proc == "VIS-CARD" else LIVE["family_counseling"]
            return "slots page=1/1\n" + "\n".join(
                f"slot_id=S{i} doctor_id=asst-centro-h1 at=2026-11-2{3 + i}T09:00:00+00:00 day=x facility={fac} procedure={proc} free=1" for i in (1, 2, 3))
        if op == "prescription":  # every number is the citizen's own, issued, priority B
            num = f.get("prescription_number", "")
            return f"prescription prescription_number={num} patient={self.cz} procedure={PROC[self.role]} priority=B state=issued"
        if op == "hold":
            self.n += 1
            hid = f"h-{self.n}"
            self.holds[hid] = (f.get("slot_id", ""), f.get("citizen", ""))
            return f"held hold_id={hid} slot_id={f.get('slot_id', '')} fence=1"
        if op == "commit":
            hid = f.get("hold_id", "")
            sid, cz = self.holds.get(hid, ("", ""))
            self.bk[hid] = cz
            return f"committed booking_id={hid} ledger_ref=c-{hid} slot_id={sid}"
        if op == "checkin":
            return f"checked_in booking_id={f.get('booking_id', '')}"
        if op == "report":
            return f"reported booking_id={f.get('booking_id', '')}"
        if op == "cancel":
            return "cancelled " + " ".join(t for t in w[1:] if "=" in t)
        if op == "consent":
            return "consented " + " ".join(t for t in w[1:] if "=" in t)
        return ""

    async def find(self, q):
        return []

    async def history(self, peer=None, *, need_id=None):
        if peer is not None:
            return [r for r in self.rows if peer in (r.sender, r.recipient)]
        return list(self.rows)


async def turn(c, sender, text):
    m = msg(sender, c.agent_id, text, c.need)
    c.rows.append(m)
    c.inbound = m
    s = len(c.out)
    err = ""
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=m), c), 20)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            err = f"{type(exc).__name__}: {exc}"[:160]
    return c.out[s:], err


async def play(item, ctx, role):
    for cache in ("_VERIFIED", "_XA_REP", "_REJECTED"):
        if isinstance(getattr(brain, cache, None), dict):
            getattr(brain, cache).clear()
    os.environ["CITY_ROLE"] = role
    N[0] += 1
    cz = f"citizen:rf-{N[0]}"
    c = City(role, f"N-rf-{N[0]}", cz)
    turns = list(item["turns"])
    if ctx == "mid":
        turns = [OPEN[role]] + turns
    elif ctx == "ref":
        turns[-1] = REF[role] + " " + turns[-1]
    said, errs = [], []
    for t in turns:
        o, e = await turn(c, cz, t)
        said += o
        if e:
            errs.append(e)
    esc = any(to == cz and text.lstrip().startswith("escalate.emergency") for to, text in said)
    first = next((text.splitlines()[0][:90] for to, text in said if to == cz), "(no reply)")
    return esc, first, errs


async def main():
    here = Path(__file__).resolve().parent
    items = json.loads((here / "redflag_corpus.json").read_text())
    # Items whose line carries prescription_number= or booking_id= (a red flag inside a booking or a
    # check-in) come with the _wide change, in their own file.
    if (here / "redflag_corpus_ref.json").exists():
        items += json.loads((here / "redflag_corpus_ref.json").read_text())
    bad = 0
    for role in ROLES:
        miss, fa = [], []
        for it in items:
            esc, first, errs = await play(it, CTX, role)
            if it["escalate"] and not esc:
                miss.append(it)
            elif esc and not it["escalate"] and it["id"] not in KNOWN_FALSE_ALARMS:
                fa.append(it)
        for it in miss:
            print(f"MISSED {role:17} {it['id']:40} {' || '.join(it['turns'])[:90]}")
        for it in fa:
            print(f"FALSE_ALARM {role:12} {it['id']:40} {' || '.join(it['turns'])[:90]}")
        print(f"{role}: red={sum(i['escalate'] for i in items)} missed={len(miss)} new_false_alarms={len(fa)}")
        bad += len(miss) + len(fa)
    print("FAILED" if bad else "OK", bad)
    sys.exit(1 if bad else 0)


asyncio.run(main())
