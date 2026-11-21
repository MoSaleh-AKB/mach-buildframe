"""L8 specialist_care: a red flag at check-in or inside the report consent answer.

j12: hidden red flag in the check-in line -> 112 line, check-in, but NO follow-up prescription.
j13: red flag sent while the check-in turn waits for the consent answer -> 112 line before any
     report or prescription (it was coalesced away / sent last), no follow-up prescription.
coalesce: a red flag whose turn finds our later report line is still escalated.
normal: check-in + consent_ref -> shared report and the follow-up prescription as before.
hyperbole: a joke at check-in is not escalated and the follow-up stays.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_specialist_checkin_red_flag.py [brain.py]
Exit 1 on any FAIL.
"""
import asyncio
import importlib.util
import os
import sys
import time

os.environ["CITY_ROLE"] = "specialist_care"
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

from city_sdk.city import LEDGER, Message, Need, OutOfScope, Peer  # noqa: E402

SP, BC, GP = "agent:spfac000001", "agent:bcdesk000001", "agent:gpdoc0000001"
N = [0]
CASE = [0]
FAILS = []


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"r{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    def __init__(self, box, inbound):
        self.agent_id, self.box, self.inbound = SP, box, inbound
        self.address = "Via Prova 1"

    async def say(self, to, text, *, status=None):
        self.box.out.append((time.perf_counter(), to, text))
        self.box.rows.append(msg(SP, to, text, self.inbound.need_id))
        if to == LEDGER:
            self.box.spawn(self.box.ledger(text))
        return "x"

    async def find(self, query):
        return [Peer(agent_id=BC, role="booking_centre"), Peer(agent_id=GP, role="gp")]

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0.01)
        return list(self.box.rows)


class Box:
    """One case on a fake Ledger: every Ledger answer is pushed as its own turn, like live."""

    def __init__(self, priority="B"):
        CASE[0] += 1
        self.need, self.bk = f"N-rfci-{CASE[0]:03d}", f"bk-rfci-{CASE[0]:03d}"
        self.cz = f"citizen:{self.need}"
        self.rows, self.out, self.tasks, self.ops = [], [], [], []
        n, bk, cz = self.need, self.bk, self.cz
        cons = f"cons-{n}-booking"
        self.rows += [
            msg(BC, SP, f"confirm offer_id=of-S1 slot_id=S1 citizen={cz} consent_ref={cons} facility={SP} "
                        f"prescription_number=prescription_number-RX1 priority={priority}", n),
            msg(SP, LEDGER, f"hold slot_id=S1 citizen={cz} prescription_number=prescription_number-RX1 consent_ref={cons}", n),
            msg(LEDGER, SP, f"held hold_id={bk} slot_id=S1 need_id={n} fence=1", n),
            msg(SP, LEDGER, f"commit hold_id={bk} consent_ref={cons} fence=1", n),
            msg(LEDGER, SP, f"committed booking_id={bk} ledger_ref=lr-{bk} slot_id=S1", n),
            msg(SP, BC, f"booking.receipt booking_id={bk} ledger_ref=lr-{bk}\nOk", n),
        ]

    def spawn(self, coro):
        self.tasks.append(asyncio.get_running_loop().create_task(coro))

    async def ledger(self, text):
        f, head = brain.fields_of(text), brain.first_word(text)
        self.ops.append((head, text))
        await asyncio.sleep(0.2)
        if head == "checkin":
            ans = f"checked_in booking_id={f.get('booking_id')}"
        elif head == "report":
            ans = f"reported booking_id={f.get('booking_id')} report_ref=rp-{len(self.ops)}"
        elif head == "prescribe":
            ans = (f"prescribed prescription_number={f.get('prescription_number')} "
                   f"procedure={f.get('procedure')} priority={f.get('priority')}")
        else:
            return
        reply = msg(LEDGER, SP, ans, self.need)
        self.rows.append(reply)
        self.spawn(self.turn(reply))

    async def turn(self, inbound):
        if inbound not in self.rows:
            self.rows.append(inbound)
        try:
            await brain.main(Need(summary=inbound.text, message=inbound), Fake(self, inbound))
        except OutOfScope:
            pass

    async def settle(self, secs):
        await asyncio.sleep(secs)
        pend = [t for t in self.tasks if not t.done()]
        if pend:
            await asyncio.wait(pend, timeout=30)

    def said(self, word):
        return [t for _, to, t in self.out if to == self.cz and brain.first_word(t) == word]

    def ops_of(self, head):
        return [t for h, t in self.ops if h == head]

    def first_index(self, pred):
        return next((i for i, (_, to, t) in enumerate(self.out) if pred(to, t)), None)


def check(name, why, box):
    print(f"--- {'PASS' if not why else 'FAIL'} {name}{' :: ' + '; '.join(why) if why else ''}")
    if why:
        FAILS.append(name)
        for _, to, t in box.out:
            print(f"    > {to[:20]:20} : {t.replace(chr(10), ' | ')[:160]}")


