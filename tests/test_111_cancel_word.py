"""#111: an agent's free text with "cancel" inside a word ("cancellato", "non-cancellable",
reason=facility_cancelled) is no cancel; the act word at the start of a line or a cancel token
naming what it cancels still is.
Run: <city python> -I tests/test_111_cancel_word.py [asst/_shared/agent.py]   (exit 0 = pass)"""
import importlib.util
import os
import sys

from city_sdk.city import Message

os.environ.setdefault("CITY_ROLE", "booking_centre")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain111", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def cancel(sender, text):
    return b._is_cancel(Message(type="chat", sender=sender, recipient="agent:y6hxsd766sys", msg_id="m",
                                need_id="N-111", body={"text": text}))


AG, LED = "agent:r8yprrs0e8be", "city:ledger"
CASES = [
    (AG, "Abbiamo cancellato nulla, confermiamo la data", False),
    (AG, "Note: this is a non-cancellable slot, booking.receipt follows", False),
    (AG, "booking.receipt booking_id=bk-1 ledger_ref=c-1 note=previous_facility_cancelled", False),
    (AG, "cancel offer_id=of-1 reason=x", True),
    (AG, "Mi dispiace.\ncancel offer_id=of-2 reason=temporarily_unavailable", True),
    (LED, "cancelled booking_id=bk-1", True),
    (AG, "fault.cancel booking_id=bk-2", True),
]
fails = [f"{s} {t!r}: got {g}, want {w}" for s, t, w in CASES if (g := cancel(s, t)) != w]
for f in fails:
    print("FAIL", f)
print(("OK" if not fails else f"FAIL {len(fails)}") + " test_111_cancel_word")
sys.exit(1 if fails else 0)
