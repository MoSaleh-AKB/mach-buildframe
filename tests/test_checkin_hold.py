"""SI-33: a citizen check-in at the facility while the Ledger is slow or the case history is down.

chaos.py sina_CH_17 (fault history_down): the first line to the citizen came only after the whole
Ledger wait (24 s). Silence > 3 s is a fail. The citizen must hear a short line within 3 s, the
Ledger answer is still awaited, and no line says "checked in" before the Ledger's checked_in.
Run: <city python> -I tests/test_checkin_hold.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import itertools
import re
import sys
import time

from city_sdk.city import LEDGER, CapError, Message, Need

BRAIN = sys.argv[1]
FIRST_LINE = 3.0  # the judge: every citizen message gets a visible reply within 3 s
WAIT = 8.0  # Ledger wait for the test (live 24 s): same code path, shorter run
CLAIM = re.compile(r"^checkin\b|check-in è registrato|checked in", re.IGNORECASE | re.MULTILINE)
_ids = itertools.count(1)


def load(role):
    spec = importlib.util.spec_from_file_location(f"brain_{next(_ids)}", BRAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # one fresh module per case: no memory carried over
    mod.role_of = lambda: role
    mod._ADVANCE_BUDGET = WAIT
    return mod


def msg(sender, to, text, need):
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{next(_ids)}", need_id=need,
                   body={"text": text})


class Fake:
    def __init__(self, role, inbound, *, history_down=False, answer="", after=0.0):
        self.agent_id = f"agent:t33/{role}"
        self.address = ""
        self.inbound = inbound
        self.rows = [inbound]
        self.citizen = []  # (seconds, text)
        self.t0 = time.monotonic()
        self.history_down, self.answer, self.after = history_down, answer, after
        self.answered_at = None

    async def say(self, to, text, *, status=None):
        self.rows.append(msg(self.agent_id, to, text, self.inbound.need_id))
        if to.startswith("citizen:"):
            self.citizen.append((round(time.monotonic() - self.t0, 2), text))
        if to == LEDGER and text.startswith("checkin") and self.answer:
            asyncio.get_running_loop().call_later(self.after, self._ledger)
        return "x"

    def _ledger(self):
        self.answered_at = round(time.monotonic() - self.t0, 2)
        self.rows.append(msg(LEDGER, self.agent_id, self.answer, self.inbound.need_id))

    async def history(self, peer=None, *, need_id=None):
        if self.history_down:
            raise CapError("UPSTREAM_TIMEOUT", "history unreachable")
        return list(self.rows)

    async def llm(self, messages, *, need_id=None):
        return {"content": ""}

    async def find(self, query):
        return []


async def run(name, role, text, **fault):
    brain = load(role)
    need = f"N-SI33-{next(_ids)}"
    inbound = msg("citizen:t33/rita", f"agent:t33/{role}", text, need)
    city = Fake(role, inbound, **fault)
    await asyncio.wait_for(brain.main(Need(summary=text, message=inbound), city), 60)
    return name, brain, city


def check(name, brain, city, fails):
    lines = city.citizen
    print(f"{name}: first={lines[0][0] if lines else None} ledger={city.answered_at} lines="
          + " | ".join(f"{t}s {x[:60]!r}" for t, x in lines))
    if not lines:
        fails.append(f"{name}: citizen got no line")
        return
    if lines[0][0] > FIRST_LINE:
        fails.append(f"{name}: first citizen line at {lines[0][0]} s (> {FIRST_LINE} s)")
    for at, text in lines:
        if CLAIM.search(text) and (city.answered_at is None or at < city.answered_at
                                   or not city.answer.startswith("checked_in")):
            fails.append(f"{name}: check-in claimed at {at} s before the Ledger confirmed: {text[:60]!r}")


async def main():
    fails = []
    cases = await asyncio.gather(
        # 1. chaos sina_CH_17: history down, so the Ledger answer can never be read back.
        run("CH17 specialist history_down", "specialist_care",
            "Buongiorno, sono qui per la visita cardiologica, booking_id=BK-5678.", history_down=True),
        # 2. Live Ledger (8-20 s): checked_in arrives after 5 s.
        run("specialist slow Ledger", "specialist_care", "checkin booking_id=BK-2",
            answer="checked_in booking_id=BK-2", after=5.0),
        # 3. Slow Ledger refuses the check-in: the refusal still reaches the citizen.
        run("hospital slow refusal", "hospital", "checkin booking_id=BK-3",
            answer="refused op=checkin booking_id=BK-3 code=UNKNOWN_BOOKING", after=5.0),
        # 4. Fast Ledger: one line only, no holding line before the real answer.
        run("hospital fast Ledger", "hospital", "checkin booking_id=BK-4",
            answer="checked_in booking_id=BK-4", after=0.2),
        # 5. Consultorio check-in with history down.
        run("consultorio history_down", "family_counseling", "checkin booking_id=BK-5", history_down=True),
    )
    for name, brain, city in cases:
        check(name, brain, city, fails)
    byname = {name: (brain, city) for name, brain, city in cases}

    brain, city = byname["CH17 specialist history_down"]
    if not any(text == brain._CHECKIN_WAIT for _, text in city.citizen):
        fails.append("CH17: no honest 'not confirmed yet' line after the Ledger wait")
    brain, city = byname["specialist slow Ledger"]
    if not any(text.startswith("checkin booking_id=BK-2") for _, text in city.citizen):
        fails.append("specialist slow Ledger: the checked-in line never reached the citizen")
    brain, city = byname["hospital slow refusal"]
    if not any("non ha accettato il check-in" in text for _, text in city.citizen):
        fails.append("hospital slow refusal: the citizen never heard the refusal")
    brain, city = byname["hospital fast Ledger"]
    if [t for _, t in city.citizen if not t.startswith("checkin booking_id=BK-4")]:
        fails.append("hospital fast Ledger: extra line next to the checked-in answer")

    if fails:
        print("FAILED:", "; ".join(fails))
        sys.exit(1)
    print("ALL PASS test_checkin_hold")


asyncio.run(main())
