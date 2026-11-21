"""#102: every test runs on the brain it was written for. scripts/magic_pass.py runs tests/gp/ on the
gp brain; a tests/test_*.py whose docstring names only the gp brain path belongs in tests/gp/ (unless
it imports a helper module from tests/), and no test exists in both folders (stale duplicates).
Reads magic_pass's own routing; runs no test and no brain.
Run: python3 -I tests/test_tests_right_brain.py   (exit 0 = pass; a brain argument is ignored)"""
import glob
import importlib.util
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("magic_pass", os.path.join(ROOT, "scripts", "magic_pass.py"))
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)
GP, SHARED = os.path.join(ROOT, "asst", "_gp", "agent.py"), os.path.join(ROOT, "asst", "_shared", "agent.py")
HELPERS = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(ROOT, "tests", "_*.py"))}
FAILS = []
top = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
gp_dir = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "tests", "gp", "test_*.py"))}
for path in top:
    name = os.path.basename(path)
    if name in gp_dir:
        FAILS.append(f"{name} is in tests/ and tests/gp/")
    rows = mp._brain_args(path, SHARED, GP)
    if [a[1] for _, a in rows] == [GP]:
        src = open(path, encoding="utf-8").read()
        if not any(re.search(rf"^\s*(?:import|from)\s+{h}\b", src, re.M) for h in HELPERS):
            FAILS.append(f"{name} runs only on the gp brain: move it to tests/gp/")
for f in FAILS:
    print("FAIL", f)
print("%d failed" % len(FAILS))
sys.exit(1 if FAILS else 0)