async def j12_red_flag_hidden_checkin():
    box = Box()
    await box.turn(msg(box.cz, SP, f"checkin booking_id={box.bk} eccomi. Da mezz'ora ho un peso sul petto che sale "
                                   "al braccio sinistro e sudo freddo, ma sarà l'ansia.", box.need))
    await box.settle(4.5)
    why = []
    if not box.said("escalate.emergency"):
        why.append("no escalate")
    if box.ops_of("prescribe") or box.said("prescription"):
        why.append("follow-up prescription after a red flag")
    check("j12_red_flag_hidden_checkin", why, box)


async def j13_red_flag_in_consent_reply():
    box = Box()
    t = asyncio.create_task(box.turn(msg(box.cz, SP, f"checkin booking_id={box.bk}", box.need)))
    await asyncio.sleep(0.8)
    await box.turn(msg(box.cz, SP, "Sì condividete pure. Però adesso non sento più il braccio destro e parlo male, "
                                   "mi gira tutto.", box.need))
    await t
    await box.settle(1.5)
    why = []
    esc = box.first_index(lambda to, t: to == box.cz and brain.first_word(t) == "escalate.emergency")
    if esc is None:
        why.append("no escalate")
    else:
        late = box.first_index(lambda to, t: to == LEDGER and brain.first_word(t) in ("report", "prescribe"))
        if late is not None and late < esc:
            why.append("112 line only after the report / prescribe")
    if box.ops_of("prescribe") or box.said("prescription"):
        why.append("follow-up prescription after a red flag")
    if len(box.said("escalate.emergency")) > 1:
        why.append("112 line twice")
    if any("consent_ref" in r for r in box.ops_of("report")):
        why.append("report shared without a consent_ref")
    check("j13_red_flag_in_consent_reply", why, box)


async def coalesce_red_flag_after_report():
    """The red-flag turn got the need lock after our report line: still the 112 line."""
    box = Box()
    n = box.need
    red = msg(box.cz, SP, "Adesso non sento più il braccio destro e parlo male, mi gira tutto.", n)
    box.rows += [
        msg(box.cz, SP, f"checkin booking_id={box.bk}", n),
        msg(SP, LEDGER, f"checkin booking_id={box.bk} citizen={box.cz}", n),
        msg(LEDGER, SP, f"checked_in booking_id={box.bk}", n),
        msg(SP, box.cz, f"checkin booking_id={box.bk}\nIl suo check-in è registrato.", n),
        msg(SP, box.cz, f"consent purpose=report_sharing booking_id={box.bk}\nPosso condividere il referto?", n),
        red,
        msg(SP, LEDGER, f"report booking_id={box.bk} outcome_code=VISIT", n),
        msg(SP, box.cz, f"report booking_id={box.bk} outcome_code=VISIT\nLa visita è registrata.", n),
    ]
    await box.turn(red)
    await box.settle(0.3)
    why = [] if box.said("escalate.emergency") else ["red flag coalesced away (no 112 line)"]
    await box.turn(red)  # the same turn again: no second 112 line
    await box.settle(0.3)
    if len(box.said("escalate.emergency")) > 1:
        why.append("112 line twice for one message")
    check("coalesce_red_flag_after_report", why, box)


async def normal_checkin_consent_shares():
    box = Box()
    t = asyncio.create_task(box.turn(msg(box.cz, SP, f"checkin booking_id={box.bk}", box.need)))
    await asyncio.sleep(0.8)
    await box.turn(msg(box.cz, SP, f"consent consent_ref=cr-{box.need} purpose=report_sharing", box.need))
    await t
    await box.settle(1.5)
    why = []
    rep = box.ops_of("report")
    if not rep or f"consent_ref=cr-{box.need}" not in rep[0]:
        why.append(f"report not shared: {rep}")
    if len(box.ops_of("prescribe")) != 1:
        why.append(f"follow-up prescribe count {len(box.ops_of('prescribe'))} (want 1 for priority B)")
    if box.said("escalate.emergency"):
        why.append("escalated a normal check-in")
    check("normal_checkin_consent_shares", why, box)


async def hyperbole_checkin_not_escalated():
    box = Box()
    t = asyncio.create_task(box.turn(msg(box.cz, SP, f"checkin booking_id={box.bk} eccomi finalmente, ci ho messo "
                                                     "una vita, un traffico da morire", box.need)))
    await asyncio.sleep(0.8)
    await box.turn(msg(box.cz, SP, f"consent consent_ref=cr-{box.need} purpose=report_sharing", box.need))
    await t
    await box.settle(1.5)
    why = []
    if box.said("escalate.emergency"):
        why.append("hyperbole escalated")
    if len(box.ops_of("prescribe")) != 1:
        why.append(f"follow-up prescribe count {len(box.ops_of('prescribe'))} (want 1)")
    if not box.ops_of("report"):
        why.append("no report")
    check("hyperbole_checkin_not_escalated", why, box)


async def run():
    for sc in (j12_red_flag_hidden_checkin, j13_red_flag_in_consent_reply, coalesce_red_flag_after_report,
               normal_checkin_consent_shares, hyperbole_checkin_not_escalated):
        await sc()
    if FAILS:
        print("FAIL test_specialist_checkin_red_flag:", " ".join(FAILS))
        sys.exit(1)
    print("OK test_specialist_checkin_red_flag")


asyncio.run(run())
