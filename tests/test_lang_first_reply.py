"""The first reply is in the citizen's language, decided once per conversation (live a463312: 26/47).

Personas in 11 languages, "English with 3 Italian words" and "Italian, then English" go to the
booking centre (no number, and a number the Ledger never answers), the GP and family counselling.
The first natural-language line of the first reply (token lines such as data.request and
prescription= are skipped) must be in the citizen's language.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_lang_first_reply.py [brain.py]
"""

import asyncio
import importlib.util
import os
import re
import sys
import time
from types import SimpleNamespace

from city_sdk.city import LEDGER, Message

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "asst", "_shared", "agent.py")
spec = importlib.util.spec_from_file_location("brain", path)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)
brain._ADVANCE_BUDGET = 0.4
brain._PULL_BUDGET = 0.4
brain._AGENT_BUDGET = 0.4
brain._HOLD_AFTER = 0.2

ROLES = {"booking_centre": "agent:t/booking_centre", "gp": "agent:t/gp", "family_counseling": "agent:t/family_counseling"}
SEQ = [0]

PERSONAS = [
    ("en", "Hello, I have back pain for a week, can you help me please"),
    ("en", "Hello, I have a pain in my back for a week. Buongiorno, grazie, ciao! Can you help me please?"),
    ("es", "Hola, tengo dolor de espalda desde hace una semana, necesito ayuda por favor"),
    ("fr", "Bonjour, j'ai mal au dos depuis une semaine, j'ai besoin d'un médecin s'il vous plaît"),
    ("de", "Guten Tag, ich habe seit einer Woche Rückenschmerzen und brauche bitte einen Arzt"),
    ("ro", "Bună ziua, am nevoie de o programare, am durere de spate de o săptămână"),
    ("sq", "Përshëndetje, kam dhimbje shpine, dua një mjek, faleminderit"),
    ("tr", "Merhaba, sırtım ağrıyor, lütfen bir doktor istiyorum"),
    ("ar", "مرحبا، عندي ألم في الظهر منذ أسبوع، أحتاج طبيبا من فضلك"),
    ("fa", "سلام، من یک هفته است کمردرد دارم و پزشک می‌خواهم"),
    ("zh", "你好，我背痛已经一个星期了，请帮帮我"),
    ("uk", "Добрий день, у мене болить спина вже тиждень, мені потрібен лікар, будь ласка"),
    ("ru", "Здравствуйте, у меня болит спина уже неделю, мне нужен врач, пожалуйста"),
]
SWITCH = [
    "Buongiorno, ho un forte mal di schiena da una settimana",
    "Sorry, can you answer me in English please? I have back pain since a week and I need help",
]
MARK = {
    "en": ("your", "I am", "Hello", "please", "I "),
    "es": ("Estoy", "Buenos", "Le he", "dígame", "comprobando", "Mensaje"),
    "fr": ("Je ", "votre", "Bonjour", "ordonnance"),
    "de": ("Ich ", "Sie ", "Guten Tag", "Rezept"),
    "ro": ("Verific", "Bună ziua", "Lucrez", "rețeta", "dvs"),
    "sq": ("Po ", "Mirëdita", "tuaj", "recetën"),
    "tr": ("Kayıt", "Merhaba", "Talebiniz", "Reçete", "reçete"),
}
ITALIAN = ("Sto ", "Buongiorno", "Mi dica", "mi dica", "ricetta", "Le ho emesso", "Per aiutarla")
TOKEN_LINE = re.compile(r"^[a-z][a-z._-]*( [\w.-]+=\S*)*$")


def msg(sender, to, text, need, ago=0.0):
    SEQ[0] += 1
    return Message(type="chat", sender=sender, recipient=to, msg_id=f"m{SEQ[0]:04d}", need_id=need,
                   conversation_id=f"cv-{need}", body={"text": text},
                   sent_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - ago)))


