"""Booking centre, P2 strict cross-ASST consent (S09/S18, -2 audit each) and confirm of a
withdrawn partner offer (-2). Only the citizen's own cross_asst consent, given after our
data.request purpose=cross_asst, lets the citizen id go to a partner of another ASST:
never a booking-purpose ref, a word ref, a withdrawn ref, another citizen's ref, or a ref
scoped to another partner. A real cross_asst ref inside the confirm counts (exp2 q2), and a
consent sent before or after a bare confirm never loses the booking. Every refusal is visible.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_desk_xa_consent.py
(BRAIN=<path> tests another brain file). Exit 1 on any FAIL.
"""

import asyncio
import datetime
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, CapError, Message, Need, Peer

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("BRAIN") or os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", PATH)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
for k, v in (("_ADVANCE_BUDGET", 0.4), ("_PAGE_BUDGET", 0.2), ("_CONSENT_WAIT", 0.3),
             ("_XA_BUDGET", 1.5), ("_XA_GRACE", 0.3)):
    if hasattr(brain, k):
        setattr(brain, k, v)

BC = "agent:t07/booking_centre"
HO = "agent:t07/hospital"
CZ = "citizen:t07/c1"
P1 = "agent:t05/hospital"
P3 = "agent:t09/hospital"
NOW = "2026-11-23T08:00:00+00:00"
TAX = "RSSMRA80A01F205X"
RX = "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"
N = [0]
NEED = [700]


def new_need():
    NEED[0] += 1
    return f"N-{NEED[0]:04d}"


_T0 = datetime.datetime.now(datetime.timezone.utc)


def msg(sender, to, text, need, kind="chat", **body):
    N[0] += 1
    # Rows are stamped "now" (the agenda clock runs 60x real time from a row's sent_at, so a
    # hard-coded date drifts the slots out of the window as the real clock moves on).
    ts = (_T0 + datetime.timedelta(microseconds=N[0])).isoformat()
    return Message(type=kind, sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text, **body},
                   recipient=to, sent_at=ts)


def slot(i, at, day):
    return f"slot_id=S{i} doctor_id=D-h{i} at={at} day={day} facility={HO} procedure=VIS-FIS free=1"


WORLD3 = "slots page=1/1\n" + "\n".join([
    slot(1, "2026-11-23T14:00:00+00:00", "mon"),
    slot(2, "2026-11-24T09:00:00+00:00", "tue"),
    slot(3, "2026-11-26T10:00:00+00:00", "thu"),
])


class City:
    def __init__(self, first, rows, slots, peers=None, bots=None):
        self.agent_id = BC
        self.inbound = first
        # Each case is a new citizen journey: the same CZ asking the same visit again would be a
        # real return (waiting list given before -> "not possible"), and its partner slot would be
        # given already (one partner slot, one citizen): each case is its own world.
        for name in ("_ONCE", "_CITIZEN_SENT", "_CITIZEN_RATE", "_WL_GIVEN", "_XA_GIVEN"):
            getattr(brain, name, {}).clear()
        self.rows = list(rows)
        self.out = []
        self.slots = slots
        self.peers = peers
        self.bots = bots or {}
        self.address = ""

    def _ledger(self, text):
        head = text.split()[0] if text.split() else ""
        f = brain.fields_of(text)
        if head == "prescription":
            return [RX] if f.get("prescription_number") == "RX1" else []
        if head == "agenda":
            return [f"agenda agenda_now={NOW}"]
        if head == "world":
            return [self.slots]
        if head == "hold":
            return [f"held hold_id=h-{f.get('slot_id')} slot_id={f.get('slot_id')} fence=1"]
        if head == "commit":
            return [f"committed booking_id={f.get('hold_id')} ledger_ref=c-1 hold_id={f.get('hold_id')}"]
        return []

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(BC, to, text, self.inbound.need_id))
        if to == LEDGER:
            for ans in self._ledger(text):
                self.rows.append(msg(LEDGER, BC, ans, self.inbound.need_id))
        bot = self.bots.get(to)
        if bot is not None:
            asyncio.create_task(bot(self, text))
        return "x"

    async def find(self, query):
        if self.peers is None:
            raise CapError("UPSTREAM_TIMEOUT", "index")
        return list(self.peers)

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    def to(self, who):
        return [t for w, t in self.out if w == who]


async def drive(c, via_main=True):
    os.environ["CITY_ROLE"] = "booking_centre"
    try:
        if via_main:
            try:
                await brain.main(Need(summary=c.inbound.text, message=c.inbound), c)
            except Exception as exc:
                if type(exc).__name__ != "OutOfScope":
                    raise
        else:
            await brain.handle("booking_centre", c, c.inbound, list(c.rows))
    finally:
        os.environ.pop("CITY_ROLE", None)
    await asyncio.sleep(0.05)
    return c


