"""Text basics for Hank, pure Python and without dependencies (spec 2.2, 4.6, 5.3).

- fold(): lower case, ss for the sharp s, umlauts and accents to plain letters, ae/oe/ue to a/o/u except after
  a vowel or q (the rule of the Lucene German normalization [R10] as the spec states it). "duengen", "düngen"
  and "Dünger" all reach the same stem.
- tokens(): split a text into search words: hyphen words give the joined form and the parts ("Bio-Bloom" gives
  "biobloom", "bio", "bloom"), ratios like 12/12 stay one token and are also split into their numbers, stop words
  and one-letter words drop out, words are stemmed by a small in-house light stemmer.
- grams(): 3-grams with a word-boundary pad, for typos and compound words.
- detect_lang(): stop-word count German against English, a tie keeps the page language.
- estimate_tokens(): characters / 2.8, rounded up (spec 4.6; adjust after the first run on the Mac).
- parse_numbers(): numbers with canonical units, decimal comma or point by language, ranges, ratios, dates and the
  number words of the addendum N1.

Input to stem_word() must already be folded; tokens() does both.
"""

from __future__ import annotations

import math
import re
import unicodedata

from vgrow.hank.types import Num

CHARS_PER_TOKEN = 2.8  # spec 4.6: cautious estimate, replaced by the measured factor after WP11

_VOWELS = frozenset("aeiouy")


def fold(s: str) -> str:
    """Lower case, sharp s to ss, umlauts and accents to their base letter, then ae/oe/ue to a/o/u unless the
    letter before is a vowel or q ("Feuer" and "Queen" keep their ue). Everything else stays as it is."""
    s = s.lower().replace("ß", "ss")
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in "aou" and i + 1 < n and s[i + 1] == "e" and (i == 0 or s[i - 1] not in _VOWELS and s[i - 1] != "q"):
            out.append(ch)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _w(text: str) -> frozenset[str]:
    return frozenset(fold(word) for word in text.split())


# ---------- word lists ----------

# Stop words: dropped from search tokens and counted for the language. Written naturally, stored folded.
STOP_DE = _w("""
aber alle allem allen aller alles also am an andere anderem anderen anderer anderes auch auf aus bei beim
bin bis bist bitte da dabei dadurch dafür dagegen daher damit danach dann daran darauf darf darfst darin darum
das dass davon dazu dein deine deinem deinen deiner dem den denn der des dessen dich die dies diese diesem
diesen dieser dieses dir doch dort du durch eigentlich ein eine einem einen einer eines einfach er es etwa
etwas euch euer eure für ganz gar gegen gerade gern gerne gibt geht hab habe haben hast hat hatte hätte hier
ich ihm ihn ihnen ihr ihre ihrem ihren ihrer im immer in ins ist ja je jede jedem jeden jeder jedes jetzt
kann kannst kein keine keinem keinen keiner keines können könnte mal man manche manchmal mehr mein meine
meinem meinen meiner meines mich mir mit muss musst müssen nach nein nicht nichts noch nun nur ob oder ohne pro
schon sehr sein seine seinem seinen seiner sich sie sind so soll sollen sollte solltest sondern sonst
sowie über um und uns unser unsere unter viel viele vom von vor war waren warum was weil welche welchem
welchen welcher welches wenn wer werde werden wie wieder wieso wieviel will wir wird wirklich wo wohl wurde
zu zum zur
""")
STOP_EN = _w("""
a about above after again against all am an and any are as at be because been before being below between
both but by can cannot could did do does doing dont down during each few for from further had has have
having he her here hers him his how i if im in into is isnt it its itself ive just me more most my myself
no nor not now of off on once only or other our ours out over own per please same she should so some such than
that the their them then there these they this those through to too under until up very was were what
whats when where which while who whom why will with would you your yours yourself
""")
STOP = STOP_DE | STOP_EN
_DETECT_DE = STOP_DE - STOP_EN  # words of both lists ("in", "so", "was", ...) say nothing about the language
_DETECT_EN = STOP_EN - STOP_DE

