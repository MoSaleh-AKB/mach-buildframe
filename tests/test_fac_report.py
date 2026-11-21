"""SO-9: the Ledger `reported` line lands before the citizen answered the consent ask.
Run: <city python> -I tests/test_fac_report.py asst/_shared/agent.py"""
import asyncio
import importlib.util
import sys

from city_sdk.city import LEDGER, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1])
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

AG, CIT, NEED = "agent:spec", "citizen:c1", "N-0001"
n = [0]


def msg(sender, to, text):
    n[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"t{n[0]}", need_id=NEED, body={"text": text}, recipient=to)


class Fake:
    def __init__(self, rows):
        self.agent_id, self.rows, self.out = AG, rows, []
        self.address = ""

    async def say(self, to, text, *, status=None):
        self.out.append((to, text))
        self.rows.append(msg(AG, to, text))
        return "x"

    async def history(self, **kw):
        return list(self.rows)


async def run(answer):
    brain._ONCE.clear()  # same agent and booking in each run: the once-per-process key is per run
    rows = [
        msg(CIT, AG, "checkin booking_id=B1"),
        msg(AG, CIT, "consent purpose=report_sharing booking_id=B1\nPosso condividere il referto?"),
        msg(AG, LEDGER, "report booking_id=B1 outcome_code=VISIT"),
    ]
    if answer:
        rows.append(msg(CIT, AG, answer))
    rep = msg(LEDGER, AG, "reported booking_id=B1")
    rows.append(rep)
    city = Fake(rows)
    await brain._on_reported(city, rep, rows)
    return [t for to, t in city.out if to == CIT]


async def main():
    said = await run(None)
    assert said, "citizen must hear a status line in the same turn"
    low = said[0].lower()
    assert "come da sua scelta" not in low and "senza condividerlo" not in low, said[0]
    assert "archiviato" in low and "consenso" in low, said[0]
    said = await run("No, grazie")
    assert "senza condividerlo" in said[0].lower(), said[0]
    said = await run("consent_ref=cons-1")
    assert "condiviso" in said[0].lower(), said[0]
    print("OK test_fac_report")


asyncio.run(main())
