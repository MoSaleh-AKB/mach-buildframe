"""Shared fake City for the historian regress tests (local only, never the network).

Every test: <city python> -I <test.py> <path to agent.py>
Prints one line per check:  RESULT <id> <role> PASS|FAIL|SKIP <why>
Exit code: 0 all PASS (or SKIP), 1 any FAIL, 3 harness error.

Model of the live city (from 1-RULES section 4 and tonight's live facts):
- live agent ids have NO team part ("agent:y6hxsd766sys"), live roles are hospital, specialist_care,
  family_counseling, booking_centre, gp
- the Ledger answers 8-20 s late (DELAY), as a pushed turn (push=True) or only in history (push=False)
- every citizen message needs a visible reply; the judge's fast-reply clock is 10 s, our rule is 3 s
- sockets are blocked: a brain that calls out (weather, model) gets an OSError at once, as when the
  remote answers 429 / times out. No test ever reaches the live site.
"""
import asyncio
import importlib.util
import os
import socket
import sys
import tempfile
import time
from collections import Counter

from city_sdk.city import LEDGER, Message, Need, Peer, understood

LIVE = {
    "booking_centre": "agent:y6hxsd766sys",
    "gp": "agent:jmqwr12vy2j6",
    "hospital": "agent:r8yprrs0e8be",
    "specialist_care": "agent:dqfw9nh15dn4",
    "family_counseling": "agent:pv3a0t3tyqdq",
}
NOW = "2026-11-23T08:00:00+00:00"
FAILS = []


def _no_net(*_a, **_k):
    raise OSError("regress: network blocked (treat like a 429 / timeout)")


socket.socket.connect = _no_net
socket.socket.connect_ex = _no_net
socket.create_connection = _no_net
socket.getaddrinfo = _no_net


# REGRESS_ROLES=gp,family_counseling runs only those roles (a base deploys only some roles);
# every other role prints SKIP and its brain is a stub that never answers.
WANT = {r for r in os.environ.get("REGRESS_ROLES", "").replace(" ", "").split(",") if r}


class _Stub:
    async def main(self, need, view):
        return None


