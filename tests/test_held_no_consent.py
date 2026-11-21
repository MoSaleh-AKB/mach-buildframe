"""#96: a held line we cannot commit (no consent in the case) must not promise a slow registry.
Before: _on_held returned quietly, _finish_booking then waited for a commit that never comes and
told the booker "il registro è lento", with an 8-minute follow. Now: the hold is cancelled on the
Ledger and the booker gets ONE cancel offer_id=... reason=CONSENT_MISSING, no slow line.
Run: <city python> -I tests/test_held_no_consent.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "hospital"
HO, BC, CZ = "agent:t96/hospital", "agent:t96/booking_centre", "citizen:t96/c1"
NEED = "X-96"  # not an N- need: no cons-<need>-booking fallback
n = [0]


def load():
    spec = importlib.util.spec_from_file_location("brain96", sys.argv[1])
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "hospital"
    b._CONFIRM_BUDGET = 1.0
    b._PULL_BUDGET = 1.0
    return b


def msg(sender, to, text):
    n[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"t{n[0]}", need_id=NEED,
                   conversation_id=f"cv-{NEED}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self):
        self.agent_id, self.address = HO, ""
        self.rows, self.out = [], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(HO, to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


async def main():
    b = load()
    city = City()
    offer = msg(HO, BC, f"offer offer_id=of-S1 slot_id=S1 facility={HO} at=2026-12-01T09:00:00+00:00")
    # The page of history we read holds the confirm but neither the consent nor our hold line.
    confirm = msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} facility={HO} priority=B")
    held = msg(LEDGER, HO, f"held hold_id=bk-96 slot_id=S1 fence=1 need_id={NEED}")
    city.rows += [offer, confirm, held]
    await b._finish_booking(city, confirm, [offer, confirm], "S1", hold_self=False)
    for task in list(b._FOLLOWING.values()):
        task.cancel()

    to_bc = [t for to, t in city.out if to == BC]
    to_ledger = [t for to, t in city.out if to == LEDGER]
    assert not any(t.startswith("commit") for t in to_ledger), f"commit without consent: {to_ledger}"
    assert "cancel hold_id=bk-96" in to_ledger, f"hold not freed: {city.out}"
    assert not any("lento" in t for t in to_bc), f"slow registry promised: {to_bc}"
    cancels = [t for t in to_bc if t.startswith("cancel offer_id=of-S1")]
    assert len(cancels) == 1, f"want ONE cancel to the booker: {to_bc}"
    assert "reason=CONSENT_MISSING" in cancels[0].split("\n")[0], cancels
    assert not b._FOLLOWING, f"a follow still waits for a commit: {list(b._FOLLOWING)}"
    print("OK test_held_no_consent")


asyncio.run(main())
