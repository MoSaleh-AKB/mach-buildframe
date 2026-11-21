"""B3 (live 21 Nov, 38 of 188 journeys): offers are already past when the citizen answers.

The city runs 60x (1 real minute = 1 agenda hour) and citizens answer an offer 234-515 s later
(4-8.5 agenda hours). The desk offered slots 30 agenda minutes ahead, all of one doctor on one day
(12 cancels reason=lang, 4 gender, 6 day), and relayed confirms of slots already past.

Fake world: slow Ledger (8 s, replies only in history, no pushed turn) and a 60x agenda clock
(`agenda` answers the start time plus 60x the real time since the start; every row carries a real
sent_at). `jump(s)` makes s real seconds pass at once (rows are back-dated, the clock moves on).
World rows look like live ones (long ids, expiry=, prescription numbers of live length).
a) lead: Fri 06:00, slots Fri 10:30-16:00 and Mon/Tue. The first offered slot is >= 14 agenda
   hours after the agenda time of the offer.
b) spread: Thu 16:00, six free slots: five of h2 on Friday, one of h1 on Monday. The one message
   (2000 character cap) must show at least 2 days and 2 doctors.
c) pastconfirm: offers out, then 18 agenda hours pass before the citizen confirms the earliest
   offer. No confirm of that slot reaches the hospital; the citizen hears the date is gone, with
   new offers that are all in the future.
Run: <city python> -I tests/test_offer_lead_spread.py asst/_shared/agent.py
Fails before the fix (Fri 10:30 offered at Fri 06:08; Friday only; the past slot relayed)."""
import asyncio
import dataclasses
import os
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("REGRESS_ROLES", "booking_centre")
os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import LIVE, result

