"""
games/wordle.py — Wordle du New York Times : réponse du jour, traduction en français, indices.

Génère :
  docs/wordle/solution.json
  docs/wordle/index.html
  docs/wordle/archive/YYYY-MM-DD.json
  docs/wordle/archive/YYYY-MM-DD.html
  docs/wordle/archive/YYYY-MM.html
  docs/wordle/archive/index.html

Sources :
  - Réponse : https://www.nytimes.com/svc/wordle/v2/AAAA-MM-JJ.json (`solution`, `days_since_launch`).
    Une requête par jour ; n'importe quelle date passée est disponible (rattrapage, backfill).
  - Sens en français : section « Anglais » du Wiktionnaire francophone (définitions rédigées
    en français) ; à défaut, définition anglaise du Wiktionnaire anglophone.
"""

import json
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape as _html_escape, unescape as _html_unescape

from core import (
    SITE_URL, DOCS_DIR, _session, _WIKI_UA, _page_wikitext, _clean_wikitext,
    date_fr, date_fr_short, atomic_write, load_all_archives as _load_archives,
    published_iso, og_image_url, FEED_LINK_TAG, updated_block, solution_box_html, hint_levels_html,
    faq_html, faq_jsonld, month_fr, de_month_fr, group_by_month,
)
import og_images
from games.tusmo import _mask_word

# ── Configuration Wordle ──────────────────────────────────────────────────────

WORDLE_DIR = DOCS_DIR / "wordle"
WORDLE_ARCHIVE = WORDLE_DIR / "archive"
WORDLE_SITE_URL = f"{SITE_URL}/wordle"
WORDLE_PLAY_URL = "https://www.nytimes.com/games/wordle"

_NYT_API = "https://www.nytimes.com/svc/wordle/v2/{}.json"
_WORDLE_EPOCH = date(2021, 6, 19)  # Wordle n°0 (repli si days_since_launch absent)
_VOWELS = set("AEIOU")


def puzzle_num_for(d: date) -> int:
    return (d - _WORDLE_EPOCH).days


# ── Réponse du jour (NYT) ─────────────────────────────────────────────────────

def get_wordle_solution(d: date) -> tuple[str | None, int | None, str]:
    """Retourne (WORD, numéro, éditeur) pour la date d, ou (None, None, "") si indisponible."""
    try:
        resp = _session.get(_NYT_API.format(d.isoformat()), timeout=10)
        if resp.ok:
            data = resp.json()
            word = (data.get("solution") or "").strip().upper()
            if data.get("print_date") == d.isoformat() and len(word) == 5 and word.isalpha():
                return word, data.get("days_since_launch") or puzzle_num_for(d), data.get("editor", "")
            print(f"   ⚠ Wordle {d} : réponse inattendue {data}")
        else:
            print(f"   ⚠ Wordle {d} : HTTP {resp.status_code}")
    except Exception as e:
        print(f"   ⚠ Wordle {d} : {e}")
    return None, None, ""


# ── Sens en français (Wiktionnaire) ───────────────────────────────────────────

_EN_POS_FR = {
    "Noun": "Nom", "Verb": "Verbe", "Adjective": "Adjectif", "Adverb": "Adverbe",
    "Interjection": "Interjection", "Preposition": "Préposition", "Pronoun": "Pronom",
    "Conjunction": "Conjonction", "Proper noun": "Nom propre",
}

_POS_LABELS = {
    "nom": "Nom", "verbe": "Verbe", "adjectif": "Adjectif", "adverbe": "Adverbe",
    "interjection": "Interjection", "onomatopée": "Onomatopée", "préposition": "Préposition",
    "pronom": "Pronom", "conjonction": "Conjonction", "nom propre": "Nom propre",
}


def _clean_sense(line: str) -> str:
    """Ligne « # … » du Wiktionnaire → texte : garde les mots des {{lien|mot|fr}}, coupe la
    {{note}} finale, retire les vestiges de modèles (« , , . », « () »)."""
    line = line.split("{{note}}")[0]
    line = re.sub(r"\{\{(?:lien|l)\|([^|{}]+)[^{}]*\}\}", r"\1", line)
    text = _clean_wikitext(line)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+([,.)])", r"\1", text)          # garde l'espace français avant « : » et « ; »
    text = re.sub(r"\s*(?:[,;:]\s*)+([.!?]|$)", r"\1", text)
    return text.strip(" ,;:")


def _extract_en_senses(wt: str, max_senses: int = 4) -> list[dict]:
    """Définitions (rédigées en français) de la section Anglais d'une page du Wiktionnaire.
    Les formes fléchies (« Prétérit de… ») ne servent que s'il n'y a rien d'autre."""
    m = re.search(r"==\s*\{\{langue\|en\}\}\s*==", wt)
    if not m:
        return []
    rest = wt[m.end():]
    nxt = re.search(r"\n==\s*\{\{langue\|", rest)
    section = rest[:nxt.start()] if nxt else rest
    senses, inflections = [], []
    for pm in re.finditer(
        r"=+\s*\{\{S\|([^|}]+)\|en([^}]*)\}\}\s*=+(.*?)(?=\n=+\s*\{\{S\||\Z)", section, re.S,
    ):
        pos, params, body = pm.group(1).strip(), pm.group(2), pm.group(3)
        if pos not in _POS_LABELS:
            continue
        target = inflections if "flexion" in params else senses
        for line in body.splitlines():
            if not re.match(r"#\s*[^*:]", line):     # ni exemple (#*) ni note (#:)
                continue
            text = _clean_sense(line[1:])
            if len(text) < 2 or any(t["text"].rstrip(".") == text.rstrip(".") for t in target):
                continue
            target.append({"pos": pos, "text": text if text.endswith((".", "!", "?")) else text + "."})
    return (senses or inflections)[:max_senses]