async def turn(c, sender, text, kind="chat", **body):
    m = msg(sender, BC, text, c.inbound.need_id, kind, **body)
    c.rows.append(m)
    c.inbound = m
    c.out.clear()
    return await drive(c)


def start_rows(need, world):
    return [
        msg(CZ, BC, f"book please prescription_number=RX1 lang=it tax_code={TAX}", need),
        msg(BC, LEDGER, "prescription prescription_number=RX1", need),
        msg(BC, LEDGER, "agenda", need),
        msg(BC, LEDGER, "world procedure=VIS-FIS page=1", need),
        msg(LEDGER, BC, RX, need),
        msg(LEDGER, BC, f"agenda agenda_now={NOW}", need),
        msg(LEDGER, BC, world, need),
    ]


async def xa_case(two=False):
    """Own world empty, partners of other ASSTs answer: partner offers + consent ask sent."""

    need = new_need()
    rows = start_rows(need, "slots page=1/1")

    def partner(pid, sid, at, day):
        async def bot(c, text):
            if text.startswith("need.request"):
                rid = brain.fields_of(text.splitlines()[0]).get("request_id", "")
                c.rows.append(msg(pid, BC, (
                    f"Ecco la disponibilita.\noffer offer_id=of-{sid} slot_id={sid} doctor_id=D-{sid} "
                    f"facility={pid} procedure=VIS-FIS agenda_date={at} agenda_day={day} "
                    f"expiry={at} priority=B request_id={rid}"), need))
        return bot

    peers = [Peer(agent_id=P1, role="hospital", capabilities=("hospital.physiatry",))]
    bots = {P1: partner(P1, "P1a", "2026-11-24T09:00:00+00:00", "tue")}
    if two:
        peers.append(Peer(agent_id=P3, role="hospital", capabilities=("hospital.physiatry",)))
        bots[P3] = partner(P3, "P3a", "2026-11-24T11:00:00+00:00", "tue")
    c = City(rows[-1], rows, "slots page=1/1", peers=peers, bots=bots)
    await drive(c, via_main=False)
    cz = "\n".join(c.to(CZ))
    assert "cross_asst=true" in cz and "purpose=cross_asst" in cz, ("setup: no partner offer/consent ask", c.out)
    assert "of-P1a" in cz and (not two or "of-P3a" in cz), ("setup: partner offers missing", c.out)
    return c


def said(c):
    return "\n".join(c.to(CZ))


def to_partner(c, who=P1):
    return [t for t in c.to(who) if t.startswith("confirm")]


def visible(c):
    text = said(c).strip()
    assert text, ("silence to the citizen", c.out)
    return text


def leaks(texts):
    return [t[:100] for t in texts if TAX in t]


def first_offer_slot(text):
    for line in text.splitlines():
        f = brain.fields_of(line)
        if f.get("offer_id") and f.get("slot_id"):
            return f["slot_id"]
    return ""


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
async def xa01_booking_ref_never_releases():
    c = await xa_case()
    await turn(c, CZ, f"confirm offer_id=of-P1a consent_ref=cons-{c.inbound.need_id}-booking")
    assert not to_partner(c), ("citizen data to partner on a booking consent", c.out)
    assert "purpose=cross_asst" in visible(c), ("cross consent not asked again", c.out)


@test
async def xa02_word_and_junk_refs_never_release():
    bad = []
    for ref in ("acconsento", "granted", "cross_asst", "consenso", "agreed", "accetto", "none", "ok"):
        c = await xa_case()
        await turn(c, CZ, f"confirm offer_id=of-P1a consent_ref={ref}")
        if to_partner(c):
            bad.append(ref)
        visible(c)
    assert not bad, ("forwarded to partner on", bad)


@test
async def xa03_booking_purpose_consent_act_never_counts():
    c = await xa_case()
    await turn(c, CZ, "consent purpose=booking consent_ref=cons-bk-1")
    await turn(c, CZ, "consent consent_ref=cons-bk-1")
    await turn(c, CZ, "confirm offer_id=of-P1a consent_ref=cons-bk-1")
    assert not to_partner(c), ("booking-purpose ref used as cross consent", c.out)
    visible(c)


@test
async def xa04_withdrawn_consent_never_counts():
    c = await xa_case()
    ref = f"cons-{c.inbound.need_id}-cross_asst"
    await turn(c, CZ, f"consent purpose=cross_asst consent_ref={ref}")
    await turn(c, CZ, "Ci ho ripensato: revoco il consenso, non voglio condividere i dati.")
    visible(c)
    await turn(c, CZ, f"confirm offer_id=of-P1a consent_ref={ref}")
    assert not to_partner(c), ("data after withdraw", c.out)
    visible(c)


