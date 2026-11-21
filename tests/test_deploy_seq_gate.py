"""R15: scripts/deploy_seq.sh refuses a dirty gp brain and a missing or stale Magic Pass line, and
deploys when the last MAGIC PASS line names HEAD and the md5 of both brains. Runs the real script in
a throwaway git repo with a stub `city` on PATH (records the call, never contacts the city).
Run: python3 -I tests/test_deploy_seq_gate.py   (exit 0 = pass; a brain argument is ignored)"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FAILS = []


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()[:8]


if not shutil.which("bash") or not shutil.which("md5sum"):
    print("SKIP bash or md5sum missing")
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    repo, bin_dir = os.path.join(tmp, "repo"), os.path.join(tmp, "bin")
    marker = os.path.join(tmp, "city_called")
    for f in ("deploy_seq.sh", "bump_version.py"):
        os.makedirs(os.path.join(repo, "scripts"), exist_ok=True)
        shutil.copy(os.path.join(ROOT, "scripts", f), os.path.join(repo, "scripts", f))
    write(os.path.join(repo, "asst", "_shared", "agent.py"), "X = 1\n")
    write(os.path.join(repo, "asst", "_gp", "agent.py"), "X = 2\n")
    write(os.path.join(repo, "asst", "gp", "agent", "agent.py"), "X = 2\n")
    write(os.path.join(repo, "asst", "gp", "card.yaml"), "name: gp\nversion: 1.0.0\nrole: gp\n")
    write(os.path.join(repo, "asst", "gp", "city.yaml"), "role: gp\n")
    write(os.path.join(repo, ".gitignore"), "magic_pass_report.txt\ndeploy-logs/\nVERSIONS.md\n")
    write(os.path.join(bin_dir, "city"), '#!/bin/sh\necho "$@" >> "%s"\necho "Status promoted"\n' % marker)
    os.chmod(os.path.join(bin_dir, "city"), 0o755)
    sh(repo, "git", "init", "-q")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "fixture")  # uses the machine's git identity
    head = sh(repo, "git", "rev-parse", "--short=7", "HEAD").strip()
    env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"])
    env.pop("BRAIN", None)
    env.pop("RELEASE", None)
    shared, gp = os.path.join(repo, "asst", "_shared", "agent.py"), os.path.join(repo, "asst", "_gp", "agent.py")

    def deploy(case, report, want_deploy):
        if os.path.exists(marker):
            os.remove(marker)
        rep = os.path.join(repo, "magic_pass_report.txt")
        if report is None:
            if os.path.exists(rep):
                os.remove(rep)
        else:
            write(rep, "check | res\n" + report + "\n")
        out = subprocess.run(["bash", os.path.join(repo, "scripts", "deploy_seq.sh"), "gp"], cwd=repo, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        deployed = os.path.exists(marker)
        ok = deployed == want_deploy
        print("%s %s (deployed=%s)" % ("OK  " if ok else "FAIL", case, deployed))
        if not ok:
            FAILS.append(case)
            print("     " + out.strip().replace("\n", "\n     "))

    good = "MAGIC PASS %s shared=%s gp=%s" % (head, md5(shared), md5(gp))
    deploy("no Magic Pass report: refused", None, False)
    deploy("Magic Pass of another commit: refused", "MAGIC PASS 0000000 shared=%s gp=%s" % (md5(shared), md5(gp)), False)
    deploy("Magic Pass on a dirty tree: refused", "MAGIC PASS %s+dirty shared=%s gp=%s" % (head, md5(shared), md5(gp)), False)
    deploy("Magic Pass with an old gp md5: refused", "MAGIC PASS %s shared=%s gp=00000000" % (head, md5(shared)), False)
    deploy("matching Magic Pass line: deployed", good, True)
    write(gp, "X = 3\n")  # uncommitted gp brain edit, even with a report that names its md5
    deploy("dirty gp brain: refused", "MAGIC PASS %s shared=%s gp=%s" % (head, md5(shared), md5(gp)), False)

print("%d failed" % len(FAILS))
sys.exit(1 if FAILS else 0)
