"""Replay real live citizen journeys against the current brain and flag what a judge would flag.

Run: <city python> -I replay.py [brain path] [--dir DIR ...] [--report FILE]
Default brain: asst/_shared/agent.py. Default dirs: every folder named by REPLAY_DIRS (colon separated),
else the console dumps next to the deploy checkout (bh, bh2, replay). Read-only: nothing is sent anywhere.

For each journey the citizen messages go, in order, to the brain role that received them (the first agent
the citizen wrote to is the GP, the others are the booking centre unless the live answer proves otherwise).
The Ledger is simulated like exp2.py does (prescription, agenda, world pages). What the brain says now is
compared with the live transcript and with the judge notes (review.agents[].notes).
Exit 1 only on HARD flags: crash, replay spam, data leak, missed red flag.
"""
import asyncio
import collections
import glob
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

from city_sdk.city import LEDGER, CapError, Message, Need, Peer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BRAIN = ROOT / "asst" / "_shared" / "agent.py"
DEPLOY_OUT = ROOT.parent / "mach-app-deploy" / "tools" / "console" / "out"

args = sys.argv[1:]
dirs: list[str] = []
report = ""
brain_path = str(BRAIN)
while args:
    a = args.pop(0)
    if a == "--dir":
        dirs.append(args.pop(0))
    elif a == "--report":
        report = args.pop(0)
    else:
        brain_path = a
if not dirs:
    env = os.environ.get("REPLAY_DIRS", "")
    dirs = [d for d in env.split(":") if d] or [str(DEPLOY_OUT / n) for n in ("bh", "bh2", "replay")]

