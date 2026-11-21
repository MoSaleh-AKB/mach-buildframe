"""release-2: j91 replay (English citizen, 3 simultaneous cancels), anti-spam, language, safety nets.
Run: <city python> -I tests/test_release2.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import os
import sys

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

BC, HO, CZ = "agent:t/booking_centre", "agent:t/hospital", "citizen:t/c1"
N = [0]
NEED = "n-j91"


def row(sender, to, text, need=NEED):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    agent_id = BC
    address = ""

    def __init__(self, rows):
        self.rows, self.out, self.inbound = list(rows), [], rows[0]

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(row(self.agent_id, to, text))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0)
        return list(self.rows)

    async def find(self, query):
        return []


def slot(i, day, d, hh, doc):
    return (f"slot_id=S{i} doctor_id={doc} at=2026-11-{d}T{hh}:00+00:00 day={day} "
            f"facility={HO} procedure=VIS-FIS free=1")


WORLD = "slots page=1/1\n" + "\n".join([
    slot(1, "mon", 23, "09:00", "D-h1"), slot(2, "tue", 24, "09:00", "D-h2"),
    slot(3, "wed", 25, "10:00", "D-h1"), slot(4, "thu", 26, "09:00", "D-h2"),
])
FIRST = ("Good morning, my dears! My back is quite a bother. Might I humbly request a physiatry "
         "visit, preferably with a kind doctor who speaks English? prescription_number=RX1")


def cancel(offer):
    return (f"Dear Doctor, thank you kindly, but I would prefer a doctor who speaks English. "
            f"cancel offer_id={offer} reason=deadline")


async def journey():
    rows = [row(CZ, BC, FIRST),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued"),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00"), row(LEDGER, BC, WORLD)]
    fake = Fake(rows)
    await brain.handle("booking_centre", fake, rows[0], rows)
    offers = [t for to, t in fake.out if to == CZ and "offer_id=" in t]
    assert offers, fake.out
    first_offer = offers[0]
    # The preferred language is honoured: an English-speaking doctor (h1) comes first, and said.
    assert "speaks English" in first_offer or "In English" in first_offer, first_offer
    ids = [tok.split("=")[1] for tok in first_offer.split() if tok.startswith("offer_id=")]
    before = len(fake.out)
    # Three near-simultaneous cancels, then a wall of follow-ups (j91 got 100+ replays).
    msgs = [row(CZ, BC, cancel(ids[0])), row(CZ, BC, cancel(ids[0])), row(CZ, BC, cancel(ids[-1]))]
    fake.rows.extend(msgs)
    await asyncio.gather(*[brain._main_locked(fake, m, m.text) for m in msgs])
    for k in range(12):
        m = row(CZ, BC, f"Dear doctor, still waiting, my back hurts, I prefer English {k}")
        fake.rows.append(m)
        fake.inbound = m
        await brain.handle("booking_centre", fake, m, list(fake.rows))
        await brain.handle("booking_centre", fake, m, list(fake.rows))
    new = [t for to, t in fake.out[before:] if to == CZ]
    if "-v" in sys.argv:
        print(fake.out)
    assert not any("Le ripropongo" in t for t in new), new
    assert len(new) == len(set(t.strip() for t in new)), "identical text twice: %s" % new
    assert len(new) <= 4, (len(new), new)
    # nothing sent after the cancel repeats an offer_id we already withdrew
    for t in new:
        assert not any(f"offer_id={i}" in t and t.startswith("offer ") for i in ids[:1]), t
    ask = [t for t in new if "speaks" in t or "Tell me what would suit" in t]
    assert ask and "English" in ask[0], new  # asked what suits, in the citizen's language


asyncio.run(journey())


async def guard():
    rows = [row(CZ, BC, "ciao")]
    fake = Fake(rows)
    assert await brain._say(fake, CZ, "uno due tre")
    assert not await brain._say(fake, CZ, "uno due tre")  # dropped repeat: reported as not sent
    assert await brain._say(fake, CZ, "offer offer_id=of-1 x")
    assert not await brain._say(fake, CZ, "offer offer_id=of-1 y")
    assert [t for _, t in fake.out] == ["uno due tre", "offer offer_id=of-1 x"], fake.out
    # a ledger write is never suppressed
    assert await brain._say(fake, LEDGER, "agenda") and await brain._say(fake, LEDGER, "agenda")
    assert len([1 for to, _ in fake.out if to == LEDGER]) == 2


asyncio.run(guard())

assert brain._pref_lang([row(CZ, BC, "a doctor who speaks English please")]) == "en"
assert brain._pref_lang([row(CZ, BC, "un medico che parla inglese")]) == "en"
assert brain._pref_lang([row(CZ, BC, "ciao, mal di schiena")]) == ""
assert brain._say_lang([row(CZ, BC, "Good morning, may I ask for a visit, thank you kindly")]) in {"en", "it"}


# --- collaboration, tips, safety net, closing -------------------------------------------------
async def receipt_flow():
    need = "n-rcpt"
    rows = [row(CZ, BC, "visita prescription_number=RX1 lang=it ho una sedia a rotelle", need),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-CARD priority=B state=issued", need),
            row(BC, CZ, "offer offer_id=of-S1 slot_id=S1 doctor_id=D-h1 agenda_date=2026-11-23T09:00:00+00:00", need),
            row(CZ, BC, "confirm offer_id=of-S1 consent_ref=c1", need),
            row(BC, HO, "confirm slot_id=S1 consent_ref=c1 citizen=" + CZ, need)]
    fake = Fake(rows)
    fake.inbound = rows[0]
    rcp = row(HO, BC, "booking.receipt booking_id=B1 ledger_ref=L1", need)
    fake.rows.append(rcp)
    await brain.handle("booking_centre", fake, rcp, list(fake.rows))
    to_fac = [t for to, t in fake.out if to == HO]
    to_cit = [t for to, t in fake.out if to == CZ]
    assert to_fac and to_fac[0].startswith("prep.note booking_id=B1"), fake.out
    assert "visit=VIS-CARD" in to_fac[0] and "access=step-free" in to_fac[0], to_fac
    assert "procedure=" not in to_fac[0] and "09:00" in to_fac[0]
    assert any(t.startswith("booking.receipt") for t in to_cit)
    tips = [t for t in to_cit if t.startswith("tips ")]
    assert tips and "elenco dei farmaci" in tips[0] and "15 minuti" in tips[0], to_cit
    assert tips[0].count("\n") == 1 and "-" not in tips[0]
    # no registry value backs a step-free entrance: say we told the facility, never that it has one
    assert "segnalato alla struttura" in tips[0] and "è senza gradini" not in tips[0], tips
    # the facility answers an agent message at once, with an instruction
    fac = Fake([row(BC, HO, to_fac[0], need)])
    fac.agent_id = HO
    fac.inbound = fac.rows[0]
    await brain.handle("hospital", fac, fac.rows[0], list(fac.rows))
    ack = [t for to, t in fac.out if to == BC]
    assert ack and ack[0].startswith("prep.ack booking_id=B1") and "arrivi 15 minuti" in ack[0], fac.out
    assert "ingresso più comodo" in ack[0] and "l'ingresso senza gradini" not in ack[0], ack
    # the desk passes it on once
    a = row(HO, BC, ack[0], need)
    fake.rows.append(a)
    await brain.handle("booking_centre", fake, a, list(fake.rows))
    await brain.handle("booking_centre", fake, a, list(fake.rows))
    assert len([t for to, t in fake.out if to == CZ and t.startswith("facility.prep")]) == 1
    # thanks closes warmly; a reminder is offered on request
    th = row(CZ, BC, "Grazie mille!", need)
    fake.rows.append(th)
    await brain.handle("booking_centre", fake, th, list(fake.rows))
    assert "piacere aiutarla" in fake.out[-1][1], fake.out[-1]
    rm = row(CZ, BC, "promemoria per favore", need)
    fake.rows.append(rm)
    await brain.handle("booking_centre", fake, rm, list(fake.rows))
    assert fake.out[-1][1].startswith("reminder booking_id=B1") and "2026-11-23" in fake.out[-1][1], fake.out[-1]


asyncio.run(receipt_flow())

assert "112" in brain._rx_text({"prescription_number": "R"}) and "116117" in brain._rx_text({"prescription_number": "R"})
assert "112" in brain._rx_text({"prescription_number": "R"}, en=True)
assert brain._wants_follow_up([row(CZ, BC, "ho palpitazioni")])
for lang in ("it", "en", "fr", "es"):  # the access line claims nothing about the building
    line = brain._tip_line(lang, "VIS-FIS", True, False)
    assert not any(w in line for w in ("è senza gradini", "is available", "est possible", "Hay acceso")), line
_role = os.environ.get("CITY_ROLE")
os.environ["CITY_ROLE"] = "hospital"
for q in ("La struttura è accessibile in sedia a rotelle?", "Is the building wheelchair accessible?"):
    ans = brain._service_answer(q, agent=False)
    ans = brain._EN_TEXT.get(ans, ans) if q.startswith("Is") else ans
    assert ans and "topic=access" in ans and "accessibility=" not in ans, ans
    assert "accessibile in sedia" not in ans and "wheelchair accessible" not in ans, ans
if _role is None:
    os.environ.pop("CITY_ROLE")
else:
    os.environ["CITY_ROLE"] = _role
assert "ombrello" in brain._tip_line("it", "VIS-FIS", False, True) and "abiti comodi" in brain._tip_line("it", "VIS-FIS", False, False)

async def share_gp():
    need = "n-rep"
    GPA = "agent:t/gp"
    rows = [row(GPA, CZ, "prescription prescription_number=RX1 procedure=VIS-CARD priority=B", need),
            row(CZ, HO, "checkin booking_id=B1", need),
            row(HO, CZ, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?", need),
            row(CZ, HO, "consent_ref=cons-9 ok", need),
            row(HO, LEDGER, "report booking_id=B1 outcome_code=VISIT consent_ref=cons-9", need)]
    fake = Fake(rows)
    fake.agent_id = HO
    rep = row(LEDGER, HO, "reported booking_id=B1", need)
    fake.rows.append(rep)
    await brain._on_reported(fake, rep, list(fake.rows))
    sent = [t for to, t in fake.out if to == GPA]
    assert sent and "consent_ref=cons-9" in sent[0] and sent[0].startswith("report.summary"), fake.out
    await brain._on_reported(fake, rep, list(fake.rows))
    assert len([1 for to, _ in fake.out if to == GPA]) == 1, fake.out
    # no consent, nothing shared
    fake2 = Fake(rows[:3] + [row(CZ, HO, "no grazie", need), rows[4]])
    fake2.agent_id = HO
    await brain._on_reported(fake2, rep, list(fake2.rows))
    assert not [1 for to, _ in fake2.out if to == GPA], fake2.out


asyncio.run(share_gp())

async def commit_visible():
    need = "n-com"
    rows = [row(BC, HO, "confirm offer_id=of-S1 slot_id=S1 consent_ref=c1 citizen=" + CZ, need),
            row(LEDGER, HO, "slots page=1/1\nslot_id=S1 doctor_id=D-h1 at=2026-11-23T09:00:00+00:00 day=mon", need)]
    fake = Fake(rows)
    fake.agent_id = HO
    com = row(LEDGER, HO, "committed booking_id=B7 ledger_ref=L7 slot_id=S1", need)
    fake.rows.append(com)
    await brain.handle("hospital", fake, com, list(fake.rows))
    to_cit = [t for to, t in fake.out if to == CZ]
    assert to_cit and "2026-11-23 alle 09:00" in to_cit[0] and "15 minuti" in to_cit[0], fake.out
    assert any(to == BC and t.startswith("booking.receipt") for to, t in fake.out)
    assert any(to == BC and t.startswith("booking.ack") for to, t in fake.out)


asyncio.run(commit_visible())


async def wl_cancel():
    need = "n-wl"
    rows = [row(CZ, BC, "visita prescription_number=RX1", need),
            row(BC, CZ, "offer offer_id=wl-n-wl-alt waiting_list=true", need)]
    fake = Fake(rows)
    for k in range(3):
        m = row(CZ, BC, f"no thanks {k} cancel offer_id=wl-n-wl-alt reason=waiting_list", need)
        fake.rows.append(m)
        await brain.handle("booking_centre", fake, m, list(fake.rows))
    out = [t for to, t in fake.out if to == CZ]
    # Refusals of one need that arrive together get ONE reply (L6: 3-4 cancels in a second).
    assert len(out) == 1 and "lista d'attesa" in out[0] and "offer_id" not in out[0], out
    assert "Messaggio" not in out[0], out


asyncio.run(wl_cancel())

async def english_offer(first, need):
    rows = [row(CZ, BC, first, need),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued", need),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00", need), row(LEDGER, BC, WORLD, need)]
    fake = Fake(rows)
    await brain.handle("booking_centre", fake, rows[0], rows)
    text = [t for to, t in fake.out if to == CZ and "offer_id=" in t][0]
    head = text.splitlines()[0]
    assert head.startswith("Good morning, I found"), head
    assert "Buongiorno" not in text and "prenoto" not in text, text
    assert "Dr h1 speaks English" in head, head
    assert "I checked the availability" in head


import json
asyncio.run(english_offer("Hello, I have back pain. I need an English-speaking doctor prescription_number=RX1", "n-j100"))
asyncio.run(english_offer("need.request lang=en prescription_number=RX1 I need a doctor who speaks English", "n-j100b"))
try:
    d91 = json.load(open("/tmp/j91.json"))["messages"]
    first91 = d91[2]["body"]["text"]
    asyncio.run(english_offer(first91 + " prescription_number=RX1", "n-j91b"))
except FileNotFoundError:
    pass

def m(text):
    return row(CZ, BC, text)


for text, code in [("Hola, hablo poco italiano, tengo dolor de espalda", "es"), ("Ciao, parlo arabo, ho mal di schiena", "ar"),
                   ("parlo persiano e ho male alla schiena", "fa"), ("Hallo, ich habe Rückenschmerzen, bitte einen Arzt", "de"),
                   ("Ho mal di schiena da una settimana", "it"), ("Bonjour, j'ai mal au dos depuis une semaine, un médecin", "fr"),
                   ("Bună ziua, am nevoie de un medic, am durere de spate", "ro"), ("Mirëdita, kam dhimbje shpine, dua një mjek", "sq"),
                   ("У мене болить спина", "uk"), ("我背痛", "zh"), ("I have back pain, please help", "en")]:
    assert brain._lang_of(m(text)) == code, (text, brain._lang_of(m(text)))

# SO-52: the rare language is sw, never "any"; the slot's languages field beats our table
assert brain._RARE == "sw"
assert brain._speaks("D-h3", "sw") and not brain._speaks("D-h3", "ar") and not brain._speaks("D-h3", "fa")
now = brain._when("2026-11-22T07:00:00+00:00")
lines = ["slot_id=A doctor_id=D-h1 at=2026-11-23T09:00:00+00:00 day=mon languages=it free=1",
         "slot_id=B doctor_id=D-h2 at=2026-11-23T10:00:00+00:00 day=wed languages=it/en free=1"]
got = brain.fitting(lines, now=now, priority="B", lang="en")
assert [x["slot_id"] for x in got] == ["B"] or [x["slot_id"] for x in got] == [], got  # h2 works wed, B is mon
got = brain.fitting(lines[:1], now=now, priority="B", lang="en")
assert got == [], got  # the row says h1 speaks only it: never claimed

# SO-53: honest when the world has no location; uses it when it has
assert "cannot sort by distance" in brain._near_note([m("the nearest one please")], [{"slot_id": "A"}], "en")
assert "Navigli" in brain._near_note([m("il più vicino")], [{"slot_id": "A", "district": "Navigli"}], "it")
assert brain._near_note([m("ciao")], [{"slot_id": "A"}], "it") == ""

# emergency in the citizen's language
for text, needle in [("ho dolore al petto", "112"), ("tengo dolor en el pecho, hola, necesito ayuda", "llame"),
                     ("Hallo, ich habe Schmerzen in der Brust, bitte", "rufen Sie"), ("I have chest pain, please help", "call 112")]:
    out = brain._escalate_text(m(text))
    assert out.startswith("escalate.emergency reason=red_flag\n") and needle in out, (text, out)

async def j106():
    # live journey 106: cancel of the waiting-list offer twice, 2 s apart, then a plain message
    need = "n-j106"
    rows = [row(CZ, BC, "Hello, I have back pain. I need an English-speaking doctor prescription_number=RX1", need),
            row(BC, CZ, "offer offer_id=of-S1 slot_id=S1 doctor_id=D-h1 agenda_date=2026-11-30T09:00:00+00:00\noffer offer_id=wl-n-j106-alt waiting_list=true", need),
            row(BC, CZ, "Mi scusi se deve riscrivere: riprendo da dove eravamo.", need)]
    fake = Fake(rows)
    fake.inbound = rows[0]
    sent_before = len(fake.out)
    texts = ["cancel offer_id=wl-n-j106-alt reason=waiting_list", "cancel offer_id=wl-n-j106-alt reason=waiting_list",
             "hello? is anybody there", "cancel offer_id=wl-n-j106-alt reason=waiting_list"]
    for tx in texts:
        m = row(CZ, BC, tx, need)
        fake.rows.append(m)
        fake.inbound = m
        brain._REFUSAL_AT.clear()  # the live cancels were seconds apart: past the burst window
        before = len([1 for to, _ in fake.out if to == CZ])
        await brain._main_locked(fake, m, m.text)
        got = [t for to, t in fake.out if to == CZ][before:]
        assert len(got) == 1, (tx, got, [t[:60] for to, t in fake.out])  # exactly one reply per citizen message
    out = [t for to, t in fake.out if to == CZ]
    assert len(out) == len(set(out)), out  # never the same line twice
    assert "waiting list" in out[0] and "English-speaking doctor is free" in out[1], out
    assert "offer_id=of-S1" in out[1] and "consent_ref" in out[1], out[1]  # the later slot, with consent


os.environ["CITY_ROLE"] = "booking_centre"
asyncio.run(j106())
os.environ.pop("CITY_ROLE")

async def plain_refusal():
    need = "n-plain"
    rows = [row(CZ, BC, "I need a visit prescription_number=RX1", need),
            row(LEDGER, BC, "prescription prescription_number=RX1 procedure=VIS-FIS priority=B state=issued", need),
            row(LEDGER, BC, "agenda agenda_now=2026-11-22T07:00:00+00:00", need), row(LEDGER, BC, WORLD, need)]
    fake = Fake(rows)
    os.environ["CITY_ROLE"] = "booking_centre"
    await brain._main_locked(fake, rows[0], rows[0].text)
    offers = [t for to, t in fake.out if to == CZ and "offer_id=" in t]
    assert offers
    for tx in ["no thanks", "Oh dear, this is not quite right for me", "non mi va", "preferisco di no"]:
        m = row(CZ, BC, tx, need)
        fake.rows.append(m)
        fake.inbound = m
        brain._REFUSAL_AT.clear()  # refusals spaced past the burst window: each is answered
        n0 = len([1 for to, _ in fake.out if to == CZ])
        await brain._main_locked(fake, m, m.text)
        got = [t for to, t in fake.out if to == CZ][n0:]
        assert len(got) >= 1, (tx, [(to[:8], x[:70]) for to, x in fake.out if to == CZ])  # never silence
        assert not any("Le ripropongo" in t for t in got), got
    out = [t for to, t in fake.out if to == CZ]
    assert len(out) == len(set(out)), out
    os.environ.pop("CITY_ROLE")
    assert brain._REFUSAL.search("preferisco di no") and not brain._REFUSAL.search("grazie mille, va bene")


asyncio.run(plain_refusal())


async def cancel_once():
    fake = Fake([row(CZ, BC, "x", "n-c1")])
    fake.inbound = fake.rows[0]
    await asyncio.gather(*[brain._say(fake, "agent:t/p", "cancel offer_id=of-1 reason=a\nSorry") for _ in range(3)])
    assert len([1 for to, t in fake.out if t.startswith("cancel offer_id=of-1")]) == 1, fake.out
    await brain._say(fake, "agent:t/q", "cancel offer_id=of-1 reason=a\nSorry")  # another party: sent
    assert len([1 for to, t in fake.out if t.startswith("cancel offer_id=of-1")]) == 2


asyncio.run(cancel_once())

# --- team identity: live agent ids have no team part (cross-team data leak = -2) ---------------
class P:
    def __init__(self, pid, role):
        self.agent_id, self.role, self.capabilities = pid, role, ()


async def handoff_live_ids():
    GPL, OURBC, OTHERBC = "agent:jmqwr12vy2j6", "agent:y6hxsd766sys", "agent:zzzzother001"
    need = "n-live"
    first = row(CZ, GPL, "ho mal di schiena", need)

    class G(Fake):
        def __init__(self, peers):
            Fake.__init__(self, [first])
            self.agent_id, self.peers = GPL, peers

        async def find(self, query):
            return self.peers

    for peers, expect in (([P(OTHERBC, "booking_centre"), P(OURBC, "booking_centre")], [OURBC]),
                          ([P(OTHERBC, "booking_centre")], []),
                          ([P(OURBC, "booking_centre"), P(OTHERBC, "booking_centre")], [OURBC])):
        g = G(peers)
        g.inbound = first
        await brain._hand_to_desk(g, [first], first, "RX1", "B")
        sent = [to for to, t in g.out if to.startswith("agent:")]
        assert sent == expect, (peers, sent, g.out, brain._our_desk(GPL, OURBC), brain._left(1.0))
        assert not any(to == OTHERBC for to, _ in g.out)


asyncio.run(handoff_live_ids())
assert brain._same_team("agent:jmqwr12vy2j6", "agent:y6hxsd766sys")
assert not brain._same_team("agent:jmqwr12vy2j6", "agent:zzzzother001")
assert not brain._same_team("agent:unknown1", "agent:unknown2")  # empty team parts never match
assert brain._same_team("agent:t07/gp", "agent:t07/booking_centre") and not brain._same_team("agent:t07/gp", "agent:t08/gp")
assert brain._xa_candidates([P("agent:r8yprrs0e8be", "facility"), P("agent:other9", "facility")], "agent:y6hxsd766sys", "VIS-FIS")[0].agent_id == "agent:other9"
print("--- release2: j91 replay, guard, language, receipt tips, prep note, closing passed")
