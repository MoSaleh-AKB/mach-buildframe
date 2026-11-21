"""S19/S26 at L8: a red flag hidden in a PARTNER agent's message gets escalate.emergency + the 112 line
back to that partner (no Ledger work, no offer, nothing invented to a citizen); a need flagged
is_emergency by an agent escalates on every role; our own agents and harmless partner text
("nessun dolore toracico") keep today's path.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_agent_red_flag.py [asst/_shared/agent.py] [role ...]
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

GP_BRAIN = hasattr(B, "_MATES")  # brain-gp serves gp + family_counseling
ROLES = sys.argv[2:] or (["gp", "family_counseling"] if GP_BRAIN else ["booking_centre", "hospital", "specialist_care"])
OURS = {"booking_centre": "agent:y6hxsd766sys", "gp": "agent:jmqwr12vy2j6", "hospital": "agent:r8yprrs0e8be",
        "specialist_care": "agent:dqfw9nh15dn4", "family_counseling": "agent:pv3a0t3tyqdq"}
P_DESK = "agent:p4rtn3rdesk1"  # another team's booking centre (live-style id, no team part)
P_HOSP = "agent:p4rtn3rhosp1"  # another team's hospital


async def _no_weather():
    return {}


if hasattr(B, "_weather"):
    B._weather = _no_weather

SEQ = [0]


class World:
    def __init__(self, role, me, need):
        self.role, self.me, self.need = role, me, need
        self.proc = "CONS-GIN" if role == "family_counseling" else "VIS-CARD"
        fac = me if role in {"hospital", "specialist_care", "family_counseling"} else OURS["hospital"]
        self.slots = "\n".join(
            f"slot_id=S{i} doctor_id=D-h{i} at=2026-11-2{3 + i // 3}T{9 + i % 3:02d}:00:00+00:00 "
            f"day={['mon', 'tue', 'wed'][i // 3]} facility={fac} procedure={self.proc} free=1" for i in range(1, 6))
        self.rows, self.log, self.tasks, self.errors = [], [], set(), []
        self.peers = [Peer(agent_id=P_DESK, role="booking_centre"), Peer(agent_id=P_HOSP, role="hospital"),
                      Peer(agent_id=me, role=role)]

    def add(self, sender, to, text, body=None):
        SEQ[0] += 1
        m = Message(type="chat", sender=sender, msg_id=f"m{SEQ[0]}", need_id=self.need,
                    body={"text": text, **(body or {})}, recipient=to)
        self.rows.append(m)
        self.log.append((sender, to, text))
        return m

    def answer(self, text):
        w = text.split()
        op = w[0] if w else ""
        f = dict(t.split("=", 1) for t in w if "=" in t)
        if op == "agenda":
            return "agenda agenda_now=2026-11-23T08:00:00+00:00"
        if op == "world":
            return "slots page=1/1\n" + self.slots
        if op == "rules":
            return "rules version=1 active=none"
        if op == "hold":
            return f"held hold_id=h-{f.get('slot_id')} slot_id={f.get('slot_id')} fence=1"
        if op == "prescription":
            return (f"prescription prescription_number={f.get('prescription_number')} procedure={self.proc} "
                    "priority=B state=issued patient=citizen:t99/x")
        return ""

    async def ledger(self, text):
        await asyncio.sleep(0.05)
        ans = self.answer(text)
        if ans:
            await self.turn(self.add(LEDGER, self.me, ans))

    async def turn(self, m, flag=False):
        try:
            await B.main(Need(summary=m.text, message=m, is_emergency=flag), View(self, m))
        except Exception as exc:
            if type(exc).__name__ != "OutOfScope":
                self.errors.append(f"{type(exc).__name__}: {exc}")

    async def settle(self):
        for _ in range(200):
            if not self.tasks:
                return
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def said(self, to=None):
        return [t for s, r, t in self.log if s == self.me and (to is None or r == to)]

    def ops(self):
        return self.said(LEDGER)


class View:
    def __init__(self, w, inbound):
        self.w, self.agent_id, self.inbound, self.address = w, w.me, inbound, ""

    async def say(self, to, text, *, status=None):
        self.w.add(self.w.me, to, text)
        if to == LEDGER:
            t = asyncio.get_running_loop().create_task(self.w.ledger(text))
            self.w.tasks.add(t)
            t.add_done_callback(self.w.tasks.discard)
        return f"m{SEQ[0]}"

    async def find(self, query):
        return list(self.w.peers)

    async def history(self, peer=None, *, need_id=None):
        rows = [r for r in self.w.rows if self.w.me in (r.sender, r.recipient)]
        if need_id:
            rows = [r for r in rows if r.need_id == need_id]
        return rows


def escalated(w, to):
    p = []
    txt = "\n".join(w.said(to=to))
    if "escalate.emergency" not in txt or "112" not in txt:
        p.append("no escalate.emergency + 112 to the sender: " + (txt[:120] or "(nothing)"))
    if "offer_id=" in txt:
        p.append("routine offer despite the red flag")
    if any(o.startswith(("hold", "commit", "world")) for o in w.ops()):
        p.append("Ledger work despite the red flag: " + str(w.ops()[:3]))
    if any(r.startswith("citizen:") for s, r, _ in w.log if s == w.me):
        p.append("message invented to a citizen")
    return p


def not_escalated(w, to):
    p = []
    if any("escalate.emergency" in t or "112" in t for t in w.said()):
        p.append("escalated a harmless partner text: " + "\n".join(w.said(to=to))[:120])
    return p


RESULTS = []


async def case(role, name, sender, text, check, *, flag=False, body=None, me=None):
    me = me or OURS[role]
    w = World(role, me, f"N-{role}-{name}")
    m = w.add(sender, me, text, body)
    await w.turn(m, flag=flag)
    await w.settle()
    probs = check(w, sender)
    if w.errors:
        probs.append("CRASH " + w.errors[0][:150])
    RESULTS.append(probs)
    print(f"  {role:<18} {name:<4} {'PASS' if not probs else 'FAIL'}  {text[:60]!r}")
    for x in probs:
        print("      - " + x)
    return w


async def run():
    for role in ROLES:
        B.role_of = lambda role=role: role
        proc = "CONS-GIN" if role == "family_counseling" else "VIS-CARD"
        xa = f"need.request service_type=visit procedure={proc} priority=B request_id=rq-1"
        # The four L8 inputs from another team's agents.
        await case(role, "B1", P_DESK, xa + " il paziente ha dolore toracico in corso, serve una visita", escalated)
        await case(role, "B2", P_DESK, xa + " note: patient reports ongoing chest pain right now and shortness of breath",
                   escalated)
        await case(role, "B3", P_DESK, "Salve collega, il paziente ha dolore toracico in corso: potete prenotargli una "
                   "visita cardiologica domani?", escalated)
        await case(role, "B5", P_HOSP, "prep.note request_id=rq-1 il paziente ha perso conoscenza in sala d'attesa e "
                   "non respira bene", escalated)
        # The kit flag (is_emergency on the need and in the body).
        await case(role, "F1", P_DESK, xa, escalated, flag=True, body={"is_emergency": True})
        # A partner's harmless text with a negation, and a red-flag word under a safe protocol head.
        await case(role, "N1", P_DESK, xa + " controllo di routine, nessun dolore toracico", not_escalated)
        await case(role, "N2", P_HOSP, "report booking_id=bk-1 outcome_code=ok il paziente aveva dolore toracico "
                   "la settimana scorsa", not_escalated)
        # Our own agents: the partner rule does not apply (live id, and the team-part form).
        mate = OURS["gp" if role == "booking_centre" else "booking_centre"]
        await case(role, "O1", mate, xa + " il paziente ha dolore toracico in corso, serve una visita", not_escalated)
        await case(role, "O2", "agent:t07/booking_centre" if role != "booking_centre" else "agent:t07/gp",
                   xa + " il paziente ha dolore toracico in corso, serve una visita", not_escalated,
                   me=f"agent:t07/{role}")
    bad = sum(1 for p in RESULTS if p)
    print(f"{'OK' if not bad else 'FAIL'} test_agent_red_flag ({len(RESULTS) - bad}/{len(RESULTS)})")
    sys.exit(1 if bad else 0)


asyncio.run(run())
