#!/usr/bin/env python3
"""Pull and normalise the team console Journeys ("Percorsi dei cittadini").

Sources (pick one):
  --live              GET the console JSON through MoSaleh's hidden Edge (`edge fetch`, his login cookies).
  --dump FILE         a JSON dump saved from the browser (dump.js, run in the console tab or via `edge js`).

Endpoints used (read-only, cookie session, same as the console page):
  GET /v1/team/journeys              list: need_id, wave_id, citizen, level, state, updated_at
  GET /v1/team/journeys/<need_id>    detail: journey, messages, verdict, ratings, review
  GET /v1/team/assignment            roster: role family -> agent ids (once confirmed)

Writes out/journeys.json (normalised list). Stdlib only. Never prints cookies or tokens.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = "https://projectnandaitaly.ai"
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
CACHE = OUT / "cache"
# Card folders in the deploy worktree hold agent_id per role (folder name = role family).
_CARD_DIRS = [HERE.parents[1] / "asst", HERE.parents[2] / "mach-app-deploy" / "asst"]
DEFAULT_CARDS = next((d for d in _CARD_DIRS if any(d.glob("*/card.yaml"))), _CARD_DIRS[0])
FOLDER_FAMILY = {"specialist": "specialist_care"}
STATE_TO_DONE = {"done", "failed", "escalated"}


# ---------------------------------------------------------------- fetching

def edge_get(path):
    """GET a console path with the hidden Edge. Returns parsed JSON or raises."""
    url = BASE + path
    for attempt in (1, 2):
        res = subprocess.run(["edge", "fetch", url], capture_output=True, text=True, timeout=90)
        body = res.stdout.strip()
        if body.startswith("{") or body.startswith("["):
            return json.loads(body)
        if attempt == 1:
            # Page not open (or session page lost): open the console once, then retry.
            subprocess.run(["edge", "open", BASE + "/team"], capture_output=True, text=True, timeout=90)
            time.sleep(2)
    raise RuntimeError(f"edge fetch {path}: not JSON (logged out of the console?) {body[:120]!r}")


def pull_live(force=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    listing = edge_get("/v1/team/journeys")
    try:
        assignment = edge_get("/v1/team/assignment")
    except Exception as exc:  # roster is optional
        print(f"warn: assignment not read: {exc}", file=sys.stderr)
        assignment = {}
    details = {}
    for row in listing.get("journeys", []):
        nid = row["need_id"]
        cpath = CACHE / (safe_name(nid) + ".json")
        if cpath.exists() and not force:
            cached = json.loads(cpath.read_text())
            same = cached.get("journey", {}).get("updated_at") == row.get("updated_at")
            judged = bool(cached.get("verdict")) and bool(cached.get("review"))
            if same and (judged or row.get("state") not in STATE_TO_DONE):
                details[nid] = cached
                continue
        try:
            d = edge_get("/v1/team/journeys/" + quote(nid))
        except Exception as exc:
            print(f"warn: detail {nid}: {exc}", file=sys.stderr)
            continue
        d.setdefault("journey", {}).setdefault("updated_at", row.get("updated_at"))
        cpath.write_text(json.dumps(d))
        details[nid] = d
    return {"list": listing, "details": details, "assignment": assignment, "pulled_at": now_iso()}


def quote(s):
    from urllib.parse import quote as q
    return q(s, safe="")


def safe_name(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[-120:]


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- roles

def role_map(assignment, cards_dir):
    """agent_id -> role family. Roster first, then local card.yaml files."""
    roles = {}
    for r in (assignment or {}).get("roles", []):
        fam = r.get("family")
        ids = list(r.get("agents") or [])
        if r.get("confirmed_agent"):
            ids.append(r["confirmed_agent"])
        for a in ids:
            aid = a.get("agent_id") if isinstance(a, dict) else a
            if aid:
                roles[aid] = fam
    cards = Path(cards_dir) if cards_dir else None
    if cards and cards.is_dir():
        for card in cards.glob("*/card.yaml"):
            for line in card.read_text().splitlines():
                if line.startswith("agent_id:"):
                    aid = line.split(":", 1)[1].strip()
                    roles.setdefault(aid, FOLDER_FAMILY.get(card.parent.name, card.parent.name))
                    break
    fallback = HERE / "roles.json"  # committed copy of the card ids, for laptops without the deploy folder
    if fallback.exists():
        for aid, fam in json.loads(fallback.read_text()).items():
            roles.setdefault(aid, fam)
    return roles


# ---------------------------------------------------------------- normalise

def body_text(body):
    if not isinstance(body, dict):
        return str(body or "")
    text = body.get("text") if isinstance(body.get("text"), str) else ""
    extra = " ".join(f"{k}={v}" for k, v in body.items() if k != "text" and not isinstance(v, (dict, list)))
    return " ".join(x for x in (text.strip(), extra) if x)


def normalise(detail, row, roles, n_last=6):
    j = dict(row or {})
    j.update(detail.get("journey") or {})
    verdict = detail.get("verdict") or {}
    review = detail.get("review") or {}
    msgs = sorted(detail.get("messages") or [], key=lambda m: (m.get("sent_at", ""), m.get("msg_id", "")))

    def who(aid):
        if not aid:
            return "?"
        if aid.startswith("citizen:"):
            return "citizen"
        return roles.get(aid, aid)

    negatives, seen = [], set()

    def add(source, subject, score, reason, note=""):
        key = (subject, str(reason), round(float(score), 2))
        if key in seen:
            return
        seen.add(key)
        negatives.append({"source": source, "subject": subject, "role": who(subject), "ours": subject in roles,
                          "score": score, "reason": reason or "", "note": note or ""})

    for r in verdict.get("rows") or []:
        if (r.get("score") or 0) < 0:
            add("verdict", r.get("subject"), r["score"], r.get("reason") or r.get("row"),
                f"{r.get('row', '')}: {r.get('reason') or ''}".strip(": "))
    for r in detail.get("ratings") or []:
        if (r.get("score") or 0) < 0 and not r.get("cancelled"):
            add("rating", r.get("subject"), r["score"], r.get("reason"), r.get("rater", ""))
    review_notes = []
    for a in review.get("agents") or []:
        notes = a.get("notes") or {}
        for dim in ("proactivity", "collaboration", "domain"):
            val = a.get(dim) or 0
            note = (notes.get(dim) or "").strip()
            if note:
                review_notes.append({"agent": a.get("agent_id"), "role": who(a.get("agent_id")), "dim": dim,
                                     "score": val, "note": note})
            if val < 0:
                add("review", a.get("agent_id"), val, f"review.{dim}", note)

    state = (j.get("state") or "").lower()
    judged = bool(verdict) or bool(review)
    if negatives or state == "failed":
        color = "red"
    elif judged or state in ("done", "escalated"):
        color = "green"
    else:
        color = "pending"

    agents = sorted({m.get(k) for m in msgs for k in ("from", "to")
                     if m.get(k) and not str(m.get(k)).startswith("citizen:") and m.get(k) in roles})
    last = [{"time": m.get("sent_at"), "from": who(m.get("from")), "to": who(m.get("to")),
             "type": m.get("type"), "text": body_text(m.get("body"))[:240]} for m in msgs[-n_last:]]
    return {
        "need_id": j.get("need_id"),
        "record_id": verdict.get("record_id") or review.get("record_id"),
        "review_record_id": review.get("record_id"),
        "time": verdict.get("time") or j.get("updated_at"),
        "state": state,
        "citizen": j.get("citizen"),
        "wave_id": j.get("wave_id"),
        "level": j.get("level"),
        "agents": [{"id": a, "role": roles.get(a, "?")} for a in agents],
        "verdict": color,
        "negatives": negatives,
        "review_notes": review_notes,
        "facts_failed": [f.get("kind") for f in verdict.get("facts") or [] if f.get("ok") is False],
        "last_messages": last,
        "n_messages": len(msgs),
    }


def normalise_dump(dump, cards_dir):
    roles = role_map(dump.get("assignment"), cards_dir)
    rows = {r["need_id"]: r for r in (dump.get("list") or {}).get("journeys", [])}
    details = dump.get("details") or {}
    out = []
    for nid, d in details.items():
        out.append(normalise(d, rows.get(nid), roles))
    for nid, r in rows.items():  # listed but detail not fetched
        if nid not in details:
            out.append(normalise({}, r, roles))
    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    return out, roles


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", action="store_true", help="fetch through the hidden Edge (edge fetch)")
    src.add_argument("--dump", help="JSON dump saved by dump.js")
    ap.add_argument("--cards", default=str(DEFAULT_CARDS), help="folder with <role>/card.yaml (agent ids)")
    ap.add_argument("--force", action="store_true", help="ignore the detail cache")
    ap.add_argument("--out", default=str(OUT / "journeys.json"))
    a = ap.parse_args(argv)
    dump = pull_live(a.force) if a.live else json.loads(Path(a.dump).read_text())
    journeys, roles = normalise_dump(dump, a.cards)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"pulled_at": dump.get("pulled_at") or now_iso(), "roles": roles,
                                       "journeys": journeys}, ensure_ascii=False, indent=1))
    n_red = sum(1 for j in journeys if j["verdict"] == "red")
    print(f"{len(journeys)} journeys ({n_red} red) -> {a.out}")
    return journeys


if __name__ == "__main__":
    main()
