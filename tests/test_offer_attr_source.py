"""Offer lines carry only what the Ledger row or the partner's offer says (judge: false offer attribute).

- expiry: sent only when the world row or the partner offer names one; never the slot time.
- doctor gender: claimed, filtered and ordered only from the Ledger row or registry row, never
  from our own doctor table.
- "the first facility is in X": X belongs to the first offered slot.
Run: <city python> -I tests/test_offer_attr_source.py asst/_shared/agent.py
"""
import importlib.util
import sys
from datetime import datetime, timezone

from city_sdk.city import Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

HO, P1, CZ = "agent:t07/hospital", "agent:t05/hospital", "citizen:t07/c1"
NOW = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
AT = "2026-11-25T09:00:00+00:00"
FAIL = []
RUN = [0]


def check(name, ok):
    RUN[0] += 1
    if not ok:
        FAIL.append(name)
        print("FAIL", name)


def row(doc, i=1, extra=""):
    return f"slot_id=S{i} doctor_id={doc} at={AT} day=wed facility={HO} procedure=VIS-FIS free=1 {extra}".strip()


# 1. expiry in our own offer line
slot = brain.fields_of(row("D-h1"))
line = brain._offer_line(slot, "RX1", "B")
check("local offer without a Ledger expiry has no expiry token", "expiry=" not in line)
check("local offer keeps agenda_date", f"agenda_date={AT}" in line)
slot = brain.fields_of(row("D-h1", extra="expiry=2026-11-24T18:00:00+00:00"))
check("local offer passes the Ledger expiry", "expiry=2026-11-24T18:00:00+00:00" in brain._offer_line(slot, None, "B"))

# 2. expiry in a cross-ASST offer line
def partner(text):
    return [Message(type="chat", sender=P1, msg_id="p1", need_id="N-x", body={"text": text}, recipient="")]


base = (f"offer offer_id=of-P1a slot_id=P1a doctor_id=D-p1 facility={P1} procedure=VIS-FIS "
        f"agenda_date={AT} agenda_day=wed request_id=R1")
for text, want in (
    (base, None),
    (base + " expiry=2026-11-24T18:00:00+00:00", "expiry=2026-11-24T18:00:00+00:00"),
    (base + " expiry=tomorrow", None),
):
    got = brain._xa_offers(partner(text), P1, now=NOW, priority="B", procedure="VIS-FIS", lang="it",
                           rid="R1", taken=set(), taken_ids=set())
    check(f"partner offer parsed: {text[-30:]}", len(got) == 1)
    if got:
        xa = brain._xa_line(got[0], "RX1", "B")
        if want is None:
            check(f"cross-ASST line has no invented expiry: {text[-30:]}", "expiry=" not in xa)
        else:
            check("cross-ASST line passes the partner expiry", want in xa)

# 3. gender: no claim, filter or order from our table (h1 is "f" in _DOCTORS)
plain = brain.fields_of(row("D-h1"))
note = brain._lang_note([plain], "", False, "it", "f")
check("no gender claim from our table", "dottoressa" not in note and "uomo" not in note)
note = brain._lang_note([plain], "", False, "en", "m")
check("no gender claim from our table (en)", "woman" not in note and " man" not in note)
check("table gender does not filter", bool(brain.fitting([row("D-h1")], now=NOW, priority="B", lang="it",
                                                         gender="m", procedure="VIS-FIS")))
check("table gender does not order", brain._prefers(plain, "m", frozenset()))

# 4. gender from the Ledger: slot row and registry row
said = brain.fields_of(row("D-h1", extra="gender=f"))
check("slot row gender is claimed", "dottoressa" in brain._lang_note([said], "", False, "it", "f"))
check("slot row gender filters", not brain.fitting([row("D-h1", extra="gender=f")], now=NOW, priority="B",
                                                   lang="it", gender="m", procedure="VIS-FIS"))
reg = "doctor_id=D-h2 specialty=physiatry languages=it gender=m"
got = brain.fitting([reg, row("D-h2", 2)], now=NOW, priority="B", lang="it", gender="f", procedure="VIS-FIS")
check("registry row gender filters", got == [])
h2 = brain.fields_of(row("D-h2", 2))
check("registry row gender is claimed", "medico uomo" in brain._lang_note([h2], "", False, "it", "m"))
check("registry row gender orders", not brain._prefers(h2, "f", frozenset()))

# 5. "the first facility is in X" names the first offered slot
chat = [Message(type="chat", sender=CZ, msg_id="c1", need_id="N-n", body={"text": "il più vicino a casa"}, recipient="")]
first = brain.fields_of(row("D-h1"))
second = brain.fields_of(row("D-h2", 2, "district=Lambrate"))
check("no district for the first slot: no claim", "Lambrate" not in brain._near_note(chat, [first, second], "it"))
check("district of the first slot is named", "Lambrate" in brain._near_note(chat, [second, first], "it"))

print(f"FAIL {len(FAIL)} of {RUN[0]}" if FAIL else f"OK {RUN[0]}")
sys.exit(1 if FAIL else 0)
