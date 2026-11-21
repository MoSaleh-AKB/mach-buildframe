"""#138: after a boot the gp recovery sweep drains a deploy gap fast. Ten citizens waiting 15-60 min
are all picked by the sweeps of the first 5 minutes (warmup ~2 min, ~6 cases per sweep), newest
first; a case older than 2 h is left alone; CITY_GP_RECOVERY=0 still turns the sweep off.
`_recover` is replaced by a recorder, so no message is sent.

Run: <city python> -I tests/gp/test_gp_recovery_drain.py [asst/_gp/agent.py]   (exit 0 = pass)
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ.pop("CITY_RESCUE_WARMUP", None)  # the brain's own default warmup is under test
from city_sdk.city import Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
spec = importlib.util.spec_from_file_location("brain", BRAIN)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "gp"
GP = sorted(brain._MATES)[0]

AGES = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60]  # minutes the citizen has waited
OLD = 180  # older than the 2 h horizon


def ask(i, ago):
    return Message(type="chat", sender=f"citizen:t/{i}", msg_id=f"m{i}", need_id=f"N-{i}",
                   body={"text": "Ho mal di schiena da due giorni"}, recipient=GP,
                   sent_at=(datetime.now(timezone.utc) - timedelta(minutes=ago)).isoformat())


class City:
    def __init__(self, rows):
        self.agent_id, self.rows, self.inbound = GP, rows, None

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]


PICKED = []


async def record(city, message, chat, role, *, sweep=False):
    PICKED.append(message.need_id)
    brain._REC_SEEN[message.need_id] = time.monotonic()  # as the real _recover does
    return True


async def main():
    fails = []
    brain._recover = record
    rows = [ask(i, a) for i, a in enumerate(AGES)] + [ask(99, OLD)]
    brain._REC_CITY[:] = [City(rows)]
    boot = time.monotonic()
    # the sweeps a fresh container runs in its first 5 minutes (one per _REC_SWEEP_EVERY)
    t = brain._REC_SWEEP_EVERY
    while t <= 300:
        brain._REC_BOOT = boot - t
        await brain._rec_sweep()
        t += brain._REC_SWEEP_EVERY
    want = [f"N-{i}" for i in range(len(AGES))]  # AGES is ascending: newest first
    if sorted(PICKED) != sorted(want):
        fails.append(f"picked {PICKED}, want all ten waiting cases within 5 min")
    if PICKED[:len(want)] != want[:len(PICKED)]:
        fails.append(f"order {PICKED}, want newest first")
    if "N-99" in PICKED:
        fails.append("a case older than 2 h was recovered")
    PICKED.clear()
    brain._REC_SEEN.clear()
    os.environ["CITY_GP_RECOVERY"] = "0"
    await brain._rec_sweep()
    del os.environ["CITY_GP_RECOVERY"]
    if PICKED:
        fails.append("CITY_GP_RECOVERY=0 did not stop the sweep")
    for f in fails:
        print("FAIL", f)
    print(("ALL PASS" if not fails else f"FAIL {len(fails)}") + " test_gp_recovery_drain")
    sys.exit(1 if fails else 0)


asyncio.run(main())
