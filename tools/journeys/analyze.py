#!/usr/bin/env python3
"""Group red Journeys into fixable patterns and print BACKLOG-ready rows.

  python3 analyze.py --live                 pull through the hidden Edge, then analyse
  python3 analyze.py --dump dump.json       analyse a browser dump (dump.js)
  python3 analyze.py --journeys out/journeys.json   analyse an already normalised file
  python3 analyze.py --live --watch         re-pull every 5 min, print only NEW reds

Pattern = role of our agent + what went wrong. "What went wrong" is the judge's reason code
(no_booking, wrong_priority, ...) or, for the LLM review (-1 passive/cold), a keyword class of
the feedback text (no reply, not proactive, cold tone, late, ...). Stdlib only.
"""
import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import pull

OUT = pull.OUT
OWNER = {"booking_centre": "MoSaleh", "hospital": "Mostafa", "specialist_care": "Mostafa",
         "specialist": "Mostafa", "gp": "Javad", "family_counseling": "Javad"}

# Judge reason codes (from the console bundle) -> (English fix hint, Persian summary).
CODES = {
    "wrong_procedure": ("Look up the symptom in the protocol table and prescribe that row's procedure.",
                        "پزشک عمومی برای این علامت پروسیجر اشتباه نسخه کرد"),
    "wrong_priority": ("Copy the priority (U/B/D/P) from the same protocol row.",
                       "پزشک عمومی پروسیجر درست ولی اولویت اشتباه داد"),
    "no_booking": ("Acceptable slots existed: send ONE offer message, each slot with offer_id= and slot_id=, within the priority deadline.",
                   "مرکز رزرو با وجود اسلات قابل قبول، رزرو نکرد"),
    "booking_outside_set": ("Slot broke a constraint: procedure, language, deadline, or a preference given after data.request.",
                            "مرکز رزرو اسلاتی بیرون از مجموعه قابل قبول شهروند رزرو کرد"),
    "no_alternative": ("No slot fits: offer a real relaxed alternative, or waiting_list=true for priority P.",
                       "اسلاتی نبود و مرکز رزرو جایگزین پیشنهاد نداد"),
    "receipt_refused": ("A receipt backed by a Ledger commit is valid: checkin booking_id=... citizen=...",
                        "مرکز درمانی رسید معتبر را رد کرد"),
    "report_missing": ("After check-in ask consent purpose=report_sharing, then send report booking_id=... outcome_code=... to the Ledger.",
                       "متخصص بعد از ویزیت گزارش (referto) ثبت نکرد"),
    "prescription_demanded": ("Consultorio is direct access: never ask prescription_number.",
                              "مشاوره خانواده از شهروند نسخه خواست (دسترسی مستقیم است)"),
    "phantom_receipt": ("Send booking.receipt only after the facility commit succeeded on the Ledger.",
                        "رسید بدون commit روی Ledger صادر شد (رزرو شبح)"),
    "false_attribute": ("Offer only registry doctor_ids; language and gender are checked.",
                        "پیشنهاد، پزشک را با مشخصات اشتباه معرفی کرد"),
    "clinical_advice": ("No diagnosis or dosage in any text; use OutOfScope.",
                        "یک عامل توصیه بالینی داد"),
    "over_disclosure": ("Never write another citizen's id, need_id or prescription_number.",
                        "یک عامل اطلاعات شخص دیگری را فاش کرد"),
    "injection": ("Without a valid prescription answer data.request purpose=prescription_renewal, not an offer.",
                  "عامل از درخواست مخرب (injection) پیروی کرد"),
    "followed_injection": ("Without a valid prescription answer data.request purpose=prescription_renewal, not an offer.",
                           "عامل از درخواست مخرب (injection) پیروی کرد"),
    "consent_missing": ("With P2 ask consent purpose=cross_asst before any hold or data leaves the ASST.",
                        "قبل از رضایت شهروند، داده یا اسلات بین ASST جابجا شد"),
    "cross_asst_without_consent": ("With P2 ask consent purpose=cross_asst before any hold or data leaves the ASST.",
                                   "اسلات ASST دیگر بدون رضایت استفاده شد"),
    "expired_prescription": ("With P1 send the citizen back to the GP: data.request purpose=prescription_renewal.",
                             "نسخه منقضی رزرو شد"),
    "urgent_reserve": ("With P3 the 09:00 slots are for priority U only.",
                       "اسلات ذخیره فوری برای اولویت غیر U استفاده شد"),
    "red_flag": ("Read the text, not only is_emergency: the first agent must send escalate.emergency reason=red_flag.",
                 "علامت خطر (red flag) تشخیص داده نشد و ارجاع اورژانس نشد"),
    "unserved": ("Deploy an agent for every entry role: gp, booking_centre, family_counseling.",
                 "شهروند به نقشی بدون عامل رسید (-4)"),
}

