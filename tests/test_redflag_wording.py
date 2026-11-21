"""Red-flag wording: real emergencies escalate, everyday phrases do not (audit 21 Nov, findings 1-3).

Ported to brain-gp: gp and consultorio run the wide net; a false hit ends the journey with 112.
Run: <city python> -I tests/test_redflag_wording.py [asst/_shared]   (exit 1 on any FAIL)
"""
import os
import sys

_arg = sys.argv[1] if len(sys.argv) > 1 else "asst/_shared"
sys.path.insert(0, os.path.dirname(_arg) if _arg.endswith(".py") else _arg)  # gate passes the brain file
import agent as A  # noqa: E402
WIDE = True  # every role runs the wide net on free text (R2.1)
from city_sdk.city import Message  # noqa: E402

ESCALATE = [
    "I have had back pain for a week and my legs are numb",
    "my heart is racing and my chest hurts",
    "pain in my chest since this morning",
    "my legs feel weak and keep giving way",
    "I can't pee since yesterday",
    "I can't control my bladder anymore",
    "I'm out of breath just sitting here",
    "I feel like I'm going to pass out",
    "Sono incinta di 10 settimane e ho perdite di sangue",
    "incinta, crampi fortissimi da stamattina",
    "Non riesco a parlare bene, mi si è storta la bocca",
    "my speech is slurred and my arm is weak",
    "dolore al petto che va al braccio sinistro",
    "sto per morire, mi manca l'aria",
    "mi voglio uccidere",
    "ho avuto una crisi epilettica stamattina",
    # real speech signs still escalate after the language carve-out
    "my husband suddenly can't speak and his face droops",
    "she couldn't talk for ten minutes yesterday",
    "I can't speak properly since this morning",
    "mi voglio uccidere",
]
HARMLESS = [
    "I can't speak Italian well, an English-speaking doctor please",
    "I cannot speak Italian, sorry",
    "Parlo male l'italiano",
    "Ho difficoltà a parlare italiano",
    "the 9am slot fits",
    "That fits my schedule",
    "I can't see the offer you sent",
    "Sto morendo di caldo",
    "I'm going to die of boredom",
    "I'm dying to see the doctor, when is the slot?",
    "Vorrei un controllo del cuore, mio padre è morto di infarto",
    "non ho mai avuto dolore al petto né svenimenti",
    "Sono incinta di 8 settimane, vorrei un primo colloquio, nessun sanguinamento",
    "vorrei un test di gravidanza, ho un ritardo e un po' di crampi",
    "ho perdite bianche, vorrei una visita ginecologica, non sono incinta",
    "Buongiorno, ho mal di schiena da una settimana",
]

# Non-native citizens say how well they speak, read or understand: never an emergency (gp, consultorio).
LANG = [
    "Non parlo bene italiano", "parlo poco italiano, scusate", "Non riesco a parlare bene l'italiano",
    "scusate il mio italiano", "non capisco bene l'italiano", "I don't speak Italian", "my Italian is bad",
    "sorry for my bad Italian", "I can't understand Italian", "I can't read Italian", "can you speak English?",
    "I can't talk now, I'm at work", "I can't talk long", "I can't see the doctor before Monday",
    "I can't speak much Italian", "I have trouble speaking Italian", "difficulty speaking Italian, English please",
    "no hablo italiano", "hablo muy poco italiano", "mi italiano es malo", "no puedo hablar bien italiano",
    "Ich spreche kein Italienisch", "mein Italienisch ist schlecht", "ich kann nicht gut Italienisch sprechen",
    "je ne parle pas italien", "je parle mal italien", "nu vorbesc italiana", "vorbesc putin italiana",
    "nuk flas italisht", "não falo italiano", "falo mal italiano", "я не розмовляю італійською",
    "я плохо говорю по-итальянски", "我不会说意大利语", "لا أتكلم الإيطالية", "من ایتالیایی بلد نیستم",
    "man italiaei balad nistam", "ana la atakallam italiano", "Hindi ako marunong mag-Italian",
    "sisemi Kiitaliano vizuri", "मुझे इतालवी नहीं आती", "আমি ইতালিয়ান বলতে পারি না",
]
HARMLESS = HARMLESS + LANG


def hit(text):
    m = Message(type="chat", sender="citizen:x", msg_id="m", need_id="n", body={"text": text}, recipient="bc")
    return bool(A._emergency(m, wide=WIDE))


fails = 0
for want, texts in ((True, ESCALATE), (False, HARMLESS)):
    for text in texts:
        if hit(text) != want:
            fails += 1
            print(f"FAIL want {'escalate' if want else 'no escalation'}: {text}")
print(f"{fails} FAIL of {len(ESCALATE) + len(HARMLESS)}")
sys.exit(1 if fails else 0)
