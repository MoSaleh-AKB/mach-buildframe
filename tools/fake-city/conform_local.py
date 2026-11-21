"""Run the city's REAL conformance suite (city_conform, full profile) in-process against OUR brain.

Same harness as `city conform` without --url (LiveAgent over a city_sdk Harness with in-memory
relay and index), but the brain is asst/<folder>/agent/agent.py (loaded the way city_sdk.boot
does) and the card comes from asst/<folder>/card.yaml + city.yaml. Differences from the plain
local run, to look like the remote runner against a deployed pod:
  * defer=True, ack_delay=0.0 (cloud setting): 202 first, brain after, renew then final ack.
  * a 202 waits up to 5.0 s for the renew (remote wait_renew), settle() waits up to 5.0 s for
    the final ack (remote settle_wait). A brain slower than that FAILS custody.ack like prod.
  * every brain turn is timed (message type, sender kind, body, seconds); every say is checked
    against the peers `find` returned in that turn (plus the inbound sender and the Ledger).
  * --post404 makes every relay post raise CapError NOT_FOUND (what the remote suite does).

Run (no network, no writes outside stdout):
  ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/conform_local.py            # all 5 folders
  ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/conform_local.py gp hospital
  ... conform_local.py --post404          # relay posts answer 404
  ... conform_local.py -v                 # print every brain turn
Exit code 1 when any role has a blocking FAIL/ERROR, a turn over 2.5 s, a brain exception or a
say to a peer find did not return.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import sys
import time
from pathlib import Path
from typing import Any

from cap.core.errors import CapError
from city.testkit import ephemeral
from city_conform import stages as S
from city_conform.agent import LiveAgent
from city_conform.report import render
from city_conform.runner import Outcome, Stage, run_profile
from city_conform.world import CITY, FIRST, PROBER, SECOND, SENDER, STRANGER
from city_sdk import boot
from city_sdk.city import LEDGER
from city_sdk.harness import Harness
from city_sdk.ports import MemoryIndex, MemoryRelay

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = ("booking_centre", "gp", "hospital", "specialist", "family_counseling")
SLOW = 2.5
SETTLE = 5.0

VERBOSE = "-v" in sys.argv
POST404 = "--post404" in sys.argv

STAGE = contextvars.ContextVar("stage", default="-")
TURN: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("turn", default=None)


class Log:
    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self.stray: list[str] = []
        self.crashes: list[str] = []


class SpyIndex(MemoryIndex):
    async def search(self, query: str):  # type: ignore[override]
        found = await super().search(query)
        turn = TURN.get()
        if turn is not None:
            turn["found"].update(p.agent_id for p in found)
            turn["finds"] += 1
        return found


class SpyRelay(MemoryRelay):
    async def post(self, **kw: Any) -> str:  # type: ignore[override]
        turn = TURN.get()
        if turn is not None:
            turn["says"].append((kw["to"], str(kw["body"].get("text", ""))[:60]))
        if POST404:
            raise CapError("NOT_FOUND", "relay post 404")
        return await super().post(**kw)


class RemoteLikeAgent(LiveAgent):
    """LiveAgent whose waits follow the remote runner (5.0 s renew and ack waits)."""

    remote = True  # like the remote runner: stage 23 shutdown is NOT TESTED in staging

    async def request(self, method, path, payload=None):  # type: ignore[no-untyped-def]
        result = await super().request(method, path, payload)
        if path == "/invoke" and result.status == 202 and payload is not None:
            d = payload["delivery"]
            await self._wait(lambda: (d["msg_id"], d["fence"]) in self.renew_log())
        return result

    async def settle(self, msg_id: str, fence: int) -> None:
        await self._wait(lambda: any(r[0] == msg_id and r[1] == fence for r in self.ack_log()))

    async def _wait(self, done) -> None:  # type: ignore[no-untyped-def]
        end = time.monotonic() + SETTLE
        while not done() and time.monotonic() < end:
            await asyncio.sleep(0.05)


def sender_kind(sender: str) -> str:
    return sender.split(":", 1)[0] if ":" in sender else "?"


def wrap_brain(real, log: Log, folder: str):  # type: ignore[no-untyped-def]
    async def timed(need, city):  # type: ignore[no-untyped-def]
        m = city.inbound
        turn = {
            "stage": STAGE.get(),
            "type": getattr(m, "type", "?"),
            "kind": sender_kind(getattr(m, "sender", "")),
            "body": dict(getattr(m, "body", {}) or {}),
            "found": set(),
            "finds": 0,
            "says": [],
            "secs": 0.0,
            "exc": "",
        }
        TURN.set(turn)
        log.turns.append(turn)
        t0 = time.perf_counter()
        try:
            await real(need, city)
        except BaseException as exc:  # noqa: BLE001
            turn["exc"] = type(exc).__name__
            # OutOfScope is a scope answer; RuntimeError on summary __error__ is the kit's own
            # error contract (stage 21 asks for it): neither is a crash.
            expected = type(exc).__name__ == "OutOfScope" or (
                isinstance(exc, RuntimeError) and getattr(need, "summary", "") == "__error__"
            )
            if not expected:
                log.crashes.append(f"{folder} {turn['stage']}: {type(exc).__name__}: {exc}")
            raise
        finally:
            turn["secs"] = time.perf_counter() - t0
            inbound_sender = getattr(m, "sender", "")
            for to, text in turn["says"]:
                if to not in turn["found"] and to != inbound_sender and to != LEDGER:
                    log.stray.append(f"{folder} {turn['stage']}: said to {to} (not in find): {text!r}")
    return timed


async def build(folder: str, log: Log, *, defer: bool = True) -> RemoteLikeAgent:
    base = ROOT / "asst" / folder
    os.environ["CITY_CARD"] = str(base / "card.yaml")
    os.environ["CITY_CITY"] = str(base / "city.yaml")
    os.environ.pop("CITY_ROLE", None)
    card = boot.load_simple_yaml((base / "card.yaml").read_text(encoding="utf-8"))
    city = boot.load_simple_yaml((base / "city.yaml").read_text(encoding="utf-8"))
    role = city.get("role") if isinstance(city.get("role"), str) else ""
    agent_id = card["agent_id"]
    agent, sender, cityk, prober, stranger = (ephemeral() for _ in range(5))
    built = boot._card(card, agent.public, agent_id, role=role, env={})
    brain = wrap_brain(boot.load_brain(base, boot.BRAIN_SPEC), log, folder)
    keys = {
        agent_id + "#k1": agent.public,
        SENDER + "#k1": sender.public,
        CITY + "#k1": cityk.public,
        PROBER + "#k1": prober.public,
    }
    harness = Harness(
        agent_id=agent_id, private=agent.private, public=agent.public, card=built, brain=brain,
        keys=keys, defer=defer, ack_delay=0.0 if defer else None,
        index=SpyIndex(peers=[FIRST, SECOND]), relay=SpyRelay(agent_id=agent_id),
    )
    await harness.start()
    return RemoteLikeAgent(
        harness, sender=(SENDER, sender.private), prober=(PROBER, prober.private),
        city=(CITY, cityk.private), stranger=(STRANGER, stranger.private),
    )


def tag(stage: Stage) -> Stage:
    async def fn(agent):  # type: ignore[no-untyped-def]
        STAGE.set(f"{stage.number}.{stage.name}")
        return await stage.fn(agent)
    return Stage(stage.number, stage.name, stage.blocking, fn)


EXTRAS = (
    ("7 invoke.semantics", S.invoke_semantics),
    ("10 emergency", S.emergency),
    ("11 scope", S.scope),
    ("21 errors.signed", S.errors_signed),
)


async def run_role(folder: str) -> bool:
    log = Log()
    agent = await build(folder, log)
    t0 = time.perf_counter()
    report = await run_profile(agent, tuple(tag(s) for s in S.CATALOG))
    took = time.perf_counter() - t0
    await agent.harness.flush_acks()
    print(f"\n===== {folder}  (suite {took:.1f}s, post404={POST404}) =====")
    worst: dict[str, float] = {}
    for t in log.turns:
        worst[t["stage"]] = max(worst.get(t["stage"], 0.0), t["secs"])
    ok = report.passed
    for row in report.rows:
        key = f"{row.number}.{row.name}"
        secs = f" brain {worst[key]:.2f}s" if key in worst else ""
        print(f"{row.number:>2} {row.name:<22} {row.outcome.value:<12} {row.detail}{secs}")
    print(f"Profile: {'PASS' if report.passed else 'FAIL'}")
    # The brain-policy stages the remote suite skips: run them on a fresh agent.
    for label, fn in EXTRAS:
        fresh = await build(folder, log, defer=False)  # synchronous: the suite reads the reply
        STAGE.set(label)
        try:
            v = await fn(fresh)
            print(f"   extra {label:<20} {v.outcome.value:<6} {v.detail}")
            if v.outcome in (Outcome.FAIL, Outcome.ERROR):
                print("        (NOT TESTED remotely; informational)")
        except Exception as exc:  # noqa: BLE001
            print(f"   extra {label:<20} ERROR  {type(exc).__name__}: {exc}")
        for t in log.turns:
            if t["stage"] == label:
                print(f"        we said: {t['says']} ({t['secs']:.2f}s, exc={t['exc'] or '-'})")
        await fresh.harness.flush_acks()
        await fresh.stop()
    await agent.stop()
    slow = [t for t in log.turns if t["secs"] > SLOW]
    print(f"brain turns: {len(log.turns)}, slowest {max((t['secs'] for t in log.turns), default=0):.2f}s")
    if VERBOSE:
        for t in log.turns:
            print(f"   {t['secs']:5.2f}s {t['stage']:<24} {t['type']} from {t['kind']} {t['body']} says={len(t['says'])}")
    for t in slow:
        print(f"SLOW {t['secs']:.2f}s {t['stage']} {t['type']} from {t['kind']} {t['body']}")
    for line in log.stray + log.crashes:
        print("ISSUE", line)
    return ok and not slow and not log.stray and not log.crashes


async def main() -> int:
    names = [a for a in sys.argv[1:] if not a.startswith("-")] or list(FOLDERS)
    bad = 0
    for folder in names:
        if not await run_role(folder):
            bad += 1
    print(f"\nroles not clean: {bad}/{len(names)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