def _fetch_en_definition(word: str) -> str:
    """Définition en anglais (Wiktionnaire anglophone), quand le Wiktionnaire FR n'a pas le mot."""
    try:
        resp = _session.get(
            f"https://en.wiktionary.org/api/rest_v1/page/definition/{word.lower()}",
            headers={"User-Agent": _WIKI_UA}, timeout=10,
        )
        if not resp.ok:
            return ""
        for entry in resp.json().get("en", []):
            for d in entry.get("definitions", []):
                raw = re.sub(r"<style.*?</style>", "", d.get("definition", ""), flags=re.S)
                text = _html_unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw))).strip()
                if text:
                    pos = entry.get("partOfSpeech", "")
                    pos = _EN_POS_FR.get(pos, pos)
                    return (f"{pos} : {text}" if pos else text)[:300]
    except Exception as e:
        print(f"   ⚠ Définition anglaise : {e}")
    return ""


def _short_translation(senses: list[dict]) -> str:
    """« Empire, influence, pouvoir. » → « empire, influence, pouvoir » (1re phrase du 1er sens, ≤ 80 car.)."""
    if not senses:
        return ""
    text = senses[0]["text"].split(". ")[0].rstrip(".")
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        text = text[0].lower() + text[1:]
    if len(text) > 80:
        cut = text[:80].rsplit(",", 1)[0] if "," in text[:80] else text[:80].rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;") + "…"
    return text


def fetch_wordle_meaning(word: str) -> dict:
    """{"translation": str, "senses": [{"pos", "text"}], "definition_en": str}"""
    senses: list[dict] = []
    try:
        wt = _page_wikitext(word.lower())
        if wt:
            senses = _extract_en_senses(wt)
    except Exception as e:
        print(f"   ⚠ Wiktionnaire : {e}")
    return {
        "translation": _short_translation(senses),
        "senses": senses,
        "definition_en": _fetch_en_definition(word),
    }


# ── Lettres ───────────────────────────────────────────────────────────────────