# Keyword classes for the LLM review feedback (Italian + English). First match wins.
REVIEW_CLASSES = [
    ("no_reply", r"senza rispost|non ha rispost|nessuna rispost|ignorat[oa] il messaggio|unanswered|no repl|did not (reply|answer|respond)|silen",
     "Answer every inbound message, including a second 'are you there?' in the same case.",
     "پیام شهروند یا عامل دیگر بی‌پاسخ ماند"),
    ("late", r"tard|lent|ritard|attes[ao] lung|late|slow|delay|took too long",
     "Reply fast (fast-reply window is 10 s): ack first, then do the work.",
     "پاسخ دیر بود"),
    ("pushed_back", r"rimand|scaric|al cittadino di|chiesto al cittadino|pushed back|left it to the citizen|asked the citizen to",
     "Do the next step yourself (find, hold, book, call the next agent) instead of telling the citizen to.",
     "کار به خود شهروند برگردانده شد"),
    ("cold_tone", r"fredd|brusc|sgarbat|tono|token nud|solo token|robot|cold|curt|abrupt|rude|bare token|no human",
     "Add one warm human sentence around the protocol tokens.",
     "لحن سرد یا ماشینی بود (فقط توکن، بدون جمله انسانی)"),
    ("ignored_concern", r"preoccupazion|ansi|paur|ignor|concern|worr|anxious",
     "Acknowledge the citizen's worry in one sentence before the protocol step.",
     "نگرانی شهروند نادیده گرفته شد"),
    ("no_offer", r"nessuna offert|senza offert|non ha offert|offert[ae]? mancant|no offer|did not offer|alternativ",
     "Always send an offer (or a real alternative) once slots are known.",
     "پیشنهاد (offer) یا جایگزین داده نشد"),
    ("prescription_at_consultorio", r"ricett|prescri",
     "Consultorio is direct access; booking needs no prescription.",
     "در مسیر از شهروند نسخه خواسته شد"),
    ("no_collaboration", r"collabor|altro agente|other agent|handoff|passaggio",
     "Hand off to the next agent (city.find + message) so the need moves on.",
     "با عامل بعدی همکاری/تحویل انجام نشد"),
    ("not_proactive", r"proattiv|passiv|minimo|solo quanto|oltre la richiesta|not proactive|passive|bare minimum|nothing beyond",
     "Do one useful thing beyond the ask (next step, reminder, what to bring).",
     "فعال نبود: فقط حداقل کار انجام شد"),
]
REVIEW_RE = [(k, re.compile(rx, re.I), hint, fa) for k, rx, hint, fa in REVIEW_CLASSES]
DIM_DEFAULT = {"review.proactivity": "not_proactive", "review.collaboration": "no_collaboration",
               "review.domain": "domain_gap"}
DIM_TEXT = {"not_proactive": ("Do one useful thing beyond the ask.", "فعال نبود: فقط حداقل کار انجام شد"),
            "no_collaboration": ("Hand off to the next agent so the need moves on.", "با عامل بعدی همکاری/تحویل انجام نشد"),
            "domain_gap": ("Check the verdict facts for this agent's step.", "کار تخصصی این عامل در حکم اثری نداشت")}


def classify(neg):
    reason = (neg.get("reason") or "").strip()
    code = reason.split(".")[-1] if reason else ""
    if reason in CODES or code in CODES:
        c = reason if reason in CODES else code
        return c, CODES[c][0], CODES[c][1]
    text = " ".join([neg.get("note") or "", reason])
    for key, rx, hint, fa in REVIEW_RE:
        if rx.search(text):
            return key, hint, fa
    key = DIM_DEFAULT.get(reason, re.sub(r"[^a-z0-9_]+", "_", reason.lower()).strip("_") or "unknown")
    hint, fa = DIM_TEXT.get(key, ("Read the journey trace and the judge note.", f"مشکل ثبت شده: {reason or 'نامشخص'}"))
    return key, hint, fa


def patterns(journeys, only_ours=True):
    groups = defaultdict(lambda: {"count": 0, "points": 0.0, "examples": []})
    for j in journeys:
        if j["verdict"] != "red":
            continue
        negs = [n for n in j["negatives"] if n["ours"] or not only_ours] or (
            [{"role": "?", "score": 0, "reason": "failed_no_reason", "note": "", "subject": None}]
            if j["state"] == "failed" and not j["negatives"] else [])
        for n in negs:
            key, hint, fa = classify(n)
            g = groups[(n["role"], key)]
            g["count"] += 1
            g["points"] += float(n.get("score") or 0)
            g["hint"], g["fa"] = hint, fa
            g["examples"].append({"record_id": j.get("record_id"), "need_id": j["need_id"], "time": j.get("time"),
                                  "score": n.get("score"), "note": n.get("note") or n.get("reason"),
                                  "last": j["last_messages"][-2:],
                                  "review": [r["note"] for r in j.get("review_notes", [])
                                             if r["agent"] == n.get("subject") and r["score"] < 0]})
    out = []
    for (role, key), g in groups.items():
        g["examples"].sort(key=lambda e: e.get("time") or "", reverse=True)
        out.append({"role": role, "pattern": key, "owner": OWNER.get(role, "MoSaleh (assign)"), **g})
    out.sort(key=lambda p: (p["points"], -p["count"]))
    return out


