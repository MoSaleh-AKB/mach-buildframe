"""Review 2026-11-21 13:15 item 25: a partner's CONSENT_MISSING refusal must ask the citizen for the
cross_asst consent for THAT offer (named by offer_id) and keep it, with no other slots; a second
refusal is answered too (it was silence).

Run: <city python> -I tests/test_xa_consent_missing_ask.py [asst/_shared/agent.py]   (exit 0 = pass)
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


async def main():
    c = await h.xa_case()
    ref = f"cons-{c.inbound.need_id}-cross_asst"
    await h.turn(c, h.CZ, f"consent purpose=cross_asst consent_ref={ref}")
    await h.turn(c, h.CZ, f"confirm offer_id=of-P1a consent_ref={ref}")
    if not h.to_partner(c):
        FAILS.append(f"setup: confirm not forwarded {c.out}")
    for n, text in enumerate(("cancel offer_id=of-P1a reason=CONSENT_MISSING\nMi serve il consenso cross_asst.",
                              "cancel offer_id=of-P1a reason=CONSENT_MISSING\nAncora senza consenso."), 1):
        await h.turn(c, h.P1, text)
        told = h.said(c)
        if not told.strip():
            FAILS.append(f"refusal {n}: silence to the citizen")
            continue
        if "offer_id=of-P1a" not in told or "purpose=cross_asst" not in told:
            FAILS.append(f"refusal {n}: the ask does not name offer_id=of-P1a and the cross_asst consent: {told[:160]!r}")
        if any(ln.startswith("offer ") and "slot_id=" in ln for ln in told.splitlines()):
            FAILS.append(f"refusal {n}: other slots offered instead of asking for the consent")
    for f in FAILS:
        print("FAIL", f)
    print(("OK" if not FAILS else f"FAIL {len(FAILS)}") + " test_xa_consent_missing_ask")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
