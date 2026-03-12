"""Phonetics constants and utilities used by the teaching assistant."""

from __future__ import annotations

import re

# ── Roman numeral → Arabic converter ─────────────────────────────────────────

_ROMAN_SCAN_RE = re.compile(r'\b([IVXLCDM]+)\b')
_ROMAN_VALID_RE = re.compile(
    r'^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', re.IGNORECASE
)
_ROMAN_VAL = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}


def roman_to_int(s: str) -> int | None:
    """Return the integer value of a Roman numeral string, or None if invalid.

    Single-character tokens are skipped to avoid false positives (e.g. the
    pronoun "I").
    """
    if len(s) < 2:
        return None
    if not _ROMAN_VALID_RE.match(s):
        return None
    result, prev = 0, 0
    for ch in reversed(s.upper()):
        curr = _ROMAN_VAL[ch]
        result += curr if curr >= prev else -curr
        prev = curr
    return result or None


def replace_roman_numerals(text: str) -> str:
    """Replace Roman numeral tokens in *text* with their Arabic equivalents."""
    return _ROMAN_SCAN_RE.sub(
        lambda m: str(n) if (n := roman_to_int(m.group(1))) is not None else m.group(0),
        text,
    )


# ── Whisper model / language catalogues ───────────────────────────────────────

WHISPER_MODELS: list[str] = [
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v1", "large-v2", "large-v3",
    "distil-large-v2", "distil-medium.en", "distil-small.en",
]

# Ordered dict: display name → BCP-47 code (None = auto-detect)
WHISPER_LANGUAGES: dict[str, str | None] = {
    "Auto-detect": None,
    "Afrikaans": "af", "Albanian": "sq", "Amharic": "am", "Arabic": "ar",
    "Armenian": "hy", "Assamese": "as", "Azerbaijani": "az", "Bashkir": "ba",
    "Basque": "eu", "Belarusian": "be", "Bengali": "bn", "Bosnian": "bs",
    "Breton": "br", "Bulgarian": "bg", "Burmese": "my", "Catalan": "ca",
    "Chinese": "zh", "Croatian": "hr", "Czech": "cs", "Danish": "da",
    "Dutch": "nl", "English": "en", "Estonian": "et", "Faroese": "fo",
    "Finnish": "fi", "French": "fr", "Galician": "gl", "Georgian": "ka",
    "German": "de", "Greek": "el", "Gujarati": "gu", "Haitian Creole": "ht",
    "Hausa": "ha", "Hawaiian": "haw", "Hebrew": "he", "Hindi": "hi",
    "Hungarian": "hu", "Icelandic": "is", "Indonesian": "id", "Italian": "it",
    "Japanese": "ja", "Javanese": "jw", "Kannada": "kn", "Kazakh": "kk",
    "Khmer": "km", "Korean": "ko", "Lao": "lo", "Latin": "la",
    "Latvian": "lv", "Lingala": "ln", "Lithuanian": "lt", "Luxembourgish": "lb",
    "Macedonian": "mk", "Malagasy": "mg", "Malay": "ms", "Malayalam": "ml",
    "Maltese": "mt", "Maori": "mi", "Marathi": "mr", "Mongolian": "mn",
    "Nepali": "ne", "Norwegian": "no", "Nynorsk": "nn", "Occitan": "oc",
    "Pashto": "ps", "Persian": "fa", "Polish": "pl", "Portuguese": "pt",
    "Punjabi": "pa", "Romanian": "ro", "Russian": "ru", "Sanskrit": "sa",
    "Serbian": "sr", "Shona": "sn", "Sindhi": "sd", "Sinhala": "si",
    "Slovak": "sk", "Slovenian": "sl", "Somali": "so", "Spanish": "es",
    "Sundanese": "su", "Swahili": "sw", "Swedish": "sv", "Tagalog": "tl",
    "Tajik": "tg", "Tamil": "ta", "Tatar": "tt", "Telugu": "te",
    "Thai": "th", "Tibetan": "bo", "Turkish": "tr", "Turkmen": "tk",
    "Ukrainian": "uk", "Urdu": "ur", "Uzbek": "uz", "Vietnamese": "vi",
    "Welsh": "cy", "Yiddish": "yi", "Yoruba": "yo",
}

# ── IPA reference (passed to the TTS preparation subagent) ───────────────────