# Everyday words that are never a typo of a plant, device or product name (spec 2.5, NOT_TYPOS). This is a
# guard for the typo matcher only: it does not decide what is a growing question. Acceptance 23.10.2026 (review 3,
# L4): words of safety questions that the matcher turned into names (child to chili, charge to chard, print to
# prune, gegessen and genießen to Gießen, legal to leaf, Niederlanden to Niederbinden).
COMMON_DE = _w("""
hallo hi hey moin servus tschuss danke dank merci ok okay super prima
lang lange langen langer länger kurz kurze schnell langsam bald später früher oft selten genug wenig weniger
gleich sofort erst zuerst danach vorher nachher morgens mittags nachts täglich wöchentlich plötzlich normal
normale richtig falsch besser schlechter gut gute guten schlecht schön alt alte jung niedrig laut leise schwer
leicht stark schwach voll volle offen genau ungefähr fast kaum nie niemals meist meistens eher lieber
vielleicht sicher wahrscheinlich möglich nötig wichtig egal toll schlimm schade leider letzte letzten nächste
nächsten erste ersten zweite dritte viertel ganze halbe beide beiden andere selbst allein zusammen weiter
weit nah nahe oben unten vorne hinten links rechts innen außen drinnen draußen überall irgendwie irgendwas
machen mache macht gemacht geben gebe gegeben nehmen nehme nimmt kommen komme kommt gehen gehe sehen sehe
sieht seh wissen weiß sagen sage sagt finden finde findet bleiben bleibt stehen steht liegen liegt lassen
lasse lässt brauchen brauche braucht brauchst wachsen wächst blühen blüht ernten gießen gieße gießt stellen
stelle stellt drehen dreh dreht schalten schalte schaltet messen misst zeigen zeigt helfen hilft fragen fragt
denken denke glauben glaube meinen meint wollen möchte möchten dürfen ziehen zieht fallen fällt hängen hängt
wirken warten warte laufen läuft starten stoppen kaufen legen setzen halten hält tun tut passen passt reicht
reichen fehlen fehlt ändern öffnen schließen füllen wechseln prüfen schauen schau kriegen bekommen bekommt
riechen riecht aussehen sollte könnte würde wäre gewesen geworden worden lesen schreiben spielen kochen backen
backe schlafen arbeiten fahren fliegen bringen holen suchen sucht kennen kenne lernen leben wohnen zahlen
kosten kostet bauen putzen waschen trinken essen esse
gegessen getrunken geschluckt genießen genieße genießt genossen erlaubt verboten legal illegal ausland
niederlande niederlanden holland wende wenden anwenden übernimm übernehmen
gegossen begossen gedüngt gemessen gepflanzt getopft umgetopft geerntet geschnitten gewachsen gesprüht gelüftet
getrocknet gewässert gekauft gesät gekeimt gestutzt entlaubt
internet computer laptop online email app programm code film musik lied rezept rezepte pizza pasta nudeln
soße sauce pesto suppe kekse wein bier tee saft schule mathe deutsch englisch sport fußball fahrrad steuer
steuern versicherung miete konto termin
problem grund teil weise fall hälfte ort raum ding leute mensch menschen familie hilfe idee tipp tipps rat
plan ziel wert werte grad mal male zeitpunkt moment augenblick sekunde gestern übermorgen woche wochenende
montag dienstag mittwoch donnerstag freitag samstag sonntag januar februar märz april mai juni juli august
september oktober november dezember stück stücke kilo paket kasse rechnung laden händler post brief nummer
gerät geräte maschine kiste rolle schachtel tüte deckel griff rahmen stange ecke mitte rest reste hälfte
fehler frage fragen antworten beispiel bild bilder video foto fotos meinung erfahrung gefühl sorge sorgen
abend abends ablage absicht ahnung alter angst anfang antwort apfel arbeit arm arzt ast auge augen auto bad
ball bank bauch baum becher beet bein berg bett beutel bild birne blatt blätter blau blume blumen boden
bohne brief brille brot bruder buch butter dach decke deckel dose dosis draht dunkel durst ecke ei eimer
ende erde ernte essen farbe faser fehler feld fenster fest feucht feuer finger flasche fleisch fliege flur
foto frage frau freund frucht früh frühling fuß futter gabel garten gast geld gelb gemüse geruch glas glück
gras grau groß grün gurt haar hals hand handy haus haut hecke heft heiß hell hemd herbst herz heute himmel
hitze hoch hof holz honig hose hund hunger insel jacke jahr kabel kaffee kalt kälte kamera kanne kante karte
kasten katze kerze kette kind kinder kiste klein knopf knospe koch kohl kopf korb kraft kranz kreis küche
kuchen kugel kunde kurz lager land lappen laub laut leben leer leiter licht liebe lied linie loch löffel luft
lust mann mantel markt mauer meer mehl menge messer milch minute mittag monat mond morgen motor mund mutter
nacht nachbar nadel nagel name nase nass nebel netz neu nudel obst ofen ohr papier pause pinsel platz preis
puppe rad rampe rand regal regen reis reise rest ring rock rohr rose rot ruhe sache saft salz sand satz
schale schalter schatten schaum schere schlauch schloss schnee schrank schuh schule schwarz see seife seite
sessel sieb sonne spaß spiel stadt stab stecker stein stelle stiel stift stock strom stück stuhl stunde
suppe tag tal tasche tasse teller test tief tier tisch tochter topf torte traum trocken trockenen tropfen
tuch tür uhr urlaub vater vogel wagen wald wand warm wärme wasser weg wein weiß welt wetter wind winter woche
wolke wort wunsch würfel wurzel zahl zahn zange zaun zeit zelt zettel zimmer zucker zug
""")
COMMON_EN = _w("""
hello hey thanks thank cheers bye okay ok great
long longer short quick quickly slow slowly soon later earlier often rarely enough little less more same
right wrong better worse nice old new young low high loud quiet heavy easy strong weak full empty open
close exact exactly about almost never always sometimes usually rather maybe sure likely possible needed
important fine bad sad last next first second third whole half both other alone together far near top
bottom front back left right inside outside everywhere somehow make makes made give gives gave take takes
took come comes came go goes went see sees saw know knows knew say says said find finds found stay stays
stand stands lie lies let lets need needs needed grow grows grew bloom blooms set sets turn turns switch
measure show shows help helps ask asks think thinks believe mean means want wants wanted like likes look
looks wait waits run runs start stop buy put keep keeps fit fits change open close fill check get gets got
smell smells taste read write play cook bake sleep drive fly bring fetch search know learn live pay cost
build clean wash drink eat order orders ordered deliver delivery ship send state states
child children charge charging print printed printer legal illegal law allowed party friends
internet computer laptop online email app code program string python java movie music song recipe pizza pasta sauce pesto soup wine beer tea juice school math maths sport football bike tax taxes rent insurance
account appointment
problem reason part case place thing people person family idea tip tips advice
plan goal value values degree moment second yesterday weekend monday tuesday wednesday thursday friday
saturday sunday piece pieces package shop store letter number device machine roll lid handle frame corner
middle question questions example image video photo photos opinion experience feeling worry
answer apple bag ball bed bit blue board book bottle bowl box bread brown cable cake camera can car card
chair chips cold color colour cup day door dry during fast field fire fish floor food friend garden glass
gold good green hand hat head heat help home hot hour house kid kitchen lamp leaf leaves light line list
long mail make milk minute money month morning name net night note open page paper pause pen phone picture
place plate point pot rain red rest ring road room salt sand school shop short side sign small soil sound
spoon star step stone sugar summer sun table test thing time today tomorrow tool tree trip trips wall warm
watch water week wet white wind window winter wood word work year yellow
""")
COMMON = COMMON_DE | COMMON_EN

