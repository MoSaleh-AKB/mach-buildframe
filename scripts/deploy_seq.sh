#!/usr/bin/env bash
# Deploy agents ONE BY ONE in the given order (captain's call 20 Nov 21:25).
# Full output is logged. On QUOTA_EXCEEDED (5 min interval) it waits 310 s and retries once.
# Never kill a running deploy: the server keeps the deployment anyway and the quota is spent.
#
# Usage:  RELEASE=2.1 bash scripts/deploy_seq.sh hospital specialist booking_centre
#   RELEASE  release line for the card version (see scripts/bump_version.py). Without it: patch + 1.
#   BRAIN    brain file to deploy (default asst/_shared/agent.py), e.g. a teammate's branch copy.
# Every attempt is written to VERSIONS.md (agent, version, commit, brain md5, result), so the
# number on the console always maps to one brain. Roll back with:
#   cd asst && city rollback <agent> --to <version>      (4 seconds, no conformance, no quota)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/asst" || exit 1
mkdir -p "$ROOT/deploy-logs"; LOG="$ROOT/deploy-logs/seq_full_$(date +%H%M).log"
BRAIN_SET="${BRAIN:-}"  # no BRAIN given: each role gets its own brain (gp, family_counseling -> asst/_gp)
BRAIN="${BRAIN:-$ROOT/asst/_shared/agent.py}"
GP_BRAIN="$ROOT/asst/_gp/agent.py"
RELEASE="${RELEASE:-}"
VERS="$ROOT/VERSIONS.md"
python3 -m py_compile "$BRAIN" || { echo "brain does not compile: $BRAIN"; exit 1; }
# HA-39: never deploy uncommitted brain edits (VERSIONS.md would name a commit that is not the brain).
# R15: the same for the gp brain, an untracked brain is refused too, and only a brain the Magic Pass
# passed at this HEAD ships (last line of magic_pass_report.txt: MAGIC PASS <sha7> shared=<8> gp=<8>).
HEAD7="$(git -C "$ROOT" rev-parse --short=7 HEAD 2>/dev/null)"
PASS_LINE="$(grep '^MAGIC PASS ' "$ROOT/magic_pass_report.txt" 2>/dev/null | tail -1)"
gate_ok() {  # brain-file md5-key(shared|gp)
  local m
  git -C "$ROOT" ls-files --error-unmatch "$1" >/dev/null 2>&1 || { echo "brain not tracked by git: $1"; return 1; }
  git -C "$ROOT" diff --quiet HEAD -- "$1" || { echo "brain has uncommitted changes: commit them first ($1)"; return 1; }
  m="$(md5sum "$1" | cut -c1-8)"
  case "$PASS_LINE " in "MAGIC PASS $HEAD7 "*"$2=$m "*) return 0 ;; esac
  echo "no Magic Pass for this brain at HEAD $HEAD7 (want '$2=$m', report has '${PASS_LINE:-nothing}'): run scripts/magic_pass.py"
  return 1
}
gate_ok "$BRAIN" shared || exit 1
# HA-38: one deploy run at a time; a second run (e.g. two after_seq.sh) waits here.
# mkdir is atomic and works in Git Bash too (no flock there).
LOCK="$ROOT/deploy-logs/.deploy_seq.lock"
mkdir -p "$ROOT/deploy-logs"
until mkdir "$LOCK" 2>/dev/null; do
  echo "=== $(date +%H:%M:%S) another deploy_seq.sh is running; waiting (stale lock after a kill: rmdir $LOCK)"
  sleep 5
