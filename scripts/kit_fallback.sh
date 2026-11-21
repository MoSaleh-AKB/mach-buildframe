#!/usr/bin/env bash
# Fallback: agent folders running the kit's official example brain, to fill a role fast.
# Run from repo root: bash scripts/kit_fallback.sh <kit-dir> ; then city deploy <folder> from asst/.
set -uo pipefail
# #77: the kit brain is EMERGENCY-ONLY. It is an old 1155-line example brain (red flags ~23/91, none
# of the team's fixes), not asst/_shared/agent.py. Refuse unless the caller sets KIT_FORCE=1.
if [ "${KIT_FORCE:-}" != "1" ]; then
  echo "REFUSED: kit brains are emergency-only (old example brain, red flags ~23/91)." >&2
  echo "Only when a role is EMPTY and MoSaleh agreed: KIT_FORCE=1 bash scripts/kit_fallback.sh <kit-dir>" >&2
  exit 2
fi
KIT="${1:?usage: KIT_FORCE=1 bash scripts/kit_fallback.sh <kit-dir>}"
[ -f "$KIT/templates/examples/agent.py" ] || { echo "no kit brain at $KIT/templates/examples/agent.py" >&2; exit 1; }
echo "WARNING: deploying the KIT brain md5 $(md5sum "$KIT/templates/examples/agent.py" | cut -c1-8), NOT asst/_shared/agent.py" >&2
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/asst" || exit 1
make() {
  local name="$1" role="$2" cap="$3"
  [ -d "$name/agent" ] || city init agent "$name" >/dev/null
  cp "$KIT/templates/examples/agent.py" "$name/agent/agent.py"
  python3 - "$name" "$role" "$cap" <<'PY'
import sys
name, role, cap = sys.argv[1:4]
crole = "facility" if role == "facility" else role
p = f"{name}/card.yaml"
t = open(p).read().splitlines(); o = []; i = 0; sr = sc = False
while i < len(t):
    l = t[i]; b = l.lstrip("# ").strip()
    if not l.startswith(" ") and b.startswith("role:") and not sr:
        o.append(f"role: {role}"); sr = True; i += 1; continue
    if not l.startswith(" ") and b.startswith("capabilities:") and not sc:
        o += ["capabilities:", f"  - {cap}"]; sc = True; i += 1
        while i < len(t) and (t[i].startswith(" ") or t[i].lstrip().lstrip("#").strip().startswith("- ")):
            i += 1
        continue
    o.append(l); i += 1
open(p, "w").write("\n".join(o) + "\n")
c = f"{name}/city.yaml"
lines = [f"role: {role}" if x.lstrip("# ").startswith("role:") else x for x in open(c).read().splitlines()]
open(c, "w").write("\n".join(lines) + "\n")
PY
  city card validate "$name" | tail -1
}
make gp_kit gp gp
make bc_kit booking_centre booking_centre
make fc_kit facility family_counseling.gynecology
