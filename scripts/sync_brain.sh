#!/usr/bin/env bash
# Copy the one shared brain into every agent folder that exists.
# Run from asst/ (the parent of the five agent folders):  bash ../scripts/sync_brain.sh
# Edit only asst/_shared/agent.py; never edit the five copies by hand.
set -euo pipefail

src="_shared/agent.py"
if [ ! -f "$src" ]; then
  echo "No $src here ($(pwd)). Run from asst/." >&2
  exit 1
fi

# B14: never copy a brain that does not compile (a broken brain would be deployed everywhere).
if ! python3 -m py_compile "$src"; then
  echo "py_compile failed on $src: nothing copied" >&2
  exit 1
fi

# gp and family_counseling (consultorio) run the gp brain asst/_gp/agent.py (live 139af01 line);
# booking_centre, hospital and specialist run the shared brain. Never ship one brain to all five.
gp_src="_gp/agent.py"
if [ -f "$gp_src" ] && ! python3 -m py_compile "$gp_src"; then
  echo "py_compile failed on $gp_src: nothing copied" >&2
  exit 1
fi

copied=0
for name in booking_centre hospital gp specialist family_counseling; do
  if [ -d "$name/agent" ]; then
    this="$src"
    case "$name" in gp|family_counseling) [ -f "$gp_src" ] && this="$gp_src" ;; esac
    cp "$this" "$name/agent/agent.py"
    echo "synced  $name/agent/agent.py  <- $this"
    copied=$((copied + 1))
  else
    echo "skipped $name (folder not created yet)"
  fi
done
if [ "$copied" -eq 0 ]; then
  echo "No agent folder found: nothing copied" >&2
  exit 1
fi
echo "$copied folder(s) synced. Test with: city run <folder>; deploy with: city deploy <folder>"
