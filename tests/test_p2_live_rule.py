"""#145: P2 v1 is live. The brain must read it ON from the RuleVersion (rule_id P2, no consent token),
a facility always asks the partner for the cross_asst consent, and a partner confirm refused for its
consent is answered CONSENT_MISSING (retry), never as an impostor.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_p2_live_rule.py [asst/_shared/agent.py | asst/_gp/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
import time

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
GP = "_gp" in PATH
ROLE = "family_counseling" if GP else "hospital"
os.environ["CITY_ROLE"] = ROLE
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: ROLE

HO, PARTNER, CZ = "agent:t07ho", "agent:t99bc", "citizen:t99-c1"
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {detail[:300]!r}"))
    if not ok:
        FAILS.append(name)


class City:
    agent_id, address, inbound = HO, "", None

    def __init__(self):
        self.out = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return []


NEED = [0]


async def run(consent, need=""):
    NEED[0] += 1
    need = need or f"N-p2-{NEED[0]}"
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_ONCE", "_SENT"):
        g = getattr(brain, name, None)
        if hasattr(g, "clear"):
            g.clear()
    brain._RULES.clear()
    city = City()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    text = f"confirm offer_id=of-1 slot_id=S1 citizen={CZ}" + (f" consent_ref={consent}" if consent else "")
    row = Message(type="chat", sender=PARTNER, recipient=HO, msg_id="m1", need_id=need, body={"text": text}, sent_at=stamp)
    city.inbound = row
    offer = Message(type="chat", sender=HO, recipient=PARTNER, msg_id="m0", need_id=need, sent_at=stamp,
                    body={"text": "offer offer_id=of-1 slot_id=S1 doctor_id=d1 at=2026-11-23T09:00:00+00:00 facility=" + HO})
    try:
        await brain._on_confirm(city, row, [offer, row], hold_self=False, own_world=True)
    except Exception as exc:  # the hold path past P2 needs a world; reaching it is the point
        city.out.append(("EXC", repr(exc)))
    return city.out


async def main():
    brain._RULES.clear()
    brain._RULES["P2"] = "version=1|effective_agenda=2026-12-29T08:00Z"
    check("P2 in _RULES (no token) reads ON", brain._p2_required([]))
    brain._RULES.clear()
    check("empty _RULES, no facility: OFF", not brain._p2_required([]))

    for bad in ("acconsento", "cons-1-booking", "ok"):
        out = await run(bad)
        txt = " | ".join(t for _, t in out)
        check(f"facility, empty _RULES, consent_ref={bad!r}: CONSENT_MISSING", "CONSENT_MISSING" in txt, txt)
        check(f"consent_ref={bad!r}: not an impostor refusal", "conferma non accettata" not in txt.lower(), txt)
    out = await run("")
    check("no consent at all: no hold", not any(to == brain.LEDGER and t.startswith("hold") for to, t in out), str(out))

    for good in ("cx-77", "cons-n-12-cross_asst-1"):
        out = await run(good, need="n-12" if "n-12" in good else "")
        txt = " | ".join(t for _, t in out)
        check(f"real cross_asst ref {good!r}: not impostor", "conferma non accettata" not in txt.lower(), txt)
        check(f"real cross_asst ref {good!r}: not CONSENT_MISSING", "CONSENT_MISSING" not in txt, txt)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
