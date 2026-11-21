"""Cross-ASST offer and consent lines follow the citizen's language (live journey 0172).

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_xa_english.py
"""

import asyncio
import importlib.util
import os

from city_sdk.city import Message

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("brain", os.path.join(HERE, "..", "asst", "_shared", "agent.py"))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)

CIT = "citizen:t07/c172"
N = [0]


def row(sender, text, need="N-xa-1", to=""):
    N[0] += 1
    return Message(type="chat", sender=sender, msg_id=f"m{N[0]}", need_id=need, body={"text": text}, recipient=to)


class City:
    agent_id = "agent:t07/booking_centre"

    def __init__(self):
        self.sent = []

    async def say(self, to, text, *, status=None):
        self.sent.append((to, text))


def italian(text):
    return any(w in text for w in ("Per prenotare", "Questa struttura", "Nei tempi", "mi serve"))


async def consent_text(chat, need):
    city = City()
    sent = brain._sent(need)
    await brain._xa_after(city, chat, CIT, need, ["offer offer_id=o1 slot_id=S1 facility=f1"], sent)
    return [t for to, t in city.sent if to == CIT]


async def main():
    en = [row(CIT, "I need a cardiology visit with an English-speaking doctor, lang=uk", "N-xa-en")]
    it = [row(CIT, "Buongiorno, ho bisogno di una visita cardiologica, lang=it", "N-xa-it")]

    # Offer line
    assert not italian(brain._xa_text(["a"], local=False, en=True))
    assert brain._xa_text(["a"], local=False).startswith("Nei tempi")
    assert "confirm" in brain._xa_text(["a", "b"], local=True, en=True)

    # Consent request after the offer
    got = await consent_text(en, "N-xa-en")
    assert len(got) == 1 and not italian(got[0]), got
    assert got[0].splitlines()[0] == "data.request purpose=cross_asst fields=consent_ref"
    got = await consent_text(it, "N-xa-it")
    assert got == [brain._XA_CONSENT], got

    # Consent ask when the citizen confirms without consent (_loc path)
    m_en = en[0]
    assert not italian(brain._loc(m_en, brain._XA_CONSENT_ASK))
    assert brain._loc(m_en, brain._XA_CONSENT_ASK).splitlines()[0] == brain._XA_CONSENT_ASK.splitlines()[0]
    assert brain._loc(it[0], brain._XA_CONSENT_ASK) == brain._XA_CONSENT_ASK
    assert not italian(brain._loc(m_en, brain._XA_CONSENT))
    assert brain._loc(it[0], brain._XA_CONSENT) == brain._XA_CONSENT
    print("OK test_xa_english")


asyncio.run(main())