@test
async def xa05_other_citizens_consent_never_counts():
    c = await xa_case()
    await turn(c, CZ, "consent purpose=cross_asst consent_ref=cons-N-9999-cross_asst citizen=citizen:t07/c9")
    await turn(c, CZ, "confirm offer_id=of-P1a consent_ref=cons-N-9999-cross_asst")
    assert not to_partner(c), ("another citizen's consent used", c.out)
    visible(c)


@test
async def xa06_consent_first_then_bare_confirm_books():
    c = await xa_case()
    ref = f"cons-{c.inbound.need_id}-cross_asst"
    await turn(c, CZ, f"consent purpose=cross_asst consent_ref={ref}")
    await turn(c, CZ, "confirm offer_id=of-P1a")
    fwd = to_partner(c)
    assert fwd and f"consent_ref={ref}" in fwd[0] and "slot_id=P1a" in fwd[0], ("booking lost", c.out)
    assert not leaks(fwd), leaks(fwd)


@test
async def xa07_bare_confirm_then_consent_books_once():
    c = await xa_case()
    await turn(c, CZ, "confirm offer_id=of-P1a")
    assert not to_partner(c), ("data before consent", c.out)
    visible(c)
    ref = f"cons-{c.inbound.need_id}-cross_asst"
    await turn(c, CZ, f"consent purpose=cross_asst consent_ref={ref}")
    fwd = to_partner(c)
    assert len(fwd) == 1 and f"consent_ref={ref}" in fwd[0], ("late consent not forwarded once", c.out)


@test
async def xa08_cross_ref_inside_confirm_counts():
    """exp2 q2: after our ask, a confirm carrying a real cross_asst ref is the citizen's answer."""
    c = await xa_case()
    ref = f"cons-{c.inbound.need_id}-cross_asst"
    await turn(c, CZ, f"confirm offer_id=of-P1a consent_ref={ref}")
    fwd = to_partner(c)
    assert fwd and f"consent_ref={ref}" in fwd[0] and f"citizen={CZ}" in fwd[0], ("cross ref in confirm ignored", c.out)
    assert not leaks(fwd), leaks(fwd)
    c = await xa_case()
    await turn(c, CZ, "confirm offer_id=of-P1a consent_ref=cons-123")
    assert not to_partner(c), ("an unmarked ref in a confirm counted as cross consent", c.out)
    c = await xa_case()
    await turn(c, CZ, f"confirm offer_id=of-P1a consent_ref={ref} ma revoco il consenso")
    assert not to_partner(c), ("withdraw inside the confirm ignored", c.out)


@test
async def xa09_consent_scoped_to_other_partner():
    """A consent naming facility=P1 does not cover partner P3 (pdesk E2)."""
    c = await xa_case(two=True)
    await turn(c, CZ, f"consent purpose=cross_asst facility={P1} consent_ref=cons-x-p1")
    await turn(c, CZ, "confirm offer_id=of-P3a")
    assert not to_partner(c, P3), ("P1 consent used for P3", c.out)
    assert "purpose=cross_asst" in visible(c), ("no new consent asked for P3", c.out)
    await turn(c, CZ, "confirm offer_id=of-P1a")
    assert to_partner(c, P1), ("P1 consent did not book at P1", c.out)


@test
async def xa10_reask_twice_never_silent():
    """The duplicate guard must not swallow the second re-ask (180 s repeat window)."""
    c = await xa_case()
    await turn(c, CZ, "confirm offer_id=of-P1a consent_ref=cons-N-booking")
    first = visible(c)
    await turn(c, CZ, "confirm offer_id=of-P1a consent_ref=cons-N-booking")
    second = visible(c)
    assert "purpose=cross_asst" in first and "purpose=cross_asst" in second, (first, second)
    assert not to_partner(c), c.out


@test
async def xa11_confirm_of_withdrawn_partner_offer():
    """The partner withdrew its offer and we relayed the cancel: a later confirm is refused."""
    c = await xa_case(two=True)
    ref = f"cons-{c.inbound.need_id}-cross_asst"
    await turn(c, CZ, f"consent purpose=cross_asst consent_ref={ref}")
    await turn(c, P1, "cancel offer_id=of-P1a reason=slot_taken")
    assert any(t.startswith("cancel offer_id=of-P1a") for t in c.to(CZ)), ("setup: cancel not relayed", c.out)
    await turn(c, CZ, f"confirm offer_id=of-P1a consent_ref={ref}")
    assert not to_partner(c), ("citizen id sent for a withdrawn offer", c.out)
    text = visible(c)
    assert "of-P1a" in text and ("non è più valida" in text or "no longer valid" in text), ("wording", text)
    assert "of-P3a" in text, ("the offer still open is not listed", text)


