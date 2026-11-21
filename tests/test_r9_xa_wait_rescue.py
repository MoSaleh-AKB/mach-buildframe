"""R9 (#94): a citizen confirmed a partner ASST offer and heard "I wait 45 s, else I offer you
alternatives". When the watcher gives up (need lock busy, history unreadable) the rescue sweep
must still pick the case, so the wait line has to be a hold line, not an answer.

Run: CITY_RESCUE_WARMUP=0 ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_r9_xa_wait_rescue.py [asst/_shared/agent.py]
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
os.environ["CITY_ROLE"] = "booking_centre"
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC, CZ, PARTNER = "agent:r9bc", "citizen:r9-c1", "agent:r9partner"
OFFER = {"offer_id": "of-PX1", "slot_id": "PX1", "facility": PARTNER}
FAILS: list[str] = []


def check(ok, what, out=""):
    print(("PASS " if ok else "FAIL ") + what + ("" if ok else f" -> {out}"))
    if not ok:
        FAILS.append(what)


def at(ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def row(sender, to, text, mid, need, ago):
    return Message(type="chat", sender=sender, recipient=to, msg_id=mid, need_id=need, body={"text": text},
                   sent_at=at(ago))


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows):
        self.rows = rows
        self.out: list[tuple[str, str]] = []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        return []


async def wait_line(lang_text, need, again=False):
    for name in ("_XA_WATCH", "_XA_ALT", "_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_ONCE", "_SENT"):
        g = getattr(brain, name, None)
        if hasattr(g, "clear"):
            g.clear()
    cz = row(CZ, BC, lang_text, f"{need}-cz", need, 0)
    fake = Fake([cz, row(BC, PARTNER, "confirm offer_id=of-PX1 slot_id=PX1 consent_ref=cons-x", f"{need}-fw", need, 0)])
    if again:
        brain._remember_key(brain._XA_WATCH, f"{need}:of-PX1")
    done = await brain._xa_confirm_hold(fake, fake.rows, cz, OFFER)
    for task in list(brain._XA_TASKS):  # the 45 s watcher: we test the case it gave up on
        task.cancel()
    lines = [t for to, t in fake.out if to == CZ]
    return done, lines


async def main():
    for label, text, again in (
        ("it wait", "confirm offer_id=of-PX1 consent_ref=cons-x", False),
        ("worded wait", "I confirm offer_id=of-PX1 consent_ref=cons-x", False),
        ("it still", "confirm offer_id=of-PX1 consent_ref=cons-x", True),
    ):
        need = f"need-r9-{label.replace(' ', '')}"
        done, lines = await wait_line(text, need, again)
        check(done and len(lines) == 1, f"{label}: one status line to the citizen", lines)
        if not lines:
            continue
        line = lines[0]
        check(brain._is_hold_line(line), f"{label}: the wait line is a hold line", repr(line[-3:]))
        # The watcher gave up silently: 60 s later the case holds only the confirm, our relay and the wait line.
        case = [
            row(CZ, BC, text, f"{need}-cz", need, 60),
            row(BC, PARTNER, "confirm offer_id=of-PX1 slot_id=PX1 consent_ref=cons-x", f"{need}-fw", need, 59),
            row(BC, CZ, line, f"{need}-wait", need, 59),
        ]
        other = row("citizen:r9-other", BC, "ciao", "other-1", "need-other", 0)
        brain._RESCUED.clear()
        brain._RESCUE_REFUSED.clear()
        picks = brain._rescue_picks(case, BC, other, datetime.now(timezone.utc))
        check(any(p[1].msg_id == f"{need}-cz" for p in picks), f"{label}: rescue picks the open confirm",
              [(p[0], p[1].msg_id) for p in picks])
    # A real answer (the alternatives) after the confirm still closes the case for the rescue.
    need = "need-r9-alt"
    case = [
        row(CZ, BC, "confirm offer_id=of-PX1 consent_ref=cons-x", f"{need}-cz", need, 90),
        row(BC, PARTNER, "confirm offer_id=of-PX1 slot_id=PX1 consent_ref=cons-x", f"{need}-fw", need, 89),
        row(BC, CZ, brain._XA_SILENT_IT.format(s=45), f"{need}-alt", need, 44),
    ]
    picks = brain._rescue_picks(case, BC, row("citizen:x", BC, "ciao", "o2", "need-o2", 0), datetime.now(timezone.utc))
    check(not any(p[1].msg_id == f"{need}-cz" for p in picks), "answered confirm: rescue leaves it", picks)


asyncio.run(main())
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAIL: {FAILS}")
sys.exit(1 if FAILS else 0)