def letter_facts(word: str) -> dict:
    letters = word.upper()
    counts = Counter(letters)
    return {
        "vowels": sum(1 for c in letters if c in _VOWELS),
        "has_y": "Y" in letters,
        "repeated": sorted(c for c, n in counts.items() if n > 1),
        "unique_letters": len(counts),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


def _vowels_text(facts: dict) -> str:
    n = facts["vowels"]
    if n == 0:
        return "aucune voyelle A, E, I, O ou U" + (" (le Y en tient lieu)" if facts["has_y"] else "")
    return _plural(n, "voyelle") + (" (plus un Y)" if facts["has_y"] else "")


def _repeated_text(facts: dict) -> str:
    rep = facts["repeated"]
    if not rep:
        return "aucune lettre répétée"
    return "lettre répétée : " + ", ".join(rep) if len(rep) == 1 else "lettres répétées : " + ", ".join(rep)


# ── Fichiers JSON ─────────────────────────────────────────────────────────────

def _entry(d: date, puzzle_num: int, word: str, editor: str, meaning: dict) -> dict:
    return {
        "date": d.isoformat(),
        "puzzle_num": puzzle_num,
        "word": word,
        "letter_count": len(word),
        "translation": meaning.get("translation", ""),
        "senses": meaning.get("senses", []),
        "definition_en": meaning.get("definition_en", ""),
        "editor": editor,
    }


def generate_solution_json(today: date, puzzle_num: int, word: str, editor: str, meaning: dict) -> dict:
    data = _entry(today, puzzle_num, word, editor, meaning)
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    WORDLE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(WORDLE_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(d: date, data: dict) -> None:
    WORDLE_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(WORDLE_ARCHIVE / f"{d.isoformat()}.json", json.dumps(data, ensure_ascii=False, indent=2))


def load_all_archives() -> list[dict]:
    return _load_archives(WORDLE_ARCHIVE, required_keys=["date", "word", "puzzle_num"])


# ── Blocs HTML ────────────────────────────────────────────────────────────────

def _tiles(word: str) -> str:
    cells = "".join(f'<span class="sutom-cell wordle-correct">{c}</span>' for c in word)
    return f'<div class="sutom-grid" aria-label="{word}">{cells}</div>'


def _meaning_html(data: dict) -> str:
    """Sens en français puis définition anglaise d'origine, '' si rien."""
    parts = []
    senses = data.get("senses") or []
    if senses:
        items = "\n".join(
            f'        <li><strong>{_POS_LABELS.get(s["pos"], s["pos"].capitalize())}</strong> : '
            f'{_html_escape(s["text"])}</li>'
            for s in senses
        )
        parts.append(f'      <ul style="margin:.25rem 0 0 1.1rem;line-height:1.7;">\n{items}\n      </ul>')
    if data.get("definition_en"):
        parts.append(f'      <p style="margin-top:.5rem;"><em>Définition en anglais</em> : '
                     f'{_html_escape(data["definition_en"])}</p>')
    return "\n".join(parts)


def _meaning_card(data: dict) -> str:
    word = data["word"]
    body = _meaning_html(data)
    if not body:
        return ""
    intro = (f'Traduction de <strong>{word.lower()}</strong> en français : '
             f'<strong>{_html_escape(data["translation"])}</strong>.' if data.get("translation")
             else f'Le Wiktionnaire francophone ne traduit pas encore <strong>{word.lower()}</strong>.')
    return f"""
    <div class="card">
      <h2>Que veut dire {word} en français ?</h2>
      <p>{intro}</p>
{body}
      <p style="font-size:.8rem;color:#6b7280;margin-top:.75rem;">Sources : Wiktionnaire et Wiktionary (licence CC BY-SA).</p>
    </div>"""


def _facts_card(word: str) -> str:
    facts = letter_facts(word)
    return f"""
    <div class="card">
      <h2>Le mot en détail</h2>
      <div class="word-hints">
        <div class="word-hint-item">
          <span class="word-hint-label">Voyelles</span>
          <span class="word-hint-value visible">{_vowels_text(facts)}</span>
        </div>
        <div class="word-hint-item">
          <span class="word-hint-label">Lettres répétées</span>
          <span class="word-hint-value visible">{", ".join(facts["repeated"]) or "aucune"}</span>
        </div>
        <div class="word-hint-item">
          <span class="word-hint-label">Lettres différentes</span>
          <span class="word-hint-value visible">{facts["unique_letters"]} sur 5</span>
        </div>
        <div class="word-hint-item">
          <span class="word-hint-label">Première / dernière lettre</span>
          <span class="word-hint-value visible">{word[0]} … {word[-1]}</span>
        </div>
      </div>
    </div>"""


def _sense_hint(data: dict) -> str:
    """Sens du mot, masqué : '' s'il n'y en a pas ; message dédié si le mot français est le même."""
    word = data["word"]
    if data.get("translation"):
        source = data["translation"]
    elif data.get("definition_en"):
        source = data["definition_en"]
    else:
        return ""
    masked = _mask_word(word, source)
    if not re.search(r"[^\W\d_]{2,}", masked.replace("___", "")):
        return "Le mot anglais ressemble beaucoup à sa traduction française."
    return _html_escape(masked)


def _progressive_hints_card(data: dict) -> str:
    word = data["word"]
    facts = letter_facts(word)
    levels = [
        ("Indice 1 : voyelles", "Nombre de voyelles dans le mot du jour.",
         f"<strong>{_vowels_text(facts)}</strong>"),
        ("Indice 2 : lettre répétée", "Une même lettre apparaît-elle plusieurs fois ?",
         f"<strong>{_repeated_text(facts)}</strong>"),
        ("Indice 3 : première lettre", "La première lettre du mot.", f"<strong>{word[0]}</strong>"),
    ]
    sense = _sense_hint(data)
    if sense:
        desc = ("Le sens du mot en français (le mot lui-même est masqué)." if data.get("translation")
                else "Le sens du mot, en anglais (le mot lui-même est masqué).")
        levels.append(("Indice 4 : sens en français", desc, sense))
    levels.append((f"Indice {len(levels) + 1} : dernière lettre", "La dernière lettre du mot.",
                   f"<strong>{word[-1]}</strong>"))
    return f"""
    <div class="card">
      <h2>Indices du Wordle du jour</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Pas envie de tout dévoiler ? Ouvrez les indices un par un avant de regarder la réponse.
      </p>{hint_levels_html(levels, "details")}
    </div>"""


def _other_games_card() -> str:
    links = [("../sutom/", "🔤 Sutom"), ("../tusmo/", "🟩 Tusmo"), ("../cemantix/", "🧠 Cémantix"),
             ("../pedantix/", "📖 Pédantix"), ("../loto/", "🎱 Loto FDJ"), ("../euromillions/", "⭐ EuroMillions")]
    items = "\n".join(
        f'        <a href="{href}" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;'
        f'text-decoration:none;color:#374151;font-weight:500;">{label}</a>'
        for href, label in links
    )
    return f"""
    <div class="card" style="margin-top:.5rem;">
      <h2 style="font-size:1rem;margin-bottom:.75rem;">Autres jeux du jour</h2>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
{items}
      </div>
    </div>"""


def _translation_suffix(e: dict) -> str:
    return f" — {_html_escape(e['translation'])}" if e.get("translation") else ""


_HEAD_COMMON = """  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">"""

_GOATCOUNTER = """  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>"""

_DISCLAIMER = ("Site non officiel, sans lien avec le New York Times. "
               "Wordle est une marque de The New York Times Company.")

_PUBLISHER = (f'{{"@type": "Organization", "name": "Solutions du Jour", "url": "{SITE_URL}/", '
              f'"logo": {{"@type": "ImageObject", "url": "{SITE_URL}/logo.png", "width": 512, "height": 512}}}}')


# ── Pages ─────────────────────────────────────────────────────────────────────

def _neighbors_card(data: dict, prev_entry: dict | None, next_entry: dict | None) -> str:
    """Mots de la veille et du lendemain (maillage interne + contexte), et rappel du jeu."""
    def item(e: dict) -> str:
        return (f'      <li class="arch-item"><span class="arch-date">{date_fr(date.fromisoformat(e["date"]))}</span>'
                f'<span class="arch-num">n°{e["puzzle_num"]}</span>'
                f'<a class="arch-link" href="{e["date"]}">{e["word"].upper()}</a>{_translation_suffix(e)}</li>')
    items = "\n".join(item(e) for e in (prev_entry, next_entry) if e)
    listing = f'\n      <ul class="arch-list">\n{items}\n      </ul>' if items else ""
    return f"""
    <div class="card">
      <h2>Les Wordle autour du {date_fr(date.fromisoformat(data["date"]))}</h2>{listing}
      <p style="margin-top:.75rem;">
        Wordle est le jeu de mots quotidien du New York Times : un mot anglais de 5 lettres à trouver
        en 6 essais. La réponse d'aujourd'hui, avec ses indices, est sur la page
        <a href="../">Wordle du jour</a> ; pour jouer en français, voir les solutions
        <a href="../../sutom/">Sutom</a> et <a href="../../tusmo/">Tusmo</a>.
      </p>
    </div>"""


def generate_archive_html(data: dict, prev_entry: dict | None, next_entry: dict | None) -> None:
    """Génère docs/wordle/archive/YYYY-MM-DD.html (réponse visible).
    prev_entry = jour précédent (plus ancien), next_entry = jour suivant, ou None."""
    WORDLE_ARCHIVE.mkdir(parents=True, exist_ok=True)
    prev_date = date.fromisoformat(prev_entry["date"]) if prev_entry else None
    next_date = date.fromisoformat(next_entry["date"]) if next_entry else None
    d = date.fromisoformat(data["date"])
    date_str = d.isoformat()
    num, word = data["puzzle_num"], data["word"]
    translation = data.get("translation", "")
    pub_iso = published_iso(d, data.get("generated_at"), 0, 15)
    og_img = og_image_url("wordle", d)
    date_display = date_fr(d)
    date_short = date_fr_short(d)
    title = f"Wordle {num} du {date_short} : réponse {word} et traduction"
    description = (f"Réponse du Wordle n°{num} du {date_display} : {word} ({translation}). "
                   f"Traduction en français, définition et détail des lettres.")
    if not translation or len(description) > 160:
        description = (f"Réponse du Wordle n°{num} du {date_display} : {word}. "
                       f"Traduction en français, définition et détail des lettres.")
    facts = letter_facts(word)

    faq_items = [
        (f"Quelle est la réponse du Wordle du {date_display} ?",
         f"La réponse du Wordle n°{num} du {date_display} est : {word}."),
    ]
    if translation:
        faq_items.append((f"Que veut dire {word.lower()} en français ?",
                          f"{word.lower()} se traduit en français par : {translation}."))
    faq_items.append((f"Combien de voyelles contient le Wordle n°{num} ?",
                      f"Le mot {word} contient {_vowels_text(facts)}, avec {_repeated_text(facts)}."))

    nav_prev = (f'<a class="nav-link" href="{prev_date.isoformat()}">&#8592; {date_fr(prev_date)}</a>'
                if prev_date else '<span class="nav-disabled">&#8592; Plus ancien</span>')
    nav_next = (f'<a class="nav-link" href="{next_date.isoformat()}">{date_fr(next_date)} &#8594;</a>'
                if next_date else '<a class="nav-link" href="../">Wordle du jour &#8594;</a>')
    tr_sentence = (f" Il se traduit en français par <strong>{_html_escape(translation)}</strong>."
                   if translation else "")

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
{_HEAD_COMMON}

  <title>{title}</title>
  <meta name="description" content="{_html_escape(description)}">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{WORDLE_SITE_URL}/archive/{date_str}">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{_html_escape(description)}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{WORDLE_SITE_URL}/archive/{date_str}">
  <meta property="og:image" content="{og_img}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{_html_escape(description)}">
  <meta property="article:published_time" content="{pub_iso}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Réponse Wordle n°{num} du {date_display}",
    "datePublished": "{pub_iso}",
    "dateModified": "{pub_iso}",
    "description": "Réponse du Wordle n°{num} pour le {date_display} : {word}.",
    "url": "{WORDLE_SITE_URL}/archive/{date_str}",
    "image": ["{og_img}"],
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {_PUBLISHER}
  }}
  </script>

