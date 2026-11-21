"""#140: the gp hand-off reaches our own desk even when the 1 s Index lookup times out.

`_hand_to_desk` gave `city.find("booking_centre")` 1.0 s; on a timeout it sent nothing although the
citizen had already been told the desk has the prescription. Now the known desk id goes first, one
failed send is retried, and a hand-off that still fails is told to the citizen with the number.

Run: <city python> -I tests/gp/test_gp_handoff_known_desk.py asst/_gp/agent.py
"""
import asyncio
import importlib.util
import sys

from city_sdk.city import CapError, Message

spec = importlib.util.spec_from_file_location("brain", sys.argv[1] if len(sys.argv) > 1 else "asst/_gp/agent.py")
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain.role_of = lambda: "gp"

CZ, GP, DESK = "citizen:t07/c1", "agent:jmqwr12vy2j6", "agent:y6hxsd766sys"
N = [0]


def msg(sender, to, text, need):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class Fake:
    address = ""

    def __init__(self, first, desk_fails=0):
        self.agent_id, self.rows, self.inbound, self.out, self.desk_fails, self.finds = GP, [first], first, [], desk_fails, 0

    async def say(self, to, text, *, status=None):
        if to == DESK and self.desk_fails > 0:
            self.desk_fails -= 1
            raise CapError("UPSTREAM_TIMEOUT", "relay")
        self.out.append((to, text))
        self.rows.append(msg(GP, to, text, self.inbound.need_id))
        return "x"

    async def history(self, peer=None, *, need_id=None):
        return list(self.rows)

    async def find(self, query):
        self.finds += 1
        await asyncio.sleep(5)  # the Index under load: longer than the 1 s budget
        return []


async def turn(need, text="ho mal di schiena da ieri, mi serve una ricetta", desk_fails=0):
    first = msg(CZ, GP, text, need)
    f = Fake(first, desk_fails)
    await asyncio.wait_for(brain.handle("gp", f, first, [first]), 30)
    return f


def hands(f):
    return [t for to, t in f.out if to == DESK and t.startswith("hand-off")]


async def main():
    oks = []

    def check(ok, name, f):
        print(("PASS " if ok else "FAIL ") + name)
        oks.append(ok)
        if not ok:
            for to, t in f.out:
                print("    ", to[:20], t[:150].replace("\n", " | "))

    f = await turn("N-1")
    check(len(hands(f)) == 1, "Index lookup times out: the hand-off still reaches our desk", f)
    check(bool(hands(f)) and "prescription_number=" in hands(f)[0] and "schiena" not in hands(f)[0], "hand-off: number only, no symptom", f)
    f = await turn("N-2", desk_fails=1)
    check(len(hands(f)) == 1, "one failed send: retried once, one hand-off", f)
    f = await turn("N-3", desk_fails=2)
    told = [t for to, t in f.out if to == CZ]
    check(not hands(f) and any("Non sono riuscito a raggiungere il centro prenotazioni" in t and "prescription_number=" in t for t in told),
          "two failed sends: the citizen is told honestly, with the number", f)
    f = await turn("N-4", text="I have had back pain since yesterday, I need a prescription")
    check(len(hands(f)) == 1 and not any("Ho passato" in t for to, t in f.out if to == CZ), "English citizen: hand-off sent, English line", f)
    print(("ALL PASS" if all(oks) else f"FAIL {oks.count(False)}/{len(oks)}") + " test_gp_handoff_known_desk")
    sys.exit(0 if all(oks) else 1)


asyncio.run(main())
