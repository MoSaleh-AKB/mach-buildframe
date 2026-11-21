"""Red-flag corpus: every phrase in redflag_phrases.json must escalate (91), every negative must not (10).

Run (any python 3.9+, stdlib only, no city_sdk needed):
    python tools/fake-city/redflag_corpus.py [asst/_shared/agent.py]

The brain's `_emergency(msg, wide=True)` is imported with a tiny stub city_sdk written to a
temp folder, so this works on any machine. Exit 0 only at 91/91 and 0 false positives.
Last line: `REDFLAG hit=<n>/<total> fp=<n>`; missed phrases are printed as `MISS <id>`.
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = '''class _X:
    def __init__(self, *a, **k):
        pass
LEDGER = "ledger"
class CapError(Exception): pass
class OutOfScope(Exception): pass
class City(_X): pass
class Need(_X): pass
class Message:
    def __init__(self, type="chat", sender="citizen:x", msg_id="m", need_id="n", body=None, recipient="gp", **k):
        self.type = type; self.sender = sender; self.msg_id = msg_id; self.need_id = need_id
        self.body = body or {}; self.recipient = recipient; self.sent_at = None
    @property
    def text(self):
        return str(self.body.get("text", ""))
def understood(m): return m
def clinical(*a, **k): return None
'''


def main() -> int:
    path = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py"))
    with open(os.path.join(HERE, "redflag_phrases.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    with tempfile.TemporaryDirectory() as tmp:
        pkg = os.path.join(tmp, "city_sdk")
        os.makedirs(pkg)
        open(os.path.join(pkg, "__init__.py"), "w").close()
        with open(os.path.join(pkg, "city.py"), "w", encoding="utf-8") as fh:
            fh.write(STUB)
        sys.path.insert(0, tmp)
        from city_sdk.city import Message  # the stub

        spec = importlib.util.spec_from_file_location("brain_under_test", path)
        brain = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(brain)
        emergency = getattr(brain, "_emergency")

        def hit(text: str) -> bool:
            return bool(emergency(Message(body={"text": text}), wide=True))

        missed, fps = [], []
        for p in data["phrases"]:
            if not any(hit(t) for t in p["turns"]):
                missed.append(p["id"])
        for n in data["negatives"]:
            if any(hit(t) for t in n["turns"]):
                fps.append(n["id"])
    total = len(data["phrases"])
    for m in missed:
        print("MISS", m)
    for f in fps:
        print("FALSE-POSITIVE", f)
    print(f"REDFLAG hit={total - len(missed)}/{total} fp={len(fps)}")
    return 1 if (missed or fps) else 0


if __name__ == "__main__":
    sys.exit(main())
