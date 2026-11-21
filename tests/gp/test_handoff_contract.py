"""Contract gp brain -> desk brain: the gp's hand-off is a request the desk acts on (offers to the citizen).
Run: <city python> -I tests/gp/test_handoff_contract.py asst/_gp/agent.py [asst/_shared/agent.py]"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("CITY_RESCUE_WARMUP", "0")
from city_sdk.city import Message  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GP_PATH = sys.argv[1]
DESK_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "..", "asst", "_shared", "agent.py")


def load(path, role, name):
    os.environ["CITY_ROLE"] = role
    spec = importlib.util.spec_from_file_location(name, path)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.role_of = lambda: role
    return b


gp = load(GP_PATH, "gp", "gpb")
desk = load(DESK_PATH, "booking_centre", "deskb")
team = desk._our_agents()  # role -> live agent id, the desk's own view of the team
GP_ID, DESK_ID = team["gp"], team["booking_centre"]
for need in (GP_ID, DESK_ID):
    assert need in gp._MATES, f"gp brain does not know team id {need}"
CZ = "citizen:t/c1"
out = []


async def say(to, text, **_):
    out.append((to, text))
    return "ok"


async def find(query):
    return [SimpleNamespace(agent_id=DESK_ID, role="booking_centre")]


def msg(sender, to, text):
    return Message(type="chat", sender=sender, recipient=to, msg_id="m1", need_id="n1", conversation_id="cv",
                   body={"text": text}, sent_at=datetime.now(timezone.utc).isoformat())


fails = 0
for lang in ("it", "en"):
    out.clear()
    city = SimpleNamespace(agent_id=GP_ID, address="", say=say, find=find, history=None)
    gp._say = lambda c, to, text, *a, **k: say(to, text)
    asyncio.run(gp._hand_to_desk(city, [], msg(CZ, GP_ID, "ho mal di schiena"), "RX123", "B", lang))
    hand = [t for to, t in out if to == DESK_ID and t.startswith("hand-off")]
    if not hand:
        fails += 1
        print(f"FAIL {lang}: no hand-off sent to the desk: {out}")
        continue
    proxy = desk._handoff_proxy(msg(GP_ID, DESK_ID, hand[0]), DESK_ID)
    if proxy is None:
        fails += 1
        print(f"FAIL {lang}: the desk ignores the gp hand-off: {hand[0]!r}")
        continue
    if proxy.sender != CZ or "prescription_number=RX123" not in proxy.text:
        fails += 1
        print(f"FAIL {lang}: proxy wrong: sender={proxy.sender} text={proxy.text!r}")
    if lang == "en" and "lang=en" not in proxy.text:
        fails += 1
        print(f"FAIL en: language lost in the hand-off: {proxy.text!r}")
    told = [t for to, t in out if to == CZ]
    want = "will send you" if lang == "en" else "le manda"
    if not any(want in t for t in told) or any("basta scegliere" in t or "only need to choose" in t for t in told):
        fails += 1
        print(f"FAIL {lang}: the citizen is told the centre sends the dates (not pushed back to them): {told}")
    if "schiena" in hand[0]:
        fails += 1
        print("FAIL: symptom leaked to the desk")
print(f"{'OK' if not fails else 'FAILED'} test_handoff_contract ({fails} failed) gp={GP_ID} desk={DESK_ID}")
sys.exit(1 if fails else 0)
