"""Same bug as #96, on the gp brain (family_counseling = consultorio books its own slots).
A held line we cannot commit (no consent in the case) must not end in "il registro risponde
lentamente". Now: the hold is cancelled on the Ledger and the citizen gets ONE
cancel offer_id=... reason=CONSENT_MISSING, no commit and no slow-registry line.
Run: <city python> -I tests/gp/test_gp_held_no_consent.py asst/_gp/agent.py"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "family_counseling"
FC, CZ = "agent:t96/family_counseling", "citizen:t96/c1"
NEED = "X-96gp"  # not an N- need: no cons-<need>-booking fallback
n = [0]


def load():
    spec = importlib.util.spec_from_file_location("brain96gp", sys.argv[1])
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "family_counseling"
    b._CONS_CONFIRM_WAIT = 1.0
    b._CONS_CONFIRM_TURN = 2.0
    b._PULL_BUDGET = 1.0
    return b


def msg(sender, to, text):
    n[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"t{n[0]}", need_id=NEED,
                   conversation_id=f"cv-{NEED}", body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self):
        self.agent_id, self.address = FC, ""
        self.rows, self.out = [], []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(FC, to, text))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


async def main():
    b = load()
    city = City()
    offer = msg(FC, CZ, f"offer offer_id=of-S1 slot_id=S1 facility={FC} at=2026-12-01T09:00:00+00:00")
    # The page of history we read holds the confirm but neither the consent nor our hold line.
    confirm = msg(CZ, FC, "confirm offer_id=of-S1 slot_id=S1")
    held = msg(LEDGER, FC, f"held hold_id=bk-96 slot_id=S1 fence=1 need_id={NEED}")
    city.rows += [offer, confirm, held]
    await b._finish_booking(city, confirm, [offer, confirm], "S1", hold_self=True)

    to_cz = [t for to, t in city.out if to == CZ]
    to_ledger = [t for to, t in city.out if to == LEDGER]
    assert not any(t.startswith("commit") for t in to_ledger), f"commit without consent: {to_ledger}"
    assert "cancel hold_id=bk-96" in to_ledger, f"hold not freed: {city.out}"
    assert not any("lentamente" in t or "lento" in t for t in to_cz), f"slow registry promised: {to_cz}"
    cancels = [t for t in to_cz if t.startswith("cancel offer_id=of-S1")]
    assert len(cancels) == 1, f"want ONE cancel to the citizen: {to_cz}"
    assert "reason=CONSENT_MISSING" in cancels[0].split("\n")[0], cancels
    print("OK test_gp_held_no_consent")


asyncio.run(main())
