"""gp / consultorio: the privacy refusal carries reason=out_of_scope (audit R2A), never consent_missing.

Run: <city python> -I tests/gp/test_gp_privacy_code.py asst/_gp/agent.py
"""
import importlib.util
import os
import sys

os.environ.setdefault("CITY_ROLE", "gp")
spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_gp/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
fails = 0
for role in ("gp", "family_counseling"):
    for agent in (False, True):
        for lang in ("it", "en"):
            text = brain._privacy_text(agent=agent, role=role, lang=lang)
            if not text.startswith("cancel reason=out_of_scope\n"):
                fails += 1
                print(f"FAIL {role} agent={agent} {lang}: {text[:60]!r}")
print(f"{'OK' if not fails else 'FAILED'} test_gp_privacy_code ({fails} failed)")
sys.exit(1 if fails else 0)