{faq_jsonld(faq_items)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Wordle", "item": "{WORDLE_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "{WORDLE_SITE_URL}/archive/"}},
      {{"@type": "ListItem", "position": 4, "name": "{month_fr(date_str[:7])}", "item": "{WORDLE_SITE_URL}/archive/{date_str[:7]}"}},
      {{"@type": "ListItem", "position": 5, "name": "Réponse du {date_display}"}}
    ]
  }}
  </script>
  {f'<link rel="prev" href="{prev_date.isoformat()}">' if prev_date else ''}
  {f'<link rel="next" href="{next_date.isoformat()}">' if next_date else ''}

  <link rel="stylesheet" href="../../css/style.css">
{_GOATCOUNTER}
</head>
<body>

<header class="site-header">
  <h1>Réponse Wordle n°{num} du {date_display}</h1>
  <p class="subtitle">Archive</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Wordle</a> &rsaquo;
  <a href="./">Archives</a> &rsaquo;
  <a href="./{date_str[:7]}">{month_fr(date_str[:7])}</a> &rsaquo;
  <span>Réponse du {date_display}</span>
</nav>
  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="./">Toutes les archives</a>
    {nav_next}
  </nav>

  <article>

    <div class="card">
      <h2>Wordle n°{num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        La <strong>réponse du Wordle du {date_display}</strong> (n°{num}) était le mot anglais
        <strong>{word}</strong>.{tr_sentence}
      </p>
    </div>

    <div class="card">
      <h2>La réponse du {date_display}</h2>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        {_tiles(word)}
      </div>
      <p class="puzzle-meta">Wordle n°{num} · {date_display}</p>
    </div>
{_meaning_card(data)}
{_facts_card(word)}
{faq_html(faq_items, open_first=True)}
{_neighbors_card(data, prev_entry, next_entry)}

  </article>

  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="./">Toutes les archives</a>
    {nav_next}
  </nav>
</main>

<footer>
  <p>
    <a href="../">Wordle du jour</a> ·
    <a href="./">Archives</a> ·
    <a href="{WORDLE_PLAY_URL}" rel="noopener" target="_blank">Jouer à Wordle</a>
  </p>
  <p style="margin-top:.4rem;">{_DISCLAIMER}</p>
</footer>

</body>
</html>"""

    atomic_write(WORDLE_ARCHIVE / f"{date_str}.html", html)


def generate_month_html(ym: str, entries: list[dict], prev_ym, next_ym) -> None:
    """Génère docs/wordle/archive/YYYY-MM.html — toutes les réponses du mois."""
    WORDLE_ARCHIVE.mkdir(parents=True, exist_ok=True)
    month_label = month_fr(ym)
    month_de = de_month_fr(ym)
    count = len(entries)

    def row_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        tr = _html_escape(e["translation"]) if e.get("translation") else "&mdash;"
        return (
            '        <tr>'
            f'<td class="arch-date">{date_fr(d)}</td>'
            f'<td class="arch-num">n°{e["puzzle_num"]}</td>'
            f'<td><a class="arch-link" href="{e["date"]}">{_html_escape(e["word"].upper())}</a></td>'
            f'<td class="arch-def">{tr}</td>'
            '</tr>'
        )

    rows_html = "\n".join(row_html(e) for e in entries)
    words_preview = ", ".join(e["word"] for e in entries[:6])
    nums = [e["puzzle_num"] for e in entries]
    num_range = f"n°{min(nums)} à n°{max(nums)}" if len(nums) > 1 else f"n°{nums[0]}"

    nav_prev = (f'<a class="nav-link" href="{prev_ym}">&#8592; {month_fr(prev_ym)}</a>'
                if prev_ym else '<span class="nav-disabled">&#8592; Mois précédent</span>')
    nav_next = (f'<a class="nav-link" href="{next_ym}">{month_fr(next_ym)} &#8594;</a>'
                if next_ym else '<a class="nav-link" href="./">Toutes les archives &#8594;</a>')
    link_prev = f'<link rel="prev" href="{prev_ym}">' if prev_ym else ''
    link_next = f'<link rel="next" href="{next_ym}">' if next_ym else ''

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
{_HEAD_COMMON}

  <title>Wordle — Toutes les réponses {month_de}</title>
  <meta name="description" content="Liste complète des réponses du Wordle {month_de} ({num_range}) : les {count} mots du jour avec leur date et leur traduction en français.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{WORDLE_SITE_URL}/archive/{ym}">
{FEED_LINK_TAG}
  {link_prev}
  {link_next}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Wordle — Réponses {month_de}">
  <meta property="og:description" content="Toutes les réponses du Wordle {month_de} ({count} mots) avec leur traduction en français.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{WORDLE_SITE_URL}/archive/{ym}">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    "name": "Wordle — Réponses {month_de}",
    "description": "Liste complète des réponses du Wordle {month_de} ({count} mots du jour) avec leur traduction en français.",
    "url": "{WORDLE_SITE_URL}/archive/{ym}",
    "isPartOf": {{"@type": "WebSite", "name": "Solutions du Jour", "url": "{SITE_URL}/"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Wordle", "item": "{WORDLE_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "{WORDLE_SITE_URL}/archive/"}},
      {{"@type": "ListItem", "position": 4, "name": "{month_label}"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
{_GOATCOUNTER}
</head>
<body>

<header class="site-header">
  <h1>Wordle — Réponses {month_de}</h1>
  <p class="subtitle">{_plural(count, "mot")} du jour</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Wordle</a> &rsaquo;
  <a href="./">Archives</a> &rsaquo;
  <span>{month_label}</span>
</nav>
  <nav class="nav-archive" aria-label="Navigation entre les mois">
    {nav_prev}
    <a class="nav-center" href="./">Tous les mois</a>
    {nav_next}
  </nav>

  <article>
    <div class="card">
      <h2>Toutes les réponses Wordle {month_de}</h2>
      <p>
        Retrouvez la <strong>liste complète des réponses du Wordle {month_de}</strong>
        ({num_range}) : {count} mots anglais ({words_preview}…), chacun avec sa <strong>date</strong>
        et sa <strong>traduction en français</strong>. Cliquez sur un mot pour ouvrir la page du jour.
      </p>
      <div style="overflow-x:auto;">
        <table class="month-table" style="width:100%;border-collapse:collapse;">
          <thead>
            <tr>
              <th style="text-align:left;">Date</th>
              <th style="text-align:left;">N°</th>
              <th style="text-align:left;">Mot</th>
              <th style="text-align:left;">En français</th>
            </tr>
          </thead>
          <tbody>
{rows_html}
          </tbody>
        </table>
      </div>
    </div>
  </article>

  <nav class="nav-archive" aria-label="Navigation entre les mois">
    {nav_prev}
    <a class="nav-center" href="./">Tous les mois</a>
    {nav_next}
  </nav>

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../">Wordle du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../">Wordle du jour</a> ·
    <a href="./">Archives</a> ·
    <a href="{WORDLE_PLAY_URL}" rel="noopener" target="_blank">Jouer à Wordle</a>
  </p>
  <p style="margin-top:.4rem;">{_DISCLAIMER}</p>
</footer>

</body>
</html>"""

    atomic_write(WORDLE_ARCHIVE / f"{ym}.html", html)


def generate_archive_index(entries: list[dict], months: dict[str, list] | None = None) -> None:
    """Génère docs/wordle/archive/index.html."""
    WORDLE_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">n°{e["puzzle_num"]}</span>'
            f'<a class="arch-link" href="{e["date"]}">{e["word"].upper()}</a>'
            f'</li>'
        )

    items_html = "\n".join(item_html(e) for e in entries)
    count = len(entries)

    months_card = ""
    if months:
        month_links = "\n".join(
            f'        <li><a class="arch-link" href="{ym}">{month_fr(ym)}</a> '
            f'<span class="arch-num">{_plural(len(months[ym]), "mot")}</span></li>'
            for ym in months
        )
        months_card = f"""
  <div class="card">
    <h2>Par mois</h2>
    <ul class="arch-list">
{month_links}
    </ul>
  </div>"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
{_HEAD_COMMON}

  <title>Archives Wordle — Toutes les réponses des jours précédents</title>
  <meta name="description" content="Toutes les réponses passées du Wordle, jour par jour : le mot d'hier, d'avant-hier et des jours précédents, avec leur numéro et leur traduction en français.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{WORDLE_SITE_URL}/archive/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives Wordle — Toutes les réponses">
  <meta property="og:description" content="Toutes les réponses passées du Wordle, avec leur traduction en français.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{WORDLE_SITE_URL}/archive/">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Wordle", "item": "{WORDLE_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
{_GOATCOUNTER}
</head>
<body>

<header class="site-header">
  <h1>Archives Wordle</h1>
  <p class="subtitle">{_plural(count, "réponse")} enregistrée{"s" if count > 1 else ""}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Wordle</a> &rsaquo;
  <span>Archives</span>
</nav>
  <div class="card">
    <h2>Toutes les réponses Wordle ({count})</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Cliquez sur un mot pour voir sa traduction et le détail de ce jour.
    </p>
    <ul class="arch-list">
{items_html}
    </ul>
  </div>
{months_card}

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../">Wordle du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../">Wordle du jour</a> ·
    <a href="{WORDLE_PLAY_URL}" rel="noopener" target="_blank">Jouer à Wordle</a>
  </p>
  <p style="margin-top:.4rem;">{_DISCLAIMER}</p>
</footer>

</body>
</html>"""

    atomic_write(WORDLE_ARCHIVE / "index.html", html)


