"""Desk recovery of STALE citizen cases, the gaps left by the background resume (bab8677).

(a) the whole-mailbox read of the sweep takes 7 s (cap was 5 s): the sweep must still resume the case;
(b) at most 2 resumes at once, none before boot + warmup + 60 s;
(c) refusals of the OLD conversation do not climb the refusal ladder: under recovery only refusals
    after the welcome-back line count;
(d) P1 is not active and the agenda jumped 10 days: the old case gets offers, not a send-back to the gp;
(e) end to end, a case with old offers (slots now past), many old refusals and a ladder line gets the
    welcome line first and then ONLY fresh offers (no old offer_id).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_desk_recovery.py [asst/_shared/agent.py]
(env CITY_RESCUE_WARMUP=0)
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "booking_centre"
os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 0.6
brain._XA_BUDGET = 0.5
brain._XA_GRACE = 0.2
brain._RESCUE_WARMUP = 0.0
brain._RESUME_BUDGET = 8.0
brain.role_of = lambda: "booking_centre"

BC, HO, CZ = "agent:t10bc", "agent:t10ho", "citizen:t10-c1"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAIL = []
HOLD = brain._REGISTRY_SLOW + brain._HOLD_TAG
FIRST = "need.request area.district=6 lang=it prescription_number=RX1\nBuongiorno, vorrei prenotare."
RX = f"prescription prescription_number=RX1 patient={CZ} procedure=VIS-FIS priority=B state=issued"
OLD_SLOT = "slot_id=OLD{n} doctor_id=asst-centro-h1 at=2026-11-13T09:00:00+00:00 day=x facility=" + HO + " procedure=VIS-FIS free=1"


def iso(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def msg(sender, to, text, need, ago):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   body={"text": text}, sent_at=iso(ago))


def old_offer(n):
    return (f"offer offer_id=of-OLD{n} slot_id=OLD{n} doctor_id=asst-centro-h1 facility={HO} procedure=VIS-FIS "
            f"agenda_date=2026-11-13T09:00:00+00:00 priority=B prescription_number=RX1\nEcco le date.")


class City:
    """One need. The Ledger answers at once with the FRESH world (agenda 23 Nov, slots S1-S3)."""

    def __init__(self, need, *, mailbox_delay=0.0, ledger_silent=False):
        for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE", "_SENT",
                     "_HELD", "_XA_NEEDS", "_RESCUED", "_RESCUE_TRIED", "_GAVE_UP", "_CITIZEN_TOKENS",
                     "_SWEEP_TOLD", "_RESUME_FLIGHT", "_RESUMED", "_RESUMING"):
            g = getattr(brain, name, None)
            if hasattr(g, "clear"):
                g.clear()
        brain._RESCUE_LAST.clear()
        brain._RULES.clear()
        self.agent_id, self.address, self.need, self.inbound = BC, "", need, None
        self.rows, self.out = [], []
        self.mailbox_delay, self.ledger_silent = mailbox_delay, ledger_silent

    def add(self, sender, to, text, ago):
        self.rows.append(msg(sender, to, text, self.need, ago))

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.need, 0))
        if to == LEDGER and not self.ledger_silent:
            head = text.split()[0]
            if head == "agenda":
                self.rows.append(msg(LEDGER, BC, "agenda agenda_now=2026-11-23T08:00:00+00:00", self.need, 0))
            elif head == "world":
                self.rows.append(msg(LEDGER, BC, "slots page=1/1\n" + "\n".join(
                    f"slot_id=S{i} doctor_id=asst-centro-h1 at=2026-11-2{3 + i}T09:00:00+00:00 day=x facility={HO} "
                    "procedure=VIS-FIS free=1" for i in (1, 2, 3)), self.need, 0))
            elif head == "prescription" and "prescription_number=RX1" in text.split():
                self.rows.append(msg(LEDGER, BC, RX, self.need, 0))
        return "x"

    async def find(self, query):
        return [Peer(agent_id=HO, role="hospital", capabilities=("hospital.physiatry",))]

    async def history(self, peer=None, *, need_id=None):
        if need_id is None and self.mailbox_delay:
            await asyncio.sleep(self.mailbox_delay)  # the whole-mailbox read of the sweep
        return [r for r in self.rows if need_id is None or r.need_id == need_id]


def frozen(city, citizen_ago=4 * 3600 + 60, hold_ago=4 * 3600):
    city.add(CZ, BC, FIRST, citizen_ago)
    city.add(LEDGER, BC, RX, citizen_ago - 5)
    city.add(LEDGER, BC, "agenda agenda_now=2026-11-13T08:00:00+00:00", citizen_ago - 5)
    city.add(LEDGER, BC, "slots page=1/1\n" + OLD_SLOT.format(n=0), citizen_ago - 5)
    city.add(BC, CZ, HOLD, hold_ago)
    return city


async def sweep(city):
    await brain._rescue(city, "booking_centre", SimpleNamespace(need_id="", msg_id=""), timer=True)
    tasks = list(getattr(brain, "_RESUME_FLIGHT", {}).values())
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 60)


def told(city):
    return [t for to, t in city.out if to == CZ]


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {detail[:300]!r}"))
    if not ok:
        FAIL.append(name)


def offer_ids(lines):
    return [tok.split("=", 1)[1] for t in lines for ln in t.splitlines() for tok in ln.split() if tok.startswith("offer_id=")]


async def case_slow_mailbox():
    city = frozen(City("N-slow", mailbox_delay=7.0))
    await sweep(city)
    check("slow mailbox (7 s read): the stale case is still resumed", any(i.startswith("of-S") for i in offer_ids(told(city))),
          " | ".join(told(city)))


async def case_concurrency_and_boot():
    # five stale needs in one sweep: at most 2 resumes do Ledger work at once
    live, peak = [0], [0]
    real = brain._advance

    async def counted(*a, **k):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        try:
            await asyncio.sleep(0.4)
            return await real(*a, **k)
        finally:
            live[0] -= 1

    brain._advance = counted
    try:
        city = City("N-c0")
        for n in range(5):
            need = f"N-c{n}"
            sender = f"citizen:t10-c{n}"
            city.rows += [msg(sender, BC, FIRST, need, 4 * 3600 + 60), msg(BC, sender, HOLD, need, 4 * 3600),
                          msg(LEDGER, BC, RX.replace(CZ, sender), need, 4 * 3600 + 30)]
        await sweep(city)
        helloed = {to for to, t in city.out if t.startswith("Bentornato")}
        check("5 stale needs: all resumed", len(helloed) == 5, str(helloed))
        check("5 stale needs: at most 2 resumes at once", peak[0] <= 2, str(peak[0]))
    finally:
        brain._advance = real
    # right after boot (warmup > 0): no resume before _BOOT + warmup + 60 s
    city = frozen(City("N-boot"))
    brain._RESCUE_WARMUP, boot = 0.05, brain._BOOT
    brain._BOOT = __import__("time").monotonic() - 1.0
    await sweep(city)
    early = [t for t in told(city) if t.startswith("Bentornato")]
    brain._BOOT = boot - 400
    brain._RESCUE_LAST.clear()
    await sweep(city)
    brain._RESCUE_WARMUP = 0.0
    late = [t for t in told(city) if t.startswith("Bentornato")]
    check("no resume in the first 60 s after the warmup", not early and len(late) == 1, f"early={len(early)} late={len(late)}")


async def case_refusal_count():
    city = City("N-count")
    for k in range(6):
        city.add(CZ, BC, f"cancel offer_id=of-OLD{k}\nNo, grazie.", 5 * 3600 - 3600 * k // 2)
    rows = city.rows
    plain = brain._refusal_count(rows, CZ)
    var = getattr(brain, "_RECOVERY_SINCE", None)
    if var is None:
        check("refusal count under recovery: only refusals after the welcome line", False, "no _RECOVERY_SINCE")
        return
    token = var.set(datetime.now(timezone.utc))
    try:
        under = brain._refusal_count(rows, CZ)
    finally:
        var.reset(token)
    check("refusal count: normal turns count the whole history", plain == 6, str(plain))
    check("refusal count under recovery: old refusals ignored", under == 0, str(under))


async def case_no_p1_no_gp():
    city = frozen(City("N-nop1"))
    await sweep(city)
    lines = told(city)
    check("P1 not active, agenda jumped: offers, no send-back to the gp",
          any(i.startswith("of-S") for i in offer_ids(lines)) and not any("prescription_renewal" in t for t in lines),
          " | ".join(lines))


async def case_end_to_end():
    city = City("N-e2e")
    city.add(CZ, BC, FIRST, 6 * 3600)
    city.add(LEDGER, BC, RX, 6 * 3600 - 5)
    city.add(LEDGER, BC, "agenda agenda_now=2026-11-13T08:00:00+00:00", 6 * 3600 - 5)
    city.add(LEDGER, BC, "slots page=1/1\n" + OLD_SLOT.format(n=1), 6 * 3600 - 5)
    for k in range(1, 7):
        city.add(BC, CZ, old_offer(k), 6 * 3600 - 100 * k)
        city.add(CZ, BC, f"cancel offer_id=of-OLD{k}\nNo, solo il giovedi mattina con una dottoressa.", 6 * 3600 - 100 * k - 30)
    city.add(BC, CZ, "Ho cercato ancora e nulla corrisponde alle sue richieste. Posso allargare la finestra?", 5 * 3600)
    city.add(CZ, BC, "Non va bene nemmeno questo, riprovate", 5 * 3600 - 60)
    sent = brain._sent("N-e2e")  # stale in-memory counters of the old conversation
    sent.ladder.update({"partner", "widen", "interp", "close"})
    sent.offers.update({"OLD1", "OLD2", "OLD3", "wl-N-e2e-alt"})
    await sweep(city)
    lines = told(city)
    ids = offer_ids(lines)
    check("e2e: welcome-back line first", bool(lines) and lines[0].startswith("Bentornato"), " | ".join(lines))
    check("e2e: fresh offers, none of the old offer_ids", any(i.startswith("of-S") for i in ids)
          and not [i for i in ids if i.startswith("of-OLD")], str(ids))
    check("e2e: not the 'nothing fits' ladder text", not any("nulla corrisponde" in t or "ASST partner" in t for t in lines[1:]),
          " | ".join(lines))


async def main():
    await case_slow_mailbox()
    await case_concurrency_and_boot()
    await case_refusal_count()
    await case_no_p1_no_gp()
    await case_end_to_end()
    print(f"\n{'OK' if not FAIL else 'FAILED'} test_desk_recovery ({len(FAIL)} failing)")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
