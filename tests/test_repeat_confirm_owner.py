"""L7/S23 repeat confirm at a facility: a booking belongs to the agent whose confirm we held.

A partner desk books a slot and gets its receipt. Then another sender repeats the same confirm line in
that partner's case: another partner id, a look-alike of the partner id, or our own desk. None of them
may get the booking id or the ledger_ref, none may cause a Ledger write, and each gets one polite line
with a reason (no silence). The real partner repeating its own confirm still gets the same receipt and
no second hold (where the brain has that same-receipt answer).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_repeat_confirm_owner.py [asst/_shared/agent.py]
Prints OK n (failures); exit 1 on any failure.
"""

import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message, Need, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for k, v in (("_ADVANCE_BUDGET", 0.6), ("_PULL_BUDGET", 0.8), ("_AGENT_BUDGET", 0.6), ("_CONSENT_WAIT", 0.3),
             ("_XA_BUDGET", 0.5), ("_XA_GRACE", 0.2), ("_VERIFY_BUDGET", 0.3), ("_LLM_NET", False)):
    if hasattr(brain, k):
        setattr(brain, k, v)

DESK = "agent:y6hxsd766sys"
ME = {"hospital": ("agent:r8yprrs0e8be", "VIS-FIS"), "specialist_care": ("agent:dqfw9nh15dn4", "VIS-CARD")}
PARTNER = "agent:bq7e62g3kkz3"
CZ = "citizen:att-rep"
# The repeat-confirm receipt (same receipt to the agent that booked) exists only where the brain has it.
SAME = hasattr(brain, "_SAME_RECEIPT")
REPEATERS = [
    ("another partner id", "agent:q5otherdesk2"),
    ("look-alike of the partner id", "agent:bq7e62g3kkz8"),
    ("our own desk", DESK),
]
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    def __init__(self, role, need, peers=()):
        self.agent_id, self.proc = ME[role]
        self.need, self.rows, self.out, self.n = need, [], [], 0
        self.peers = list(peers)
        self.slots = {}
        self.inbound = None

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(self.agent_id, to, text, self.need))
        if to == LEDGER:
            ans = self.ledger(text)
            if ans:
                self.rows.append(msg(LEDGER, self.agent_id, ans, self.need))
        return "x"

    def ledger(self, text):
        w = text.split()
        f = dict(t.split("=", 1) for t in w[1:] if "=" in t)
        if w[0] == "agenda":
            return "agenda agenda_now=2026-11-23T08:00:00+00:00"
        if w[0] == "world":
            return "slots page=1/1\n" + "\n".join(
                f"slot_id=S{i} doctor_id=asst-centro-h1 at=2026-11-2{3 + i}T09:00:00+00:00 "
                f"facility={self.agent_id} procedure={self.proc} free=1" for i in (1, 2, 3))
        if w[0] == "hold":
            self.n += 1
            hid = f"h-rep-{self.n}"
            self.slots[hid] = f.get("slot_id", "")
            return f"held hold_id={hid} slot_id={f.get('slot_id', '')} fence=1"
        if w[0] == "commit":
            hid = f.get("hold_id", "")
            return f"committed booking_id={hid} ledger_ref=lr-{hid} slot_id={self.slots.get(hid, '')}"
        return ""

    async def find(self, q):
        return list(self.peers)

    async def history(self, peer=None, *, need_id=None):
        if peer is not None:
            return [r for r in self.rows if peer in (r.sender, r.recipient)]
        return list(self.rows)

    def writes(self, start=0):
        return [t for to, t in self.out[start:] if to == LEDGER and t.split()[0] in ("hold", "commit", "cancel")]


async def turn(c, sender, text):
    m = msg(sender, c.agent_id, text, c.need)
    c.rows.append(m)
    c.inbound = m
    start = len(c.out)
    try:
        await asyncio.wait_for(brain.main(Need(summary=text, message=m), c), 20)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            raise
    return c.out[start:]


def reset():
    for cache in ("_VERIFIED", "_XA_REP", "_REJECTED", "_ONCE", "_CANCELS_SENT", "_ASKED", "_FILED", "_DUPS",
                  "_CITIZEN_SENT", "_RESCUED"):
        if isinstance(getattr(brain, cache, None), (dict, set)):
            getattr(brain, cache).clear()


async def booked(role, tag):
    """The partner desk asks, confirms our offer and gets its receipt. Returns (city, confirm line, booking id)."""
    me = ME[role][0]
    reset()
    peers = [Peer(agent_id=PARTNER, role="booking_centre", capabilities=()),
             Peer(agent_id="agent:q5otherdesk2", role="booking_centre", capabilities=())]
    c = City(role, f"N-rco-{role}-{tag}", peers=peers)
    said = await turn(c, PARTNER, f"need.request procedure={c.proc} priority=B request_id=r-{tag}")
    offers = [ln for _, t in said for ln in t.splitlines() if ln.startswith("offer ") and "slot_id=" in ln]
    if not offers:
        return c, "", ""
    f = brain.fields_of(offers[0])
    line = (f"confirm offer_id={f['offer_id']} slot_id={f['slot_id']} citizen={CZ} "
            f"consent_ref=cons-{tag}-cross_asst facility={me}")
    said = await turn(c, PARTNER, line)
    rc = [t for to, t in said if to == PARTNER and "booking.receipt" in t]
    bid = brain.fields_of(rc[0]).get("booking_id", "") if rc else ""
    return c, line, bid


async def main():
    bad = []
    for role in ME:
        os.environ["CITY_ROLE"] = role
        # 1. three other senders repeat the partner's confirm in the partner's case
        for i, (name, sender) in enumerate(REPEATERS):
            c, line, bid = await booked(role, f"x{i}")
            if not bid:
                bad.append((role, name, "partner booking not made first", c.out[-3:]))
                continue
            start = len(c.out)
            said = await turn(c, sender, line)
            got = [t for to, t in said if to == sender]
            if any(bid in t or "ledger_ref=" in t or "booking.receipt" in t for t in got):
                bad.append((role, name, "partner's receipt sent to the repeater", got))
            if c.writes(start):
                bad.append((role, name, "Ledger write for the repeater", c.writes(start)))
            if not got:
                bad.append((role, name, "repeater got no reply (silence)", said))
            elif not any("verific" in t.lower() or "non posso" in t.lower() for t in got):
                bad.append((role, name, "refusal without a reason", got))
            # the real partner still gets the same receipt, no second hold
            start = len(c.out)
            said = await turn(c, PARTNER, line)
            again = [t for to, t in said if to == PARTNER and "booking.receipt" in t]
            if SAME and (not again or bid not in again[0]):
                bad.append((role, name, "real partner's repeat lost its receipt", said))
            if c.writes(start):
                bad.append((role, name, "second Ledger write on the real partner's repeat", c.writes(start)))
        # 2. the real partner's repeat alone (b08aac8 behaviour)
        c, line, bid = await booked(role, "own")
        if bid:
            start = len(c.out)
            said = await turn(c, PARTNER, line)
            again = [t for to, t in said if to == PARTNER and "booking.receipt" in t]
            if c.writes(start) or (SAME and (not again or bid not in again[0])):
                bad.append((role, "real partner repeat", said))
        else:
            bad.append((role, "real partner repeat", "booking not made first"))
    for b in bad:
        print("FAIL", str(b)[:300])
    print(f"OK {len(bad)}" if not bad else f"FAILED {len(bad)}")
    return len(bad)


sys.exit(1 if asyncio.run(main()) else 0)
