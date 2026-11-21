"""Item 1 guard: every text the brain can send must survive the kit parser without glued punctuation.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/check_tokens.py asst/_shared/agent.py
Checks every module-level string constant and the text helpers (receipt, cancel, waiting list, rx),
parsed with the kit's own `understood()`: no key=value value may end in . ; : ! ?
"""
import importlib.util
import sys

from city_sdk.city import Message, understood

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BAD_TAIL = ".;:!?"
bad = []
count = 0


def check(label, text):
    global count
    count += 1
    msg = Message(type="chat", sender="x", msg_id="g", need_id="n", body={"text": text}, recipient="y")
    for key, value in understood(msg).body.items():
        if key != "text" and isinstance(value, str) and (not value or value[-1] in BAD_TAIL):
            bad.append(f"{label}: {key}={value!r}")


for name, value in vars(brain).items():
    if isinstance(value, str) and "=" in value:
        check(name, value)

samples = {
    "_receipt_text": brain._receipt_text("bk-1", "lr-9"),
    "_cancel_text": brain._cancel_text("of-1", "facility_unreachable"),
    "_cancel_text_down": brain._cancel_text("of-1", "facility_unreachable", down=True),
    "_wl_text": brain._wl_text("wl-1"),
    "_rx_text": brain._rx_text({"prescription_number": "RX1", "procedure": "VIS-FIS", "priority": "B"}, "SYM-BACK"),
}
_peer = type("P", (), {"capabilities": ("hospital.physiatry",)})()
_offer = {"offer_id": "of-1", "slot_id": "s1", "doctor_id": "D-1", "facility": "agent:t05/hospital",
          "procedure": "VIS-FIS", "at": "2026-11-24T09:00:00+00:00", "day": "tue"}
_xl = brain._xa_line(_offer, "RX1", "B")
samples.update({
    "_xa_request": brain._xa_request(_peer, "VIS-FIS", "B", "N-0001"),
    "_xa_line": _xl,
    "_xa_text_local": brain._xa_text([_xl, _xl], local=True) + "\n" + _xl,
    "_xa_text_only": brain._xa_text([_xl], local=False) + "\n" + _xl,
})
for name, text in samples.items():
    check(name, text)

print(f"{count} texts checked, {len(bad)} with glued punctuation")
for line in bad:
    print("  BAD", line)
sys.exit(1 if bad else 0)
