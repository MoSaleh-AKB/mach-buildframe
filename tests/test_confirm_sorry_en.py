"""A lost confirm in English says the date is gone (the resume line must not replace it).

_SORRY_EN was defined twice: the lost-confirm line near the top and the resume line much later.
The later one won, so an English citizen whose confirmed date was gone read "Sorry you have to
write again ..." glued to the new offers instead of "the date you confirmed is no longer available".

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_confirm_sorry_en.py [asst/_shared/agent.py]
"""

import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

# 1. the English sorry the lost-confirm fallback reads says the date is gone, with a leading space
names = [n for n in brain._confirm_fallback.__code__.co_names if "SORRY" in n and n.endswith("_EN")]
assert names, "the lost-confirm fallback has no English sorry line"
for n in names:
    text = getattr(brain, n)
    assert "no longer available" in text, f"{n} = {text!r}"
    assert text.startswith(" "), f"{n} is glued to the offer head: {text!r}"

# 2. the Italian line is the same message
assert "non è più disponibile" in brain._SORRY_IT, brain._SORRY_IT

# 3. the resume line keeps its own text (used by the returning-citizen path)
_RESUME_EN = getattr(brain, "_SORRY_EN", None) or brain._SORRY_AGAIN_EN  # development names the resume line _SORRY_AGAIN_EN
assert "write again" in _RESUME_EN, _RESUME_EN

# 4. no sorry constant is bound twice at the top level
src = open(BRAIN, encoding="utf-8").read()
for n in set(re.findall(r"^(_[A-Z_]*SORRY[A-Z_]*) *[:=]", src, re.M)):
    count = len(re.findall(rf"^{n} *[:=]", src, re.M))
    assert count == 1, f"{n} defined {count} times"

print("OK test_confirm_sorry_en")
