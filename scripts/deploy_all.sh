#!/usr/bin/env bash
# One-shot deploy of the five agents (MACH, 20 Nov 2026).
# Run from the repo root of a CLEAN checkout on main (we use the worktree ../mach-app-deploy).
#
#   bash scripts/deploy_all.sh preflight     # safe any time: tools, git, brain compile and tests
#   bash scripts/deploy_all.sh prepare       # after `city login`: init missing folders, cards, brain copy, card validate
#   bash scripts/deploy_all.sh deploy --go   # releases specialist, hospital, gp, booking_centre, family_counseling
#   bash scripts/deploy_all.sh status        # city status for all five
#   bash scripts/deploy_all.sh one <agent> --go   # redeploy a single agent (after a brain fix)
#
# Never: city retire, city delete. Rollback: city rollback agent:<slug> --to <version> (from asst/).
# Log of every run: deploy-logs/<timestamp>.log (git-ignored by deploy-logs/.gitignore).

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASST="$ROOT/asst"
ORDER=(specialist hospital gp booking_centre family_counseling)
CITYPY="$HOME/.local/share/uv/tools/city-cli/bin/python"
mkdir -p "$ROOT/deploy-logs"
[ -f "$ROOT/deploy-logs/.gitignore" ] || printf '*\n' > "$ROOT/deploy-logs/.gitignore"
LOG="$ROOT/deploy-logs/$(date +%Y%m%d_%H%M%S)_${1:-none}.log"
exec > >(tee -a "$LOG") 2>&1

ok()   { printf '  OK    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILED=1; }
step() { printf '\n== %s\n' "$*"; }
FAILED=0

preflight() {
  step "1. tools"
  command -v city >/dev/null && ok "city on PATH" || bad "city missing: uv tool install city-cli"
  local have latest
  have="$("$CITYPY" -c 'import importlib.metadata as m; print(m.version("city-cli"))' 2>/dev/null || echo '?')"
  latest="$(curl -s --max-time 8 https://pypi.org/pypi/city-cli/json | python3 -c 'import sys,json; print(json.load(sys.stdin)["info"]["version"])' 2>/dev/null || echo '?')"
  if [ "$have" = "$latest" ]; then ok "city-cli $have (latest)"; else bad "city-cli $have, PyPI $latest: run uv tool upgrade city-cli"; fi

  step "2. git: clean main equal to origin"
  git -C "$ROOT" fetch -q origin
  local branch; branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  [ "$branch" = "main" ] && ok "on main" || bad "on $branch, expected main"
  if [ "$(git -C "$ROOT" rev-parse HEAD)" = "$(git -C "$ROOT" rev-parse origin/main)" ]; then
    ok "main == origin/main ($(git -C "$ROOT" log --oneline -1))"
  else
    bad "main differs from origin/main: git pull --ff-only"
  fi
  if git -C "$ROOT" status --porcelain -- asst/_shared | grep -q .; then bad "uncommitted changes in asst/_shared"; else ok "asst/_shared clean"; fi

  step "3. brain checks"
  python3 -m py_compile "$ASST/_shared/agent.py" && ok "py_compile" || bad "py_compile"
  if [ -f "$ROOT/tools/fake-city/check_tokens.py" ]; then
    "$CITYPY" -I "$ROOT/tools/fake-city/check_tokens.py" "$ASST/_shared/agent.py" | tail -1
  fi
  if "$CITYPY" -I "$ROOT/tools/fake-city/exp2.py" "$ASST/_shared/agent.py" > "$ROOT/deploy-logs/exp2_last.txt" 2>&1; then
    ok "fake-city exp2 ($(grep -c '^---' "$ROOT/deploy-logs/exp2_last.txt") scenarios)"
  else
    bad "fake-city exp2 failed: see deploy-logs/exp2_last.txt"
  fi

  step "4. secrets never in the repo"
  if git -C "$ROOT" ls-files | grep -E '(^|/)\.config/|llm\.vk|controller.*key' >/dev/null; then bad "secret-like file tracked"; else ok "no secret files tracked"; fi

  step "5. manual checks (captain)"
  echo "  [ ] console https://projectnandaitaly.ai/team open, La vostra ASST shows OUR ASST (else GRANT_PERMISSION_DENIED, e19:749)"
  echo "  [ ] city login done on this Mac (team BUILDFRAME)"
}

prepare() {
  step "init missing agent folders"
  mkdir -p "$ASST"
  cd "$ASST" || exit 1
  for a in "${ORDER[@]}"; do
    if [ -d "$a/agent" ]; then ok "$a exists"; else
      city init agent "$a" && ok "init $a" || bad "init $a"
    fi
  done
  step "cards (role, capabilities)"
  python3 "$ROOT/scripts/set_cards.py" || bad "set_cards"
  step "brain copy"
  bash "$ROOT/scripts/sync_brain.sh" || bad "sync_brain"
  step "card validate"
  for a in "${ORDER[@]}"; do
    city card validate "$a" && ok "card $a" || bad "card $a"
  done
  step "seat check"
  for a in "${ORDER[@]}"; do
    printf '  %-18s %s | %s\n' "$a" "$(grep -m1 '^role:' "$a/card.yaml")" "$(grep -A1 '^capabilities:' "$a/card.yaml" | tail -1 | tr -s ' ')"
  done
}

need_go() {
  for x in "$@"; do [ "$x" = "--go" ] && return 0; done
  echo "Dry run. Add --go to really deploy (captain's go)."
  return 1
}

deploy_all() {
  need_go "$@" || return 0
  cd "$ASST" || exit 1
  for a in "${ORDER[@]}"; do
    step "deploy $a"
    if ! [ -d "$a/agent" ]; then bad "$a folder missing: run prepare"; continue; fi
    src="$ASST/_shared/agent.py"
    case "$a" in gp|family_counseling) [ -f "$ASST/_gp/agent.py" ] && src="$ASST/_gp/agent.py" ;; esac
    cmp -s "$src" "$a/agent/agent.py" || { bad "$a brain differs from ${src#"$ASST"/}: run prepare"; continue; }
    if city deploy "$a"; then ok "deployed $a"; else bad "deploy $a (read the conformance stage above; previous version keeps serving)"; fi
  done
  status_all
}

deploy_one() {
  local a="${1:-}"; shift || true
  [ -n "$a" ] || { echo "usage: one <agent> --go"; exit 2; }
  need_go "$@" || return 0
  cd "$ASST" || exit 1
  bash "$ROOT/scripts/sync_brain.sh" >/dev/null || { bad "sync_brain"; return; }
  city deploy "$a" && ok "deployed $a" || bad "deploy $a"
  city status "$a"
}

status_all() {
  step "status"
  cd "$ASST" || exit 1
  for a in "${ORDER[@]}"; do
    echo "--- $a"; city status "$a" 2>&1 | tail -8
  done
}

case "${1:-}" in
  preflight) preflight ;;
  prepare)   prepare ;;
  deploy)    shift; deploy_all "$@" ;;
  one)       shift; deploy_one "$@" ;;
  status)    status_all ;;
  *) sed -n 2,12p "$0"; exit 2 ;;
esac
echo
if [ "$FAILED" -eq 0 ]; then echo "RESULT: all OK   (log: $LOG)"; else echo "RESULT: something FAILED, read above   (log: $LOG)"; exit 1; fi