# Number words (addendum N1). "ein/eine" and "one/a/an" count only right before a unit.
_NUMBER_WORDS_DE = {
    "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
    "elf": 11, "zwolf": 12, "zwanzig": 20, "dreissig": 30, "vierzig": 40, "funfzig": 50, "hundert": 100,
    "tausend": 1000, "halb": 0.5, "halbe": 0.5, "halben": 0.5, "halber": 0.5, "halbes": 0.5,
    "doppelt": 2, "doppelte": 2, "doppelten": 2, "doppelter": 2, "doppeltes": 2,
}
_NUMBER_WORDS_EN = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
    "thousand": 1000, "half": 0.5, "double": 2, "twice": 2,
}
_ONE_BEFORE_UNIT_DE = frozenset({"ein", "eine", "einen", "einem", "einer", "eines"})
_ONE_BEFORE_UNIT_EN = frozenset({"one", "a", "an"})

# Canonical units. Keys are spellings after a number (matched case-insensitively, longest first, a word unit
# needs a word end after it); values are the canonical unit stored in Num.unit.
_UNIT_SPELLINGS: dict[str, str] = {
    "µmol/m²/s": "µmol", "µmol/m2/s": "µmol", "umol/m²/s": "µmol", "umol/m2/s": "µmol", "µmol": "µmol",
    "\u03bcmol": "µmol", "umol": "µmol", "ppfd": "µmol",
    "ml pro liter": "ml/l", "ml pro l": "ml/l", "ml per liter": "ml/l", "ml per litre": "ml/l", "ml/liter": "ml/l",
    "ml/l": "ml/l",
    "ms/cm": "mS/cm", "ms": "mS/cm", "µs/cm": "µS/cm", "us/cm": "µS/cm", "µs": "µS/cm",
    "°c": "°C", "° c": "°C", "grad celsius": "°C", "grad": "°C", "degrees celsius": "°C", "degrees": "°C",
    "celsius": "°C", "°f": "°F", "fahrenheit": "°F", "°": "°C",
    "kpa": "kPa",
    "%": "%", "prozent": "%", "percent": "%", "per cent": "%",
    "milliliter": "ml", "millilitre": "ml", "ml": "ml",
    "litern": "l", "liter": "l", "litre": "l", "liters": "l", "litres": "l", "l": "l",
    "zentimeter": "cm", "centimeter": "cm", "centimetre": "cm", "cm": "cm", "mm": "mm",
    "m²": "m²", "meter": "m", "metre": "m", "m": "m",
    "kwh": "kWh", "kw": "kW", "watt": "W", "w": "W",
    "kg": "kg", "gramm": "g", "grams": "g", "gram": "g", "g": "g",
    "ppm": "ppm", "lux": "lx", "lx": "lx",
    "stunden": "h", "stunde": "h", "std": "h", "hours": "h", "hour": "h", "hrs": "h", "hr": "h", "h": "h",
    "minuten": "min", "minute": "min", "minutes": "min", "mins": "min", "min": "min",
    "sekunden": "s", "sekunde": "s", "sek": "s", "seconds": "s", "second": "s", "secs": "s", "sec": "s", "s": "s",
    "tagen": "d", "tage": "d", "tages": "d", "tag": "d", "days": "d", "day": "d",
    "wochen": "wk", "woche": "wk", "weeks": "wk", "week": "wk",
    "monaten": "mo", "monate": "mo", "monat": "mo", "months": "mo", "month": "mo",
    "jahren": "yr", "jahre": "yr", "jahr": "yr", "years": "yr", "year": "yr",
    "stufen": "step", "stufe": "step", "steps": "step", "step": "step", "levels": "step", "level": "step",
    "punkten": "pt", "punkte": "pt", "punkt": "pt", "points": "pt", "point": "pt",
    "euro": "EUR", "eur": "EUR", "€": "EUR",
}
UNIT_NAMES = frozenset(_UNIT_SPELLINGS.values())
_UNIT_RE = re.compile(
    r"[\s\u00a0\u202f]*("
    + "|".join(re.escape(k) for k in sorted(_UNIT_SPELLINGS, key=len, reverse=True))
    + r")(?![^\W\d_])",
    re.IGNORECASE,
)


