"""External audit B (21 Nov): language, English preferences, bare-date expiry, rescue lock.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_audit_b.py [asst/_shared/agent.py]
"""

import importlib.util
import os
import sys
from datetime import datetime, timezone

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

N = [0]


def chat(*texts):
    rows = []
    for text in texts:
        N[0] += 1
        rows.append(Message(type="chat", sender="citizen:t/c1", msg_id=f"m{N[0]}", need_id="n1",
                            body={"text": text}, recipient="agent:t/booking_centre"))
    return rows


# A (#11): the language is a two-letter lowercase code whatever the lang= token looked like
for raw in ("EN", "en-GB", "En", "en_US"):
    assert brain._say_lang(chat(f"lang={raw} visita")) == "en", raw
assert brain._say_lang(chat("lang=IT visita")) == "it"

# B (#12): one Italian-looking word ("male", "per", "non") does not turn an English sentence Italian
assert brain._text_en("I am a 45 year old male with back pain")
assert brain._text_en("I need help for my pain")
assert not brain._text_en("ho un dolore e mi serve una visita per favore")
assert brain._say_lang(chat("I am a 45 year old male with back pain, please help")) == "en"

# C (#10): English negations flip the preference, "too late" is not an evening wish
assert "mon" not in brain._days(chat("I can't do Monday"))
assert "mon" not in brain._days(chat("I cannot do Monday"))
assert "mon" not in brain._days(chat("I am unable to come on Monday"))
assert brain._days(chat("I can do Monday")) == frozenset({"mon"})
assert brain._window(chat("18:00 is too late")) is None
assert brain._window(chat("it is too late for me")) is None
assert brain._window(chat("è troppo tardi")) is None
assert brain._window(chat("I prefer late")) == (16 * 60, 24 * 60)
assert brain._window(chat("sera")) == (16 * 60, 24 * 60)

# D (#9): a bare YYYY-MM-DD valid_until runs to the end of that day (Rome), then it is expired
now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
assert brain._rx_expired({"valid_until": "2026-11-15"}, now)
assert brain._rx_expired({"expires": "2026-11-19"}, now)
assert not brain._rx_expired({"valid_until": "2026-11-20"}, now)
assert not brain._rx_expired({"valid_until": "2026-11-30"}, now)
assert brain._rx_expired({"valid_until": "2026-11-20T09:00:00+00:00"}, now)
assert not brain._rx_expired({"valid_until": "soon"}, now)
print("OK audit_b")
