"""#108 (R23): the double-booking guard (_rx_booked_elsewhere) read the WHOLE desk mailbox (up to
2 s) on the first turn of every booking case and again on every confirm: one read per case, so
every new case paid it before its first Ledger ask and every confirm before it booked.

Now one whole-mailbox read indexes every forwarded confirm for every prescription; it serves every
case for _RX_CLEAR_TTL, turns share the read in flight, and our own forwarded confirms are added to
the index as they go out. The guard itself stays: a live booking in another case still blocks.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/test_108_mailbox_read.py [brain.py]
"""
import asyncio
import importlib.util
import os
import sys
import time

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain108", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"
if hasattr(brain, "_LLM_NET"):
    brain._LLM_NET = False

BC, HO = "agent:t07/booking_centre", "agent:t07/hospital"
MAILBOX_READ = 1.9  # a big desk mailbox: one whole-mailbox read
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m108-{N[0]}", need_id=need,
                   body={"text": text})


def booked_case(need, citizen, number):
    return [
        msg(citizen, BC, f"need.request lang=it prescription_number={number} vorrei prenotare", need),
        msg(BC, HO, f"confirm offer_id=of-S1 slot_id=S1 citizen={citizen} consent_ref=c1 prescription_number={number}", need),
        msg(HO, BC, "booking.receipt booking_id=bk-108 ledger_ref=lr-1", need),
        msg(BC, citizen, "booking.receipt booking_id=bk-108 ledger_ref=lr-1\nLa sua prenotazione è confermata.", need),
    ]


class City:
    agent_id = BC
    address = ""

    def __init__(self, rows):
        self.rows, self.out = list(rows), []
        self.mailbox_reads = 0

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        if need_id is None:
            self.mailbox_reads += 1
            await asyncio.sleep(MAILBOX_READ)
        return [r for r in self.rows if need_id is None or r.need_id == need_id]


async def _settle():
    for task in list(getattr(brain, "_RX_SCANNING", {}).values()):
        try:
            await task
        except Exception:
            pass


async def main():
    fails = []
    old = "citizen:W01AAA-01-000108"
    city = City(booked_case("N-108-old", old, "RX108-C"))

    # 1. First turn of case A (a search): the guard may start the mailbox read, never waits it all.
    a = msg("citizen:W01BBB-01-000201", BC, "vorrei prenotare la visita prescription_number=RX108-A lang=it", "N-108-a")
    t0 = time.monotonic()
    hit = await brain._rx_booked_elsewhere(city, [a], a, "RX108-A", a.sender)
    took = time.monotonic() - t0
    if hit or took > 1.0:
        fails.append(f"search turn of case A: hit={hit} took {took:.1f} s (want no hit, < 1 s)")
    await _settle()
    reads = city.mailbox_reads

    # 2. Confirm turn of case B (another case, another prescription) a few seconds later: the
    #    mailbox was read moments ago for case A, so no second whole-mailbox read.
    b = msg("citizen:W01CCC-01-000302", BC, "confirm offer_id=of-S2 consent_ref=c2", "N-108-b")
    t0 = time.monotonic()
    hit = await brain._rx_booked_elsewhere(city, [b], b, "RX108-B", b.sender)
    took = time.monotonic() - t0
    if hit:
        fails.append("confirm of case B: refused with no other booking")
    if city.mailbox_reads != reads or took > 1.0:
        fails.append(f"confirm of case B read the whole mailbox again: reads {reads} -> {city.mailbox_reads}, "
                     f"took {took:.1f} s")

    # 3. The guard still holds: a confirm on a prescription with a live booking in another case.
    d = msg("citizen:W01AAA-01-0002-000108", BC, "confirm offer_id=of-S3 consent_ref=c3", "N-108-d")
    hit = await brain._rx_booked_elsewhere(city, [d], d, "RX108-C", d.sender)
    told = " ".join(t for to, t in city.out if to == d.sender)
    if not hit or "bk-108" not in told:
        fails.append(f"double booking not blocked: hit={hit} told={told!r}")

    for f in fails:
        print("FAIL", f)
    print("FAILED" if fails else "test_108_mailbox_read ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