# ---------- tokens ----------

_TOKEN_RE = re.compile(
    r"\d+/\d+"  # ratio: 12/12, 1/4
    r"|\d+(?:[.,]\d+)*"  # number: 4, 0,8, 1.400
    r"|[^\W\d_][^\W_]*(?:['\u2019][^\W_]+)*(?:-[^\W_]+(?:['\u2019][^\W_]+)*)*"  # word, hyphen word, CO2
)
_S_END = frozenset("bdfghklmnrt")
_ST_END = frozenset("bdfghklmnt")


def _stem_de(w: str) -> str:
    """Light German stemmer in the manner of Savoy and the Lucene GermanLightStemmer, plus Wurzeln -> Wurzel."""
    n = len(w)
    if n > 5 and w.endswith("ern"):
        w = w[:-3]
    elif n > 4 and w[-2] == "e" and w[-1] in "mnrs":
        w = w[:-2]
    elif n > 3 and w[-1] == "e":
        w = w[:-1]
    elif n > 3 and w[-1] == "s" and w[-2] in _S_END:
        w = w[:-1]
    elif n > 4 and w.endswith(("ln", "rn")):
        w = w[:-1]
    n = len(w)
    if n > 5 and w.endswith("est"):
        w = w[:-3]
    elif n > 4 and w[-2] == "e" and w[-1] in "rn":
        w = w[:-2]
    elif n > 4 and w.endswith("st") and w[-3] in _ST_END:
        w = w[:-2]
    return w


