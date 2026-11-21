#!/usr/bin/env python3
"""Magic Pass: the one-command release gate for the MACH agents (stdlib only, Windows and macOS).

    python scripts/magic_pass.py [--brain asst/_shared/agent.py] [--quick]

Checks (all run, even after a failure, so the table shows everything):
  1 python      the interpreter that has city_sdk (env MAGIC_PY, else the city-cli uv tool, else this one)
  2 md5         the 5 role copies asst/*/agent/agent.py equal the brain
  3 compile     py_compile of the brain
  4 tests       every tests/test_*.py + tools/fake-city test_gp_protocol / test_bc_lang / test_bc_prefs
  5 conform     tools/fake-city/conform_local.py reports "roles not clean: 0/5"
  6 slowledger  (full only) tools/fake-city/slowledger.py ends with "OK slowledger" (shared brain:
                it tests the booking centre, a role the gp brain never serves)
  7 redflag     (full only) tools/fake-city/redflag_corpus.py: 91/91 escalate, 0 false positives,
                on the shared AND the gp brain; asst/_gp/test_gp_never_silent.py on the gp brain

Last line: `MAGIC PASS <sha7> shared=<8> gp=<8>` (md5 of each brain; scripts/deploy_seq.sh checks
both) or `MAGIC FAIL <reason>`. Exit 0 / 1. Same text in magic_pass_report.txt.
Never deploys, never touches the network.
"""
import argparse
import glob
import hashlib
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")  # !220: tests start right after boot, no 300 s rescue warm-up

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROLES = ["booking_centre", "hospital", "gp", "specialist", "family_counseling"]
FAKE = os.path.join(ROOT, "tools", "fake-city")
TIMEOUT = 600


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def city_python():
    env = os.environ.get("MAGIC_PY")
    if env:
        return env, "env MAGIC_PY"
    home = os.path.expanduser("~")
    cands = [
        os.path.join(home, ".local", "share", "uv", "tools", "city-cli", "bin", "python"),
        os.path.join(home, ".local", "share", "uv", "tools", "city-cli", "Scripts", "python.exe"),
        os.path.join(os.environ.get("APPDATA", ""), "uv", "tools", "city-cli", "Scripts", "python.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "uv", "tools", "city-cli", "Scripts", "python.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c, "city-cli uv tool"
    return sys.executable, "current interpreter"


def run(args, cwd=ROOT):
    """Run a subprocess; return (returncode, output lines). Never raises."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        p = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT)
        out = p.stdout.decode("utf-8", errors="replace")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "FAIL timeout after %ds" % TIMEOUT, 124
    except OSError as e:
        out, rc = "FAIL cannot start: %s" % e, 127
    return rc, [ln.rstrip() for ln in out.splitlines()]


def first_bad(lines, rc):
    for ln in lines:
        if re.search(r"FAIL|Traceback|Error", ln) and not re.search(r"\b0 FAIL", ln):
            return ln.strip()[:110]
    tail = [ln for ln in lines if ln.strip()]
    return ("exit %d: %s" % (rc, tail[-1].strip()[:90])) if tail else "exit %d, no output" % rc


class Gate:
    def __init__(self):
        self.rows = []  # (name, ok, seconds, note)

    def add(self, name, ok, t0, note=""):
        self.rows.append((name, ok, time.time() - t0, "" if ok else note))


def check_script(gate, name, py, args, expect_last=None, expect_in=None):
    """Run one script check; returns a row (name, ok, seconds, note). Thread-safe."""
    t0 = time.time()
    rc, lines = run([py, "-I"] + args)
    nonempty = [ln for ln in lines if ln.strip()]
    bad = rc != 0 or any(re.search(r"FAIL", ln) and not re.search(r"\b0 FAIL", ln) for ln in lines)
    if expect_last is not None and (not nonempty or nonempty[-1].strip() != expect_last):
        bad = True
    if expect_in is not None and not any(expect_in in ln for ln in lines):
        bad = True
    return (name, not bad, time.time() - t0, first_bad(lines, rc) if bad else "")


def git_sha():
    rc, out = run(["git", "rev-parse", "--short=7", "HEAD"])
    sha = out[0].strip() if rc == 0 and out else "nogit00"
    rc, out = run(["git", "status", "--porcelain", "--untracked-files=no"])
    if rc == 0 and any(ln.strip() for ln in out):
        sha += "+dirty"
    return sha


def _brain_args(test: str, brain: str, gp_brain: str) -> list[tuple[str, list[str]]]:
    """Which brain(s) a tests/test_*.py runs on, from the paths its module docstring names.

    Only asst/_gp/agent.py named: the gp brain. Both named: both brains (two rows), or both as
    two arguments when the usage shows them side by side ("[asst/_gp/agent.py] [asst/_shared/agent.py]").
    Nothing or only the shared brain named: the brain under test, as before."""

    if not os.path.isfile(gp_brain):
        return [("", [test, brain])]
    try:
        with open(test, encoding="utf-8") as fh:
            src = fh.read(4000)
    except OSError:
        return [("", [test, brain])]
    doc = src.split('"""', 2)[1] if src.count('"""') >= 2 else ""
    gp, shared = "asst/_gp/agent.py" in doc, "asst/_shared/agent.py" in doc
    if re.search(r"\[asst/_gp/agent\.py\]\s*\[asst/_shared/agent\.py\]", doc):
        return [("", [test, gp_brain, brain])]
    if gp and not shared:
        return [(" (gp brain)", [test, gp_brain])]
    if gp and shared:
        return [("", [test, brain]), (" (gp brain)", [test, gp_brain])]
    return [("", [test, brain])]


def main():
    ap = argparse.ArgumentParser(description="Magic Pass release gate")
    ap.add_argument("--brain", default=os.path.join("asst", "_shared", "agent.py"))
    ap.add_argument("--quick", action="store_true", help="skip slowledger and the red-flag corpus")
    a = ap.parse_args()
    brain = os.path.abspath(a.brain)
    started = time.time()
    gate = Gate()
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    if not os.path.isfile(brain):
        say("MAGIC FAIL brain not found: %s" % brain)
        return finish(out, 1)

    # 1 python
    t0 = time.time()
    py, why = city_python()
    rc, lines = run([py, "-I", "-c", "import city_sdk,sys;print(sys.version.split()[0])"])
    say("python: %s (%s)%s" % (py, why, "" if rc == 0 else "  [city_sdk NOT importable]"))
    gate.add("python has city_sdk", rc == 0, t0, "set MAGIC_PY to the python that has city_sdk")
    if rc != 0:
        py = sys.executable  # still run what can run; city-dependent checks will show FAIL

    bsum = md5(brain)

    # 2 md5 of the 5 role copies
    t0 = time.time()
    off = []
    gp_brain = os.path.join(ROOT, "asst", "_gp", "agent.py")
    gsum = md5(gp_brain) if os.path.isfile(gp_brain) else bsum
    for role in ROLES:
        p = os.path.join(ROOT, "asst", role, "agent", "agent.py")
        # gp and family_counseling run the gp brain (scripts/sync_brain.sh), the rest the shared one
        want = gsum if role in ("gp", "family_counseling") else bsum
        if not os.path.isfile(p) or md5(p) != want:
            off.append(role)
    gate.add("role copies = brain (md5)", not off, t0,
             "FAIL %s differ: run scripts/sync_brain.sh" % ",".join(off))

    # 3 py_compile
    t0 = time.time()
    try:
        py_compile.compile(brain, cfile=os.path.join(tempfile.gettempdir(), "magic_%s.pyc" % uuid.uuid4().hex), doraise=True)
        gate.add("py_compile brain", True, t0)
    except py_compile.PyCompileError as e:
        gate.add("py_compile brain", False, t0, "FAIL " + str(e).strip().splitlines()[-1])

    # 4-7 script checks run in parallel (they are independent); rows keep a fixed order
    jobs = []  # (name, args, expect_last, expect_in)
    tests = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    for name in ("test_gp_protocol.py", "test_bc_lang.py", "test_bc_prefs.py"):
        p = os.path.join(FAKE, name)
        if os.path.isfile(p):
            tests.append(p)
    for t in tests:
        name = "test " + os.path.relpath(t, ROOT).replace("\\", "/")
        for label, args in _brain_args(t, brain, gp_brain):
            jobs.append((name + label, args, None, None))
    # gp and family_counseling run asst/_gp/agent.py: their tests run on that brain (blind spot
    # before: tests/gp/ never ran, and gp-only tests in tests/ ran on the shared brain).
    if os.path.isfile(gp_brain):
        for t in sorted(glob.glob(os.path.join(ROOT, "tests", "gp", "test_*.py"))):
            jobs.append(("test " + os.path.relpath(t, ROOT).replace("\\", "/"), [t, gp_brain], None, None))
        never_silent = os.path.join(ROOT, "asst", "_gp", "test_gp_never_silent.py")
        if os.path.isfile(never_silent):
            jobs.append(("test asst/_gp/test_gp_never_silent.py (gp brain)", [never_silent, gp_brain], None, None))
    jobs.append(("conform_local", [os.path.join(FAKE, "conform_local.py")], None, "roles not clean: 0/5"))
    tmp = None
    if not a.quick:
        # slowledger runs from a unique temp copy in the same folder, deleted afterwards
        tmp = os.path.join(FAKE, "_magic_slowledger_%s.py" % uuid.uuid4().hex[:8])
        with open(os.path.join(FAKE, "slowledger.py"), "rb") as src, open(tmp, "wb") as dst:
            dst.write(src.read())
        jobs.append(("slowledger A-F", [tmp, brain], "OK slowledger", None))
        jobs.append(("red-flag corpus 91/91", [os.path.join(FAKE, "redflag_corpus.py"), brain], None,
                     "REDFLAG hit=91/91 fp=0"))
        if os.path.isfile(gp_brain):  # R16: gp and family_counseling run this brain; R3 shipped unchecked
            jobs.append(("red-flag corpus 91/91 (gp brain)", [os.path.join(FAKE, "redflag_corpus.py"), gp_brain], None,
                         "REDFLAG hit=91/91 fp=0"))
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(check_script, gate, n, py, args, el, ei) for n, args, el, ei in jobs]
            gate.rows.extend(f.result() for f in futs)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # table
    w = max(len(r[0]) for r in gate.rows)
    say("")
    say("%-*s | %-4s | %6s | %s" % (w, "check", "res", "sec", "first failing line"))
    say("-" * (w + 40))
    for name, ok, sec, note in gate.rows:
        say("%-*s | %-4s | %6.1f | %s" % (w, name, "PASS" if ok else "FAIL", sec, note))
    say("")
    failed = [r[0] for r in gate.rows if not r[1]]
    say("mode: %s | runtime %.0fs | brain %s" % ("quick" if a.quick else "full", time.time() - started, os.path.relpath(brain, ROOT)))
    if failed:
        say("MAGIC FAIL " + "; ".join(failed))
        return finish(out, 1)
    say("MAGIC PASS %s shared=%s gp=%s" % (git_sha(), bsum[:8], gsum[:8]))
    return finish(out, 0)


def finish(out, code):
    try:
        with open(os.path.join(ROOT, "magic_pass_report.txt"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
