"""Review 1300: five small fixes in asst/_shared/agent.py (fake-city style).
1 a Ledger 429 on a commit is a rate limit: no refusal, no cancel, no _COMMIT_REFUSED.
2 _impostor_confirm checks the consent shape only when P2 is in force.
3 a later Ledger refused row never turns a committed "yes" into "no".
4 the report refusal texts exist in English (EN citizen).
5 _held_no_consent with no booker frees the claim and tells the case's citizen.
Run: <city python> -I tests/test_review_1300.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone

from city_sdk.city import LEDGER, Message

os.environ["CITY_ROLE"] = "hospital"
HO, PEER, CZ = "agent:t13/hospital", "agent:t99/booking_centre", "citizen:t13/c1"
NEED = "X-1300"
n = [0]


def load():
    n[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain1300_{n[0]}", sys.argv[1])
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "hospital"
    return b


def msg(sender, to, text, need=NEED):
    n[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"r{n[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
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
    # 1: 429 is not a refused write
    b = load()
    city = City()
    b._LAST_FAIL[LEDGER] = "RATE_LIMITED"
    offer = msg(HO, PEER, f"offer offer_id=of-S1 slot_id=S1 facility={HO}")
    confirm = msg(PEER, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={CZ} facility={HO}")
    got = await b._relay_refused_write(city, confirm, [offer, confirm], "S1", "of-S1", to=PEER)
    assert got is False and not city.out, f"429 treated as refusal: {got} {city.out}"
    b._LAST_FAIL[LEDGER] = "SCHEMA_INVALID"
    got = await b._relay_refused_write(city, confirm, [offer, confirm], "S1", "of-S1", to=PEER)
    assert got is True and any("cancel" in t for _, t in city.out), "a real 4xx must still cancel"

    # 2: consent shape only under P2
    b = load()
    chat = [msg(PEER, HO, "confirm offer_id=of-S1 slot_id=S1")]
    m = chat[0]
    b._RULES.clear()
    for consent in ("cons-1", ""):
        why = b._impostor_confirm(chat, m, CZ, consent, HO)
        assert why == "", f"P2 off, consent={consent!r} held as {why!r}"
    b._RULES["P2"] = "cross_asst_consent=required"
    assert b._impostor_confirm(chat, m, CZ, "cons-1", HO) == "consent", "P2 on must hold a bad consent"

    # 3: refused after committed stays yes
    b = load()
    com = msg(LEDGER, HO, "committed booking_id=bk-1 slot_id=S1 ledger_ref=L-1")
    ref = msg(LEDGER, HO, "refused booking_id=bk-1 code=DUPLICATE_COMMIT")
    can = msg(LEDGER, HO, "cancelled booking_id=bk-1")
    assert b._ledger_receipt_verdict([com, ref], "bk-1", "L-1", "S1") == "yes"
    assert b._ledger_receipt_verdict([ref], "bk-1", "L-1", "S1") == "no"
    assert b._ledger_receipt_verdict([com, can], "bk-1", "L-1", "S1") == "no"

    # 4: English texts for the report refusals
    b = load()
    for name in ("_REPORT_REFUSED", "_REPORT_RESENT", "_REPORT_UNSHARED"):
        it = getattr(b, name)
        assert b._EN_TEXT.get(it) and b._EN_TEXT[it] != it, f"{name} has no EN text"

    # 5: no booker: claim freed, citizen told
    b = load()
    city = City()
    b._CLAIMS["S1"] = (NEED, 0)
    held = msg(LEDGER, HO, f"held hold_id=bk-5 slot_id=S1 fence=1 need_id={NEED}")
    ask = msg(CZ, HO, "vorrei prenotare")
    chat = [ask, held]
    city.rows += chat
    await b._held_no_consent(city, chat, "bk-5", "S1")
    assert "S1" not in b._CLAIMS, "soft claim not released"
    assert any(to == CZ for to, _ in city.out), f"citizen left silent: {city.out}"
    print("OK test_review_1300")


asyncio.run(main())
