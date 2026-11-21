"""#77: scripts/kit_fallback.sh refuses unless KIT_FORCE=1, and warns with the kit brain md5 when forced.

Run: python3 -I tests/test_kit_fallback_force.py

Runs a copy of the script in a temp tree (scripts/ + asst/) with a stub `city` on PATH, so no real
agent folder is touched and the city is never contacted.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "kit_fallback.sh")
OKS = []


def check(label, ok, res=None):
    OKS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + label)
    if not ok and res is not None:
        print("      exit", res.returncode, "| stderr:", res.stderr.strip()[:300])


def run(tmp, env_extra, *args):
    env = {**os.environ, "PATH": os.path.join(tmp, "bin") + os.pathsep + os.environ["PATH"], **env_extra}
    env.pop("KIT_FORCE", None) if "KIT_FORCE" not in env_extra else None
    return subprocess.run(["bash", os.path.join(tmp, "scripts", "kit_fallback.sh"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def main():
    tmp = tempfile.mkdtemp(prefix="kitfb-")
    try:
        os.makedirs(os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, "asst"))
        os.makedirs(os.path.join(tmp, "bin"))
        shutil.copy(SCRIPT, os.path.join(tmp, "scripts", "kit_fallback.sh"))
        kit = os.path.join(tmp, "kit")
        os.makedirs(os.path.join(kit, "templates", "examples"))
        with open(os.path.join(kit, "templates", "examples", "agent.py"), "w") as f:
            f.write("# kit brain\n")
        stub = os.path.join(tmp, "bin", "city")
        with open(stub, "w") as f:  # `city init agent <name>` makes the files the script edits
            f.write('#!/usr/bin/env bash\nif [ "$1" = init ]; then mkdir -p "$3/agent"; '
                    'printf "role: x\\ncapabilities:\\n  - y\\n" > "$3/card.yaml"; printf "role: x\\n" > "$3/city.yaml"; fi\n'
                    'echo "stub city $*"\n')
        os.chmod(stub, 0o755)

        res = run(tmp, {}, kit)
        check("no KIT_FORCE: refused with exit 2", res.returncode == 2 and "REFUSED" in res.stderr, res)
        check("no KIT_FORCE: no kit folder created", not os.path.exists(os.path.join(tmp, "asst", "gp_kit")), res)

        res = run(tmp, {"KIT_FORCE": "0"}, kit)
        check("KIT_FORCE=0: still refused", res.returncode == 2, res)

        res = run(tmp, {"KIT_FORCE": "1"}, kit)
        check("KIT_FORCE=1: runs and warns with the kit brain md5",
              res.returncode == 0 and "WARNING: deploying the KIT brain md5" in res.stderr
              and os.path.exists(os.path.join(tmp, "asst", "gp_kit", "agent", "agent.py")), res)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL OK" if all(OKS) else "SOME FAILED")
    raise SystemExit(0 if all(OKS) else 1)


main()
