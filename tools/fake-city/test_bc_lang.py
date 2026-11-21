"""Booking centre language match: the rare-language doctor learned from world lines.

Run: city-cli python -I tools/fake-city/test_bc_lang.py   (exit 1 on any FAIL)
"""
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "asst" / "_shared"))
os.environ.pop("CARE_RARE_LANG", None)
import agent as A  # noqa: E402
from city_sdk.city import Message  # noqa: E402

NOW = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)


def line(i, doc, days, extra=""):
    at = NOW + timedelta(days=days, hours=2)
    return (f"slot_id=S{i} doctor_id=asst-centro-{doc} at={at.isoformat()} "
            f"day={at.strftime('%a').lower()} procedure=VIS-FIS free=1 {extra}").strip()


def lang_of(raw):
    return A._lang([Message(type="chat", sender="citizen:x", msg_id="m", need_id="n",
                            body={"text": f"need.request lang={raw}"}, recipient="bc")])


def doctors(lines, raw):
    return sorted({s["doctor_id"][-2:] for s in A.fitting(lines, now=NOW, priority="D", lang=lang_of(raw))})


fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got}, want {want}")


base = [line(1, "h1", 1), line(2, "h2", 2), line(3, "h3", 1)]
check("no world languages: ro is not claimed (default rare = sw)", doctors(base, "ro"), [])
check("no world languages: sw gets h3", doctors(base, "sw"), ["h3"])
A._SEEN_LANGS.clear()
named = base + ["doctor_id=asst-centro-h3 specialty=physiatry languages=it/ro gender=m"]
check("world names h3 it/ro: ro gets h3", doctors(named, "ro"), ["h3"])
check("world names h3 it/ro: es gets no h3", doctors(named, "es"), [])
check("world names h3 it/ro: it still gets all", doctors(named, "it"), ["h1", "h2", "h3"])
A._SEEN_LANGS.clear()
on_slot = [line(1, "h1", 1), line(3, "h3", 1, "languages=it,ar")]
check("slot line languages=it,ar: ar gets h3", doctors(on_slot, "ar"), ["h3"])
check("slot line languages=it,ar: zh gets nothing", doctors(on_slot, "zh"), [])
print(f"\n{fails} FAIL")
sys.exit(1 if fails else 0)
