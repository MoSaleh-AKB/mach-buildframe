"""2.99.1 gp silence: a late redelivery of an old message must not leave the citizen's newer turn silent.

Live (21 Nov 14:03, 14:05): "brain begin -> LLM classify NONE -> brain done", no relay post. A stale case got
two turns at once: the platform redelivered the citizen's OLD message and the citizen's NEW message arrived.
The old turn took the recovery lock and, through R6, issued the prescription on the OLD message, so
`_rx_claim` belonged to it; the new turn found the lock held, went to `_gp` and returned at
`if not _rx_claim(...): return` with nothing said. Before R6 (71dec37) the recovery answered the newest
message and that turn answered.

Checks, every case with the LLM stubbed to NONE:
1. old (redelivered) + new message with a symptom, both turns at once: the NEW message's turn says a line,
   one prescription only.
2. two old messages + a new one: the newest message's turn says a line, one prescription only.
3. the old message's turn alone (no newer message): still recovers (intro + prescription), as before.
4. a turn that loses the `_rx_claim` race is never silent: it says one short line.

Run: CITY_RESCUE_WARMUP=0 <city python> -I tests/gp/test_gp_r6_redelivery.py asst/_gp/agent.py
"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
os.environ["CITY_ROLE"] = "gp"
from city_sdk.city import LEDGER, Message, Need  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "asst", "_gp", "agent.py")
N = [0]
FAILS = []


def load(tag):
    spec = importlib.util.spec_from_file_location(f"brain_r6r_{tag}", BRAIN)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: "gp"
    return b


def ago(sec):
    return (datetime.now(timezone.utc) - timedelta(seconds=sec)).isoformat()


def msg(sender, to, text, need, sent_at):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"r6r{N[0]}", need_id=need, body={"text": text},
                   recipient=to, sent_at=sent_at)


class World:
    def __init__(self, gp, need):
        self.gp, self.need, self.rows, self.out = gp, need, [], []


class View:
    """One turn's view of the shared mailbox; the model answers NONE after a short think."""

    address = ""

    def __init__(self, w, inbound):
        self.w, self.inbound, self.agent_id = w, inbound, w.gp

    async def say(self, to, text, *, status=None):
        self.w.out.append((self.inbound.msg_id, to, text))
        self.w.rows.append(msg(self.w.gp, to, text, self.w.need, ago(0)))
        return "x"

    async def find(self, q):
        return []

    async def history(self, peer=None, *, need_id=None):
        await asyncio.sleep(0.05)
        return list(self.w.rows)

    async def llm(self, messages, **_k):
        await asyncio.sleep(0.3)
        return {"content": "NONE"}


SYM = "Buongiorno, ho un forte mal di schiena da tre giorni e vorrei una visita, grazie mille"
NUDGE = "Buongiorno, sto ancora aspettando una risposta per la mia richiesta di visita medica"
SYM2 = "Scusi, il mal di schiena e peggiorato da ieri sera, quando posso avere la visita dal medico?"
CZ = "citizen:t-r6r"


async def run_case(tag, old_rows, new_texts, redeliver_old=True):
    b = load(tag)
    gp = sorted(b._MATES)[0] if b._MATES else "agent:jmqwr12vy2j6"
    gp = next((a for a in sorted(b._MATES) if a != b._our_desk()), gp)
    w = World(gp, f"N-{tag}")
    olds = [msg(CZ, gp, t, w.need, ago(a)) for t, a in old_rows]
    news = [msg(CZ, gp, t, w.need, ago(0)) for t in new_texts]
    w.rows.extend(olds + news)
    turns = (olds if redeliver_old else []) + news

    async def one(m):
        try:
            await asyncio.wait_for(b.main(Need(summary=m.text, message=m), View(w, m)), 30)
        except Exception as exc:
            if type(exc).__name__ != "OutOfScope":
                FAILS.append(f"{tag}: turn {m.msg_id} raised {type(exc).__name__}: {exc}")

    await asyncio.gather(*(one(m) for m in turns))
    await asyncio.sleep(0.3)
    lines = {m.msg_id: [t for mid, to, t in w.out if mid == m.msg_id and to == CZ] for m in turns}
    rx = [t for _, to, t in w.out if to == LEDGER and t.startswith("prescribe ")]
    return b, news, olds, lines, rx


def check(ok, why):
    if not ok:
        FAILS.append(why)
    print(("PASS " if ok else "FAIL ") + why, flush=True)


async def main():
    _, news, _, lines, rx = await run_case("one_old", [(SYM, 1500)], [SYM2])
    check(bool(lines[news[-1].msg_id]), f"1 old+new: the new message's turn says a line (got {lines})")
    check(len(rx) == 1, f"1 old+new: one prescription (got {len(rx)})")

    _, news, _, lines, rx = await run_case("two_old", [(SYM, 1800), (NUDGE, 1500)], [SYM2])
    check(bool(lines[news[-1].msg_id]), f"2 two old+new: the newest message's turn says a line (got {lines})")
    check(len(rx) == 1, f"2 two old+new: one prescription (got {len(rx)})")

    b, _, olds, lines, rx = await run_case("alone", [(SYM, 1500)], [], redeliver_old=True)
    said = lines[olds[0].msg_id]
    intro = b._REC_LINE["gp"]["it"][:40]
    check(any(t.startswith(intro) for t in said) and any(t.startswith("prescription ") for t in said),
          f"3 old message alone: recovery intro + prescription (got {[t[:40] for t in said]})")
    check(len(rx) == 1, f"3 old message alone: one prescription (got {len(rx)})")

    # 4: a sibling turn of this case holds the claim on this prescription number right now.
    b = load("claim")
    gp = next(a for a in sorted(b._MATES) if a != b._our_desk())
    w = World(gp, "N-claim")
    first = msg(CZ, gp, SYM, w.need, ago(5))
    second = msg(CZ, gp, SYM2, w.need, ago(0))
    w.rows.extend([first, second])
    number = b._rx(w.need, second.msg_id)
    b._RX_CLAIMS[f"{w.need}|{number}"] = (b.time.monotonic(), first.msg_id)
    try:
        await asyncio.wait_for(b.main(Need(summary=second.text, message=second), View(w, second)), 30)
    except Exception as exc:
        if type(exc).__name__ != "OutOfScope":
            FAILS.append(f"4 raised {type(exc).__name__}: {exc}")
    said = [t for mid, to, t in w.out if mid == second.msg_id and to == CZ]
    check(len(said) == 1, f"4 lost the rx claim race: one short line, never silent (got {[t[:50] for t in said]})")

    print("FAIL test_gp_r6_redelivery" if FAILS else "OK test_gp_r6_redelivery")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