IPA_REFERENCE = """
VOWELS (monophthongs)
  i   FLEECE  – see, beat, machine
  ɪ   KIT     – sit, bit, hymn
  e   face vowel base (some accents); French été
  ɛ   DRESS   – bed, head, many
  æ   TRAP    – cat, bad  (AmE BATH)
  a   open front – Spanish 'a', French patte
  ɑ   LOT/PALM – father, hot (AmE); also START vowel base
  ɑː  BATH/START – father, car (BrE RP)
  ɒ   LOT (BrE) – hot, dog
  ʌ   STRUT   – cup, but, love
  ə   schwa   – COMMA, about, sofa (unstressed)
  ɜ   NURSE base – bird (before ɹ)
  ɜː  NURSE (BrE RP) – bird, word, her
  ɚ   NURSE/COMMA-R (AmE) – butter, winner
  ɝ   NURSE stressed (AmE) – bird, word
  u   GOOSE   – food, blue, through
  ʊ   FOOT    – book, put, could
  o   GOAT base (some accents); Spanish/Italian 'o'
  ɔ   THOUGHT/NORTH – caught, law
  ɔː  THOUGHT (BrE RP) – caught, law, north

VOWELS (diphthongs)
  eɪ  FACE    – day, name, weight
  aɪ  PRICE   – my, time, high
  ɔɪ  CHOICE  – boy, coin, noise
  oʊ  GOAT (AmE) – go, home, bone
  əʊ  GOAT (BrE RP) – go, home
  aʊ  MOUTH   – now, out, cow
  ɪə  NEAR (BrE) – here, ear
  ɛə  SQUARE (BrE) – there, air, care
  ʊə  CURE (BrE) – pure, tour

R-COLOURED VOWELS (AmE rhotic)
  ɑɹ  START (AmE) – car, bar
  ɔɹ  NORTH/FORCE (AmE) – for, more
  ɪɹ  NEAR (AmE) – here, ear
  ɛɹ  SQUARE (AmE) – there, care
  ʊɹ  CURE (AmE) – pure, tour

CONSONANTS – plosives
  p   voiceless bilabial – pat, spin
  b   voiced bilabial – bat, cab
  t   voiceless alveolar – top, stop
  d   voiced alveolar – dog, bad
  k   voiceless velar – cat, skin
  ɡ   voiced velar – get, bag
  ʔ   glottal stop – uh-oh; BrE butter (Cockney)

CONSONANTS – fricatives
  f   voiceless labiodental – fat, off
  v   voiced labiodental – vat, love
  θ   voiceless dental – thin, think, both
  ð   voiced dental – this, that, breathe
  s   voiceless alveolar – sat, kiss
  z   voiced alveolar – zip, nose
  ʃ   voiceless postalveolar – ship, she, nation
  ʒ   voiced postalveolar – measure, vision, beige
  h   glottal – hat, ahead
  x   voiceless velar – Scottish loch, German Bach
  ç   voiceless palatal – German ich, huge (some AmE)
  ɸ   voiceless bilabial – Japanese fu (フ)

CONSONANTS – affricates
  tʃ  voiceless – church, chair, match
  dʒ  voiced – judge, jam, age

CONSONANTS – nasals
  m   bilabial – man, swim
  n   alveolar – not, sun
  ŋ   velar – sing, ring, bank
  ɲ   palatal – Spanish ñ, French gn, Italian gn

CONSONANTS – liquids & approximants
  l   lateral – let, fill
  ɫ   dark/velarised l – feel, bell, milk (AmE final l)
  ɹ   alveolar approximant – red, right (AmE/AusE r)
  r   alveolar trill – Spanish rr, Italian r, Scottish r
  ɾ   alveolar tap/flap – AmE butter/ladder; Spanish para
  j   palatal – yes, you, beauty
  w   labio-velar – wet, win, queen
  ɰ   velar approximant – Japanese w (ワ)

CONSONANTS – affricates (other)
  ts  voiceless alveolar – Japanese tsu (つ), German z
  dz  voiced alveolar – Japanese zu (ず), Italian z

DIACRITICS
  ː   long vowel – iː, uː, ɑː
  ˈ   primary stress (before syllable) – ˈbʌtər
  ˌ   secondary stress – ˌæbsəˈluːt
  ̃    nasalisation – French bon /bõ/
  ʰ   aspirated – English p/t/k in onset: pʰæt
"""

# ── Accent profiles ────────────────────────────────────────────────────────────

