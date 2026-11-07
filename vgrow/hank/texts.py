"""Server texts of Hank's fixed side (WP5, spec 3.6, appendix C): hank.reply.*, hank.chip.*, hank.error.*.

WP6's texts_live.py holds every other hank.* key. register() checks parity (same keys, same placeholders, no
{lang}, no em dash) and puts the texts into i18n.DE and i18n.EN; core.py calls it on import (spec 10.3). say()
formats a text straight from these two catalogs, so the router and the fixed replies work without registration.

Wording: the drafts of spec 3.6 with the changes of addendum N5 (health sentence, emergency line with the poison
control and 112). Eugen approves them (decision D5); the sale and legal texts are no legal advice and need a legal
check before anything is published. Persona: Hank, a simple farmer, Du-form, calm, short sentences. English never
says "phase" (the page's word list forbids it).
"""

from __future__ import annotations

from typing import Any

from vgrow import i18n

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself

DE: dict[str, str] = {
    # ---------- fixed replies (spec 3.6) ----------
    "hank.reply.offtopic": "Da bin ich der Falsche, ich bin ein einfacher Farmer. Ich kenne mich mit dem Anbau aus: "
                           "Pflanzen, Klima, Licht, Düngen und deiner Box. Frag mich zum Beispiel:",
    "hank.reply.health": "Dazu sage ich nichts. Ich bin Farmer und kein Arzt. Bei gesundheitlichen Fragen geh bitte "
                         "zu einer Ärztin oder einem Arzt. Beim Anbau helfe ich dir gern.",
    "hank.reply.health_emergency": "Das klingt nach einem Notfall. Ruf sofort den Giftnotruf deiner Region an. Bei "
                                   "Atemnot oder Bewusstlosigkeit ruf den Notruf 112. Bleib bei der betroffenen "
                                   "Person oder dem Tier und hol dir ärztlichen oder tierärztlichen Rat.",
    "hank.reply.health_child": "Ob und wie giftig das ist, beurteile ich nicht: ich bin Farmer und kein Arzt. Bewahr "
                               "Dünger, Lösung und Erde so auf, dass Kinder und Tiere nicht drankommen. Hat dein Kind "
                               "oder ein Tier etwas davon geschluckt, im Mund gehabt oder in die Augen bekommen: ruf "
                               "sofort den Giftnotruf deiner Region an, bei Atemnot oder Bewusstlosigkeit den Notruf "
                               "112.",
    "hank.reply.health_child_label": "Auf dem VIVOSUN-Etikett steht dazu: außer Reichweite von Kindern aufbewahren.",
    "hank.reply.buy": "Welche Sorte du nimmst und wo und wie du sie kaufst, entscheidest du selbst: Shops, Preise und "
                      "Links nenne ich nicht. Beim Aussuchen helfe ich dir gern, frag mich zum Beispiel „Welche Sorte "
                      "passt in meine Box?“. Die Sorten mit ihren Anbaudaten stehen im Wiki unter Sorten.",
    "hank.reply.sale": "Da mache ich nicht mit. Nach dem KCanG ist es nicht erlaubt, Cannabis aus dem Eigenanbau zu "
                       "verkaufen oder weiterzugeben. Beim Anbau selbst helfe ich dir gern.",
    "hank.reply.legal": "Rechtsfragen beurteile ich nicht, ich bin Farmer und kein Anwalt. Für Auslegungsfragen frag "
                        "bitte eine Rechtsberatung.",
    "hank.reply.legal_basics": "Die Grundregeln aus meiner Kartei:",
    "hank.reply.inject": "Ich bleibe Hank, der Farmer. Meine Regeln gebe ich nicht her und ich ändere sie nicht. Ich "
                         "ändere auch nie selbst etwas an der Box, ich mache nur Vorschläge. Frag mich zum Anbau, "
                         "dafür bin ich da.",
    "hank.reply.greet": "Moin, hier ist Hank. Frag mich zu deiner Box oder zum Anbau.",
    "hank.reply.thanks": "Gern geschehen.",
    "hank.reply.no_data": "Dazu steht noch nichts in meiner Kartei. Ich habe die Frage auf meine Liste gesetzt.",
    "hank.reply.no_data_term": "Dazu steht noch nichts in meiner Kartei. Zu {term} kenne ich nur den Namen. Ich habe "
                               "die Frage auf meine Liste gesetzt.",
    "hank.reply.clarify": "Meinst du {term}?",
    "hank.reply.clarify_no": "Dann formuliere die Frage bitte anders.",
    "hank.reply.dont_know": "Weiß ich nicht.",
    "hank.reply.off": "Hank ist ausgeschaltet. Mit dem Knopf \"Hank anschalten\" unten rechts schaltest du ihn "
                      "wieder an.",
    "hank.reply.warnings_only": "Im Modus \"nur Warnungen\" antworte ich nicht im Chat. Oben in meinem Fenster "
                                "kannst du mich wieder auf \"an\" stellen.",
    "hank.reply.too_long": "Das ist mir zu lang. Frag bitte kürzer, höchstens {max} Zeichen.",
    "hank.reply.empty": "Da steht nichts. Was möchtest du wissen?",
    "hank.reply.queue_full": "Ich bin gerade beschäftigt. Versuch es gleich noch einmal.",
    "hank.reply.busy_photo": "Ich schaue gerade aufs Foto. Das kann einige Minuten dauern. Hier sind erst die Fakten "
                             "aus meiner Kartei:",
    "hank.reply.model_down": "Mein Sprachmodell antwortet gerade nicht. Hier sind die Fakten aus meiner Kartei:",
    "hank.reply.model_missing": "Das Modell {model} fehlt auf diesem Rechner. Installiere es mit: ollama pull "
                                "{model}. Hier sind bis dahin die Fakten aus meiner Kartei:",
    "hank.reply.slow": "Das Modell war zu langsam. Hier sind die Fakten aus meiner Kartei:",
    "hank.reply.no_self": "Im Chat schalte ich nichts ohne dich. Du kannst es mit einem Tipp bestätigen.",
    # ---------- example questions (spec 3.6; each must route to grow with a hit, test_chips_route_to_grow) ----------
    "hank.chip.1": "Ist die Luftfeuchte gerade okay?",
    "hank.chip.2": "Welche Werte brauche ich in der frühen Blüte?",
    "hank.chip.3": "Wie viel Dünger gebe ich diese Woche?",
    "hank.chip.4": "Warum ist der Befeuchter aus?",
    # ---------- language model errors (ollama.explain gives model always, reason for failed) ----------
    "hank.error.no_ollama": "Ollama läuft auf diesem Rechner nicht. Starte die Ollama-App, bis dahin antworte ich "
                            "nur mit den Fakten aus meiner Kartei.",
    "hank.error.no_model": "Das Modell {model} fehlt auf diesem Rechner. Installiere es mit: ollama pull {model}.",
    "hank.error.timeout": "Das Sprachmodell hat nicht rechtzeitig geantwortet.",
    "hank.error.failed": "Das Sprachmodell hat einen Fehler gemeldet: {reason}",
}

