"""Review 2026-11-21 13:15 item 23: a citizen who refuses the cross_asst consent (state=denied, or
"no, non acconsento") was asked again, and the third reply got silence. A refusal now ends the
partner path once: nothing goes to the partner, the citizen hears it plainly with the other options,
the consent is never asked again, and every reply gets an answer.

Run: <city python> -I tests/test_xa_consent_refused.py [asst/_shared/agent.py]   (exit 0 = pass)
"""
import asyncio
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
    os.environ["BRAIN"] = os.path.abspath(sys.argv[1])
spec = importlib.util.spec_from_file_location("xa_harness", os.path.join(HERE, "test_desk_xa_consent.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
FAILS = []


async def case(label, replies):
    c = await h.xa_case()
    for i, text in enumerate(replies, 1):
        await h.turn(c, h.CZ, text)
        told = h.said(c).strip()
        if not told:
            FAILS.append(f"{label}: reply {i} got silence")
        elif "purpose=cross_asst" in told and "data.request" in told:
            FAILS.append(f"{label}: reply {i}: the consent was asked again after a refusal")
        if h.to_partner(c):
            FAILS.append(f"{label}: reply {i}: citizen data went to the partner")


async def main():
    await case("state=denied x3", ["consent purpose=cross_asst state=denied\nNo, non acconsento."] * 3)
    await case("words x3", ["No, non acconsento alla condivisione.", "Ho detto di no.", "No grazie."])
    for f in FAILS:
        print("FAIL", f)
    print(("OK" if not FAILS else f"FAIL {len(FAILS)}") + " test_xa_consent_refused")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