class City:
    address = ""

    def __init__(self, me, rows):
        self.agent_id, self.rows, self.out, self.inbound = me, list(rows), [], None

    async def say(self, to, text, *, status=None, need=None):
        need = need or self.inbound.need_id
        if to.startswith("citizen:"):
            self.out.append(text)
        self.rows.append(msg(self.agent_id, to, text, need))
        if to == LEDGER and text.startswith("prescribe"):
            number = re.search(r"prescription_number=(\S+)", text).group(1)
            self.rows.append(msg(LEDGER, self.agent_id,
                                 f"prescription prescription_number={number} procedure=VIS-FIS priority=B state=issued", need))
        return "ok"

    async def history(self, peer=None, *, need_id=None):
        return [r for r in self.rows if need_id is None or r.need_id == need_id]

    async def find(self, query):
        return []


def reset(role):
    brain.role_of = lambda: role
    for name in ("_CITIZEN_SENT", "_CITIZEN_RATE", "_DUPS", "_CANCELS_SENT", "_REJECTED", "_ONCE",
                 "_RESCUED", "_RESCUE_REFUSED", "_GATES", "_LOCKS", "_SENT", "_HELD"):
        getattr(brain, name).clear()
    brain._RESCUE_LAST[:] = [time.monotonic()]


async def first_reply(role, texts, before=()):
    """The first text sent to the citizen when the last of ``texts`` arrives."""

    reset(role)
    need = f"N-{SEQ[0]}"
    cz = f"citizen:t/c{SEQ[0]}"
    city = City(ROLES[role], [])
    for text in texts[:-1]:
        city.rows.append(msg(cz, city.agent_id, text, need, ago=60))
    for text in before:
        city.rows.append(msg(city.agent_id, cz, text, need, ago=50))
    row = msg(cz, city.agent_id, texts[-1], need)
    city.rows.append(row)
    city.inbound = row
    view = SimpleNamespace(inbound=row, agent_id=city.agent_id, address="", find=city.find, history=city.history,
                           say=lambda to, text, *, status=None: city.say(to, text, need=need))
    await asyncio.wait_for(brain.main(SimpleNamespace(summary="x", is_emergency=False), view), 30)
    return city.out[0] if city.out else ""


def natural_line(text):
    for line in text.replace("​", "").splitlines():
        if line.strip() and not TOKEN_LINE.match(line.strip()):
            return line
    return ""


def in_language(line, code):
    if not line:
        return False
    if code == "zh":
        return bool(re.search("[一-鿿]", line))
    if code in ("ar", "fa"):
        return bool(re.search("[؀-ۿ]", line))
    if code in ("uk", "ru"):
        ukr = bool(re.search("[іїєґ]", line))
        return bool(re.search("[Ѐ-ӿ]", line)) and (ukr if code == "uk" else not ukr or "лікар" not in line)
    return any(m in line for m in MARK[code]) and not any(i in line for i in ITALIAN)


async def main():
    results = []

    def check(label, code, text):
        line = natural_line(text)
        ok = in_language(line, code)
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + f"{label} [{code}]: {line[:70]!r}")

    for role in ROLES:
        for code, text in PERSONAS:
            if role == "booking_centre":
                check(f"{role} no number", code, await first_reply(role, [text]))
                check(f"{role} ledger silent", code, await first_reply(role, [text + " prescription_number=RX-1"]))
            else:
                check(role, code, await first_reply(role, [text]))
        check(f"{role} switch it->en", "en", await first_reply(role, SWITCH, before=["Buongiorno, la aiuto subito."]))
    # the prescription sentence itself, every language
    for code in ("en", "es", "fr", "de", "ro", "sq", "tr", "ar", "fa", "zh", "uk", "ru"):
        reset("gp")
        try:
            rx = brain._rx_text({"prescription_number": "P1", "procedure": "VIS-FIS", "priority": "B"},
                                en=code == "en", lang=code)
        except TypeError:
            rx = ""
        check("rx text", code, rx)
    print(f"{sum(results)}/{len(results)} first lines in the citizen's language")
    print("ALL OK" if all(results) else "SOME FAILED")
    raise SystemExit(0 if all(results) else 1)


asyncio.run(main())
