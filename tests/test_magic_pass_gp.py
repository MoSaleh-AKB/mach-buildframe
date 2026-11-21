"""R16: the Magic Pass runs the red-flag corpus and test_gp_never_silent on the gp brain too, and its
PASS line carries both brain md5s (scripts/deploy_seq.sh reads them). No subprocess runs: every
check is replaced by a recording stub.
Run: python3 -I tests/test_magic_pass_gp.py   (exit 0 = pass; a brain argument is ignored)"""
import contextlib
import importlib.util
import io
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("magic_pass", os.path.join(ROOT, "scripts", "magic_pass.py"))
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)

GP = os.path.join(ROOT, "asst", "_gp", "agent.py")
SHARED = os.path.join(ROOT, "asst", "_shared", "agent.py")
CALLS = []
mp.run = lambda args, cwd=ROOT: (0, ["ok"])
mp.check_script = lambda gate, name, py, args, el, ei: CALLS.append((name, args)) or (name, True, 0.0, "")
mp.finish = lambda out, code: code
mp.git_sha = lambda: "abc1234"
sys.argv = ["magic_pass.py"]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = mp.main()
last = [ln for ln in buf.getvalue().splitlines() if ln.strip()][-1]

FAILS = []


def ran(script, brain):
    return any(a and a[0].endswith(script) and brain in a[1:] for _, a in CALLS)


if not ran("redflag_corpus.py", GP):
    FAILS.append("red-flag corpus not run on the gp brain")
if not ran("redflag_corpus.py", SHARED):
    FAILS.append("red-flag corpus not run on the shared brain")
if not ran("test_gp_never_silent.py", GP):
    FAILS.append("asst/_gp/test_gp_never_silent.py not run on the gp brain")
want = "MAGIC PASS abc1234 shared=%s gp=%s" % (mp.md5(SHARED)[:8], mp.md5(GP)[:8])
if last != want:
    FAILS.append("PASS line %r, want %r" % (last, want))
for f in FAILS:
    print("FAIL", f)
print("%d failed" % len(FAILS))
sys.exit(1 if FAILS or rc else 0)
