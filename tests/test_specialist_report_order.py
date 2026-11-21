"""Specialist: the report links its follow-up only when the Ledger has the follow-up prescribe first.
The live Ledger takes 8-20 s per write: a report sent right behind the prescribe can be processed
before it and then names no follow_up (report_missing -1). So the report waits for the prescribe's
answer (8 s at most, a refusal counts), in the background, once per booking.
Run: <city python> -I tests/test_specialist_report_order.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys
import time

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._FOLLOW_UP_ALWAYS = True

AG, CIT = "agent:t/specialist_care", "citizen:t/c1"
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    address = ""

    def __init__(self, need, ledger):
        self.agent_id, self.need, self.ledger = AG, need, ledger
        self.rows, self.sent, self.t0 = [], [], time.monotonic()

    async def say(self, to, text, *, status=None):
        self.sent.append((time.monotonic() - self.t0, to, text))
        self.rows.append(msg(AG, to, text, self.need))
        if to == LEDGER:
            for delay, answer in self.ledger(text):
                asyncio.get_running_loop().call_later(
                    delay, self.rows.append, msg(LEDGER, AG, answer, self.need))
        return "x"

    async def find(self, query):
        return []

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)


def rx_of(text):
    return next(w.split("=", 1)[1] for w in text.split() if w.startswith("prescription_number="))


def slow_ack(delay):
    def led(text):
        if text.startswith("prescribe "):
            return [(delay, f"prescribed patient={CIT} prescription_number={rx_of(text)}")]
        if text.startswith("report "):
            return [(0.2, "reported " + text[7:])]
        return []
    return led


def refused(text):
    if text.startswith("prescribe "):
        return [(0.3, f"refused op=prescribe prescription_number={rx_of(text)} code=INVALID")]
    return slow_ack(0)(text) if text.startswith("report ") else []


def silent(text):
    return [(0.2, "reported " + text[7:])] if text.startswith("report ") else []


async def scenario(name, ledger):
    need, booking = f"N-{name}", f"bk-{name}"
    city = City(need, ledger)
    gp = msg("agent:t/gp", AG, "prescription prescription_number=RX-1 procedure=VIS-CARD priority=B", need)
    ask = msg(AG, CIT, f"consent purpose=report_sharing booking_id={booking}\nPosso?", need)
    city.rows += [msg(CIT, AG, f"checkin booking_id={booking}", need), gp, ask]
    first = city.rows[0]
    t0 = time.monotonic()
    await brain._file_report(city, first, list(city.rows), booking, CIT, "")
    returned = time.monotonic() - t0
    await brain._file_report(city, first, list(city.rows), booking, CIT, "")  # same key: no second file
    await asyncio.sleep(14.0)
    led = [(t, text) for t, to, text in city.sent if to == LEDGER]
    return returned, led


def times(led, head):
    return [t for t, text in led if text.startswith(head)]


async def main():
    (ra, la), (rb, lb), (rc, lc) = await asyncio.gather(
        scenario("a", slow_ack(5.0)), scenario("b", silent), scenario("c", refused))
    # (a) slow Ledger: the report goes out after the prescribe's ack, and links the follow-up
    rep = times(la, "report ")
    assert len(rep) == 1, la
    assert rep[0] >= 5.0 - 0.5, f"report at {rep[0]:.1f}s, before the prescribe ack (5 s): {la}"
    assert "follow_up_prescription_number=" in next(t for _, t in la if t.startswith("report ")), la
    assert ra < 2.0, f"_file_report blocked the turn {ra:.1f}s"
    # (b) no ack at all: the report still goes out at about 8 s
    rep = times(lb, "report ")
    assert len(rep) == 1 and 7.5 <= rep[0] <= 11.0, lb
    assert rb < 2.0, rb
    # (c) refused prescribe: the report goes out at once
    rep = times(lc, "report ")
    assert len(rep) == 1 and rep[0] < 5.0, lc
    # (d) one prescribe and one report per booking in every case
    for led in (la, lb, lc):
        assert len(times(led, "prescribe ")) == 1 and len(times(led, "report ")) == 1, led
    print("OK test_specialist_report_order")


asyncio.run(main())