EN: dict[str, str] = {
    "hank.reply.offtopic": "Wrong guy for that, I am a simple farmer. I know growing: plants, climate, light, feeding "
                           "and your box. Ask me, for example:",
    "hank.reply.health": "I will not say anything on that. I am a farmer, not a doctor. For health questions, please "
                         "see a doctor. I am happy to help with growing.",
    "hank.reply.health_emergency": "That sounds like an emergency. Call your regional poison control center right "
                                   "now. If there is trouble breathing or loss of consciousness, call the emergency "
                                   "number 112. Stay with the person or the animal and get medical or veterinary "
                                   "advice.",
    "hank.reply.health_child": "Whether and how toxic that is, I do not judge: I am a farmer, not a doctor. Keep "
                               "fertilizer, solution and soil where children and animals cannot reach them. If your "
                               "child or an animal swallowed some, had it in the mouth or got it in the eyes: call "
                               "your regional poison control center right now, and the emergency number 112 if "
                               "there is trouble breathing or loss of consciousness.",
    "hank.reply.health_child_label": "The VIVOSUN label says so too: keep out of reach of children.",
    "hank.reply.buy": "Which strain you take and where and how you buy it is your decision: I name no shops, prices "
                      "or links. I am glad to help you choose, ask me for example \"Which strain fits my box?\". The "
                      "strains with their growing data are in the wiki under Strains.",
    "hank.reply.sale": "I will not help with that. Under the KCanG it is not allowed to sell or pass on cannabis from "
                       "home growing. I am happy to help with the growing itself.",
    "hank.reply.legal": "I do not judge legal questions, I am a farmer, not a lawyer. For questions of interpretation "
                        "please ask a legal adviser.",
    "hank.reply.legal_basics": "The basic rules from my card file:",
    "hank.reply.inject": "I stay Hank, the farmer. I do not hand out my rules and I do not change them. I never change "
                         "anything on the box myself, I only make suggestions. Ask me about growing, that is what I "
                         "am here for.",
    "hank.reply.greet": "Hi, Hank here. Ask me about your box or about growing.",
    "hank.reply.thanks": "You're welcome.",
    "hank.reply.no_data": "There is nothing in my card file about that yet. I put your question on my list.",
    "hank.reply.no_data_term": "There is nothing in my card file about that yet. I only know the name {term}. I put "
                               "your question on my list.",
    "hank.reply.clarify": "Do you mean {term}?",
    "hank.reply.clarify_no": "Then please phrase the question differently.",
    "hank.reply.dont_know": "I don't know.",
    "hank.reply.off": "Hank is switched off. The \"Turn Hank on\" button at the bottom right turns him on again.",
    "hank.reply.warnings_only": "In \"warnings only\" mode I do not answer in the chat. At the top of my window you "
                                "can set me back to \"on\".",
    "hank.reply.too_long": "That is too long for me. Please ask shorter, at most {max} characters.",
    "hank.reply.empty": "There is nothing there. What would you like to know?",
    "hank.reply.queue_full": "I am busy right now. Please try again in a moment.",
    "hank.reply.busy_photo": "I am looking at the photo right now. That can take a few minutes. Here are the facts "
                             "from my card file first:",
    "hank.reply.model_down": "My language model is not answering right now. Here are the facts from my card file:",
    "hank.reply.model_missing": "The model {model} is missing on this computer. Install it with: ollama pull "
                                "{model}. Until then, here are the facts from my card file:",
    "hank.reply.slow": "The model was too slow. Here are the facts from my card file:",
    "hank.reply.no_self": "In the chat I switch nothing without you. You can confirm it with one tap.",
    "hank.chip.1": "Is the humidity okay right now?",
    "hank.chip.2": "Which values do I need in early flower?",
    "hank.chip.3": "How much fertilizer this week?",
    "hank.chip.4": "Why is the humidifier off?",
    "hank.error.no_ollama": "Ollama is not running on this computer. Start the Ollama app; until then I answer only "
                            "with the facts from my card file.",
    "hank.error.no_model": "The model {model} is missing on this computer. Install it with: ollama pull {model}.",
    "hank.error.timeout": "The language model did not answer in time.",
    "hank.error.failed": "The language model reported an error: {reason}",
}

