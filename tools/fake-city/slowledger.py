"""Slow Ledger: the booking centre must deliver real offers even when the Ledger needs up to 15 s.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/slowledger.py asst/_shared/agent.py [A B C D E F]

The fake city answers `prescription`, `agenda` and `world` after DELAYS seconds. A Ledger reply is
appended to the case history WITHOUT opening a turn (live behaviour: the relay accepts the reply and
the agent rejects the push). Variant OPEN also hands the reply to the brain as its own turn.
Scenarios:
  A  first need.request, slow Ledger: offers must reach the citizen (also with the replies in history only)
  B  cancel + confirm in the same second, Ledger 8/14 s and 20/20/20 s: a commit reaches the Ledger or a booking.receipt reaches the citizen
  C  slow request: one offer message per turn, holding line by about 3 s
  D  lang=uk token but English text: English reply, English-speaking doctor wish
  E  rescue sweep: a confirm as the last citizen message is picked and committed (Ledger 8/14 and 14/14, inside the 15 s sweep budget)
  F  rescue sweep: a need that only got the holding line is still pending
"""

import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")  # !220: the brain reads it at import, tests start right after boot

from city_sdk.city import LEDGER, CapError, Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = next((a for a in sys.argv[1:] if a.endswith(".py")), os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py"))
only = {a.upper() for a in sys.argv[1:] if not a.endswith(".py")}
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "booking_centre"

BC = "agent:t07/booking_centre"
HO = "agent:t07/hospital"
CZ = "citizen:t07/c1"
NOW = datetime.now(timezone.utc)
SEQ = [0]
FAILS: list[str] = []


def agenda_now() -> str:
    return (NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()


def slots_text(facility: str = HO) -> str:
    base = (NOW + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    rows = []
    for i, doc in enumerate(("asst-centro-h1", "asst-centro-h1", "asst-centro-h2"), start=1):
        at = (base + timedelta(hours=3 * (i - 1))).isoformat()
        day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[base.weekday()]
        rows.append(f"slot_id=S{i} doctor_id={doc} at={at} day={day} facility={facility} procedure=VIS-FIS free=1")
    return "slots page=1/1\n" + "\n".join(rows)


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=(datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat())


class SlowCity:
    def __init__(self, need, delays=(6.0, 9.0, 12.0), opens_turn=False, rows=None, hc=(0.3, 0.3)):
        self.agent_id = BC
        self.address = ""
        self.need = need
        self.delays = {"prescription": delays[0], "agenda": delays[1], "world": delays[2]}
        self.hold_delay, self.commit_delay = hc
        self.opens_turn = opens_turn
        self.rows = list(rows or [])
        self.inbound = None
        self.out: list[tuple[float, str, str]] = []
        self.t0 = time.monotonic()
        self.tasks: list[asyncio.Task] = []

    def out_all(self):
        """Everything we sent: citizen lines with their time, plus the Ledger sends from rows."""

        sent = [(0.0, r.recipient, r.text) for r in self.rows if r.sender == self.agent_id and r.recipient == LEDGER]
        return sent + list(self.out)

    def reset_clock(self):
        self.t0 = time.monotonic()

    async def say(self, to, text, *, status=None, need=None):
        if to == LEDGER:
            head = text.split()[0]
            if head in self.delays or head in ("hold", "commit"):
                self.tasks.append(asyncio.create_task(self._ledger(head, text)))
        else:
            self.out.append((time.monotonic() - self.t0, to, text))
        self.rows.append(msg(self.agent_id, to, text, need or self.need))
        return "ok"

    async def _ledger(self, head, text):
        await asyncio.sleep(
            {"hold": self.hold_delay, "commit": self.commit_delay}.get(head) or self.delays.get(head, 0.3)
        )
        fields = dict(w.split("=", 1) for w in text.split() if "=" in w)
        if head == "hold":
            body = (f"held hold_id=H-{fields.get('slot_id')} slot_id={fields.get('slot_id')} "
                    f"citizen={fields.get('citizen', '')} expires_in=300")
        elif head == "commit":
            body = (f"committed booking_id={fields.get('hold_id')} ledger_ref=LR-{fields.get('hold_id')} "
                    f"slot_id={fields.get('hold_id', '').removeprefix('H-')}")
        elif head == "prescription":
            number = text.split("prescription_number=")[-1].split()[0]
            body = f"prescription prescription_number={number} procedure=VIS-FIS priority=B state=issued"
        elif head == "agenda":
            body = f"agenda agenda_now={agenda_now()}"
        else:
            body = slots_text()
        row = msg(LEDGER, self.agent_id, body, self.need)
        self.rows.append(row)
        if self.opens_turn:
            try:
                await self.turn(row)
            except Exception as exc:  # noqa: BLE001
                print('      ledger turn raised', type(exc).__name__, exc)

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []

    async def turn(self, row):
        c = SimpleNamespace(**{k: getattr(self, k) for k in ()})  # noqa: F841 (documentation only)
        view = _View(self, row)
        await brain.main(SimpleNamespace(summary="x", is_emergency=False), view)


class _View:
    """One turn's city: shares rows and out with the SlowCity, own inbound."""

    def __init__(self, city, inbound):
        self._c = city
        self.inbound = inbound
        self.agent_id = city.agent_id
        self.address = ""

    async def say(self, to, text, *, status=None):
        return await self._c.say(to, text, status=status, need=self.inbound.need_id)

    async def history(self, peer=None, *, need_id=None):
        return await self._c.history(peer, need_id=need_id)

    async def find(self, query):
        return await self._c.find(query)


def reset():
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS"):
        getattr(brain, name, {}).clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST.clear()
    if hasattr(brain, "_RESCUE_LAST"):
        brain._RESCUE_LAST.append(time.monotonic())  # no sweep unless a scenario asks for it
    sent = getattr(brain, "_SENT", None)
    if hasattr(sent, "clear"):
        sent.clear()


def offers(city):
    return [(t, text) for t, to, text in city.out if to == CZ and "offer_id=of-" in text]


def check(ok, label, city=None):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)
        if city is not None:
            for t, to, text in city.out:
                print(f"      [{t:5.1f}s] {to[-6:]} : {text[:110]!r}")


NEED_RX = "need.request area={'district':_6} lang=it prescription_number=prescription_number-N-0001"


async def run_turn(city, row, limit=40.0):
    city.rows.append(row)
    city.reset_clock()
    view = _View(city, row)
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), limit)