def generate_index_html(data: dict, recent_archives: list | None = None) -> None:
    """Génère docs/wordle/index.html (réponse floutée, indices à ouvrir un par un)."""
    today = date.fromisoformat(data["date"])
    num, word = data["puzzle_num"], data["word"]
    date_str = today.isoformat()
    date_display = date_fr(today)
    date_short = date_fr_short(today)
    pub_iso = published_iso(today, data.get("generated_at"), 0, 15)
    og_img = og_image_url("wordle", today)
    title = f"Wordle du jour {date_short} : réponse n°{num} et indices en français"
    description = (f"Bloqué sur le Wordle du {date_display} ? Indices progressifs, réponse n°{num} "
                   f"et traduction du mot en français. Mis à jour chaque nuit.")
    og_description = f"Indices et réponse du Wordle n°{num} du {date_display}, avec la traduction en français."

    yesterday_card = ""
    recent_archives_card = ""
    yesterday = today - timedelta(days=1)
    if recent_archives:
        y = recent_archives[0]
        if y["date"] == yesterday.isoformat():
            yesterday_card = f"""
    <div class="card">
      <h2>Réponse du Wordle d'hier</h2>
      <p>
        Le mot du Wordle d'hier ({date_fr(yesterday)}, n°{y["puzzle_num"]}) était
        <a class="arch-link" href="archive/{y["date"]}"><strong>{y["word"].upper()}</strong></a>{_translation_suffix(y)}.
      </p>
    </div>"""

        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">n°{e["puzzle_num"]}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}">{e["word"].upper()}</a>'
                f'</li>'
            )
        items = "\n".join(arch_item(e) for e in recent_archives[:7])
        recent_archives_card = f"""
    <div class="card">
      <h2>Réponses des jours précédents</h2>
      <ul class="arch-list">
{items}
      </ul>
      <p style="margin-top:.75rem;font-size:.9rem;">
        <a href="archive/">Voir toutes les archives &#8594;</a>
      </p>
    </div>"""

    meaning = _meaning_html(data)
    meaning_block = ""
    if meaning:
        tr = (f'<p>En français : <strong>{_html_escape(data["translation"])}</strong>.</p>'
              if data.get("translation") else "")
        meaning_block = f"""
      <details style="margin-top:.75rem;">
        <summary style="cursor:pointer;font-weight:600;">Toutes les traductions et définitions du mot</summary>
        <div style="margin-top:.5rem;">
          {tr}
{meaning}
          <p style="font-size:.8rem;color:#6b7280;margin-top:.5rem;">Sources : Wiktionnaire et Wiktionary (licence CC BY-SA).</p>
        </div>
      </details>"""

    visible_faq = [
        ("À quelle heure change le mot du Wordle ?",
         "Le Wordle change chaque jour à minuit, heure locale du joueur : en France, le nouveau mot "
         "arrive à minuit (heure de Paris). La réponse et les indices sont publiés ici vers 0h20."),
        ("Le Wordle existe-t-il en français ?",
         "Le Wordle du New York Times est uniquement en anglais. Pour jouer en français, Sutom et Tusmo "
         "reprennent le même principe avec des mots français ; leurs solutions sont aussi publiées sur ce site."),
        ("À quoi correspond le numéro du Wordle ?",
         f"C'est le nombre de jours écoulés depuis le premier Wordle, le 19 juin 2021 : "
         f"le Wordle n°{num} est celui du {date_display}."),
        ("Où retrouver les réponses Wordle des jours précédents ?",
         "Toutes les réponses passées sont listées dans les archives Wordle, jour par jour et mois par mois, "
         "avec leur traduction en français."),
    ]
    jsonld_faq = [
        (f"Quelle est la réponse du Wordle du {date_display} ?",
         f"La réponse du Wordle n°{num} du {date_display} est : {word}."),
    ] + visible_faq

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
{_HEAD_COMMON}

  <title>{title}</title>
  <meta name="description" content="{_html_escape(description)}">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{WORDLE_SITE_URL}/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{og_description}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{WORDLE_SITE_URL}/">
  <meta property="og:image" content="{og_img}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{og_description}">
  <meta property="article:published_time" content="{pub_iso}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Réponse Wordle n°{num} du {date_display}",
    "datePublished": "{pub_iso}",
    "dateModified": "{pub_iso}",
    "description": "Indices, réponse et traduction en français du Wordle n°{num} pour le {date_display}.",
    "url": "{WORDLE_SITE_URL}/",
    "image": ["{og_img}"],
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {_PUBLISHER}
  }}
  </script>