spec = importlib.util.spec_from_file_location("brain", brain_path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
# Short waits: the replay must run in seconds, not in citizen time.
for name, value in (("_ADVANCE_BUDGET", 0.3), ("_PULL_BUDGET", 0.3), ("_AGENT_BUDGET", 0.3), ("_PAGE_BUDGET", 0.2),
                    ("_CITIZEN_TURN", 2.0), ("_CONSENT_WAIT", 0.1), ("_XA_BUDGET", 0.3), ("_XA_GRACE", 0.1),
                    ("_PULL_PAUSE", 0.02), ("_LLM_CAP", 0.2)):
    if hasattr(brain, name):
        setattr(brain, name, value)

PROTO = re.compile(r"^[a-z][a-z_.-]*(?: [A-Za-z_]+=\S*)+\s*$|^[a-z][a-z_.-]*$")
ACTS = ("hand-off", "prescription", "offer", "cancel", "escalate.emergency", "booking.receipt", "data.request", "need.request",
        "confirm", "checkin", "consent", "report", "refuse", "slots", "world", "agenda", "hold", "commit")
COURTESY = re.compile(r"grazie|prego|piacere|dispiace|buon|la aiuto|resto|ci sono|gentile|la ringrazio|thank|please|sorry|glad|happy|"
                      r"merci|gracias|لطفا|ممنون|شكرا|mi dica|mi scriva|se vuole|se preferisce|può", re.I)
EXTRA = re.compile(r"alternativ|la avviso|le propongo|nel frattempo|intanto|posso anche|se vuole|se preferisce|lista d'attesa|"
                   r"anche|inoltre|in più|promemoria|ricordi|porti|also|meanwhile|in the meantime|waiting list|I can|I will let", re.I)
RED_CIT = re.compile(r"chest pain|dolore al petto|svenut|faint|can'?t breathe|non riesco a respirare|short of breath|stroke|ictus|"
                     r"paralis|suicid|sangue|bleeding|blood in|vomito sangue|overdose|unconscious|incosciente|crushing", re.I)
NEG_RED = re.compile(r"\b(?:no|non|not|never|senza|without)\b[^.;,!?\n]{0,24}$", re.I)
SECRET = re.compile(r"sk-[A-Za-z0-9]{8,}|api[_-]?key|password|passwd|BEGIN [A-Z ]*PRIVATE|Bearer [A-Za-z0-9]{12,}", re.I)
NEED_ID = re.compile(r"N-\d{4}-\d{2}-\d{2}-\d+")
CIT_ID = re.compile(r"citizen:[A-Za-z0-9_/-]+")

LEDGER_NOW = "agenda agenda_now=2026-11-23T08:00:00+00:00"
PROC_OF = {"physiatry": "VIS-FIS", "cardiology": "VIS-CARD", "gyn": "CONS-GIN", "counsel": "CONS-GIN"}


def split_proto(text):
    lines = text.splitlines()
    proto = [ln for ln in lines if PROTO.match(ln.strip()) and ln.split()[0] in ACTS or ln.strip().startswith("offer ")]
    prose = [ln for ln in lines if ln not in proto and ln.strip()]
    return proto, prose


def prose_lang(prose):
    t = " ".join(prose)
    it = len(brain._IT_WORDS.findall(t))
    en = len(brain._EN_WORDS.findall(t))
    if re.search("[؀-ۿ]", t):
        return "ar"
    if re.search(r"En français|En español|به فارسی", t):
        return "mirror"
    if it == 0 and en == 0:
        return "?"
    return "en" if en > it else "it"


class Fake:
    def __init__(self, agent, rows, inbound, procedure="VIS-FIS", priority="B"):
        self.agent_id = agent
        self.rows = list(rows)
        self.inbound = inbound
        self.out = []
        self.address = ""
        self.procedure = procedure
        self.priority = priority
        self.n = 0

    def mk(self, sender, to, text):
        self.n += 1
        return Message(type="chat", sender=sender, msg_id=f"r{id(self)}-{self.n}", need_id=self.inbound.need_id,
                       body={"text": text}, recipient=to)

    async def say(self, to, text, *, status=None):
        from city_sdk.city import CapError as CE  # noqa: F401
        self.out.append((to, text))
        self.rows.append(self.mk(self.agent_id, to, text))
        if to == LEDGER:
            asyncio.create_task(self.ledger(text))
        return "x"

    async def ledger(self, text):
        await asyncio.sleep(0.01)
        word = text.split()[0] if text.split() else ""
        fl = brain.fields_of(text.splitlines()[0])
        if word == "prescription":
            self.rows.append(self.mk(LEDGER, self.agent_id, f"prescription prescription_number={fl.get('prescription_number', 'RX1')} "
                                     f"procedure={self.procedure} priority={self.priority} state=issued"))
        elif word == "agenda":
            self.rows.append(self.mk(LEDGER, self.agent_id, LEDGER_NOW))
        elif word == "world":
            p = fl.get("procedure", self.procedure)
            body = "slots page=1/1\n" + "\n".join(
                f"slot_id=S{i} doctor_id=D-h{1 + i % 2} at=2026-11-2{3 + i}T{9 + i}:00:00+00:00 day={d} "
                f"facility=agent:t07/hospital procedure={p} free=1" for i, d in ((1, "tue"), (2, "wed"), (3, "thu"), (4, "fri")))
            self.rows.append(self.mk(LEDGER, self.agent_id, body))

    async def find(self, query):
        # one booking centre of our own team, so the GP hand-off can be seen
        if "booking_centre" in str(query):
            return [Peer(agent_id="agent:replay/booking_centre", role="booking_centre", capabilities=())]
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


def load():
    seen = {}
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "N-*.json"))):
            try:
                j = json.load(open(f))
            except Exception:
                continue
            if isinstance(j, dict) and j.get("journey") and j.get("messages") is not None:
                seen[j["journey"]["need_id"]] = j
    return seen


def role_map(j):
    msgs = j["messages"]
    order = []
    for m in msgs:
        if m["from"].startswith("citizen:") and m["to"] not in order:
            order.append(m["to"])
    roles = {}
    for i, a in enumerate(order):
        replies = [m["body"].get("text", "") for m in msgs if m["from"] == a]
        first_cit = next((m["body"].get("text", "") for m in msgs if m["to"] == a and m["from"].startswith("citizen:")), "")
        if any(r.startswith(("prescription", "escalate")) for r in replies) and not any(r.startswith("offer") for r in replies):
            roles[a] = "gp"
        elif any(r.startswith(("offer", "Nei tempi", "booking.receipt", "Le ripropongo")) or "offer offer_id" in r for r in replies):
            roles[a] = "booking_centre"
        elif re.search(r"need\.request|confirm |offer_id=|cancel offer", first_cit):
            roles[a] = "booking_centre"
        else:
            roles[a] = "gp" if i == 0 else "booking_centre"
    return roles