async def settle(city, until=24.0):
    """Let continuations (Ledger turns, rescue) run; the clock is the case clock."""
    while time.monotonic() - city.t0 < until:
        await asyncio.sleep(0.25)
    for task in city.tasks:
        task.cancel()


async def scenario_a(label, opens_turn, delays):
    reset()
    need = "N-A"
    city = SlowCity(need, delays=delays, opens_turn=opens_turn)
    first = msg(CZ, BC, "Mi fa male il ginocchio quando cammino. Vorrei una visita. " + NEED_RX, need)
    t0 = time.monotonic()
    await run_turn(city, first)
    took = time.monotonic() - t0
    # The world can be asked only once the prescription names the procedure, so the first offers
    # come at prescription + world delay at the earliest (8 + 20 = 28 s for 8/14/20). Before the
    # 1 s desk turn they went out inside the turn (34 s); now from the background resume.
    await settle(city, until=max(20.0, delays[0] + delays[2] + 4.0))
    got = offers(city)
    check(bool(got), f"A {label}: offers delivered (turn took {took:.1f}s, first offer at {got[0][0]:.1f}s)" if got
          else f"A {label}: offers delivered (turn took {took:.1f}s)", city)
    hold = [t for t, to, text in city.out if to == CZ and "Sto verificando" in text]
    if hold:
        check(hold[0] <= 4.5, f"A {label}: holding line by 3-4 s (at {hold[0]:.1f}s)", city)
    check(len(got) <= 1, f"A {label}: one offer message", city)
    return city


def _acted(city):
    """The confirm reached the Ledger as a commit, or the citizen got a booking.receipt."""

    commit = any(to == LEDGER and text.split()[0] == "commit" for t, to, text in city.out_all())
    receipt = any(to == CZ and "booking.receipt" in text.split() for t, to, text in city.out)
    return commit or receipt


def _confirm_rows(need):
    rows = [
        msg(CZ, BC, "Vorrei una visita. " + NEED_RX, need, ago=70),
        msg(BC, LEDGER, "prescription prescription_number=prescription_number-N-0001", need, ago=69),
        msg(BC, LEDGER, "agenda", need, ago=69),
        msg(BC, LEDGER, "world procedure=VIS-FIS page=1", need, ago=69),
        msg(LEDGER, BC, "prescription prescription_number=prescription_number-N-0001 procedure=VIS-FIS priority=B state=issued", need, ago=68),
        msg(LEDGER, BC, f"agenda agenda_now={agenda_now()}", need, ago=68),
        msg(LEDGER, BC, slots_text(BC), need, ago=68),
        msg(BC, CZ, f"offer offer_id=of-S1 slot_id=S1 doctor_id=asst-centro-h1 facility={BC} procedure=VIS-FIS "
            "prescription_number=prescription_number-N-0001", need, ago=60),
    ]
    return rows


