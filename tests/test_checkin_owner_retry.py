"""S23: a check-in is deduplicated per (booking_id, citizen), and a refused one can be tried again.

Another citizen who sends the victim's booking_id first gets the Ledger refusal; the real owner's
check-in must still reach the Ledger. A different citizen after the owner's check-in gets one
privacy line, never silence; the owner's repeat gets one short line and no second Ledger write.
Run: <city python> -I tests/test_checkin_owner_retry.py asst/_shared/agent.py
"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message

os.environ.setdefault("CITY_ROLE", "hospital")
spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for key, value in (("_PULL_BUDGET", 0.6), ("_CONSENT_WAIT", 0.1)):
    if hasattr(brain, key):
        setattr(brain, key, value)

AG, VIC = "agent:fac", "citizen:vic"
# Variants of the stranger: another citizen, and a look-alike of the owner's id (Cyrillic "с").
# The third variant, the owner's own retry after a refusal, is case 2.
STRANGERS = ("citizen:att", "citizen:vi\u0441")
ATT = STRANGERS[0]
n = [0]
need = ["N-ci-0"]
fails = []


def msg(sender, to, text):
    n[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"t{n[0]}", need_id=need[0], body={"text": text}, recipient=to)


class Fake:
    """Ledger: booking B1 belongs to VIC; ``refuse_once`` refuses the owner's first try."""

    def __init__(self, refuse_once=False, hold=False, defer=False):
        need[0] = f"N-ci-{n[0]}"  # one need per case
        self.agent_id, self.address = AG, ""
        self.rows = [
            msg(AG, LEDGER, "commit hold_id=B1 consent_ref=cons-1 fence=1"),
            msg(LEDGER, AG, "committed booking_id=B1 ledger_ref=c-B1 slot_id=S1"),
            msg(AG, VIC, "booking.confirmed booking_id=B1\nLa sua visita è confermata."),
        ]
        if hold:  # our hold names the booking's citizen: the brain knows the owner
            self.rows.insert(0, msg(AG, LEDGER, f"hold slot_id=S1 citizen={VIC} consent_ref=cons-1"))
        self.out, self.refuse_once, self.defer = [], refuse_once, defer

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text))
        if to == LEDGER and text.startswith("checkin") and not self.defer:
            fields = brain.fields_of(text)
            if fields.get("citizen") != VIC:
                self.rows.append(msg(LEDGER, AG, "refused op=checkin booking_id=B1 code=NOT_YOUR_BOOKING"))
            elif self.refuse_once:
                self.refuse_once = False
                self.rows.append(msg(LEDGER, AG, "refused op=checkin booking_id=B1 code=TOO_EARLY"))
            else:
                self.rows.append(msg(LEDGER, AG, "checked_in booking_id=B1"))
        elif to == LEDGER and text.startswith("report"):
            self.rows.append(msg(LEDGER, AG, "reported booking_id=B1"))
        return "x"

    async def history(self, **kw):
        return list(self.rows)

    def find(self, *a, **kw):
        return []


async def turn(city, sender, text="checkin booking_id=B1", reports=False):
    inbound = msg(sender, AG, text)
    city.rows.append(inbound)
    city.inbound = inbound  # the brain keys its send-once guards by the inbound need
    start = len(city.out)
    await brain._on_checkin(city, inbound, list(city.rows), reports=reports)
    return city.out[start:]


def writes(out, who):
    return [t for to, t in out if to == LEDGER and f"citizen={who}" in t]


def said(out, who):
    return [t for to, t in out if to == who]


def check(ok, what):
    if not ok:
        fails.append(f"[{ATT!r}] {what}")


async def main():
    global ATT
    for ATT in STRANGERS:
        await cases()
    for line in fails:
        print("FAIL", line)
    print(f"OK {len(fails)}" if not fails else f"test_checkin_owner_retry: {len(fails)} failed")
    sys.exit(1 if fails else 0)


