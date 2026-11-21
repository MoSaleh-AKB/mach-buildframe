"""!80 port: "ai cancelli" is not a cancel; wrong-facility check-in never told to wait here;
"Non sono qui in citta" is not thanked for being here.

Run: <city python> -I tests/test_fac_wrong_facility.py asst/_shared/agent.py
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
for key, value in (("_PULL_BUDGET", 0.6), ("_CONSENT_WAIT", 0.1), ("_ADVANCE_BUDGET", 0.6)):
    if hasattr(brain, key):
        setattr(brain, key, value)

AG, CIT = "agent:fac", "citizen:c1"
n = [0]
fails = []


def check(ok, what):
    if not ok:
        fails.append(what)


def msg(sender, to, text, need="N-wf-1"):
    n[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"w{n[0]}", need_id=need, body={"text": text}, recipient=to)


# 1. cancel regex
for t in ("Sono qui, davanti ai cancelli, booking_id=bk-1", "sono ai cancelli", "dei cancelli"):
    check(not brain._CANCEL_RE.search(t), f"false cancel: {t!r}")
for t in ("Mi cancelli la visita", "cancelli la prenotazione per favore", "Vorrei annullare la visita"):
    check(brain._CANCEL_RE.search(t), f"missed cancel: {t!r}")
check(not brain._NOT_YET_RE.search("Sono qui, sono arrivato") and brain._NOT_YET_RE.search("Non sono qui in città"),
      "not-yet regex")


class Fake:
    def __init__(self, ledger):
        self.agent_id, self.address = AG, ""
        self.ledger, self.out = ledger, []
        self.rows = [
            msg(LEDGER, AG, "committed booking_id=OURS ledger_ref=c-1 slot_id=S1"),
            msg(AG, CIT, "booking.receipt booking_id=OURS\nVisita confermata."),
        ]

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text))
        if to == LEDGER and text.startswith("checkin"):
            if self.ledger == "ok":
                self.rows.append(msg(LEDGER, AG, "checked_in booking_id=OTHER"))
            elif self.ledger == "refuse":
                self.rows.append(msg(LEDGER, AG, "refused op=checkin booking_id=OTHER code=WRONG_FACILITY"))
        return "x"

    async def history(self, **kw):
        return list(self.rows)

    def find(self, *a, **kw):
        return []


async def turn(city, text):
    inbound = msg(CIT, AG, text)
    city.rows.append(inbound)
    city.inbound = inbound
    start = len(city.out)
    await brain._on_checkin(city, inbound, list(city.rows), reports=False)
    return city.out[start:]


async def main():
    txt = "Il biglietto dice ospedale A ma sono all'ospedale B: checkin booking_id=OTHER"
    for ledger, want in (("refuse", brain._WRONG_FACILITY), ("slow", brain._FOREIGN_WAIT)):
        brain._ONCE.clear()
        city = Fake(ledger)
        out = await turn(city, txt)
        to_cit = [t for to, t in out if to == CIT]
        check(any(to == LEDGER for to, _ in out), f"{ledger}: still forwarded to the Ledger")
        check(want in to_cit, f"{ledger}: want foreign line, got {to_cit}")
        check(not any("sala d'attesa" in t for t in to_cit), f"{ledger}: told to wait here")
    brain._ONCE.clear()
    city = Fake("ok")
    out = await turn(city, txt)
    check(any("_WRONG" in t or t == brain._WRONG_FACILITY for _, t in out) is False, "ok: wrong-facility said after confirm")
    # our own booking is not foreign
    brain._ONCE.clear()
    city = Fake("slow")
    out = await turn(city, "checkin booking_id=OURS")
    check(not any(t in (brain._FOREIGN_WAIT, brain._WRONG_FACILITY) for to, t in out if to == CIT),
          "own booking read as foreign")
    for line in fails:
        print("FAIL", line)
    print("test_fac_wrong_facility OK" if not fails else f"test_fac_wrong_facility: {len(fails)} failed")
    sys.exit(1 if fails else 0)


asyncio.run(main())