ACCENT_PROFILES: dict[str, str] = {
    "General American": (
        "Use General American (GenAm) phonemes. "
        "Rhotic: always pronounce /ɹ/ wherever written. "
        "TRAP-BATH unsplit: use /æ/ in both 'cat' and 'bath'. "
        "LOT-PALM merged: /ɑ/ for both 'lot' and 'palm'. "
        "GOAT: /oʊ/. GOOSE: /u/. NURSE: /ɝ/ stressed, /ɚ/ unstressed. "
        "Intervocalic t/d often flapped to /ɾ/ (butter→/ˈbʌɾɚ/). "
        "Dark l /ɫ/ in coda position."
    ),
    "British RP": (
        "Use British Received Pronunciation (RP) phonemes. "
        "Non-rhotic: omit /r/ unless immediately before a vowel. "
        "TRAP-BATH split: /æ/ in 'trap/cat', /ɑː/ in 'bath/dance/ask'. "
        "LOT: /ɒ/. PALM: /ɑː/. GOAT: /əʊ/. GOOSE: /uː/. "
        "NURSE: /ɜː/. THOUGHT: /ɔː/. "
        "Clear l /l/ in all positions (no dark l). "
        "No flapping: intervocalic t stays /t/."
    ),
    "Australian": (
        "Use Australian English phonemes. "
        "Non-rhotic: omit /r/ unless before a vowel. "
        "TRAP: /træp/ but raised — /tɹæɪp/ in some analyses; use /æ/ raised toward /eɪ/. "
        "BATH: /bɑːθ/ (like RP). GOAT: /ɡəʊt/ (centred onset). "
        "FACE: /fæɪs/ (raised/diphthongised). PRICE: /prɑɪs/. "
        "GOOSE: /ɡʉːs/ (fronted). NURSE: /nɜːs/."
    ),
    "Irish (Dublin)": (
        "Use Dublin Irish English phonemes. "
        "Rhotic: pronounce /ɹ/ in all positions. "
        "TRAP and BATH both /a/ (open). "
        "GOAT: /ɡoːt/ (pure monophthong). GOOSE: /ɡuːs/. "
        "No flapping. Clear t in all positions. "
        "THOUGHT and NORTH merged with /oː/."
    ),
    "Scottish": (
        "Use Scottish Standard English phonemes. "
        "Rhotic: always pronounce /r/ (trill or tap). "
        "TRAP, BATH, PALM all /a/. LOT, THOUGHT merged /ɔ/. "
        "GOOSE: /ɡʉs/ (fronted). GOAT: /ɡot/ (monophthong). "
        "NURSE: /nɪrs/ or /nɛrs/. No vowel length distinctions."
    ),
    "Southern American": (
        "Use Southern American English phonemes. "
        "Rhotic but often weakened before consonants. "
        "PIN-PEN merger: /ɪ/ before nasals for both. "
        "PRICE monophthong: /prɑːs/ (before voiceless). "
        "GOAT: /ɡoʊt/ but with fronted onset /ɡʌʊt/. "
        "Intervocalic t may be /ɾ/ or deleted."
    ),
    "Japanese": (
        "Transcribe using Japanese phonology. "
        "Vowels: /a i u e o/ only (no schwa). /u/ is compressed, not rounded: /ɯ/. "
        "No consonant clusters; insert epenthetic vowels. "
        "Use /ɸ/ for 'fu' (フ). Use /ts/ for 'tsu' (つ). Use /dz/ for 'zu' (ず). "
        "Pitch accent: mark primary pitch with ˈ. "
        "Long vowels written double: /oː/ for おう/おお. "
        "Tap /ɾ/ for the Japanese r (ら行). No /l/ sound."
    ),
    "Spanish (Castilian)": (
        "Transcribe using Castilian Spanish phonology. "
        "Five pure vowels: /a e i o u/. No diphthongisation. "
        "Theta: /θ/ for 'c' before e/i and 'z'. "
        "Voiced fricatives: /b v/ → /β/ between vowels, /d/ → /ð/ between vowels, "
        "/g/ → /ɣ/ between vowels. "
        "Tap /ɾ/ for single r; trill /r/ for rr and initial r. "
        "Nasal assimilation to following consonant place."
    ),
    "French": (
        "Transcribe using standard French (Parisian) phonology. "
        "Nasal vowels: /ɛ̃ œ̃ ɔ̃ ɑ̃/. "
        "Front rounded vowels: /y ø œ/. "
        "Uvular r: /ʁ/. No /h/ sound (h is silent). "
        "No stress — syllables are isochronous with slight final lengthening. "
        "Liaison: final consonants link to following vowel-initial words."
    ),
    "Mandarin": (
        "Transcribe using Standard Mandarin (Putonghua) phonology. "
        "Tones: mark with tone numbers after the syllable (1=ˉ 2=ˊ 3=ˇ 4=ˋ neutral=·). "
        "Initials: /p pʰ m f t tʰ n l k kʰ x tɕ tɕʰ ɕ ts tsʰ s tʂ tʂʰ ʂ ʐ/. "
        "Finals include: /a o e i u y an en in un ün ang eng ing ong/. "
        "Retroflex initials: zh ch sh r = /tʂ tʂʰ ʂ ʐ/. "
        "Palatal initials: j q x = /tɕ tɕʰ ɕ/ before i/ü."
    ),
}

DEFAULT_ACCENT = "General American"
