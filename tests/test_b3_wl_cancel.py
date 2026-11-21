"""Live N-2026-11-02-8909040002720008 (10:18:33 -> 10:22:05): the citizen cancels both slot offers
(reason=past) and the waiting-list offer (cancel offer_id=wl-...-alt reason=waiting_list) in one
burst; the desk answered "Sto confermando la prenotazione, un attimo." and then "Va bene: resta in
lista d'attesa". A cancel of the wl- offer means "not the waiting list": neither line may go out.
Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_b3_wl_cancel.py [asst/_shared/agent.py]"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import LEDGER, Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brainb3", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
b.role_of = lambda: "booking_centre"
if hasattr(b, "_LLM_NET"):
    b._LLM_NET = False
b._RESCUE_WARMUP = 1e9
BC, CZ, FAC, NEED = "agent:y6hxsd766sys", "citizen:W01QQQ-04-0008-000272", "agent:r8yprrs0e8be", "N-b3"
N = [0]


def msg(sender, to, text):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"b3-{N[0]}", need_id=NEED, body={"text": text},
                   sent_at=datetime.now(timezone.utc).isoformat())


class City:
    agent_id, address = BC, ""

    def __init__(self, rows):
        self.rows, self.out, self.inbound = list(rows), [], None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


async def main():
    rx = "prescription_number=prescription_number-N-b3"
    offers = "\n".join(
        f"offer offer_id=of-W01QQQ-asst-centro-{d}-20261228-1430 slot_id=W01QQQ-asst-centro-{d}-20261228-1430 "
        f"doctor_id=asst-centro-{d} facility={FAC} procedure=VIS-FIS agenda_date=2026-12-28T14:30:00.000Z "
        f"agenda_day=mon priority=D {rx}" for d in ("h1", "h2")) + f"\noffer offer_id=wl-{NEED}-alt waiting_list=true"
    rows = [msg(CZ, BC, f"Vorrei prenotare. need.request lang=it {rx} service_type=hospital.physiatry"),
            msg(LEDGER, BC, "agenda agenda_now=2026-12-23T12:00:00Z"),
            msg(BC, CZ, "Buongiorno, ho trovato alcune disponibilità adatte alla sua ricetta, qui sotto.\n" + offers)]
    city = City(rows)
    start = len(city.out)
    cancels = ["Grazie, ma no. cancel offer_id=of-W01QQQ-asst-centro-h1-20261228-1430 reason=past",
               "Purtroppo mi tocca declinare. cancel offer_id=of-W01QQQ-asst-centro-h2-20261228-1430 reason=past",
               f"Purtroppo mi tocca declinare. cancel offer_id=wl-{NEED}-alt reason=waiting_list"]
    turns = []
    for text in cancels:
        m = msg(CZ, BC, text)
        city.rows.append(m)
        city.inbound = m
        turns.append(asyncio.wait_for(b.main(SimpleNamespace(summary="x", is_emergency=False), city), 20))
    for res in await asyncio.gather(*turns, return_exceptions=True):
        if isinstance(res, Exception) and type(res).__name__ not in ("TimeoutError", "OutOfScope"):
            raise res
    told = [t for to, t in city.out[start:] if to == CZ]
    fails = []
    if any(t.startswith("Sto confermando") for t in told):
        fails.append("'Sto confermando la prenotazione' after three cancels")
    if any("resta in lista d'attesa" in t for t in told):
        fails.append("'resta in lista d'attesa' after the citizen cancelled the waiting list")
    for f in fails:
        print("FAIL", f)
    if fails:
        for t in told:
            print("   >", t[:110].replace("\n", " | "))
    print(("OK" if not fails else f"FAIL {len(fails)}") + " test_b3_wl_cancel")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


asyncio.run(main())