async def scenario_b(label, hc):
    reset()
    need = "N-B"
    rows = _confirm_rows(need)
    city = SlowCity(need, delays=(8.0, 14.0, 20.0), rows=rows, hc=hc)
    city.rows.append(msg(BC, CZ, "offer offer_id=wl-N-B-alt waiting_list=true", need, ago=40))
    cancel = msg(CZ, BC, "cancel offer_id=wl-N-B-alt reason=waiting_list", need)
    confirm = msg(CZ, BC, "confirm consent_ref=cons-N-0001-booking offer_id=of-S1", need)
    city.rows.extend([cancel, confirm])
    city.reset_clock()
    # Both pushes arrive at once; the cancel is handled first on purpose (the live order).
    await asyncio.gather(
        asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), _View(city, cancel)), 45),
        asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), _View(city, confirm)), 45),
    )
    await settle(city, until=max(hc) + 3.0)
    ledger = [text.split()[0] for t, to, text in city.out_all() if to == LEDGER]
    check(_acted(city), f"B {label}: confirm + cancel, Ledger {hc[0]:.0f}/{hc[1]:.0f}s: confirm committed or receipt sent (Ledger got {ledger})", city)
    line = [t for t, to, text in city.out if to == CZ and "ricevuta appena arriva" in text]
    check(not line or _acted(city), f"B {label}: no 'ricevuta appena arriva' promise left unkept", city)


async def scenario_c(opens_turn):
    reset()
    need = "N-C"
    city = SlowCity(need, delays=(3.0, 4.0, 5.0), opens_turn=opens_turn)
    first = msg(CZ, BC, "Ho mal di schiena. Vorrei una visita. " + NEED_RX, need)
    await run_turn(city, first)
    await settle(city, until=14.0)
    got = offers(city)
    check(len(got) == 1, f"C one offer message per turn ({len(got)} sent)", city)


async def scenario_d():
    reset()
    need = "N-D"
    city = SlowCity(need, delays=(0.2, 0.2, 0.2))
    first = msg(CZ, BC, "My back is killing me, I would love to see a physiatrist who speaks English, please. "
                "need.request area={'district':_6} lang=uk prescription_number=prescription_number-N-0001", need)
    await run_turn(city, first)
    await settle(city, until=4.0)
    texts = [text for t, to, text in city.out if to == CZ]
    joined = " ".join(texts)
    italian = ("Buongiorno" in joined) or ("disponibilit" in joined and "availab" not in joined.lower())
    check(bool(texts) and not italian and ("availab" in joined.lower() or "offer" in joined.lower()),
          "D English text with lang=uk gets an English reply", city)
    check(brain._pref_lang([first]) == "en" if hasattr(brain, "_pref_lang") else True,
          "D 'speaks English' is the doctor-language wish")


async def scenario_e(label, hc):
    reset()
    need = "N-E"
    rows = _confirm_rows(need)
    rows.append(msg(CZ, BC, "confirm consent_ref=cons-N-0001-booking offer_id=of-S1", need, ago=45))
    other = msg(CZ, BC, "ci siete?", "N-other", ago=1)
    city = SlowCity(need, delays=(8.0, 14.0, 20.0), rows=rows, hc=hc)
    city.rows.append(other)
    city.reset_clock()
    brain._RESCUE_LAST.clear()
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), _View(city, other)), 45)
    await settle(city, until=max(hc) + 3.0)
    check(_acted(city), f"E {label}: rescue sweep commits a confirm as last citizen message (Ledger {hc[0]:.0f}/{hc[1]:.0f}s)", city)


async def scenario_f():
    """Hold line sent to a citizen whose need message got nothing else: the rescue still picks it,
    also a plain new need (cardiology request with a prescription), not only a confirm."""

    reset()
    need = "N-F"
    need_msg = msg(CZ, BC, "Ho un dolore e vorrei una visita cardiologica. " + NEED_RX, need, ago=60)
    rows = [need_msg, msg(BC, CZ, brain._HOLD_NOTE, need, ago=50)]
    other = msg(CZ, BC, "ci siete?", "N-other", ago=1)
    city = SlowCity(need, delays=(0.3, 0.3, 0.3), rows=rows)
    city.rows.append(other)
    city.reset_clock()
    brain._RESCUE_LAST.clear()
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), _View(city, other)), 45)
    await settle(city, until=6.0)
    check(bool(offers(city)), "F rescue picks a need that only got the holding line (offers delivered)", city)


async def main():
    if not only or "A" in only:
        await scenario_a("no-turn", False, (6.0, 9.0, 12.0))
        await scenario_a("turn-opens", True, (6.0, 9.0, 12.0))
        await scenario_a("no-turn 8/14/20s", False, (8.0, 14.0, 20.0))
    if not only or "B" in only:
        await scenario_b("8/14", (8.0, 14.0))
        await scenario_b("20/20/20", (20.0, 20.0))
    if not only or "C" in only:
        await scenario_c(False)
        await scenario_c(True)
    if not only or "D" in only:
        await scenario_d()
    if not only or "E" in only:
        await scenario_e("8/14", (8.0, 14.0))
        await scenario_e("14/14", (14.0, 14.0))
    if not only or "F" in only:
        await scenario_f()
    print("FAILED: " + "; ".join(FAILS) if FAILS else "OK slowledger")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
