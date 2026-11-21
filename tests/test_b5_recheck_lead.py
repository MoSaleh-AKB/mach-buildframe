"""Live N-2026-11-02-8909040002720008 (10:34:34 -> 10:34:50): a citizen's confirm went through the
rule recheck (_rule_recheck) and was cancelled with reason=facility_cancelled. The recheck asked the
chosen slot for the OFFER lead (14 agenda hours, desk floor _MIN_LEAD), so a free slot a few hours
ahead counted as gone. A slot the citizen already chose only has to be in the future (_LEAD). A
slot that is past is still replaced, and the re-sent offers never include past slots.
Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_b5_recheck_lead.py [asst/_shared/agent.py]"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brainb5", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
b.role_of = lambda: "booking_centre"
if hasattr(b, "_LLM_NET"):
    b._LLM_NET = False
b._ADVANCE_BUDGET = 1.0
BC, CZ, FAC = "agent:y6hxsd766sys", "citizen:W01QQQ-04-0008-000272", "agent:r8yprrs0e8be"
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"b5-{N[0]}", need_id=need, body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    agent_id, address = BC, ""

    def __init__(self, need, now, slot_at):
        self.need, self.now, self.slot_at, self.rows, self.out, self.inbound = need, now, slot_at, [], [], None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need))
        head = text.split()[0]
        if to == LEDGER and head == "agenda":
            self.rows.append(msg(LEDGER, BC, f"agenda agenda_now={self.now}", self.need))
        elif to == LEDGER and head == "world":
            self.rows.append(msg(LEDGER, BC, "slots page=1/1\nslot_id=S1 doctor_id=asst-centro-h1 "
                                 f"facility={FAC} procedure=VIS-FIS at={self.slot_at} free=1", self.need))
        elif to == LEDGER and head == "prescription":
            self.rows.append(msg(LEDGER, BC, "prescription prescription_number=RX-B5 state=issued procedure=VIS-FIS", self.need))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


async def recheck(need, now, slot_at):
    city = City(need, now, slot_at)
    offer = {"offer_id": "of-S1", "slot_id": "S1", "priority": "D", "procedure": "VIS-FIS"}
    m = msg(CZ, BC, "confirm offer_id=of-S1 consent_ref=cons-x-booking", need)
    city.rows.append(m)
    city.inbound = m
    try:
        ok = await asyncio.wait_for(b._rule_recheck(city, m, list(city.rows), offer, "RX-B5"), 15)
    except asyncio.TimeoutError:
        ok = None
    return ok, [t for to, t in city.out if to == CZ]


async def main():
    fails = []
    ok, told = await recheck("N-b5a", "2026-12-23T10:00:00Z", "2026-12-23T14:00:00Z")  # 4 h ahead, free
    if ok is not True or any("facility_cancelled" in t for t in told):
        fails.append(f"free slot 4 h ahead: recheck={ok}, citizen got {[t[:60] for t in told]}")
    ok, told = await recheck("N-b5b", "2026-12-24T07:40:00Z", "2026-12-23T16:00:00Z")  # the live case: past
    if ok is not False:
        fails.append(f"past slot (live 0002720008): recheck={ok}, want it replaced")
    for f in fails:
        print("FAIL", f)
    print(("OK" if not fails else f"FAIL {len(fails)}") + " test_b5_recheck_lead")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


asyncio.run(main())
