"""Q4: a slot the Ledger refused is hidden from every offer list only as long as a Ledger hold
lives (480 s), not 600 s: when slots are scarce, a lapsed hold's slot is offered again at once.
Run: <city python> -I tests/test_dead_ttl_480.py [asst/_shared/agent.py]   (exit 0 = pass)"""
import importlib.util
import os
import sys

os.environ.setdefault("CITY_ROLE", "booking_centre")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else os.path.join(
    HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brainq4", PATH)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

T = [1000.0]
b.time.monotonic = lambda: T[0]  # the brain's clock only (b.time is the time module: restored at exit)
fails = []
b._mark_dead("S1")
T[0] = 1000.0 + 479
if not b._is_dead("S1"):
    fails.append("slot offered again at t0+479 s, before the hold life ends")
T[0] = 1000.0 + 481
if b._is_dead("S1"):
    fails.append("slot still hidden at t0+481 s, after the 480 s hold life")
for f in fails:
    print("FAIL", f)
print(("OK" if not fails else f"FAIL {len(fails)}") + " test_dead_ttl_480")
sys.exit(1 if fails else 0)