def _has_vowel(w: str) -> bool:
    return any(ch in _VOWELS for ch in w)


def _undouble(w: str) -> str:
    if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "lsz":
        return w[:-1]
    return w


def _stem_en(w: str) -> str:
    """Light English stemmer: plurals, -ing, -ed, a final e."""
    if len(w) <= 3:
        return w
    if len(w) > 4 and w.endswith(("ies", "ied")):
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("es") and w[:-2].endswith(("s", "x", "z", "ch", "sh", "o")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if len(w) > 5 and w.endswith("ing") and _has_vowel(w[:-3]):
        w = _undouble(w[:-3])
    elif len(w) > 4 and w.endswith("ed") and not w.endswith("eed") and _has_vowel(w[:-2]):
        w = _undouble(w[:-2])
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


# German loanwords the light stemmer would cut into another word: "Timer" became "tim", the stem of the English
# "time", so "When do I feed the next time?" named the timer (screens review, finding 7).
_DE_KEEP = {"timer": "timer", "timers": "timer", "timern": "timer"}


def stem_word(word: str, lang: str | None = None) -> str:
    """Stem one folded word. "en" uses the English rules, anything else the German ones. Words with digits
    (co2, s17) and words of three letters or fewer stay as they are."""
    if len(word) <= 3 or any(ch.isdigit() for ch in word) or not word.isalpha():
        return word
    if lang != "en" and word in _DE_KEEP:
        return _DE_KEEP[word]
    return _stem_en(word) if lang == "en" else _stem_de(word)


def tokens(s: str, *, stem: bool = True, lang: str | None = None, stop: bool = True) -> list[str]:
    """Search tokens of a text, in order, duplicates kept (BM25 counts them).

    Numbers keep a decimal point ("0,8" gives "0.8"), ratios stay one token and add their parts ("12/12" gives
    "12/12", "12", "12"), a hyphen word adds its joined form before its parts. With stop=True stop words and
    one-letter words drop out. With stem=True words are stemmed with the rules of `lang` (German by default)."""
    out: list[str] = []
    for match in _TOKEN_RE.finditer(fold(s)):
        tok = match.group(0)
        if tok[0].isdigit():
            if "/" in tok:
                out.append(tok)
                out.extend(tok.split("/"))
            else:
                out.append(tok.replace(",", "."))
            continue
        tok = tok.replace("'", "").replace("\u2019", "")
        parts = tok.split("-")
        words = ["".join(parts), *parts] if len(parts) > 1 else parts
        for word in words:
            if not word or stop and (len(word) == 1 or word in STOP):
                continue
            out.append(stem_word(word, lang) if stem else word)
    return out


def grams(s: str, n: int = 3, *, stop: bool = True) -> list[str]:
    """Letter n-grams of the folded, unstemmed words of a text, each word padded with "#" on both sides
    ("tomate" gives "#to", "tom", ..., "te#"). Numbers and ratios give no grams. Duplicates kept."""
    out: list[str] = []
    for word in tokens(s, stem=False, stop=stop):
        if word[0].isdigit():
            continue
        padded = f"#{word}#"
        if len(padded) <= n:
            out.append(padded)
        else:
            out.extend(padded[i:i + n] for i in range(len(padded) - n + 1))
    return out


_COLLOQUIAL_IS = frozenset({"is", "isn", "iss", "isses", "issn"})


def detect_lang(s: str, default: str = "de") -> str:
    """German or English by counting stop words that belong to only one of the two lists. A tie (also: no stop
    word at all) gives `default`, the page language; a default other than de/en counts as German."""
    words = tokens(s, stem=False, stop=False)
    de = sum(1 for w in words if w in _DETECT_DE)
    en = sum(1 for w in words if w in _DETECT_EN)
    if len(words) >= 2 and words[0] == "was" and words[1] in _COLLOQUIAL_IS:
        de += 2  # "was is lolipopping", "was isn dli": spoken German, not English "was" (review 1, S02)
    if de > en:
        return "de"
    if en > de:
        return "en"
    return default if default in ("de", "en") else "de"


def estimate_tokens(s: str) -> int:
    """Model tokens of a text, estimated on the safe side: characters / CHARS_PER_TOKEN, rounded up."""
    return math.ceil(len(s) / CHARS_PER_TOKEN) if s else 0


# ---------- numbers ----------

_NUM_RE = re.compile(r"(?<![^\W\d_])(?<![\d.,])\d+(?:[.,]\d+)*")
_WORD_RE = re.compile(r"[^\W\d_]+")
_RANGE_GAP_RE = re.compile(r"^[\s\u00a0\u202f]*(?:-|\u2013|bis zu|bis|to)[\s\u00a0\u202f]*$", re.IGNORECASE)
_AND_GAP_RE = re.compile(r"^[\s\u00a0\u202f]*(?:und|and)[\s\u00a0\u202f]*$", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"(?:zwischen|between)[\s\u00a0\u202f]*$", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"^\.[\s\u00a0\u202f]+[^\W\d_]")


def _number_values(raw: str, lang: str, after: str) -> list[float]:
    """Read one run of digits and separators. Decimal mark by language, the other mark only as a thousands
    separator before groups of exactly three digits. A German day.month. date and any run that fits neither
    reading (22.10.2026, 1.2.3) give their parts as whole numbers."""
    decimal, thousands = (",", ".") if lang == "de" else (".", ",")
    seps = [ch for ch in raw if ch in ".,"]
    groups = re.split(r"[.,]", raw)
    if not seps:
        return [float(raw)]
    if lang == "de" and len(seps) == 1 and seps[0] == "." and after.startswith(".") and len(groups[0]) <= 2 \
            and len(groups[1]) == 2 and 1 <= int(groups[0]) <= 31 and 1 <= int(groups[1]) <= 12:
        return [float(groups[0]), float(groups[1])]  # 22.10. is a date, not 22.1
    if len(seps) == 1:
        if seps[0] == thousands and len(groups[1]) == 3:
            return [float(groups[0] + groups[1])]
        return [float(groups[0] + "." + groups[1])]
    if decimal in seps and seps[-1] == decimal and all(sep == thousands for sep in seps[:-1]) \
            and all(len(g) == 3 for g in groups[1:-1]):
        return [float("".join(groups[:-1]) + "." + groups[-1])]
    if all(sep == thousands for sep in seps) and all(len(g) == 3 for g in groups[1:]):
        return [float("".join(groups))]
    return [float(g) for g in groups]


def _unit_after(s: str, pos: int) -> tuple[str, int]:
    match = _UNIT_RE.match(s, pos)
    if not match:
        return "", pos
    return _UNIT_SPELLINGS[match.group(1).lower()], match.end()


def parse_numbers(s: str, lang: str = "de", *, words: bool = True) -> list[Num]:
    """All numbers of a text in order, each with its canonical unit or "".

    - Decimal comma in German, decimal point in English; "1.400" is 1400 in German and 1.4 in English.
    - Ranges "45 bis 60 %", "45-60%", "45 to 60 %", "zwischen 45 und 60 %": both ends, the unit of the second end
      also goes to the first. Signs are ignored (45-60 is a range, never minus 60).
    - Ratios "18/6", "1/4" give their parts. A German "4." before a word is an ordinal and takes no unit.
    - Numbers glued to letters on the left (S17, CO2, F2, gemma4) are names, not numbers.
    - With words=True the number words of addendum N1 count too ("drei", "zwanzig", "halb", "doppelt"; "eine"
      only before a unit, "eine Woche" is 1 week)."""
    found: list[tuple[int, int, float, str]] = []  # start, end, value, unit
    for match in _NUM_RE.finditer(s):
        raw, start, end = match.group(0), match.start(), match.end()
        values = _number_values(raw, lang, s[end:end + 1])
        if _ORDINAL_RE.match(s, end) and lang == "de" and len(values) == 1:
            found.append((start, end, values[0], ""))
            continue
        if len(values) > 1 or s[end:end + 1] == "/":
            found.extend((start, end, value, "") for value in values)
            continue
        unit, unit_end = _unit_after(s, end)
        found.append((start, unit_end if unit else end, values[0], unit))
    if words:
        found.extend(_number_words(s, lang))
    found.sort(key=lambda item: item[0])
    for i in range(len(found) - 1):
        start, end, value, unit = found[i]
        nxt = found[i + 1]
        if unit or not nxt[3] or nxt[0] < end:
            continue
        gap = s[end:nxt[0]]
        if _RANGE_GAP_RE.match(gap) or _AND_GAP_RE.match(gap) and _BETWEEN_RE.search(s[:start]):
            found[i] = (start, end, value, nxt[3])
    return [Num(value, unit) for _, _, value, unit in found]


def _number_words(s: str, lang: str) -> list[tuple[int, int, float, str]]:
    tables: list[dict[str, float]] = []
    ones: set[str] = set()
    if lang != "en":
        tables.append(_NUMBER_WORDS_DE)
        ones |= _ONE_BEFORE_UNIT_DE
    if lang != "de":
        tables.append(_NUMBER_WORDS_EN)
        ones |= _ONE_BEFORE_UNIT_EN
    out: list[tuple[int, int, float, str]] = []
    for match in _WORD_RE.finditer(s):
        word = fold(match.group(0))
        unit, unit_end = _unit_after(s, match.end())
        value = next((table[word] for table in tables if word in table), None)
        if value is None and word in ones and unit:
            value = 1.0
        if value is None:
            continue
        out.append((match.start(), unit_end if unit else match.end(), float(value), unit))
    return out


def is_number_word(word: str) -> bool:
    """True for a number word that parse_numbers() reads (addendum N1: zwei to zwölf, zwanzig, hundert, halb, ...)."""
    folded = fold(word)
    return folded in _NUMBER_WORDS_DE or folded in _NUMBER_WORDS_EN


def ratios(s: str) -> list[str]:
    """Ratio tokens like "18/6" or "1/4", in order (spec 5.3: checked as a token and as numbers)."""
    return re.findall(r"(?<![\w/])\d+/\d+(?![\w/])", s)