{faq_jsonld(jsonld_faq)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Wordle", "item": "{WORDLE_SITE_URL}/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
{_GOATCOUNTER}
</head>
<body>

<header class="site-header">
  <h1>Réponse Wordle n°{num} du {date_display}</h1>
  <p class="subtitle">Indices, réponse et traduction en français</p>
{updated_block(pub_iso)}
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <span>Wordle</span>
</nav>
  <article>

    <div class="card">
      <h2>Wordle n°{num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Vous cherchez la <strong>réponse du Wordle du jour</strong> ? Le mot anglais du
        {date_display} compte <strong>5 lettres</strong>. Ouvrez les indices un par un pour garder
        le plaisir du jeu, ou révélez directement la réponse, avec sa <strong>traduction en français</strong>.
      </p>
    </div>
{_progressive_hints_card(data)}
    <div class="card">
      <h2>La réponse du Wordle du {date_display}</h2>
{solution_box_html(word, reveal=False)}{meaning_block}
      <p class="puzzle-meta">Wordle n°{num} · Mis à jour automatiquement le {date_display}</p>
    </div>
{yesterday_card}
    <div class="card">
      <h2>Comment jouer à Wordle ?</h2>
      <p>
        <strong>Wordle</strong> est le jeu de mots quotidien du New York Times, jouable gratuitement sur
        <a href="{WORDLE_PLAY_URL}" rel="noopener" target="_blank">nytimes.com</a>.
        Il faut deviner un <strong>mot anglais de 5 lettres</strong> en 6 essais. Après chaque essai,
        une lettre en <strong>vert</strong> est bien placée, une lettre en <strong>jaune</strong> est
        dans le mot mais ailleurs, une lettre en <strong>gris</strong> n'y est pas.
      </p>
      <p style="margin-top:.75rem;">
        Cette page est mise à jour chaque nuit vers 0h20 avec la <strong>réponse du Wordle du jour</strong>,
        des indices et la traduction du mot. Vous préférez jouer en français ? Voir la
        <a href="../sutom/">solution Sutom du jour</a> et la <a href="../tusmo/">solution Tusmo du jour</a>.
      </p>
    </div>
{faq_html(visible_faq)}
{_other_games_card()}
{recent_archives_card}
  </article>
</main>

<footer>
  <p>{_DISCLAIMER} · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="{WORDLE_PLAY_URL}" rel="noopener" target="_blank">nytimes.com</a></p>
</footer>

<script>
  function revealSolution() {{
    document.getElementById('solution-wrap').classList.add('revealed');
    document.getElementById('reveal-btn').style.display = 'none';
  }}
</script>

</body>
</html>"""

    atomic_write(WORDLE_DIR / "index.html", html)


def generate_unavailable_html(today: date) -> None:
    """Page 'réponse non disponible' (noindex)."""
    WORDLE_DIR.mkdir(parents=True, exist_ok=True)
    date_display = date_fr(today)
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
{_HEAD_COMMON}
  <title>Wordle {date_display} — Réponse non disponible</title>
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="../css/style.css">
</head>
<body>

<header class="site-header">
  <h1>Wordle — Réponse du jour</h1>
  <p class="subtitle">{date_display}</p>
</header>

<main>
  <div class="card">
    <h2>Réponse non disponible</h2>
    <p>
      La réponse du Wordle du {date_display} n'a pas pu être récupérée automatiquement.
      Réessayez plus tard ou jouez directement sur
      <a href="{WORDLE_PLAY_URL}" rel="noopener" target="_blank">nytimes.com</a>.
    </p>
  </div>
</main>

<footer>
  <p><a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives Wordle</a></p>
</footer>

</body>
</html>"""
    atomic_write(WORDLE_DIR / "index.html", html)


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(today: date, data: dict) -> None:
    """Génère tous les fichiers HTML Wordle à partir des JSON déjà en place."""
    og_images.word_game("wordle", today, data["puzzle_num"])
    today_str = today.isoformat()
    past_archives = [e for e in load_all_archives() if e["date"] < today_str]

    print(f"[Wordle] Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        prev_entry = past_archives[i + 1] if i + 1 < len(past_archives) else None
        next_entry = past_archives[i - 1] if i > 0 else None
        generate_archive_html(entry, prev_entry, next_entry)

    months = group_by_month(past_archives)
    month_keys = list(months.keys())
    print(f"[Wordle] Génération des pages mensuelles ({len(month_keys)} mois)…")
    for i, ym in enumerate(month_keys):
        next_ym = month_keys[i - 1] if i > 0 else None
        prev_ym = month_keys[i + 1] if i + 1 < len(month_keys) else None
        generate_month_html(ym, months[ym], prev_ym, next_ym)

    print("[Wordle] Génération de docs/wordle/archive/index.html…")
    generate_archive_index(past_archives, months)

    recent_archives = [e for e in past_archives[:7] if (WORDLE_ARCHIVE / f"{e['date']}.html").exists()]
    print("[Wordle] Génération de docs/wordle/index.html…")
    generate_index_html(data, recent_archives)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def _archive_day(d: date) -> dict | None:
    """Récupère et archive (JSON) le Wordle du jour d. Retourne l'entrée ou None."""
    word, num, editor = get_wordle_solution(d)
    if not word:
        return None
    entry = _entry(d, num, word, editor, fetch_wordle_meaning(word))
    generate_archive_json(d, entry)
    return entry


def _archive_yesterday(today: date) -> None:
    """Filet de sécurité : si le run d'hier a échoué, archive le mot d'hier."""
    yesterday = today - timedelta(days=1)
    if (WORDLE_ARCHIVE / f"{yesterday.isoformat()}.json").exists():
        return
    entry = _archive_day(yesterday)
    if entry:
        print(f"[Wordle] Rattrapage d'hier : {entry['word']!r}")


def run(today: date) -> dict | None:
    """Récupère la réponse Wordle du jour et génère tous les fichiers.
    Retourne le dict data ou None si la réponse est indisponible."""
    WORDLE_ARCHIVE.mkdir(parents=True, exist_ok=True)
    _archive_yesterday(today)

    solution_path = WORDLE_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == today.isoformat() and existing.get("word"):
            if not existing.get("senses") and not existing.get("definition_en"):
                print("[Wordle] Récupération de la traduction manquante…")
                meaning = fetch_wordle_meaning(existing["word"])
                if meaning["senses"] or meaning["definition_en"]:
                    existing.update(meaning)
                    atomic_write(solution_path, json.dumps(existing, ensure_ascii=False, indent=2))
            print(f"[Wordle] ℹ Réponse déjà présente : {existing['word']!r} — régénération HTML uniquement.")
            generate_archive_json(today, existing)
            _generate_all_html(today, existing)
            return existing

    print("[Wordle] Récupération de la réponse…")
    word, num, editor = get_wordle_solution(today)
    if not word:
        print("[Wordle] ⚠ Réponse non disponible — génération page indisponible.")
        generate_unavailable_html(today)
        return None
    print(f"[Wordle] ✅ Réponse : {word!r} (n°{num})")

    print("[Wordle] Récupération de la traduction…")
    meaning = fetch_wordle_meaning(word)
    print(f"[Wordle]    Traduction : {meaning['translation'] or meaning['definition_en'][:60] or '(aucune)'}")

    data = generate_solution_json(today, num, word, editor, meaning)
    generate_archive_json(today, data)
    _generate_all_html(today, data)
    print(f"[Wordle] 🎉 Site généré ({today.isoformat()}, n°{num}, {word!r})")
    return data


# ── Backfill (one-shot local, hors run quotidien) ─────────────────────────────

def backfill_archives(since: date, until: date | None = None, delay: float = 1.0) -> dict:
    """Archive (JSON) les Wordle de `since` à `until` (défaut : hier) encore absents.
    One-shot local — 1 requête NYT + 1 à 2 requêtes Wiktionnaire par jour."""
    until = until or (date.today() - timedelta(days=1))
    days = [since + timedelta(days=i) for i in range((until - since).days + 1)]
    missing = [d for d in days if not (WORDLE_ARCHIVE / f"{d.isoformat()}.json").exists()]
    success = failed = 0
    for i, d in enumerate(missing):
        entry = _archive_day(d)
        if entry:
            success += 1
            print(f"[Wordle backfill] ({i + 1}/{len(missing)}) {d} ✅ {entry['word']} — {entry['translation'] or '…'}")
        else:
            failed += 1
            print(f"[Wordle backfill] ({i + 1}/{len(missing)}) {d} ⚠ indisponible")
        if i + 1 < len(missing):
            time.sleep(delay)
    result = {"total": len(missing), "success": success, "failed": failed}
    print(f"[Wordle backfill] Terminé : {result}")
    return result