async def cases():
    # 1. Attacker first (refused), then the owner: her check-in reaches the Ledger.
    brain._ONCE.clear()  # each case is a fresh process
    city = Fake()
    out = await turn(city, ATT)
    check(writes(out, ATT) and said(out, ATT), "attacker: refused at the Ledger, with a reply")
    out = await turn(city, VIC)
    check(writes(out, VIC), "owner after a refused attacker: check-in not sent to the Ledger")
    check(any("check-in è registrato" in t for t in said(out, VIC)), f"owner: no confirmation {said(out, VIC)}")
    check(not any("registrato" in t for t in said(city.out, ATT)), "attacker told the check-in is registered")

    # 2. The owner's own refused try is not a dedup reason: her retry goes to the Ledger.
    brain._ONCE.clear()
    city = Fake(refuse_once=True)
    await turn(city, VIC)
    out = await turn(city, VIC)
    check(writes(out, VIC), "owner retry after a refusal: not sent to the Ledger")
    check(any("check-in è registrato" in t for t in said(out, VIC)), f"owner retry: no confirmation {said(out, VIC)}")

    # 3. Another citizen after the owner's check-in: one privacy line, no Ledger write.
    brain._ONCE.clear()  # each case is a fresh process
    city = Fake()
    await turn(city, VIC)
    out = await turn(city, ATT, "Sono il marito, faccio io il check-in per lei: checkin booking_id=B1")
    check(not writes(out, ATT), "other citizen after success: written to the Ledger")
    lines = said(out, ATT)
    check(len(lines) == 1, f"other citizen after success: want one line, got {lines}")
    check(any("privacy" in t.lower() for t in lines), f"other citizen: no privacy reason {lines}")
    check(not any("registrato" in t or VIC in t for t in lines), f"other citizen: status leaked {lines}")

    # 4. The owner repeats after success: one short line, no second Ledger write.
    out = await turn(city, VIC)
    check(not writes(out, VIC), "owner repeat after success: second Ledger write")
    check(len(said(out, VIC)) == 1, f"owner repeat after success: want one line, got {said(out, VIC)}")

    # 5. Specialist: the owner's consent answer after the check-in still files the shared report.
    # Once only: the brain remembers asked and filed reports per booking_id for the process.
    if ATT == STRANGERS[0]:
        brain._ONCE.clear()  # each case is a fresh process
        city = Fake()
        await turn(city, VIC, reports=True)
        out = await turn(city, VIC, "consent_ref=cons-9 booking_id=B1", reports=True)
        check(any(to == LEDGER and "consent_ref=cons-9" in t for to, t in city.out), f"consent after check-in: no shared report {out}")

    # 6. Specialist with the owner known from our hold: a stranger's try never reaches the Ledger.
    if hasattr(brain, "_booking_citizen"):  # that owner check lives on the specialist branch
        brain._ONCE.clear()
        await known_owner(Fake(hold=True))

    # 7. Two tries in flight, Ledger answers in REVERSE order (owner known from our own hold):
    # the acceptance is the owner's, the refusal the stranger's.
    brain._ONCE.clear()
    city = Fake(hold=True, defer=True)
    await turn(city, ATT)
    await turn(city, VIC)
    check(writes(city.out, ATT) and writes(city.out, VIC), "in flight: both tries must reach the Ledger")
    city.defer = False
    ok = msg(LEDGER, AG, "checked_in booking_id=B1")
    city.rows += [ok, msg(LEDGER, AG, "refused op=checkin booking_id=B1 code=NOT_YOUR_BOOKING")]
    tries = getattr(brain, "_checkin_tries", lambda *a: {})(city.rows, AG, "B1")
    check(tries == {ATT: "refused", VIC: "checked_in"}, f"reverse order: wrong pairing {tries}")
    start = len(city.out)
    city.inbound = ok
    await brain._on_checked_in(city, ok, list(city.rows), reports=False)
    check(any("check-in è registrato" in t for t in said(city.out[start:], VIC)), "reverse order: owner not confirmed")
    check(not said(city.out[start:], ATT), f"reverse order: stranger told {said(city.out[start:], ATT)}")
    out = await turn(city, VIC)
    check(not any("privacy" in t.lower() for t in said(out, VIC)), f"reverse order: owner got the privacy line {out}")
    check(not writes(out, VIC) and len(said(out, VIC)) == 1, f"reverse order: owner repeat {out}")
    out = await turn(city, ATT)
    check(any("privacy" in t.lower() for t in said(out, ATT)), f"reverse order: stranger repeat {out}")


async def known_owner(city):
    out = await turn(city, ATT, reports=True)
    check(not writes(out, ATT), "known owner: stranger's check-in written to the Ledger")
    check(any("privacy" in t.lower() for t in said(out, ATT)), f"known owner: no privacy line {said(out, ATT)}")
    out = await turn(city, VIC, reports=True)
    check(writes(out, VIC), "known owner: owner's check-in not sent")
    check(any("check-in è registrato" in t for t in said(out, VIC)), f"known owner: no confirmation {said(out, VIC)}")


asyncio.run(main())