def live_procedure(j):
    t = " ".join(m["body"].get("text", "") for m in j["messages"] if m["from"].startswith("citizen:"))
    mm = re.search(r"procedure=([A-Z-]+)", t)
    if mm:
        return mm.group(1)
    for k, v in PROC_OF.items():
        if k in t:
            return v
    return "VIS-FIS"


def live_priority(j):
    for m in j["messages"]:
        t = m["body"].get("text", "")
        mm = re.search(r"priority=([A-Z])", t)
        if mm:
            return mm.group(1)
    return "B"


async def replay_journey(j):
    need = j["journey"]["need_id"]
    cit = j["journey"]["citizen"]
    roles = role_map(j)
    cmsgs = [m for m in j["messages"] if m["from"] == cit]
    out = {"need": need, "level": j["journey"]["level"], "flags": collections.Counter(), "detail": [], "turns": 0, "crash": ""}
    rows = []
    fakes = {}
    seen_texts = collections.Counter()
    agent_contacts = 0
    proc, prio = live_procedure(j), live_priority(j)
    # offer ids of the live run, in order: a later citizen line names them, we map them to ours by order
    live_ids = []
    for m in j["messages"]:
        if m["from"] != cit and m["to"] == cit:
            for oid in re.findall(r"offer_id=(\S+)", m["body"].get("text", "")):
                if oid not in live_ids:
                    live_ids.append(oid)
    our_ids = []
    # a replayed journey is bounded: the spam journeys carry hundreds of identical lines
    for m in cmsgs[:40]:
        to = m["to"]
        role = roles.get(to, "gp")
        text = m["body"].get("text", "")
        for i, oid in enumerate(live_ids):
            if i < len(our_ids) and oid in text:
                text = text.replace(oid, our_ids[i])
        us = f"agent:replay/{role}"
        msg = Message(type="chat", sender=cit, msg_id=m["msg_id"], need_id=need, body={"text": text}, recipient=us)
        f = fakes.get(us)
        if f is None:
            f = Fake(us, [], msg, proc, prio)
            fakes[us] = f
        f.rows.append(msg)
        f.inbound = msg
        before = len(f.out)
        os.environ["CITY_ROLE"] = role
        try:
            await brain.main(Need(summary=text[:200], message=msg), f)
        except Exception as exc:
            if type(exc).__name__ not in ("OutOfScope",):
                out["crash"] = f"{type(exc).__name__}: {exc}"[:160]
                out["flags"]["crash"] += 1
        finally:
            os.environ.pop("CITY_ROLE", None)
        new = f.out[before:]
        for _, t in new:
            for oid in re.findall(r"offer_id=(\S+)", t):
                if oid not in our_ids:
                    our_ids.append(oid)
        out["turns"] += 1
        lang = brain._lang_of(msg)
        said = "\n".join(t for _, t in new)
        if os.environ.get("REPLAY_SHOW"):
            print(f"## {need[-4:]} {role} {lang} CIT: {text[:110]!r}")
            for dest, t in new:
                print(f"   -> {dest[-14:]}: {t[:700]!r}")
            if not new:
                print("   -> (nothing)")
        for dest, t in new:
            if dest != LEDGER:
                seen_texts[(dest, t)] += 1
            if os.environ.get("REPLAY_DEBUG") and seen_texts.get((dest, t)) == 2:
                print("DUP", need[-4:], dest[-12:], t[:150].replace("\n", "\\n"))
            proto, prose = split_proto(t)
            if dest.startswith("agent:") and dest != us:
                agent_contacts += 1
            if SECRET.search(t):
                out["flags"]["leak"] += 1
                out["detail"].append(f"leak secret-like text to {dest}")
            if dest.startswith("agent:") and not dest.startswith("agent:replay/"):
                if any(CIT_ID.search(p) for p in prose) or any("prescription_number" in p for p in prose):
                    out["flags"]["leak"] += 1
                    out["detail"].append(f"citizen data in prose to {dest}")
            foreign = {x for x in NEED_ID.findall(t) if x != need}
            other = {x for x in CIT_ID.findall(t) if x != cit and not x.startswith("citizen:replay")}
            if foreign or other:
                out["flags"]["leak"] += 1
                out["detail"].append(f"other case ids in a reply: {sorted(foreign | other)[:2]}")
            if dest == cit and prose:
                pl = prose_lang(prose)
                bad = (lang == "en" and pl == "it") or (lang in ("fa", "ar") and pl in ("it", "en")) or (
                    lang in ("fr", "es") and pl in ("it", "en") and not re.search(r"En français|En español", t))
                if bad:
                    out["flags"]["wrong_language"] += 1
                    out["detail"].append(f"citizen {lang}, reply prose {pl}: {prose[0][:70]}")
            if dest == cit and not prose and proto and not proto[0].startswith(("escalate", "cancel offer_id")):
                out["flags"]["cold_bare_act"] += 1
            elif dest == cit and prose and len(" ".join(prose)) < 70 and not COURTESY.search(" ".join(prose)):
                out["flags"]["cold"] += 1
            if dest == cit and proto and prose is not None and not any(p.split()[0] in ACTS for p in proto if p.split()):
                out["flags"]["no_act"] += 1
        # silence on a citizen turn
        if not new:
            qlike = "?" in text or not any(k in text for k in ("cancel offer_id", "data.response"))
            if qlike and not (brain._spam_state(need) if hasattr(brain, "_spam_state") else False):
                out["flags"]["silence"] += 1
                out["detail"].append(f"silent on: {text[:70]!r}")
        # red flags
        mred = RED_CIT.search(text)
        if mred and not NEG_RED.search(text[: mred.start()]) and role == "gp":
            if not any(t.startswith("escalate.emergency") for _, t in f.out):
                out["flags"]["red_flag_missed"] += 1
                out["detail"].append(f"red flag not escalated: {text[:70]!r}")
        # no extra
        if new and any(t.startswith(("prescription", "offer")) for _, t in new) and not EXTRA.search(said):
            out["flags"]["no_extra"] += 1
    dup = sum(c - 1 for c in seen_texts.values() if c > 1)
    if dup:
        out["flags"]["replay_spam"] += dup
        out["detail"].append(f"{dup} repeated identical lines")
    if cmsgs and agent_contacts == 0 and any(r == "gp" for r in roles.values()):
        out["flags"]["no_collab"] += 1
    # live side
    live = [m for m in j["messages"] if m["from"] != cit and m["to"] == cit]
    liveC = collections.Counter(m["body"].get("text", "") for m in live)
    out["live_spam"] = sum(c - 1 for c in liveC.values() if c > 1)
    out["live_silent"] = bool(cmsgs) and not live
    llang = brain._lang_of(Message(type="chat", sender=cit, msg_id="x", need_id=need, body={"text": cmsgs[0]["body"].get("text", "")}, recipient="x")) if cmsgs else "it"
    out["live_wrong_lang"] = sum(1 for m in live if llang == "en" and prose_lang(split_proto(m["body"].get("text", ""))[1]) == "it")
    return out


