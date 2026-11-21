"""Red flags at the facility check-in and inside a desk booking line (L8 C11), fake City only.

Run: <city python> -I tests/test_redflag_checkin.py asst/_shared/agent.py
Per facility role (hospital, specialist_care, family_counseling):
  1. check-in + a real red flag -> escalate.emergency to the citizen AND the check-in written to the Ledger
  2. check-in + everyday words -> checked in, no 112 (hyperbole at the check-in: test_redflag_hyperbole.py)
  3. check-in after an earlier 112 in the same case -> checked in
  J26. the controller's live check-in wording -> escalate AND checked in
Desk (booking_centre): 4, J25, K10. prescription_number + a buried red flag -> escalate.emergency, no offer, no hold.
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need

os.environ.setdefault("CITY_ROLE", "hospital")
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for k, v in (("_ADVANCE_BUDGET", 0.6), ("_PULL_BUDGET", 0.8), ("_AGENT_BUDGET", 0.6), ("_CONSENT_WAIT", 0.3),
             ("_XA_BUDGET", 0.5), ("_XA_GRACE", 0.2), ("_VERIFY_BUDGET", 0.3), ("_LLM_NET", False)):
    if hasattr(brain, k):
        setattr(brain, k, v)

LIVE = {"gp": "agent:jmqwr12vy2j6", "booking_centre": "agent:y6hxsd766sys", "hospital": "agent:r8yprrs0e8be",
        "specialist_care": "agent:dqfw9nh15dn4", "family_counseling": "agent:pv3a0t3tyqdq"}
PROC = {"hospital": "VIS-FIS", "specialist_care": "VIS-CARD", "family_counseling": "CONS-GIN", "booking_centre": "VIS-FIS"}
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    def __init__(self, role, need, cz):
        self.role, self.agent_id, self.need, self.address, self.cz = role, LIVE[role], need, "", cz
        self.rows, self.out, self.writes, self.inbound, self.n, self.holds = [], [], [], None, 0, {}

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, self.need))
        if to == LEDGER:
            self.writes.append(text)
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
        if op == "prescription":
            return (f"prescription prescription_number={f.get('prescription_number', '')} patient={self.cz} "
                    f"procedure={PROC[self.role]} priority=B state=issued")
        if op == "hold":
            self.n += 1
            self.holds[f"h-{self.n}"] = f.get("slot_id", "")
            return f"held hold_id=h-{self.n} slot_id={f.get('slot_id', '')} fence=1"
        if op == "commit":
            hid = f.get("hold_id", "")
            return f"committed booking_id={hid} ledger_ref=c-{hid} slot_id={self.holds.get(hid, '')}"
        if op == "checkin":
            return f"checked_in booking_id={f.get('booking_id', '')}"
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
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=m), c), 20)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            print("   crash", type(exc).__name__, exc)
    await asyncio.sleep(0.02)
    return c.out[s:]


def esc(out, cz):
    return any(to == cz and t.lstrip().startswith("escalate.emergency") for to, t in out)


async def booked_case(role, tag):
    os.environ["CITY_ROLE"] = role
    for cache in ("_VERIFIED", "_XA_REP", "_REJECTED"):
        if isinstance(getattr(brain, cache, None), dict):
            getattr(brain, cache).clear()
    cz = f"citizen:ci-{role[:3]}-{tag}"
    need = f"N-ci-{role[:3]}-{tag}"
    c = City(role, need, cz)
    await turn(c, LIVE["booking_centre"], f"confirm offer_id=of-S1 slot_id=S1 citizen={cz} consent_ref=cons-{need}-booking "
                                          f"facility={LIVE[role]} prescription_number=RX-CZ-{tag}")
    hid = next(iter(c.holds), "h-1")
    return c, cz, hid


def checked_in(c, hid):
    return any(w.startswith("checkin") and f"booking_id={hid}" in w for w in c.writes)


async def main():
    bad = []
    for role in ("hospital", "specialist_care", "family_counseling"):
        c, cz, hid = await booked_case(role, "1")
        out = await turn(c, cz, f"checkin booking_id={hid} sono arrivato, ma mi manca il fiato e ho un peso sul petto, sudo freddo")
        if not esc(out, cz) or not checked_in(c, hid):
            bad.append(f"{role} 1 red flag at check-in: escalate={esc(out, cz)} checked_in={checked_in(c, hid)}")
        c, cz, hid = await booked_case(role, "2")
        out = await turn(c, cz, f"checkin booking_id={hid} sono in sala d'attesa, quanto manca?")
        if esc(out, cz) or not checked_in(c, hid):
            bad.append(f"{role} 2 calm check-in: escalate={esc(out, cz)} checked_in={checked_in(c, hid)}")
        c, cz, hid = await booked_case(role, "3")
        first = await turn(c, cz, "scusate, ho un dolore al petto fortissimo")
        out = await turn(c, cz, f"checkin booking_id={hid}")
        if not esc(first, cz) or not checked_in(c, hid):
            bad.append(f"{role} 3 check-in after a 112: first_escalate={esc(first, cz)} checked_in={checked_in(c, hid)}")
        # controller J26: the live wording, two lines (check-in line, then the sign)
        c, cz, hid = await booked_case(role, "J26")
        out = await turn(c, cz, f"checkin booking_id={hid} ledger_ref=LR-1\nSono arrivato, ma mentre aspettavo ho un dolore "
                                "fortissimo al petto e mi manca il respiro.")
        if not esc(out, cz) or not checked_in(c, hid):
            bad.append(f"{role} J26 red flag at check-in: escalate={esc(out, cz)} checked_in={checked_in(c, hid)}")
    os.environ["CITY_ROLE"] = "booking_centre"
    desk = [("4", "prescription_number=RX-CZ-4 vorrei prenotare la visita, però da stamattina non sento più il braccio sinistro e parlo male"),
            ("J25", "Vorrei prenotare la fisioterapia prescription_number=RX-J25-7720. Ah, da stamattina ho il braccio destro "
                    "addormentato e faccio fatica a parlare, ma sarà la stanchezza."),
            ("K10", "Hi, book my physio please prescription_number=RX-K10-1210 lang=en. I have a crushing pain in my chest right "
                    "now and my left arm is numb.")]
    for tag, text in desk:
        cz = f"citizen:ci-desk-{tag}"
        c = City("booking_centre", f"N-ci-desk-{tag}", cz)
        out = await turn(c, cz, text)
        offer = any(to == cz and "offer_id=" in t for to, t in out)
        hold = any(w.startswith("hold") for w in c.writes)
        if not esc(out, cz) or offer or hold:
            bad.append(f"booking_centre {tag} buried red flag with prescription: escalate={esc(out, cz)} offer={offer} hold={hold}")
    for b in bad:
        print("FAIL", b)
    print("FAILED" if bad else "OK", len(bad))
    sys.exit(1 if bad else 0)


asyncio.run(main())
