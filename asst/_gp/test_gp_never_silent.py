"""gp / consultorio never leave a citizen silent: hold line within ~3 s, speak before a Timeout is
re-raised, one retry of a 5xx citizen send.
Run: <city python> -I tools/fake-city/test_gp_never_silent.py asst/_shared/agent.py   (exit 0 = pass)"""

import asyncio
import importlib.util
import os
import sys
import time
from types import SimpleNamespace

from city_sdk.city import CapError, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

TEXT = "mal di schiena da diverse settimane"
LIMIT = 4.5  # hold line at 3 s + one short read
N = [0]
FAILS: list[str] = []


class Timeout(Exception):
    """Name carries 'Timeout' like the transport's ReadTimeout."""


class Server503(CapError):
    def http_status(self):
        return 503


class FakeCity:
    def __init__(self, role, inbound, *, ledger_delay=0.0, history_delay=0.0, ledger_exc=None, first_503=False,
                 rows=None):
        self.agent_id = f"agent:t/{role}"
        self.inbound = inbound
        self.address = ""
        self.t0 = time.monotonic()
        self.citizen_lines: list[tuple[float, str]] = []
        self.ledger_lines: list[str] = []
        self.ledger_delay = ledger_delay
        self.history_delay = history_delay
        self.ledger_exc = ledger_exc
        self.first_503 = first_503
        self.sends = 0
        self.rows = rows or []

    async def say(self, to, text, *, status=None):
        if to.startswith("citizen:"):
            self.sends += 1
            if self.first_503 and self.sends == 1:
                raise Server503("DRAINING", "503")
            self.citizen_lines.append((time.monotonic() - self.t0, text))
            return
        self.ledger_lines.append(text)
        if self.ledger_exc:
            raise self.ledger_exc
        await asyncio.sleep(self.ledger_delay)

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(self.history_delay)
        return [*self.rows, self.inbound]

    async def find(self, query):
        return []


def mk(role):
    N[0] += 1
    m = Message(type="chat", sender="citizen:t/c1", msg_id=f"ns{N[0]}", need_id=f"nn{N[0]}",
                body={"text": TEXT}, recipient=f"agent:t/{role}")
    return m


async def run_turn(role, city, wait):
    os.environ["CITY_ROLE"] = role
    need = SimpleNamespace(summary=TEXT, is_emergency=False)
    task = asyncio.create_task(brain.main(need, city))
    try:
        await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        pass
    except BaseException as exc:
        return exc
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
    return None


def check(name, ok, info=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {info}" if info else ""))
    if not ok:
        FAILS.append(name)


async def main():
    for role in ("gp", "family_counseling"):
        # 1 normal Ledger: answered at once
        m = mk(role)
        c = FakeCity(role, m)
        await run_turn(role, c, 6)
        check(f"{role} normal", bool(c.citizen_lines) and c.citizen_lines[0][0] < 1.0,
              f"first={c.citizen_lines[0][0]:.2f}s" if c.citizen_lines else "NO LINE")
        # 2 slow Ledger (12 s)
        m = mk(role)
        c = FakeCity(role, m, ledger_delay=12.0)
        await run_turn(role, c, LIMIT)
        check(f"{role} slow ledger 12 s", bool(c.citizen_lines) and c.citizen_lines[0][0] <= LIMIT,
              f"first={c.citizen_lines[0][0]:.2f}s" if c.citizen_lines else "NO LINE")
        # 3 hanging history
        m = mk(role)
        c = FakeCity(role, m, history_delay=3600.0)
        await run_turn(role, c, LIMIT + 1.0)
        check(f"{role} hanging history", bool(c.citizen_lines) and c.citizen_lines[0][0] <= LIMIT + 1.0,
              f"first={c.citizen_lines[0][0]:.2f}s" if c.citizen_lines else "NO LINE")
        # 4 Timeout inside the turn: a citizen line is sent before it is re-raised
        m = mk(role)
        c = FakeCity(role, m, ledger_exc=Timeout("ReadTimeout"))
        exc = await run_turn(role, c, 8)
        check(f"{role} timeout speaks then raises", bool(c.citizen_lines) and (isinstance(exc, Timeout) or exc is None),
              f"lines={len(c.citizen_lines)} exc={type(exc).__name__}")
        # 5 first citizen send 503: retried once
        m = mk(role)
        c = FakeCity(role, m, first_503=True)
        await run_turn(role, c, 6)
        check(f"{role} 503 retried", bool(c.citizen_lines) and c.sends >= 2, f"sends={c.sends}")
    # 6 gp: a slow turn of the same need ahead (per-need lock), a second message must still get a line
    brain._GATES.clear()
    m = mk("gp")
    c = FakeCity("gp", m, ledger_delay=20.0)
    gate = brain._Gate()
    brain._GATES[m.need_id] = gate
    await gate.lock.acquire()  # a slow turn of this need holds the lock
    await run_turn("gp", c, LIMIT)
    gate.lock.release()
    check("gp lock held by another turn", bool(c.citizen_lines) and c.citizen_lines[0][0] <= LIMIT,
          f"first={c.citizen_lines[0][0]:.2f}s" if c.citizen_lines else "NO LINE")
    os.environ.pop("CITY_ROLE", None)
    print(f"{len(FAILS)} failed")
    return 1 if FAILS else 0


sys.exit(asyncio.run(main()))