def load(path, role):
    if WANT and role not in WANT:
        return _Stub()
    os.environ["CITY_ROLE"] = role
    os.environ.setdefault("CITY_HOME", tempfile.mkdtemp(prefix="regress_home_"))
    spec = importlib.util.spec_from_file_location("brain_" + role, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def result(rid, role, ok, why):
    if WANT and role not in WANT and role != "HARNESS":
        print(f"RESULT {rid} {role} SKIP role not in REGRESS_ROLES", flush=True)
        return
    state = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    if ok is False:
        FAILS.append(rid)
    print(f"RESULT {rid} {role} {state} {why}", flush=True)


def done():
    sys.exit(1 if FAILS else 0)


def fields(text):
    return dict(t.split("=", 1) for t in (text or "").split() if "=" in t)


def check_tokens(text):
    parsed = understood(Message(type="chat", sender="x", msg_id="g", need_id="n", body={"text": text}, recipient="y"))
    bad = []
    for key, value in parsed.body.items():
        if key != "text" and isinstance(value, str) and (not value or value[-1] in ".;:!?"):
            bad.append(f"{key}={value!r}")
    return bad


def slot(i, at, day, fac, doc="asst-centro-h1", proc="VIS-FIS", extra=""):
    return f"slot_id={i} doctor_id={doc} at={at} day={day} facility={fac} procedure={proc} free=1{extra}"


class World:
    """One fake City shared by all turns of a test. `rx` maps prescription numbers to Ledger lines;
    `slots` is the free world; `bots` maps an agent id to an async fn(world, sender, text, need)."""

    def __init__(self, brain, role, *, delay=8.0, push=True, slots=None, rx=None, peers=None,
                 bots=None, agenda=NOW, index_delay=0.0, ledger=None):
        self.brain, self.role, self.me = brain, role, LIVE[role]
        self.delay, self.push = delay, push
        self.slots = list(slots or [])
        self.rx = dict(rx or {})
        self.peers = list(peers if peers is not None else default_peers())
        self.bots = dict(bots or {})
        self.agenda = agenda
        self.index_delay = index_delay
        self.custom = ledger  # optional fn(op_text) -> answer or None (falls back to the default)
        self.rows = []  # every message of every need
        self.out = []  # (to, text, t, need, src_msg_id)
        self.ledger_ops = []
        self.tasks = set()
        self.errors = []
        self.taken, self.holds, self.n, self.msgn = set(), {}, 0, 0
        self.t0 = time.perf_counter()
        self.bad_tokens = []
        self.hist_exc = None  # exception raised by the next history() call (relay ReadTimeout)
        self.hist_exc_calls = 0
        self.timeouts = []  # turns that ended by re-raising a transport Timeout
        self.op_delay = {}  # per Ledger op delay override, e.g. {"agenda": 25.0}

    def now(self):
        return time.perf_counter() - self.t0

    def msg(self, sender, to, text, need):
        self.msgn += 1
        return Message(type="chat", sender=sender, msg_id=f"m{self.msgn}", need_id=need,
                       body={"text": text}, recipient=to, sent_at="")

    def spawn(self, coro):
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    # ------------------------------------------------------------------ the Ledger
    def answer(self, text, need):
        if self.custom is not None:
            got = self.custom(self, text, need)
            if got is not None:
                return got
        w = text.split()
        op = w[0] if w else ""
        f = fields(text)
        if op == "agenda":
            return f"agenda agenda_now={self.agenda}"
        if op == "world":
            free = [s for s in self.slots if fields(s).get("slot_id") not in self.taken]
            return "slots page=1/1\n" + "\n".join(free) if free else "slots page=1/1 none"
        if op == "hold":
            sid = f.get("slot_id", "")
            if sid in self.taken:
                return f"refused op=hold slot_id={sid} code=SLOT_TAKEN"
            self.n += 1
            hid = f"h-{self.n}"
            self.holds[hid] = sid
            self.taken.add(sid)
            return f"held hold_id={hid} slot_id={sid} need_id={need} fence=1"
        if op == "commit":
            hid = f.get("hold_id", "")
            return f"committed booking_id={hid} ledger_ref=lr-{hid} slot_id={self.holds.get(hid, '')}"
        if op == "checkin":
            return f"checked_in booking_id={f.get('booking_id', '')}"
        if op == "cancel":
            bid = f.get("booking_id") or f.get("hold_id", "")
            self.taken.discard(self.holds.get(bid, ""))
            return "cancelled " + " ".join(t for t in w[1:] if "=" in t)
        if op == "prescribe":
            self.n += 1
            num = f.get("prescription_number") or f"RX-N{self.n}"
            return (f"prescribed prescription_number={num} procedure={f.get('procedure', '')} "
                    f"priority={f.get('priority', '')} state=issued")
        if op == "prescription":
            num = f.get("prescription_number", "")
            if num in self.rx:
                return self.rx[num]
            return f"refused op=prescription prescription_number={num} code=NOT_FOUND"
        if op == "report":
            return f"reported booking_id={f.get('booking_id', '')} report_id=rp-1"
        if op == "consent":
            return f"consent consent_ref=cr-{need} purpose={f.get('purpose', '')}"
        return ""  # rules, remember, recall, history: nothing

    async def _deliver(self, text, need):
        op = text.split()[0] if text.split() else ""
        await asyncio.sleep(self.op_delay.get(op, self.delay))
        ans = self.answer(text, need)
        if not ans:
            return
        m = self.msg(LEDGER, self.me, ans, need)
        self.rows.append(m)
        if self.push:
            await self.run(m)

    # ------------------------------------------------------------------ turns
    async def run(self, m):
        view = View(self, m)
        try:
            await self.brain.main(Need(summary=m.text, message=m), view)
        except Exception as exc:  # OutOfScope is a normal end of a turn
            if "Timeout" in type(exc).__name__:
                self.timeouts.append(type(exc).__name__)
            elif type(exc).__name__ not in ("OutOfScope",):
                self.errors.append(f"{type(exc).__name__}: {exc}")

    async def send(self, sender, text, need, *, wait=True):
        m = self.msg(sender, self.me, text, need)
        m_at = self.now()
        self.rows.append(m)
        if wait:
            await self.run(m)
        else:
            self.spawn(self.run(m))
        return m, m_at

    async def settle(self, cap=90.0):
        t = self.now()
        while self.tasks and self.now() - t < cap:
            await asyncio.wait(list(self.tasks), timeout=max(0.1, cap - (self.now() - t)))

    # ------------------------------------------------------------------ reading the result
    def said_to(self, who, need=None, since=0.0):
        return [(tx, at) for to, tx, at, nd, _ in self.out
                if to == who and at >= since and (need is None or nd == need)]

    def first_reply(self, who, since, need=None):
        got = [at for _, at in self.said_to(who, need, since)]
        return (min(got) - since) if got else None

    def acts(self, who=None, need=None):
        return [tx.split()[0] if tx.split() else "" for to, tx, _, nd, _ in self.out
                if (who is None or to == who) and (need is None or nd == need)]

    def ops(self, op):
        return [t for t in self.ledger_ops if t.split() and t.split()[0] == op]


class View:
    def __init__(self, world, inbound):
        self.world, self.inbound = world, inbound
        self.agent_id, self.address = world.me, ""

    async def say(self, to, text, *, status=None):
        w = self.world
        bad = check_tokens(text)
        if bad:
            w.bad_tokens.append((to, bad, text[:80]))
        need = self.inbound.need_id
        w.out.append((to, text, w.now(), need, self.inbound.msg_id))
        w.rows.append(w.msg(w.me, to, text, need))
        if to == LEDGER:
            w.ledger_ops.append(text)
            w.spawn(w._deliver(text, need))
        bot = w.bots.get(to)
        if bot is not None:
            w.spawn(bot(w, to, text, need))
        return f"x{len(w.out)}"

    async def find(self, query):
        if self.world.index_delay:
            await asyncio.sleep(self.world.index_delay)
        return list(self.world.peers)

    async def history(self, peer=None, *, need_id=None):
        w = self.world
        if w.hist_exc is not None and w.hist_exc_calls > 0:
            w.hist_exc_calls -= 1
            raise w.hist_exc
        rows = list(w.rows)
        if need_id is not None:
            rows = [r for r in rows if r.need_id == need_id]
        return rows


def default_peers():
    return [
        Peer(agent_id=LIVE["booking_centre"], role="booking_centre", service="booking", district=1,
             capabilities=("booking",)),
        Peer(agent_id=LIVE["gp"], role="gp", service="gp", district=1),
        Peer(agent_id=LIVE["hospital"], role="hospital", service="hospital", district=1,
             capabilities=("VIS-FIS", "VIS-CARD", "VIS-ORTO")),
        Peer(agent_id=LIVE["specialist_care"], role="specialist_care", service="specialist", district=1),
        Peer(agent_id=LIVE["family_counseling"], role="family_counseling", service="consultorio", district=1),
    ]


def run(coro):
    try:
        asyncio.run(coro)
    except SystemExit:
        raise
    except BaseException as exc:  # harness error, not a brain verdict
        print(f"RESULT HARNESS - SKIP harness error {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        sys.exit(3)
    done()


def counts(seq):
    return Counter(seq)


def offer_lines(world, who, since=0.0, need=None):
    """Every `offer offer_id=... slot_id=...` line said to `who`, as dicts, in order."""
    got = []
    for tx, _ in world.said_to(who, need, since):
        for ln in tx.splitlines():
            if ln.startswith("offer ") and "offer_id=" in ln:
                got.append(fields(ln))
    return got


def escalated(world, who=None, since=0.0):
    return any(tx.split()[:1] == ["escalate.emergency"] or "escalate.emergency" in tx.split("\n")[0]
               for to, tx, at, _, _ in world.out if at >= since and (who is None or to == who))


HO = LIVE["hospital"]
SP = LIVE["specialist_care"]
FC = LIVE["family_counseling"]
BC = LIVE["booking_centre"]
GP = LIVE["gp"]
RX1 = "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"
DESK_ASK = "need.request prescription_number=RX1 lang=it area.district=1 text=vorrei_prenotare_la_visita"
CONS_ASK = "Buongiorno, vorrei prenotare una visita ginecologica al consultorio"


def hosp_slots():
    return [
        slot("S1", "2026-11-23T14:00:00+00:00", "mon", HO),
        slot("S2", "2026-11-24T09:00:00+00:00", "tue", HO),
        slot("S3", "2026-11-25T09:00:00+00:00", "wed", HO, doc="asst-centro-h2"),
        slot("S4", "2026-11-25T11:00:00+00:00", "wed", HO),
        slot("S5", "2026-11-26T10:00:00+00:00", "thu", HO, doc="asst-centro-h2"),
    ]


def cons_slots():
    return [
        slot("C1", "2026-11-23T14:00:00+00:00", "mon", FC, doc="D-c1", proc="CONS-GIN"),
        slot("C2", "2026-11-24T10:00:00+00:00", "tue", FC, doc="D-c1", proc="CONS-GIN"),
        slot("C3", "2026-11-25T11:00:00+00:00", "wed", FC, doc="D-c2", proc="CONS-GIN"),
        slot("C4", "2026-11-26T09:00:00+00:00", "thu", FC, doc="D-c2", proc="CONS-GIN"),
    ]


def booked_rows(world, cz, need, sid="S2", bid="bk-1"):
    """History of a desk case that offered S1..S5, got a confirm and relayed the receipt."""
    m = world.msg
    rows = [m(cz, BC, DESK_ASK, need), m(BC, LEDGER, "prescription prescription_number=RX1", need),
            m(LEDGER, BC, RX1, need), m(LEDGER, BC, f"agenda agenda_now={world.agenda}", need),
            m(LEDGER, BC, "slots page=1/1\n" + "\n".join(world.slots), need)]
    off = "\n".join(f"offer offer_id=of-{fields(s)['slot_id']} slot_id={fields(s)['slot_id']} doctor_id={fields(s)['doctor_id']} "
                    f"facility={HO} procedure=VIS-FIS prescription_number=RX1" for s in world.slots)
    rows += [m(BC, cz, off + "\nScelga una data", need),
             m(cz, BC, f"confirm offer_id=of-{sid} consent_ref=cons-{need}", need),
             m(BC, HO, f"confirm offer_id=of-{sid} slot_id={sid} citizen={cz} consent_ref=cons-{need} facility={HO} prescription_number=RX1", need),
             m(HO, BC, f"booking.receipt booking_id={bid} ledger_ref=lr-{bid} slot_id={sid}\nOk", need),
             m(BC, cz, f"booking.receipt booking_id={bid} ledger_ref=lr-{bid} slot_id={sid}\nLa sua prenotazione e confermata", need)]
    world.rows.extend(rows)
    world.taken.add(sid)
    return rows
