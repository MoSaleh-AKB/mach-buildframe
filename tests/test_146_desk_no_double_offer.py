"""#146: a desk follow-up turn ("va bene, martedì?") on a need that already got offers, whose short
case read (_DESK_READ, 2 s) times out on a slow relay, must not start a new search: no Ledger ask
in the turn, no second offer set, at most one short line to the citizen. The turn runs again in
the background with the full read.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_146_desk_no_double_offer.py [asst/_shared/agent.py]
"""
import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain146", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False
brain._RESCUE_WARMUP = 1e9

BC, CZ, NEED = "agent:t07/booking_centre", "citizen:t07/c146", "N-146"
OFFERS = ("Ecco le date disponibili:\n"
          "offer offer_id=of-1 slot_id=S1 facility=agent:t07/hospital at=2026-11-24T09:00:00+00:00 day=tue\n"
          "offer offer_id=of-2 slot_id=S2 facility=agent:t07/hospital at=2026-11-25T09:00:00+00:00 day=wed")
N = [0]


def msg(sender, to, text):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"s146-{N[0]}", need_id=NEED, body={"text": text})


class City:
    agent_id, address = BC, ""

    def __init__(self, rows, inbound):
        self.rows, self.out, self.inbound, self.slow = list(rows), [], inbound, 0.0

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(self.slow)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


async def main():
    first = msg(CZ, BC, "vorrei prenotare la visita, prescription_number=RX146 lang=it")
    city = City([first], first)
    await brain._say(city, CZ, OFFERS)  # the earlier turn's offers, sent the way the desk sends them
    follow = msg(CZ, BC, "va bene, martedì?")
    city.rows.append(follow)
    city.inbound, city.slow, start = follow, 5.0, len(city.out)
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), city), 30)
    turn = city.out[start:]
    fails = []
    if any(to == LEDGER for to, _ in turn):
        fails.append(f"new Ledger search in the follow-up turn: {[t for to, t in turn if to == LEDGER]}")
    told = [t for to, t in turn if to == CZ]
    if any("offer_id=" in t for t in told):
        fails.append("second offer set")
    acts = [t.split()[0] for t in told if t.split() and t.split()[0] in ("data.request", "offer", "cancel", "hold")]
    if acts:
        fails.append(f"the blind turn answered as a new request ({acts}): it asked again what the citizen gave")
    if len(told) > 1:
        fails.append(f"{len(told)} lines to the citizen, want at most 1: {told}")
    if not fails and hasattr(brain, "_BLIND_NOTE"):
        await asyncio.sleep(8)  # the background re-run: full 5 s read, then the normal turn
        later = city.out[start + len(told):]
        if not later:  # this fake case has no Ledger rows, so the normal turn re-checks the prescription
            fails.append("background re-run did not run the turn: the citizen would get only the short line")
    for f in fails:
        print("FAIL", f)
    print(("OK" if not fails else f"FAIL {len(fails)}") + " test_146_desk_no_double_offer")
    sys.stdout.flush()
    os._exit(1 if fails else 0)  # background resume / re-run tasks are not part of this check


asyncio.run(main())