LEDGER = _rh.LEDGER
HO = LIVE["hospital"]
NUM = "prescription_number-N-2026-11-02-8909040002370005"
RX = f"prescription prescription_number={NUM} procedure=VIS-FIS priority=B state=issued"
ASK = f"need.request prescription_number={NUM} lang=it area.district=1 text=vorrei_prenotare_la_visita"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def when(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def live_slot(doc, day, hhmm, wd):
    at = datetime(2026, 12, day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)
    sid = f"W01QQQ-asst-centro-{doc}-202612{day:02d}-{hhmm}"
    return (f"slot_id={sid} procedure=VIS-FIS facility={HO} doctor_id=asst-centro-{doc} at={iso(at)} "
            f"day={wd} free=1 expiry={iso(at)}")


class Clock60(_rh.World):
    """World with a 60x agenda clock and real sent_at on every row."""

    def __init__(self, *a, start, **k):
        super().__init__(*a, **k)
        self.start = when(start)
        self.real0 = time.time()
        self.skew = 0.0

    def agenda_at(self, t_real=None):
        t = (time.time() if t_real is None else t_real) - self.real0 + self.skew
        return self.start + timedelta(seconds=t * 60)

    def msg(self, sender, to, text, need):
        m = super().msg(sender, to, text, need)
        return dataclasses.replace(m, sent_at=iso(datetime.now(timezone.utc)))

    def answer(self, text, need):
        if text.split()[:1] == ["agenda"]:
            return f"agenda agenda_now={iso(self.agenda_at())}"
        if text.split()[:1] == ["world"]:
            now = self.agenda_at()
            free = [s for s in self.slots if _rh.fields(s).get("slot_id") not in self.taken
                    and when(_rh.fields(s)["at"]) > now]
            return (f"slots wave_id=W01QQQ asst=asst-centro from={now.date()} matching={len(free)} page=1/1\n"
                    + "\n".join(free))
        return super().answer(text, need)

    def jump(self, seconds):
        """`seconds` real seconds pass at once: every row so far is that much older."""
        self.skew += seconds
        back = timedelta(seconds=seconds)
        self.rows[:] = [dataclasses.replace(r, sent_at=iso(when(r.sent_at) - back)) if r.sent_at else r
                        for r in self.rows]


def world(start, slots, bots=None):
    b = _rh.load(sys.argv[1], "booking_centre")
    return Clock60(b, "booking_centre", delay=8.0, push=False, slots=slots, rx={NUM: RX},
                   bots=bots or {}, start=start)


async def first_offers(w, cz, need):
    await w.send(cz, ASK, need)
    await w.settle(40)
    deadline = time.time() + 30
    while time.time() < deadline and not _rh.offer_lines(w, cz):
        await asyncio.sleep(0.5)
        await w.settle(5)
    return _rh.offer_lines(w, cz)


def said_agenda(w, cz):
    """Agenda time when the first offer message was said (no jump before it)."""
    for tx, at in w.said_to(cz):
        if any(ln.startswith("offer ") and "slot_id=" in ln for ln in tx.splitlines()):
            return w.agenda_at(time.time() - (w.now() - at))
    return None


async def case_lead():
    slots = [live_slot("h2", 4, t, "fri") for t in ("1030", "1200", "1430", "1600")] + [
        live_slot("h1", 7, "1030", "mon"), live_slot("h1", 7, "1200", "mon"), live_slot("h3", 8, "1030", "tue")]
    w = world("2026-12-04T06:00:00Z", slots)
    cz, need = "citizen:b3-lead", "N-b3-lead"
    offs = [o for o in await first_offers(w, cz, need) if o.get("agenda_date")]
    if not offs:
        result("B3a", "booking_centre", False, "no offer at all")
        return
    said = said_agenda(w, cz)
    first = min(when(o["agenda_date"]) for o in offs)
    ahead = (first - said).total_seconds() / 3600
    result("B3a", "booking_centre", ahead >= 14,
           f"first offered slot {iso(first)} is {ahead:.1f} agenda h after the offer ({iso(said)}); want >= 14")


async def case_spread():
    slots = [live_slot("h2", 4, t, "fri") for t in ("0900", "1030", "1200", "1430", "1600")] + [
        live_slot("h1", 7, "1030", "mon")]
    w = world("2026-12-03T16:00:00Z", slots)
    cz, need = "citizen:b3-spread", "N-b3-spread"
    offs = [o for o in await first_offers(w, cz, need) if o.get("agenda_date")]
    days = {o.get("agenda_date", "")[:10] for o in offs}
    docs = {o.get("doctor_id") for o in offs}
    result("B3b", "booking_centre", len(days) >= 2 and len(docs) >= 2,
           f"{len(offs)} offers, days={sorted(days)} doctors={sorted(docs)}; want >= 2 days and >= 2 doctors")


async def case_past_confirm():
    confirms = []

    async def hospital(w, to, text, need):
        if text.startswith("confirm "):
            confirms.append(_rh.fields(text.split("\n")[0]).get("slot_id", ""))

    slots = [live_slot("h2", 4, t, "fri") for t in ("0900", "1030", "1200")] + [
        live_slot("h1", 7, "1030", "mon"), live_slot("h1", 7, "1200", "mon"), live_slot("h1", 7, "1430", "mon")]
    w = world("2026-12-03T16:00:00Z", slots, bots={HO: hospital})
    cz, need = "citizen:b3-past", "N-b3-past"
    offs = [o for o in await first_offers(w, cz, need) if o.get("agenda_date")]
    if not offs:
        result("B3c", "booking_centre", None, "no first offers (cannot test the confirm)")
        return
    early = min(offs, key=lambda o: o["agenda_date"])
    gap = (when(early["agenda_date"]) - w.agenda_at()).total_seconds()
    w.jump(gap / 60 + 3600 / 60)  # the citizen answers when that slot is an agenda hour past
    _, at = await w.send(cz, f"confirm offer_id={early['offer_id']} consent_ref=cons-{need}\nVa bene, confermo.", need)
    await w.settle(40)
    deadline = time.time() + 30
    while time.time() < deadline and not [o for o in _rh.offer_lines(w, cz, since=at) if o.get("slot_id")]:
        await asyncio.sleep(0.5)
        await w.settle(5)
    now = w.agenda_at()
    relayed = early["slot_id"] in confirms
    told = [tx for tx, _ in w.said_to(cz, since=at)]
    cancelled = any(tx.startswith(f"cancel offer_id={early['offer_id']}") or "no longer available" in tx
                    or "non è più disponibile" in tx for tx in told)
    new = [o for o in _rh.offer_lines(w, cz, since=at) if o.get("agenda_date")]
    future = bool(new) and all(when(o["agenda_date"]) > now for o in new)
    result("B3c", "booking_centre", not relayed and cancelled and future,
           f"past slot {early['slot_id']} relayed={relayed} told_gone={cancelled} "
           f"new_offers={len(new)} all_future={future}")


async def main():
    await case_lead()
    await case_spread()
    await case_past_confirm()


_rh.run(main())
