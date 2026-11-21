"""Set the seat of each agent folder (role, capabilities) as the kit table says.

Run from asst/ (the parent of the five agent folders), after `city init agent <name>`:

    python3 ../scripts/set_cards.py

For each existing folder among booking_centre, hospital, gp, specialist,
family_counseling it sets `role` and `capabilities` in card.yaml and `role` in
city.yaml. Everything else in the files stays as `city init` wrote it. Safe to
run again: a file already right is left alone. Stdlib only, Python 3.12+.
"""

from __future__ import annotations

import sys
from pathlib import Path

# folder -> (card role, capability, city.yaml role), from the kit (getting-started.md)
SEATS: dict[str, tuple[str, str, str]] = {
    "booking_centre": ("booking_centre", "booking_centre", "booking_centre"),
    "hospital": ("facility", "hospital.physiatry", "facility"),
    "gp": ("gp", "gp", "gp"),
    "specialist": ("facility", "specialist_care.cardiology", "facility"),
    "family_counseling": ("facility", "family_counseling.gynecology", "facility"),
}


def set_card(text: str, role: str, capability: str) -> str:
    """Replace the role line and the capabilities block; keep every other line."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    seen_role = seen_caps = False
    while i < len(lines):
        line = lines[i]
        bare = line.lstrip("# ").strip()
        if not line.startswith(" ") and (bare.startswith("role:") or line.startswith("role:")) and not seen_role:
            out.append(f"role: {role}")
            seen_role = True
            i += 1
            continue
        if not line.startswith(" ") and (bare.startswith("capabilities:") or line.startswith("capabilities:")) and not seen_caps:
            out.append("capabilities:")
            out.append(f"  - {capability}")
            seen_caps = True
            i += 1
            # drop the old list items (indented "- x" or commented TODO items)
            while i < len(lines) and (
                lines[i].startswith(" ") or lines[i].lstrip().lstrip("#").strip().startswith("- ")
            ):
                i += 1
            continue
        out.append(line)
        i += 1
    if not seen_role:
        out.append(f"role: {role}")
    if not seen_caps:
        out += ["capabilities:", f"  - {capability}"]
    return "\n".join(out) + "\n"


def set_city(text: str, role: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    seen = False
    for line in lines:
        bare = line.lstrip("# ").strip()
        if not line.startswith(" ") and bare.startswith("role:") and not seen:
            out.append(f"role: {role}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"role: {role}")
    return "\n".join(out) + "\n"


def main() -> int:
    here = Path.cwd()
    found = [name for name in SEATS if (here / name).is_dir()]
    if not found:
        print(f"No agent folders here ({here}). Run from asst/ after `city init agent <name>`.")
        return 1
    errors = 0
    for name in found:
        card = here / name / "card.yaml"
        city = here / name / "city.yaml"
        if not card.is_file() or not city.is_file():
            print(f"REFUSED {name}: card.yaml or city.yaml missing (run `city init agent {name}` first)")
            errors += 1
            continue
        role, capability, city_role = SEATS[name]
        for path, new in (
            (card, set_card(card.read_text(encoding="utf-8"), role, capability)),
            (city, set_city(city.read_text(encoding="utf-8"), city_role)),
        ):
            old = path.read_text(encoding="utf-8")
            if old == new:
                print(f"ok      {name}/{path.name} already right")
            else:
                path.write_text(new, encoding="utf-8")
                print(f"changed {name}/{path.name}")
    missing = [n for n in SEATS if n not in found]
    if missing:
        print("not created yet:", ", ".join(missing))
    print("Next: city card validate <folder> for each, then city deploy (specialist first).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
