"""Set the `version:` of a card.yaml for the next deploy. Prints old -> new.

Version scheme (from 21 Nov 00:30), the same number on every agent for the same brain:
    MAJOR = release line (1 = release-1, 2 = release-2 line, 3 = release-3, ...)
    MINOR = build of that release (2.1 = release-2.1); 9x = a teammate branch (2.90 = brain-gp)
    PATCH = deploy attempt of that build (0 first, +1 for every retry)
Every deploy must carry a version never used before on that agent: a redeploy of an old number
fails at promote (ingress route never ready) and can un-certify the live revision.

Usage:
    python3 -I scripts/bump_version.py asst/gp/card.yaml            # patch + 1
    python3 -I scripts/bump_version.py asst/gp/card.yaml 2.1        # 2.1.0, or 2.1.(n+1) if already on 2.1
"""

import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
want = sys.argv[2] if len(sys.argv) > 2 else ""
text = path.read_text()
m = re.search(r"^version:\s*\"?(\d+)\.(\d+)\.(\d+)\"?\s*$", text, re.M)
if not m:
    sys.exit(f"no version line in {path}")
major, minor, patch = (int(x) for x in m.groups())
if want:
    w = re.fullmatch(r"(\d+)\.(\d+)", want)
    if not w:
        sys.exit(f"release must look like 2.1, got {want!r}")
    wmaj, wmin = int(w.group(1)), int(w.group(2))
    if (wmaj, wmin) < (major, minor):
        sys.exit(f"{path.parent.name}: card is already at {major}.{minor}.{patch}, newer than {want}")
    new = f"{wmaj}.{wmin}.{patch + 1 if (wmaj, wmin) == (major, minor) else 0}"
else:
    new = f"{major}.{minor}.{patch + 1}"
path.write_text(text[: m.start()] + f"version: {new}" + text[m.end():])
print(f"{path.parent.name}: version {major}.{minor}.{patch} -> {new}")