done
trap 'rmdir "$LOCK"' EXIT
FAILED=""
record() {  # agent result
  local v sha md
  v="$(grep -m1 '^version:' "$1/card.yaml" | tr -d ' "' | cut -d: -f2)"
  sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null)"
  md="$(md5sum "$1/agent/agent.py" | cut -c1-8)"
  [ -f "$VERS" ] || printf '# Deployed versions\n\n| time | agent | version | commit | brain md5 | brain file | result |\n|---|---|---|---|---|---|---|\n' > "$VERS"
  printf '| %s | %s | %s | %s | %s | %s | %s |\n' "$(date '+%d %b %H:%M:%S')" "$1" "$v" "$sha" "$md" "$(b="${b:-$BRAIN}"; echo "${b#"$ROOT"/}")" "$2" >> "$VERS"
}
one() {
  local a="$1" out
  out="$(city deploy "$a" 2>&1 | tr '\r' '\n' | grep -v '^\s*$')"
  printf '%s\n' "$out" >> "$LOG"
  printf '%s\n' "$out" | grep -E "Status|FAIL|ingress|TIMEOUT|QUOTA|error|✗" | sed "s/^/[$a] /"
  printf '%s\n' "$out" | grep -q "QUOTA_EXCEEDED" && return 3
  # Platform route flake at promote (specialist 22:03/22:13, booking_centre 23:26/23:36):
  # the old version keeps serving; a fresh version a few minutes later promotes.
  printf '%s\n' "$out" | grep -q "ingress route did not become ready" && return 4
  printf '%s\n' "$out" | grep -q "Status promoted" && return 0
  return 5
}
result() { case "$1" in 0) echo promoted;; 3) echo quota;; 4) echo "failed: ingress route";; *) echo "failed: see $(basename "$LOG")";; esac; }
for a in "$@"; do
  echo "=== $(date +%H:%M:%S) START $a"
  # SI-23: never deploy an agent with an empty or mismatched role (it would sit visible but unreachable).
  card_role="$(grep -E '^role:' "$a/card.yaml" 2>/dev/null | head -1 | sed 's/^role:[[:space:]]*//; s/[[:space:]]*$//')"
  city_role="$(grep -E '^role:' "$a/city.yaml" 2>/dev/null | head -1 | sed 's/^role:[[:space:]]*//; s/[[:space:]]*$//')"
  if [ -z "$card_role" ] || { [ -f "$a/city.yaml" ] && [ "$card_role" != "$city_role" ]; }; then
    echo "=== $a REFUSED: role empty or card.yaml ($card_role) != city.yaml ($city_role)"
    continue
  fi
  b="$BRAIN"
  case "$(basename "$a")" in gp|family_counseling) [ -z "$BRAIN_SET" ] && [ -f "$GP_BRAIN" ] && b="$GP_BRAIN" ;; esac
  python3 -m py_compile "$b" || { echo "=== $a REFUSED: brain does not compile: $b"; continue; }
  [ "$b" = "$BRAIN" ] || gate_ok "$b" gp || { echo "=== $a REFUSED: gp brain not committed or not Magic-Passed"; FAILED="$FAILED $a"; continue; }
  cmp -s "$b" "$a/agent/agent.py" || cp "$b" "$a/agent/agent.py"
  # A version number is never reused on an agent (same number = failed promote).
  python3 -I "$ROOT/scripts/bump_version.py" "$a/card.yaml" $RELEASE || { echo "=== $a: version not set, skipped"; FAILED="$FAILED $a"; continue; }
  one "$a"; rc=$?
  if [ $rc -eq 3 ]; then
    echo "=== $(date +%H:%M:%S) $a quota: waiting 310 s then one retry"
    sleep 310
    one "$a"; rc=$?
  elif [ $rc -eq 4 ]; then
    record "$a" "$(result $rc)"
    echo "=== $(date +%H:%M:%S) $a ingress flake: new version, one retry"
    sleep 60
    if python3 -I "$ROOT/scripts/bump_version.py" "$a/card.yaml" $RELEASE; then
      one "$a"; rc=$?
      if [ $rc -eq 3 ]; then sleep 250; one "$a"; rc=$?; fi
    else
      echo "=== $a: version not set, no retry"; rc=1
    fi
  fi
  record "$a" "$(result $rc)"
  echo "=== $(date +%H:%M:%S) END $a ($(result $rc))"
  [ $rc -eq 0 ] || FAILED="$FAILED $a"
done
# HA-40: say plainly when an old brain is still live, and exit non-zero.
if [ -n "$FAILED" ]; then
  echo "=== FAILED:$FAILED (the previous version keeps serving) $(date +%H:%M:%S)"; exit 1
fi
echo "=== ALL PROMOTED $(date +%H:%M:%S)"