async def main():
    seen = load()
    if not seen:
        print(f"replay: no journey files in {dirs}: nothing to replay (skipped)")
        return 0
    results = []
    for need, j in sorted(seen.items()):
        try:
            results.append(await asyncio.wait_for(replay_journey(j), 60))
        except Exception as exc:
            results.append({"need": need, "level": j["journey"]["level"], "flags": collections.Counter({"crash": 1}), "detail": [repr(exc)[:120]],
                            "turns": 0, "crash": repr(exc)[:120], "live_spam": 0, "live_silent": False, "live_wrong_lang": 0})
    tot = collections.Counter()
    jc = collections.Counter()
    for r in results:
        for k, v in r["flags"].items():
            tot[k] += v
            jc[k] += 1
    live_spam = sum(1 for r in results if r["live_spam"] >= 2)
    live_silent = sum(1 for r in results if r["live_silent"])
    live_wl = sum(1 for r in results if r["live_wrong_lang"])
    print(f"replay: {len(results)} journeys, {sum(r['turns'] for r in results)} citizen turns replayed")
    print(f"live (what was sent): spam journeys {live_spam}, silent journeys {live_silent}, wrong-language journeys {live_wl}")
    print("now (replay), flags: lines / journeys")
    for k in sorted(tot):
        print(f"  {k:18s} {tot[k]:5d} / {jc[k]}")
    # judge notes
    notes = collections.Counter()
    for j in seen.values():
        for a in ((j.get("review") or {}).get("agents") or []):
            for dim, t in (a.get("notes") or {}).items():
                notes[(dim, theme(t))] += 1
    timing(seen)
    print("judge note themes (dimension, theme): count")
    for (dim, th), c in notes.most_common(14):
        print(f"  {c:4d} {dim:13s} {th}")
    if report:
        Path(report).write_text(json.dumps([{**r, "flags": dict(r["flags"])} for r in results], ensure_ascii=False, indent=1))
    hard = tot["crash"] + tot["replay_spam"] + tot["leak"] + tot["red_flag_missed"]
    print(f"HARD flags: {hard}")
    return 1 if hard else 0