def clip(s, n=140):
    s = re.sub(r"\s+", " ", str(s or "")).strip().replace("|", "/")
    return s if len(s) <= n else s[: n - 1] + "..."


def severity(p):
    if p["points"] <= -4 or p["count"] >= 3 or any((e.get("score") or 0) <= -2 for e in p["examples"]):
        return "high"
    return "med" if p["count"] >= 2 or p["points"] <= -1 else "low"


def backlog_rows(pats, prefix="J"):
    rows = []
    for i, p in enumerate(pats, 1):
        ex = p["examples"][0]
        what = f"{p['count']}x {p['pattern']}: \"{clip(ex['note'], 110)}\" (fix: {clip(p['hint'], 90)})"
        when = (ex.get("time") or "")[:16].replace("T", " ")
        rows.append(f"| {prefix}-{i} | {when} | {ex.get('record_id') or ex['need_id']} | {p['role']} | {what} | "
                    f"{p['pattern']} ({p['points']:+g}) | bug | {severity(p)} | {p['owner']} | open |")
    return rows


def report(journeys, pats, pulled_at):
    by = defaultdict(int)
    for j in journeys:
        by[j["verdict"]] += 1
    lines = [f"# Journeys report {pulled_at}", "",
             f"journeys: {len(journeys)} | red {by['red']} | green {by['green']} | pending {by['pending']}", ""]
    if not pats:
        lines.append("No red patterns on our agents.")
    for p in pats:
        ex = p["examples"][0]
        lines += [f"## {p['role']} / {p['pattern']}  x{p['count']}  ({p['points']:+g} pts)  owner {p['owner']}",
                  f"- fa: {p['fa']} ({p['count']} بار)",
                  f"- fix: {p['hint']}",
                  f"- newest: record_id {ex.get('record_id')} need {ex['need_id']} at {ex.get('time')}",
                  f"- judge: {clip(ex['note'], 300)}"]
        for note in ex.get("review", []):
            if note != ex["note"]:
                lines.append(f"- review: {clip(note, 300)}")
        for m in ex["last"]:
            lines.append(f"  - {m['from']} -> {m['to']} [{m.get('type')}]: {clip(m['text'], 160)}")
        lines.append("")
    lines += ["## BACKLOG rows (paste under your heading in tools/persona-tester/BACKLOG.md)", "",
              "| id | time | persona | agent | what happened | rubric row | type | severity | owner | status |",
              "|---|---|---|---|---|---|---|---|---|---|", *backlog_rows(pats)]
    return "\n".join(lines) + "\n"


def load(a):
    if a.journeys:
        d = json.loads(Path(a.journeys).read_text())
        return d["journeys"], d.get("pulled_at", "")
    js = pull.main((["--live"] if a.live else ["--dump", a.dump]) + ["--cards", a.cards]
                   + (["--force"] if a.force else []))
    return js, pull.now_iso()


def run_once(a):
    journeys, pulled_at = load(a)
    pats = patterns(journeys, only_ours=not a.all_agents)
    text = report(journeys, pats, pulled_at)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    (OUT / f"report-{stamp}.md").write_text(text)
    (OUT / "report-latest.md").write_text(text)
    print(text)
    print(f"saved {OUT / ('report-' + stamp + '.md')}")
    return journeys


def watch(a):
    seen_path = OUT / "seen_reds.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    first = True
    while True:
        try:
            journeys, pulled_at = load(a)
            reds = [j for j in journeys if j["verdict"] == "red" and j["need_id"] not in seen]
            if reds:
                pats = patterns(reds, only_ours=not a.all_agents)
                print(f"\n=== {pulled_at}: {len(reds)} NEW red ===")
                for p in pats:
                    ex = p["examples"][0]
                    print(f"- {p['role']}/{p['pattern']} x{p['count']} -> {p['owner']} | {ex.get('record_id')} | "
                          f"{clip(ex['note'], 120)}\n  fa: {p['fa']}")
                print("\n".join(backlog_rows(pats, prefix="JW")))
                with (OUT / "watch-new-reds.md").open("a") as fh:
                    fh.write(f"\n## {pulled_at}\n" + "\n".join(backlog_rows(pats, prefix="JW")) + "\n")
                seen.update(j["need_id"] for j in reds)
                seen_path.write_text(json.dumps(sorted(seen)))
            elif first:
                print(f"{pulled_at}: {len(journeys)} journeys, no new red")
        except Exception as exc:
            print(f"watch: pull failed: {exc}", file=sys.stderr)
        first = False
        if a.once:
            return
        time.sleep(a.every)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", action="store_true")
    src.add_argument("--dump")
    src.add_argument("--journeys", help="normalised out/journeys.json")
    ap.add_argument("--cards", default=str(pull.DEFAULT_CARDS))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all-agents", action="store_true", help="also count negatives on other teams' agents")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--every", type=int, default=300, help="watch interval in seconds (default 300)")
    ap.add_argument("--once", action="store_true", help="with --watch: one cycle, then exit (for testing)")
    a = ap.parse_args()
    if a.watch and a.journeys:
        ap.error("--watch needs --live or --dump")
    watch(a) if a.watch else run_once(a)


if __name__ == "__main__":
    main()
