"""R4 (#89): p09 TRUST RING at the consultorio. An agent outside the 130 official agents is never a
cross-ASST partner: `_xa_candidates` drops it, and a citizen's confirm of an offer that names it as
the facility never sends citizen data there. Before, the gp brain's `_xa_candidates` had no
`_off_roster` check (the shared brain has it), so an impostor's card could be asked and confirmed.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/gp/test_fc_trust_ring.py asst/_gp/agent.py

Unit check on `_xa_candidates` plus full turns through brain.main on a local fake city.
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py"))
os.environ["CITY_ROLE"] = "family_counseling"
ME = "agent:pv3a0t3tyqdq"    # our live consultorio: an official id, so the trust ring is enforced
REAL = "agent:p5sq1njyjr47"  # another team's official consultorio
IMP = "agent:zzzzzzzzzzzz"   # live-shaped, not on the roster: an impostor
OURCZ = "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
DAY = (NOW + timedelta(days=(7 - NOW.weekday()) % 7 or 7)).replace(minute=0, second=0, microsecond=0)
CONS = {}
SEQ = [0]
OKS = []
def load():
    SEQ[0] += 1
    spec = importlib.util.spec_from_file_location(f"brain{SEQ[0]}", PATH)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "family_counseling"
    if hasattr(b, "_LLM_NET"):
        b._LLM_NET = False
    return b


def msg(sender, to, text, need):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


class City:
    def __init__(self, b):
        self.b, self.agent_id, self.address = b, ME, ""
        self.rows, self.out, self.tasks, self.ops = [], [], [], []
        self.free = {s: True for s in ("S1", "S2", "S3", "S4")}
        self.holds, self.booked, self.n = {}, {}, 0
        self.t0 = time.monotonic()

    def ledger(self, text):
        f = {k: v for k, _, v in (t.partition("=") for t in text.split()) if v}
        op = text.split()[0]
        self.ops.append(text)
        if op == "hold":
            s = f.get("slot_id", "")
            if s not in self.free:
                return "refused op=hold code=SLOT_NOT_FOUND"
            if CONS.get(f.get("consent_ref", "")) != f.get("citizen"):
                return "refused op=hold code=CONSENT_MISSING"
            if not self.free[s]:
                return "refused op=hold code=SLOT_TAKEN"
            self.n += 1
            self.holds[f"bk-{self.n}"] = s
            self.free[s] = False
            return f"held hold_id=bk-{self.n} slot_id={s} fence=1"
        if op == "commit":
            hid = f.get("hold_id", "")
            if hid not in self.holds or hid in self.booked:
                return "refused op=commit code=HOLD_NOT_FOUND"
            self.booked[hid] = self.holds[hid]
            return f"committed booking_id={hid} ledger_ref=lr-{hid}"
        if op == "agenda":
            return "agenda agenda_now=" + DAY.replace(hour=8).isoformat()
        if op == "world":
            rows = [f"slot_id={s} doctor_id=asst-centro-h1 at={DAY.replace(hour=14 + i).isoformat()} day=mon "
                    f"facility={ME} procedure=CONS-GIN free=1" for i, s in enumerate(self.free) if self.free[s]]
            return "slots page=1/1\n" + "\n".join(rows)
        return None

    async def say(self, to, text, *, status=None, need=None):
        self.out.append((time.monotonic() - self.t0, need, to, text))
        self.rows.append(msg(ME, to, text, need))
        if to == LEDGER:
            body = self.ledger(text)
            if body:
                row = msg(LEDGER, ME, body, need)
                self.rows.append(row)
                self.tasks.append(asyncio.get_running_loop().create_task(self.turn(row)))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        rows = [r for r in self.rows if need_id is None or r.need_id == need_id]
        return [r for r in rows if not peer or peer in (r.sender, r.recipient)]

    async def find(self, query):
        return []

    async def turn(self, row):
        if all(r.msg_id != row.msg_id for r in self.rows):
            self.rows.append(row)
        await asyncio.wait_for(self.b.main(SimpleNamespace(summary="x", is_emergency=False), View(self, row)), 60)

    async def deliver(self, row, extra=0.2):
        await self.turn(row)
        await asyncio.sleep(extra)
        while any(not t.done() for t in self.tasks):
            await asyncio.gather(*[t for t in self.tasks if not t.done()], return_exceptions=True)

    def to(self, who, head=None):
        return [x for _, _, w, x in self.out if w == who and (head is None or x.split()[0] == head)]

    def receipts_to_citizens(self):
        return [(w, x) for _, _, w, x in self.out if w.startswith("citizen:") and x.startswith("booking.receipt")]


class View:
    def __init__(self, city, inbound):
        self._c, self.inbound, self.agent_id, self.address = city, inbound, city.agent_id, ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def check(label, ok, city=None):
    OKS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + label)
    if not ok and city is not None:
        for t, n, w, x in city.out:
            print(f"      [{t:5.1f}s] {w[-26:]:>26} : {x[:140]!r}")



def peer(pid):
    return SimpleNamespace(agent_id=pid, role="family_counseling", capabilities=("family_counseling.gynecology",))


def candidates():
    b = load()
    got = [p.agent_id for p in b._xa_candidates([peer(IMP), peer(REAL)], ME, "CONS-GIN")]
    check("_xa_candidates: the impostor is dropped, the official partner kept", got == [REAL])


async def confirm_to_impostor():
    city = City(load())
    need = "TR1"
    city.rows += [
        msg(OURCZ, ME, "Vorrei un colloquio al consultorio", need),
        msg(ME, IMP, "need.request service_type=family_counseling procedure=CONS-GIN priority=D request_id=rq-x", need),
        msg(IMP, ME, f"offer offer_id=of-P1 slot_id=P1 at={DAY.replace(hour=10).isoformat()} facility={IMP}", need),
        msg(ME, OURCZ, f"offer offer_id=of-x1 slot_id=P1 facility={IMP} cross_asst=true", need),
    ]
    await city.deliver(msg(OURCZ, ME, "confirm offer_id=of-x1 consent_ref=xc-c1", need))
    await city.deliver(msg(OURCZ, ME, "data.response purpose=cross_asst consent_ref=xc-x1", need))
    check("confirm of an offer naming the impostor: no citizen data sent to it",
          not any("citizen=" in x or OURCZ in x for x in city.to(IMP)), city)
    check("the citizen still gets an answer", bool(city.to(OURCZ)), city)


async def main():
    candidates()
    await confirm_to_impostor()
    print("ALL OK" if all(OKS) else "SOME FAILED")
    raise SystemExit(0 if all(OKS) else 1)


asyncio.run(main())