@test
async def xa12_withdraw_other_language_typo_mixed():
    """The withdrawal rule in other languages, with a typo, and mixed with a normal question."""
    bad = []
    for text in (
        "I withdraw my consent, please do not share my data.",
        "Sorry, I no longer consent to sharing.",
        "Retiro mi consentimiento, no compartan mis datos.",
        "Je retire mon consentement, ne partagez pas mes données.",
        "أسحب موافقتي على مشاركة بياناتي",
        "ritiro il consenzo, non condivido",
        "ho cambbiato idea, revoko il consenso",
        "Revoco il consenso. A che ora apre la struttura?",
        "I changed my mind about sharing; also, where is the hospital?",
    ):
        c = await xa_case()
        ref = f"cons-{c.inbound.need_id}-cross_asst"
        await turn(c, CZ, f"consent purpose=cross_asst consent_ref={ref}")
        await turn(c, CZ, text)
        quiet = not said(c).strip()
        await turn(c, CZ, f"confirm offer_id=of-P1a consent_ref={ref}")
        if to_partner(c) or quiet or not said(c).strip():
            bad.append((text, "sent" if to_partner(c) else "silence"))
    assert not bad, ("data to partner after a withdrawal, or silence", bad)


@test
async def xa13_consent_other_language_typo_mixed():
    """A real cross_asst consent act still books when the words around it vary."""
    bad = []
    for extra in (
        "Yes, I agree to share my data with the other ASST.",
        "Sí, doy mi consentimiento.",
        "consnet",  # typo in the act word: purpose=cross_asst still marks it
        "Va bene. A che ora devo arrivare? Posso portare mia figlia?",
    ):
        c = await xa_case()
        ref = f"cons-{c.inbound.need_id}-cross_asst"
        head = "consent" if extra != "consnet" else "consnet"
        body = "" if extra == "consnet" else f"\n{extra}"
        await turn(c, CZ, f"{head} purpose=cross_asst consent_ref={ref}{body}")
        await turn(c, CZ, "confirm offer_id=of-P1a")
        fwd = to_partner(c)
        if not (fwd and f"consent_ref={ref}" in fwd[0]) or leaks(fwd):
            bad.append(extra)
    assert not bad, ("real cross consent lost", bad)


@test
async def xa14_purpose_typo_or_mixed_booking_never_releases():
    """A misspelt purpose, or a booking consent mixed into a question, is never the cross consent."""
    bad = []
    for text in (
        "consent purpose=cross_ast consent_ref=cons-77",
        "consent purpose=booking consent_ref=cons-77 e mi dice dove si trova la struttura?",
        "I consent to the booking only consent_ref=cons-booking-77, where do I park?",
    ):
        c = await xa_case()
        await turn(c, CZ, text)
        await turn(c, CZ, "confirm offer_id=of-P1a consent_ref=cons-77")
        if to_partner(c) or not said(c).strip():
            bad.append((text, "sent" if to_partner(c) else "silence"))
    assert not bad, ("data to partner without a cross_asst consent", bad)


@test
async def xa15_withdraw_after_forward_cancels_partner():
    """Consent, confirm forwarded to the partner, then the consent is withdrawn: the partner gets
    cancel reason=consent_withdrawn, the citizen one line, and a late receipt is no booking."""
    bad = []
    for text in (
        "consent purpose=cross_asst consent_ref={ref} state=withdrawn\nRitiro il consenso: non voglio più che i miei dati vadano all'altra ASST.",
        "Ho cambiato idea, non condividete i miei dati con l'altra ASST.",
        "I changed my mind, I withdraw my consent. Also, what time do you open?",
    ):
        c = await xa_case()
        ref = f"cons-{c.inbound.need_id}-cross_asst"
        await turn(c, CZ, f"consent purpose=cross_asst consent_ref={ref}")
        await turn(c, CZ, "confirm offer_id=of-P1a")
        if not to_partner(c) or not said(c).strip():
            bad.append((text[:30], "setup: no forward or silent confirm"))
            continue
        await turn(c, CZ, text.format(ref=ref))
        cancel = [t for t in c.to(P1) if t.startswith("cancel offer_id=of-P1a") and "reason=consent_withdrawn" in t]
        if not cancel or not said(c).strip() or leaks(c.to(P1)):
            bad.append((text[:30], "no partner cancel" if not cancel else "silence or leak"))
            continue
        await turn(c, P1, "booking.receipt booking_id=pbk-1 ledger_ref=plr-1 offer_id=of-P1a slot_id=P1a")
        if any("pbk-1" in t or "confermata" in t for t in c.to(CZ)):
            bad.append((text[:30], "late receipt relayed as a booking"))
    assert not bad, bad


async def run():
    failed = 0
    for fn in TESTS:
        try:
            await fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}\n      {str(exc)[:500]}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {str(exc)[:400]}")
    print(f"RESULT {'FAIL' if failed else 'PASS'} {failed}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(run()) else 0)