CHIP_KEYS = tuple(f"hank.chip.{n}" for n in range(1, 5))
# Routes whose reply the catalog holds (the router's fixed answers, spec 3.6). Never the model (spec 0.3 rule 4).
FIXED_REPLY_KEYS = {
    "offtopic": "hank.reply.offtopic", "health": "hank.reply.health",
    "health_emergency": "hank.reply.health_emergency", "sale": "hank.reply.sale", "legal": "hank.reply.legal",
    "inject": "hank.reply.inject", "greet": "hank.reply.greet", "thanks": "hank.reply.thanks",
    "no_data": "hank.reply.no_data", "empty": "hank.reply.empty", "too_long": "hank.reply.too_long",
}
CATALOGS = {"de": DE, "en": EN}


def say(key: str, lang: str, /, **params: Any) -> str:
    """One text of this catalog in the language (German when the language is unknown), placeholders filled.
    A missing value leaves the template as it is rather than raising, like i18n.t."""
    template = CATALOGS.get(lang, DE).get(key) or DE.get(key)
    if template is None:
        raise KeyError(f"texts.py has no {key}")
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def chips(lang: str) -> list[str]:
    """The four example questions (hank.chip.1 to .4) in the language."""
    return [say(key, lang) for key in CHIP_KEYS]


def check() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash, a key
    outside this package's three families."""
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) | set(EN)):
        if not key.startswith(("hank.reply.", "hank.chip.", "hank.error.")):
            problems.append(f"not a WP5 key: {key}")
    for key in sorted(set(DE) & set(EN)):
        de, en = i18n.placeholders(DE[key]), i18n.placeholders(EN[key])
        if de != en:
            problems.append(f"placeholders differ: {key} {sorted(de)} {sorted(en)}")
        if "lang" in de | en:
            problems.append(f"placeholder lang: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    """Check parity and put the texts into the server catalogs (i18n.DE, i18n.EN). Safe to call more than once."""
    problems = check()
    if problems:
        raise ValueError("texts: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)