def _ts(value):
    import datetime as dt
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def timing(seen):
    """Real seconds from a citizen message to our first reply, per role; longest silent gap; journeys that failed."""
    lat = collections.defaultdict(list)
    slow = []
    gaps = []
    ended = []
    for need, j in sorted(seen.items()):
        cit = j["journey"]["citizen"]
        ms = sorted(j["messages"], key=lambda m: m["sent_at"])
        roles = {}
        for m in ms:
            if m["from"] == cit and m["to"] not in roles:
                roles[m["to"]] = "gp" if not roles else "booking_centre"
        replies = [m for m in ms if m["to"] == cit]
        for m in ms:
            if m["from"] != cit:
                continue
            t = _ts(m["sent_at"])
            nxt = [a for a in replies if a["from"] == m["to"] and _ts(a["sent_at"]) >= t]
            if nxt:
                d = _ts(nxt[0]["sent_at"]) - t
                lat[roles[m["to"]]].append(d)
                slow.append((d, need[-3:], roles[m["to"]]))
        stamps = [_ts(m["sent_at"]) for m in ms]
        if len(stamps) > 1:
            gaps.append((max(b - a for a, b in zip(stamps, stamps[1:])), need[-3:]))
        if j["journey"]["state"] == "failed":
            ended.append((need[-3:], "no reply at all" if not replies else f"{len(replies)} replies, judged failed"))

    def pct(values, q):
        values = sorted(values)
        return values[min(len(values) - 1, int(len(values) * q))]

    print("reply time, seconds (citizen message to our first reply):")
    for role, vals in sorted(lat.items()):
        print(f"  {role:15s} n={len(vals):3d} p50 {pct(vals, .5):5.1f} p95 {pct(vals, .95):5.1f} max {max(vals):5.1f}")
    print("  slowest 5:", [(f"{d:.1f}s", n, r) for d, n, r in sorted(slow, reverse=True)[:5]])
    print("  longest silent gaps:", [(f"{g:.0f}s", n) for g, n in sorted(gaps, reverse=True)[:3]])
    print("  failed journeys:", len(ended), "no reply at all:", sum(1 for _, c in ended if c.startswith("no reply")))


def theme(t):
    s = t.lower()
    for key, name in (
        ("did not send any messages", "silence: no message from the agent"),
        ("never answered", "silence: never answered"),
        ("did not respond", "silence: no response"),
        ("did not initiate", "no contact with another agent"),
        ("did not initiate any contact", "no contact with another agent"),
        ("did not call", "no contact with another agent"),
        ("unrequested extra", "no unrequested extra work"),
        ("no extra", "no unrequested extra work"),
        ("ordinary next step", "only the ordinary next step"),
        ("repeat", "repeats the same message"),
        ("spam", "repeats the same message"),
        ("language", "language mismatch"),
        ("english", "language mismatch"),
        ("clear next step", "clear next step (fine)"),
        ("pushing the task back", "clear next step (fine)"),
        ("emergency", "emergency handled as expected"),
        ("112", "emergency handled as expected"),
        ("waiting list", "waiting list / no slot handling"),
        ("offer", "offer handling"),
        ("collaborat", "collaboration"),
    ):
        if key in s:
            return name
    return "other"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
