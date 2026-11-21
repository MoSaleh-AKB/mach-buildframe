"""#97 (R12 specialist): a report the Ledger refuses is never dropped in silence.

Before: on "refused op=report" the specialist said nothing (the citizen had been told a
confirmation would come), and a placeholder consent_ref ("consent_ref=si") shared the report.
After: a refused shared report is filed again unshared and the citizen hears it; a refused
unshared report (in the turn or late, after the "I will confirm" line) is told to the citizen;
a junk consent_ref is no consent.

Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_r12_specialist_refused.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "specialist_care"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "specialist_care"
brain._PULL_BUDGET = 0.6
brain._FOLLOW_PAUSE = 0.05

AG, CIT = "agent:r12spec", "citizen:r12-c1"
SEQ = [0]


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"r{SEQ[0]}", need_id=need, body={"text": text},
                   sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))


class City:
    """Ledger answers a report with `refused` when ``refuse(text)`` says so, else `reported`;
    ``silent`` keeps the first answer back (the 'I will confirm' path), ``late`` sends it later."""

    def __init__(self, need, refuse, silent=False):
        for name in ("_FILED", "_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_FOLLOWING"):
            g = getattr(brain, name, None)
            if hasattr(g, "clear"):
                g.clear()
        self.agent_id, self.address, self.need, self.inbound = AG, "", need, None
        self.refuse, self.silent, self.rows, self.out, self.held = refuse, silent, [], [], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text, self.need))
        if to == LEDGER and text.split()[0] == "report":
            booking = next(w for w in text.split() if w.startswith("booking_id="))
            line = (f"refused op=report {booking} code=INVALID_CONSENT" if self.refuse(text)
                    else f"reported {booking} outcome_code=VISIT")
            answer = msg(LEDGER, AG, line, self.need)
            if self.silent:
                self.silent = False
                self.held.append(answer)
            else:
                self.rows.append(answer)
        return "x"

    async def history(self, **kw):
        return list(self.rows)


def case(need):
    return [
        msg(CIT, AG, "checkin booking_id=B1 ledger_ref=c-2", need),
        msg(AG, LEDGER, f"checkin booking_id=B1 citizen={CIT}", need),
        msg(LEDGER, AG, f"checked_in booking_id=B1 citizen={CIT}", need),
        msg(AG, CIT, "checkin booking_id=B1\nIl suo check-in è registrato.", need),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", need),
    ]


def to_cit(city):
    return [t for to, t in city.out if to == CIT]


def reports(city):
    return [t for to, t in city.out if to == LEDGER and t.startswith("report ")]


async def file(need, consent_ref, refuse, silent=False):
    city = City(need, refuse, silent)
    city.rows = case(need)
    city.rows.append(msg(CIT, AG, f"consent purpose=report_sharing consent_ref={consent_ref}" if consent_ref
                         else "No grazie", need))
    m = city.rows[-1]
    city.inbound = m
    await brain._file_report(city, m, list(city.rows), "B1", CIT, consent_ref)
    return city


async def main():
    fails = []

    # 1. The Ledger refuses the SHARED report: file it again unshared and tell the citizen.
    city = await file("N-r12-shared", "cons-rs-1", lambda t: "consent_ref=" in t)
    sent = reports(city)
    if not (len(sent) == 2 and "consent_ref=" in sent[0] and "consent_ref=" not in sent[1]):
        fails.append(f"shared refused: no unshared re-file {sent}")
    if not any("senza condividerlo" in t for t in to_cit(city)):
        fails.append(f"shared refused: citizen not told {to_cit(city)}")

    # 2. The Ledger refuses the UNSHARED report in the turn: the citizen hears it, no silence.
    city = await file("N-r12-plain", "", lambda t: True)
    if len(reports(city)) != 1:
        fails.append(f"plain refused: resent a refused report {reports(city)}")
    if not any("non ha accettato" in t for t in to_cit(city)):
        fails.append(f"plain refused: silence {to_cit(city)}")

    # 3. Late refusal after the "glielo confermo" promise: the follow tells the citizen.
    city = await file("N-r12-late", "", lambda t: True, silent=True)
    promised = any("glielo confermo" in t for t in to_cit(city))
    if not promised:
        fails.append(f"late: no promise line {to_cit(city)}")
    city.rows.extend(city.held)
    for _ in range(60):
        await asyncio.sleep(0.05)
        if any("non ha accettato" in t for t in to_cit(city)):
            break
    else:
        fails.append(f"late refused: dropped after the promise {to_cit(city)}")
    for task in list(brain._FOLLOWING.values()):
        task.cancel()

    # 4. A placeholder consent_ref is no consent to share the report.
    for junk in ("si", "ok", "x", "yes"):
        row = msg(CIT, AG, f"consent purpose=report_sharing consent_ref={junk}", "N-r12-junk")
        if brain._consent_reply(row) is not None:
            fails.append(f"junk consent_ref={junk} accepted: {brain._consent_reply(row)!r}")
    good = msg(CIT, AG, "consent purpose=report_sharing consent_ref=cons-rs-1", "N-r12-good")
    if brain._consent_reply(good) != "cons-rs-1":
        fails.append("real consent_ref refused")

    if fails:
        print("FAIL test_r12_specialist_refused")
        for f in fails:
            print(" -", f)
        sys.exit(1)
    print("OK test_r12_specialist_refused")


asyncio.run(main())
