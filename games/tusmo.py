"""
games/tusmo.py — Logique complète du jeu Tusmo (tusmo.xyz, Wordle FR).

Génère :
  docs/tusmo/solution.json
  docs/tusmo/index.html
  docs/tusmo/archive/YYYY-MM-DD.json
  docs/tusmo/archive/YYYY-MM-DD.html
  docs/tusmo/archive/YYYY-MM.html
  docs/tusmo/archive/index.html

Source : API publique de tusmo.xyz (session anonyme par cookie).
  - Mot du jour : le giveup est refusé en mode "daily" → on joue 6 mots valides
    (longueur + 1re lettre), la session perdue renvoie `answer`.
  - Jours passés : mode "archive" (60 derniers jours, J-1 minimum) + giveup.
"""

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from html import escape as _html_escape
from pathlib import Path

import requests

from core import (
    SITE_URL, DOCS_DIR, _session, date_fr, date_fr_short, atomic_write, load_all_archives as _load_archives,
    published_iso, og_image_url, FEED_LINK_TAG, updated_block, solution_box_html, hint_levels_html,
    fetch_definition, faq_html, faq_jsonld, month_fr, de_month_fr, group_by_month, _strip_accents,
)
import og_images
from games.sutom import sutom_letter_facts, SUTOM_ARCHIVE

# ── Configuration Tusmo ───────────────────────────────────────────────────────

TUSMO_DIR = DOCS_DIR / "tusmo"
TUSMO_ARCHIVE = TUSMO_DIR / "archive"
TUSMO_SITE_URL = f"{SITE_URL}/tusmo"

_TUSMO_API = "https://www.tusmo.xyz/api"
# Puzzle #1 (déduit de /api/daily/fr/meta : #64 le 2026-09-30) — repli si meta indisponible
_TUSMO_EPOCH = date(2026, 7, 29)
_MAX_TRIES = 6
_MODEL_PATH = Path("frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin")


def puzzle_num_for(d: date) -> int:
    return (d - _TUSMO_EPOCH).days + 1


# ── API Tusmo ─────────────────────────────────────────────────────────────────

_client_instance: requests.Session | None = None


def _client() -> requests.Session:
    """Session partagée : l'API lie chaque partie aux cookies tusmo_device/tusmo_token d'un
    invité, et limite la création d'invités (429) → un seul invité par processus."""
    global _client_instance
    if _client_instance is None:
        _client_instance = requests.Session()
        _client_instance.headers.update(_session.headers)
        _client_instance.headers.update({"Origin": "https://www.tusmo.xyz", "Referer": "https://www.tusmo.xyz/"})
    return _client_instance


def get_tusmo_meta() -> dict | None:
    """{"date": "YYYY-MM-DD", "wordLength": 7, "firstLetter": "E", "number": 64}"""
    try:
        resp = _client().get(f"{_TUSMO_API}/daily/fr/meta", timeout=10)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"   ⚠ Tusmo meta : {e}")
    return None


def get_archive_dates() -> list[str]:
    try:
        resp = _client().get(f"{_TUSMO_API}/archive/fr", timeout=10)
        if resp.ok:
            return resp.json().get("dates", [])
    except Exception as e:
        print(f"   ⚠ Tusmo archive : {e}")
    return []


def get_archive_solution(d: date) -> str | None:
    """Mot d'un jour passé (≤ J-1, fenêtre de 60 jours) via mode archive + abandon."""
    client = _client()
    try:
        game = client.post(f"{_TUSMO_API}/game", json={"lang": "fr", "mode": "archive", "date": d.isoformat()}, timeout=10)
        if not game.ok:
            return None
        resp = client.post(f"{_TUSMO_API}/game/{game.json()['id']}/giveup", json={}, timeout=10)
        if resp.ok:
            answer = (resp.json().get("session") or {}).get("answer")
            return answer.upper() if answer else None
    except Exception as e:
        print(f"   ⚠ Tusmo archive {d} : {e}")
    return None


def _candidate_words(length: int, first_letter: str, limit: int = 150_000) -> list[str]:
    """Mots à jouer pour épuiser les essais : archives Tusmo/Sutom d'abord (mots validés
    par un Wordle FR), puis vocabulaire word2vec (trié par fréquence)."""
    def keep(w: str) -> bool:
        return len(w) == length and w[0] == first_letter and w.isalpha() and w.isascii()

    words = [e["word"].upper() for e in _load_archives(TUSMO_ARCHIVE) + _load_archives(SUTOM_ARCHIVE)]
    if _MODEL_PATH.exists():
        try:
            from gensim.models import KeyedVectors
            kv = KeyedVectors.load_word2vec_format(
                str(_MODEL_PATH), binary=True, unicode_errors="ignore", limit=limit,
            )
            words += [_strip_accents(w).upper() for w in kv.index_to_key]
        except Exception as e:
            print(f"   ⚠ Tusmo vocabulaire word2vec : {e}")
    return list(dict.fromkeys(w for w in words if keep(w)))


def get_daily_solution(meta: dict, max_requests: int = 150) -> str | None:
    """Mot du jour : joue des mots valides jusqu'à la fin de la partie, puis lit `answer`.
    Les mots refusés (INVALID_WORD) ne consomment pas d'essai."""
    client = _client()
    length, first = meta["wordLength"], meta["firstLetter"].upper()
    try:
        game = client.post(f"{_TUSMO_API}/game", json={"lang": "fr", "mode": "daily"}, timeout=10)
        game.raise_for_status()
        game_id = game.json()["id"]
        for word in _candidate_words(length, first)[:max_requests]:
            resp = client.post(f"{_TUSMO_API}/game/{game_id}/guess", json={"guess": word}, timeout=10)
            if not resp.ok:
                continue
            payload = resp.json()
            session = payload.get("session") or {}
            if session.get("status") == "won":
                return word
            if session.get("answer"):
                return session["answer"].upper()
            time.sleep(0.3)
    except Exception as e:
        print(f"   ⚠ Tusmo daily : {e}")
    return None


def get_tusmo_solution(today: date) -> tuple[str | None, int | None]:
    """Retourne (word, puzzle_num) du jour, ou (None, None) si indisponible."""
    meta = get_tusmo_meta()
    if not meta or meta.get("date") != today.isoformat():
        print(f"   ⚠ Tusmo : meta indisponible ou pas encore à jour ({meta})")
        return None, None
    word = get_daily_solution(meta)
    if not word:
        return None, None
    if len(word) != meta["wordLength"] or word[0] != meta["firstLetter"].upper():
        print(f"   ⚠ Tusmo : réponse {word!r} incohérente avec meta {meta}")
        return None, None
    return word, meta.get("number") or puzzle_num_for(today)


# ── Définition ────────────────────────────────────────────────────────────────

# « Pluriel de frisson. », « Féminin pluriel de déplacé. », « Variante orthographique de renaître. »
_INFLECTION_RE = re.compile(
    r"^(?:(?:masculin|féminin)\s+)?(?:singulier|pluriel|variante orthographique)\s+(?:de|d’|d')\s*([^\s.]+)\.?$",
    re.IGNORECASE,
)


def fetch_tusmo_definition(word: str) -> str:
    """Définition Wiktionnaire ; pour une forme fléchie, ajoute la définition du lemme
    (« Pluriel de frisson. Frisson : Tremblement… ») — ~1/3 des mots Tusmo sont fléchis."""
    definition = fetch_definition(word, resolve_accents=True)
    m = _INFLECTION_RE.match(definition.strip())
    if not m:
        return definition
    lemma = m.group(1)
    lemma_def = fetch_definition(lemma)
    if not lemma_def or _INFLECTION_RE.match(lemma_def.strip()):
        return definition
    return f"{definition} {lemma[0].upper()}{lemma[1:]} : {lemma_def}"[:400]


def _mask_word(word: str, text: str) -> str:
    """Masque le mot et ses variantes (accents, lemme d'une forme fléchie) par ___."""
    target = word.upper()

    def mask(m: re.Match) -> str:
        token = _strip_accents(m.group(0)).upper()
        if len(token) >= 4 and (target.startswith(token) or token.startswith(target)):
            return "___"
        return m.group(0)

    return re.sub(r"[^\W\d_]+", mask, text)


# ── Génération des fichiers ───────────────────────────────────────────────────

def generate_solution_json(today: date, puzzle_num: int, word: str, definition: str = "") -> dict:
    data = {
        "date": today.isoformat(),
        "puzzle_num": puzzle_num,
        "word": word,
        "letter_count": len(word),
        "first_letter": word[0] if word else "",
        "definition": definition,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    TUSMO_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(TUSMO_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(d: date, data: dict) -> None:
    TUSMO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(TUSMO_ARCHIVE / f"{d.isoformat()}.json",
                 json.dumps(data, ensure_ascii=False, indent=2))


def load_all_archives() -> list[dict]:
    return _load_archives(TUSMO_ARCHIVE)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


def _first_letter_grid(word: str) -> str:
    cells = "".join('<span class="sutom-cell sutom-empty">_</span>' for _ in range(len(word) - 1))
    return f"""
    <div class="card">
      <h2>Indice : première lettre</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Comme dans le jeu Tusmo, la première lettre est toujours révélée.
      </p>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="sutom-grid">
          <span class="sutom-cell sutom-correct">{word[0]}</span>
          {cells}
        </div>
        <p class="puzzle-meta">{len(word)} lettres · commence par {word[0]}</p>
      </div>
    </div>"""


def _facts_card(word: str) -> str:
    facts = sutom_letter_facts(word)
    doubled = "".join(f'''
        <div class="word-hint-item">
          <span class="word-hint-label">Lettre doublée</span>
          <span class="word-hint-value visible">{l}{l}</span>
        </div>''' for l in facts["doubled_letters"])
    return f"""
    <div class="card">
      <h2>Le mot en détail</h2>
      <div class="word-hints">
        <div class="word-hint-item">
          <span class="word-hint-label">Nombre de lettres</span>
          <span class="word-hint-value visible">{facts['length']}</span>
        </div>
        <div class="word-hint-item">
          <span class="word-hint-label">Voyelles / consonnes</span>
          <span class="word-hint-value visible">{_plural(facts['vowels'], 'voyelle')} · {_plural(facts['consonants'], 'consonne')}</span>
        </div>
        <div class="word-hint-item">
          <span class="word-hint-label">Lettres uniques</span>
          <span class="word-hint-value visible">{facts['unique_letters']}</span>
        </div>{doubled}
      </div>
    </div>"""


def _progressive_hints_card(word: str, definition: str) -> str:
    """Indices dévoilés un par un (page du jour) : sert l'intention « indice » avant la réponse."""
    facts = sutom_letter_facts(word)
    doubled = ", ".join(f"{l}{l}" for l in facts["doubled_letters"]) or "aucune lettre doublée consécutive"
    levels = [
        ("Indice 1 : voyelles et consonnes",
         "Répartition des lettres du mot du jour.",
         f"<strong>{_plural(facts['vowels'], 'voyelle')}</strong> et "
         f"<strong>{_plural(facts['consonants'], 'consonne')}</strong>, "
         f"{facts['unique_letters']} lettres différentes."),
        ("Indice 2 : lettres doublées",
         "Deux lettres identiques qui se suivent dans le mot.",
         f"<strong>{doubled}</strong>"),
        ("Indice 3 : dernière lettre",
         "La dernière lettre du mot du jour.",
         f"<strong>{word[-1]}</strong>"),
    ]
    if definition:
        levels.append(("Indice 4 : définition",
                       "La définition du mot (le mot lui-même est masqué).",
                       _html_escape(_mask_word(word, definition))))
    return f"""
    <div class="card">
      <h2>Indices progressifs</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Pas envie de tout dévoiler ? Ouvrez les indices un par un avant de regarder la réponse.
      </p>{hint_levels_html(levels, "details")}
    </div>"""


def _other_games_card() -> str:
    links = [("../sutom/", "🔤 Sutom"), ("../cemantix/", "🧠 Cémantix"), ("../pedantix/", "📖 Pédantix"),
             ("../loto/", "🎱 Loto FDJ"), ("../euromillions/", "⭐ EuroMillions")]
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


_TUSMO_VS_SUTOM = (
    "Tusmo et Sutom sont deux Wordle en français au principe identique : un mot par jour, "
    "la première lettre révélée et 6 essais. Sutom (sutom.nocle.fr) est le jeu d'origine ; "
    "Tusmo (tusmo.xyz) ajoute des modes multijoueur, des séries et des archives jouables. "
    "Les deux jeux n'ont pas le même mot du jour."
)


def generate_archive_html(
    d: date,
    puzzle_num: int,
    word: str,
    prev_date,
    next_date,
    definition: str = "",
    generated_at: str | None = None,
) -> None:
    """Génère docs/tusmo/archive/YYYY-MM-DD.html (réponse visible)."""
    TUSMO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    pub_iso = published_iso(d, generated_at, 0, 15)
    og_img = og_image_url("tusmo", d)
    date_display = date_fr(d)
    date_short = date_fr_short(d)
    letter_count = len(word)
    first_letter = word[0]
    title = f"Tusmo du {date_short} : réponse #{puzzle_num} en {letter_count} lettres"
    description = (f"Réponse du Tusmo #{puzzle_num} du {date_display} : mot de {letter_count} lettres "
                   f"commençant par {first_letter}, avec sa définition et le détail des lettres.")

    definition_card = ""
    if definition:
        definition_card = f"""
    <div class="card">
      <h2>Définition</h2>
      <p>{_html_escape(definition)}</p>
    </div>"""

    faq_items = [
        (f"Quelle est la réponse du Tusmo du {date_display} ?",
         f"La réponse du Tusmo #{puzzle_num} du {date_display} est : {word}."),
        (f"Combien de lettres fait le mot Tusmo du {date_display} ?",
         f"Le mot du {date_display} (Tusmo #{puzzle_num}) contient {letter_count} lettres et commence par {first_letter}."),
    ]
    if definition:
        faq_items.append((f"Que signifie le mot {word} ?", definition))

    nav_prev = (f'<a class="nav-link" href="{prev_date.isoformat()}">&#8592; {date_fr(prev_date)}</a>'
                if prev_date else '<span class="nav-disabled">&#8592; Plus ancien</span>')
    nav_next = (f'<a class="nav-link" href="{next_date.isoformat()}">{date_fr(next_date)} &#8594;</a>'
                if next_date else '<a class="nav-link" href="../">Solution du jour &#8594;</a>')

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>{title}</title>
  <meta name="description" content="{description}">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{TUSMO_SITE_URL}/archive/{date_str}">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{TUSMO_SITE_URL}/archive/{date_str}">
  <meta property="og:image" content="{og_img}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{description}">
  <meta property="article:published_time" content="{pub_iso}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution Tusmo #{puzzle_num} du {date_display}",
    "datePublished": "{pub_iso}",
    "dateModified": "{pub_iso}",
    "description": "Solution du Tusmo #{puzzle_num} pour le {date_display} : {word}.",
    "url": "{TUSMO_SITE_URL}/archive/{date_str}",
    "image": ["{og_img}"],
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "{SITE_URL}/", "logo": {{"@type": "ImageObject", "url": "{SITE_URL}/logo.png", "width": 512, "height": 512}}}}
  }}
  </script>

{faq_jsonld(faq_items)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Tusmo", "item": "{TUSMO_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "{TUSMO_SITE_URL}/archive/"}},
      {{"@type": "ListItem", "position": 4, "name": "{month_fr(date_str[:7])}", "item": "{TUSMO_SITE_URL}/archive/{date_str[:7]}"}},
      {{"@type": "ListItem", "position": 5, "name": "Solution du {date_display}"}}
    ]
  }}
  </script>
  {f'<link rel="prev" href="{prev_date.isoformat()}">' if prev_date else ''}
  {f'<link rel="next" href="{next_date.isoformat()}">' if next_date else ''}

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Solution Tusmo #{puzzle_num} du {date_display}</h1>
  <p class="subtitle">Archive</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Tusmo</a> &rsaquo;
  <a href="./">Archives</a> &rsaquo;
  <a href="./{date_str[:7]}">{month_fr(date_str[:7])}</a> &rsaquo;
  <span>Solution du {date_display}</span>
</nav>
  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="./">Toutes les archives</a>
    {nav_next}
  </nav>

  <article>

    <div class="card">
      <h2>Tusmo #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez la <strong>réponse du Tusmo du {date_display}</strong> (mot #{puzzle_num}).
        Le mot du jour contenait <strong>{letter_count} lettres</strong> et commençait par
        <strong>{first_letter}</strong>.
      </p>
    </div>
{_first_letter_grid(word)}
    <div class="card">
      <h2>La solution du {date_display}</h2>
{solution_box_html(word, reveal=True)}
      <p class="puzzle-meta">Tusmo #{puzzle_num} · {date_display}</p>
    </div>
{_facts_card(word)}
{definition_card}
{faq_html(faq_items, open_first=True)}

  </article>

  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="./">Toutes les archives</a>
    {nav_next}
  </nav>
</main>

<footer>
  <p>
    <a href="../">Solution du jour</a> ·
    <a href="./">Archives</a> ·
    <a href="https://www.tusmo.xyz" rel="noopener" target="_blank">Jouer à Tusmo</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(TUSMO_ARCHIVE / f"{date_str}.html", html)


def generate_month_html(ym: str, entries: list[dict], prev_ym, next_ym) -> None:
    """Génère docs/tusmo/archive/YYYY-MM.html — récap de toutes les solutions du mois."""
    TUSMO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    month_label = month_fr(ym)
    month_de = de_month_fr(ym)
    count = len(entries)

    def row_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        defn = e.get("definition", "").strip()
        defn_html = _html_escape(defn) if defn else "&mdash;"
        return (
            '        <tr>'
            f'<td class="arch-date">{date_fr(d)}</td>'
            f'<td class="arch-num">#{e["puzzle_num"]}</td>'
            f'<td><a class="arch-link" href="{e["date"]}">{_html_escape(e["word"].upper())}</a></td>'
            f'<td class="arch-def">{defn_html}</td>'
            '</tr>'
        )

    rows_html = "\n".join(row_html(e) for e in entries)
    words_preview = ", ".join(e["word"] for e in entries[:6])

    nav_prev = (f'<a class="nav-link" href="{prev_ym}">&#8592; {month_fr(prev_ym)}</a>'
                if prev_ym else '<span class="nav-disabled">&#8592; Mois précédent</span>')
    nav_next = (f'<a class="nav-link" href="{next_ym}">{month_fr(next_ym)} &#8594;</a>'
                if next_ym else '<a class="nav-link" href="./">Toutes les archives &#8594;</a>')
    link_prev = f'<link rel="prev" href="{prev_ym}">' if prev_ym else ''
    link_next = f'<link rel="next" href="{next_ym}">' if next_ym else ''

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Tusmo — Toutes les solutions {month_de}</title>
  <meta name="description" content="Liste complète des réponses du Tusmo {month_de} : les {count} mots du jour avec leur date, leur numéro et leur définition.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{TUSMO_SITE_URL}/archive/{ym}">
{FEED_LINK_TAG}
  {link_prev}
  {link_next}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Tusmo — Solutions {month_de}">
  <meta property="og:description" content="Toutes les réponses du Tusmo {month_de} ({count} mots du jour) avec définitions.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{TUSMO_SITE_URL}/archive/{ym}">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    "name": "Tusmo — Solutions {month_de}",
    "description": "Liste complète des réponses du Tusmo {month_de} ({count} mots du jour) avec leur définition.",
    "url": "{TUSMO_SITE_URL}/archive/{ym}",
    "isPartOf": {{"@type": "WebSite", "name": "Solutions du Jour", "url": "{SITE_URL}/"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Tusmo", "item": "{TUSMO_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "{TUSMO_SITE_URL}/archive/"}},
      {{"@type": "ListItem", "position": 4, "name": "{month_label}"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Tusmo — Solutions {month_de}</h1>
  <p class="subtitle">{_plural(count, "mot")} du jour</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Tusmo</a> &rsaquo;
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
      <h2>Toutes les solutions Tusmo {month_de}</h2>
      <p>
        Retrouvez la <strong>liste complète des réponses du Tusmo {month_de}</strong> :
        {count} mots du jour ({words_preview}…), chacun avec sa <strong>date</strong> et son
        <strong>numéro</strong>. Cliquez sur un mot pour ouvrir la page détaillée du jour.
      </p>
      <div style="overflow-x:auto;">
        <table class="month-table" style="width:100%;border-collapse:collapse;">
          <thead>
            <tr>
              <th style="text-align:left;">Date</th>
              <th style="text-align:left;">N°</th>
              <th style="text-align:left;">Mot</th>
              <th style="text-align:left;">Définition</th>
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
    <a class="reveal-btn" href="../">Solution du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../">Solution du jour</a> ·
    <a href="./">Archives</a> ·
    <a href="https://www.tusmo.xyz" rel="noopener" target="_blank">Jouer à Tusmo</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(TUSMO_ARCHIVE / f"{ym}.html", html)


def generate_archive_index(entries: list[dict], months: dict[str, list] | None = None) -> None:
    """Génère docs/tusmo/archive/index.html."""
    TUSMO_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">#{e["puzzle_num"]}</span>'
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
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Archives Tusmo — Toutes les réponses des jours précédents</title>
  <meta name="description" content="Toutes les réponses passées du Tusmo, jour par jour : le mot d'hier, d'avant-hier et de chaque jour depuis le début, avec définitions.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{TUSMO_SITE_URL}/archive/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives Tusmo — Toutes les réponses">
  <meta property="og:description" content="Toutes les réponses passées du jeu Tusmo.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{TUSMO_SITE_URL}/archive/">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Tusmo", "item": "{TUSMO_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Archives Tusmo</h1>
  <p class="subtitle">{_plural(count, "réponse")} enregistrée{"s" if count > 1 else ""}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Tusmo</a> &rsaquo;
  <span>Archives</span>
</nav>
  <div class="card">
    <h2>Toutes les réponses Tusmo ({count})</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Cliquez sur un mot pour voir la solution complète de ce jour.
    </p>
    <ul class="arch-list">
{items_html}
    </ul>
  </div>
{months_card}

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../">Solution du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../">Solution du jour</a> ·
    <a href="https://www.tusmo.xyz" rel="noopener" target="_blank">Jouer à Tusmo</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(TUSMO_ARCHIVE / "index.html", html)


def generate_index_html(
    today: date,
    puzzle_num: int,
    word: str,
    recent_archives: list | None = None,
    generated_at: str | None = None,
    definition: str = "",
) -> None:
    """Génère docs/tusmo/index.html."""
    date_str = today.isoformat()
    date_display = date_fr(today)
    date_short = date_fr_short(today)
    letter_count = len(word)
    first_letter = word[0]
    pub_iso = published_iso(today, generated_at, 0, 15)
    og_img = og_image_url("tusmo", today)
    title = f"Tusmo #{puzzle_num} du {date_short} : solution en {letter_count} lettres"
    description = (f"Bloqué sur le Tusmo du {date_display} ? Réponse du mot du jour en {letter_count} lettres "
                   f"commençant par {first_letter}, avec indices progressifs. Mis à jour chaque nuit vers 0h20.")

    yesterday_card = ""
    recent_archives_card = ""
    yesterday = today - timedelta(days=1)
    if recent_archives:
        y = recent_archives[0]
        if y["date"] == yesterday.isoformat():
            yesterday_card = f"""
    <div class="card">
      <h2>Solution Tusmo d'hier</h2>
      <p>
        Le mot du Tusmo d'hier ({date_fr(yesterday)}, #{y["puzzle_num"]}) était
        <a class="arch-link" href="archive/{y["date"]}"><strong>{y["word"].upper()}</strong></a>.
      </p>
    </div>"""

        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">#{e["puzzle_num"]}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}">{e["word"].upper()}</a>'
                f'</li>'
            )
        items = "\n".join(arch_item(e) for e in recent_archives[:7])
        recent_archives_card = f"""
    <div class="card">
      <h2>Solutions précédentes</h2>
      <ul class="arch-list">
{items}
      </ul>
      <p style="margin-top:.75rem;font-size:.9rem;">
        <a href="archive/">Voir toutes les archives &#8594;</a>
      </p>
    </div>"""

    # JSON-LD avec la réponse (comme Sutom) ; FAQ visible sans spoiler
    visible_faq = [
        ("À quelle heure sort la solution Tusmo ?",
         "Le mot Tusmo change chaque jour à minuit. La solution est publiée ici automatiquement vers 0h20."),
        ("Quelle est la différence entre Tusmo et Sutom ?", _TUSMO_VS_SUTOM),
        ("Où retrouver les réponses Tusmo des jours précédents ?",
         "Toutes les réponses passées sont listées dans les archives Tusmo, jour par jour et mois par mois."),
    ]
    jsonld_faq = [
        (f"Quelle est la solution du Tusmo du {date_display} ?",
         f"La réponse du Tusmo #{puzzle_num} du {date_display} est : {word}."),
        (f"Combien de lettres fait le mot Tusmo du {date_display} ?",
         f"Le mot Tusmo du {date_display} (#{puzzle_num}) contient {letter_count} lettres et commence par {first_letter}."),
    ] + visible_faq

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>{title}</title>
  <meta name="description" content="{description}">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{TUSMO_SITE_URL}/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="{title}">
  <meta property="og:description" content="Réponse du Tusmo du {date_display} : mot en {letter_count} lettres commençant par {first_letter}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{TUSMO_SITE_URL}/">
  <meta property="og:image" content="{og_img}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="Réponse du Tusmo du {date_display} : mot en {letter_count} lettres commençant par {first_letter}.">
  <meta property="article:published_time" content="{pub_iso}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution Tusmo #{puzzle_num} du {date_display}",
    "datePublished": "{pub_iso}",
    "dateModified": "{pub_iso}",
    "description": "Solution et réponse du jeu Tusmo #{puzzle_num} pour le {date_display}.",
    "url": "{TUSMO_SITE_URL}/",
    "image": ["{og_img}"],
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "{SITE_URL}/", "logo": {{"@type": "ImageObject", "url": "{SITE_URL}/logo.png", "width": 512, "height": 512}}}}
  }}
  </script>

{faq_jsonld(jsonld_faq)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Tusmo", "item": "{TUSMO_SITE_URL}/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Solution Tusmo #{puzzle_num} du {date_display}</h1>
  <p class="subtitle">Réponse et indices du mot du jour</p>
{updated_block(pub_iso)}
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <span>Tusmo</span>
</nav>
  <article>

    <div class="card">
      <h2>Tusmo #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Vous cherchez la <strong>solution du Tusmo du {date_display}</strong> ?
        Le mot du jour contient <strong>{letter_count} lettres</strong> et commence par
        <strong>{first_letter}</strong>. Ouvrez les indices un par un, ou révélez directement
        la réponse ci-dessous si vous êtes bloqué.
      </p>
    </div>
{_first_letter_grid(word)}
{_progressive_hints_card(word, definition)}
    <div class="card">
      <h2>La réponse du Tusmo du {date_display}</h2>
{solution_box_html(word, reveal=False)}
      <p class="puzzle-meta">Tusmo #{puzzle_num} · Généré automatiquement le {date_display}</p>
    </div>
{yesterday_card}
    <div class="card">
      <h2>Comment jouer à Tusmo ?</h2>
      <p>
        <strong>Tusmo</strong> est un Wordle français disponible sur
        <a href="https://www.tusmo.xyz" rel="noopener" target="_blank">tusmo.xyz</a>.
        Chaque jour, un nouveau mot est à deviner en 6 essais ; la première lettre est toujours révélée.
        Après chaque essai, les lettres bien placées, mal placées et absentes sont colorées.
      </p>
      <p style="margin-top:.75rem;">
        Cette page est mise à jour automatiquement chaque nuit vers 0h20 avec la
        <strong>solution Tusmo du jour</strong>. Vous jouez aussi à Sutom ?
        La <a href="../sutom/">solution Sutom du jour</a> est disponible ici.
      </p>
      <p style="margin-top:.75rem;font-size:.9rem;">
        Guides : <a href="comment-jouer/">règles de Tusmo</a> ·
        <a href="meilleurs-mots/">meilleurs mots de départ et lettres fréquentes</a>
      </p>
    </div>
{faq_html(visible_faq)}
{_other_games_card()}
{recent_archives_card}
  </article>
</main>

<footer>
  <p>Site non officiel — Solution générée automatiquement · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://www.tusmo.xyz" rel="noopener" target="_blank">tusmo.xyz</a></p>
</footer>

<script>
  function revealSolution() {{
    document.getElementById('solution-wrap').classList.add('revealed');
    document.getElementById('reveal-btn').style.display = 'none';
  }}
</script>

</body>
</html>"""

    atomic_write(TUSMO_DIR / "index.html", html)


def generate_unavailable_html(today: date) -> None:
    """Génère une page 'solution non disponible' pour Tusmo."""
    TUSMO_DIR.mkdir(parents=True, exist_ok=True)
    date_display = date_fr(today)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <title>Tusmo {date_display} — Solution non disponible</title>
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="../css/style.css">
</head>
<body>

<header class="site-header">
  <h1>Tusmo — Solution du jour</h1>
  <p class="subtitle">{date_display}</p>
</header>

<main>
  <div class="card">
    <h2>Solution non disponible</h2>
    <p>
      La solution Tusmo du {date_display} n'a pas pu être récupérée automatiquement.
      Réessayez plus tard ou rendez-vous directement sur
      <a href="https://www.tusmo.xyz" rel="noopener" target="_blank">tusmo.xyz</a>.
    </p>
  </div>
</main>

<footer>
  <p><a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives Tusmo</a></p>
</footer>

</body>
</html>"""

    atomic_write(TUSMO_DIR / "index.html", html)


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(
    today: date, puzzle_num: int, word: str, definition: str = "", generated_at: str | None = None,
) -> None:
    """Génère tous les fichiers HTML Tusmo à partir des JSON déjà en place."""
    og_images.word_game("tusmo", today, puzzle_num, word=word)
    all_archives = load_all_archives()
    today_str = today.isoformat()
    past_archives = [e for e in all_archives if e["date"] != today_str]

    print(f"[Tusmo] Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        d = date.fromisoformat(entry["date"])
        prev_date = date.fromisoformat(past_archives[i + 1]["date"]) if i + 1 < len(past_archives) else None
        next_date = date.fromisoformat(past_archives[i - 1]["date"]) if i > 0 else None
        generate_archive_html(
            d, entry["puzzle_num"], entry["word"], prev_date, next_date,
            entry.get("definition", ""),
            generated_at=entry.get("generated_at"),
        )

    months = group_by_month(past_archives)
    month_keys = list(months.keys())
    print(f"[Tusmo] Génération des pages mensuelles ({len(month_keys)} mois)…")
    for i, ym in enumerate(month_keys):
        next_ym = month_keys[i - 1] if i > 0 else None
        prev_ym = month_keys[i + 1] if i + 1 < len(month_keys) else None
        generate_month_html(ym, months[ym], prev_ym, next_ym)

    print("[Tusmo] Génération de docs/tusmo/archive/index.html…")
    generate_archive_index(past_archives, months)

    recent_archives = [e for e in past_archives[:7] if (TUSMO_ARCHIVE / f"{e['date']}.html").exists()]
    print("[Tusmo] Génération de docs/tusmo/index.html…")
    generate_index_html(today, puzzle_num, word, recent_archives, generated_at, definition)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def _archive_yesterday(today: date) -> None:
    """Filet de sécurité : si le run d'hier a échoué, récupère le mot d'hier via le mode archive."""
    yesterday = today - timedelta(days=1)
    if (TUSMO_ARCHIVE / f"{yesterday.isoformat()}.json").exists():
        return
    word = get_archive_solution(yesterday)
    if not word:
        return
    print(f"[Tusmo] Rattrapage d'hier : {word!r}")
    generate_archive_json(yesterday, {
        "date": yesterday.isoformat(),
        "puzzle_num": puzzle_num_for(yesterday),
        "word": word,
        "letter_count": len(word),
        "first_letter": word[0],
        "definition": fetch_tusmo_definition(word),
    })


def run(today: date) -> dict | None:
    """
    Récupère la solution Tusmo du jour et génère tous les fichiers.
    Retourne le dict data ou None si la solution est indisponible.
    """
    TUSMO_DIR.mkdir(parents=True, exist_ok=True)
    TUSMO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    _archive_yesterday(today)

    solution_path = TUSMO_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == today.isoformat() and existing.get("word"):
            word = existing["word"]
            definition = existing.get("definition", "")
            if not definition:
                print("[Tusmo] Récupération de la définition manquante…")
                definition = fetch_tusmo_definition(word)
                if definition:
                    existing["definition"] = definition
                    atomic_write(solution_path, json.dumps(existing, ensure_ascii=False, indent=2))
            print(f"[Tusmo] ℹ Solution déjà présente : {word!r} — régénération HTML uniquement.")
            generate_archive_json(today, existing)
            _generate_all_html(today, existing["puzzle_num"], word, definition, existing.get("generated_at"))
            return existing

    print("[Tusmo] Récupération de la solution…")
    word, puzzle_num = get_tusmo_solution(today)

    if not word:
        print("[Tusmo] ⚠ Solution non disponible — génération page indisponible.")
        generate_unavailable_html(today)
        return None

    print(f"[Tusmo] ✅ Solution : {word!r} (#{puzzle_num}, {len(word)} lettres)")

    print("[Tusmo] Récupération de la définition…")
    definition = fetch_tusmo_definition(word)
    print(f"[Tusmo]    Définition : {definition[:80]}…" if definition else "[Tusmo]    Aucune définition trouvée.")

    data = generate_solution_json(today, puzzle_num, word, definition)
    generate_archive_json(today, data)
    _generate_all_html(today, puzzle_num, word, definition, data.get("generated_at"))

    print(f"[Tusmo] 🎉 Site généré ({today.isoformat()}, #{puzzle_num}, {word!r})")
    return data


# ── Backfill (one-shot local, hors run quotidien) ─────────────────────────────

def backfill_archives(delay: float = 1.5) -> dict:
    """Importe les jours encore jouables en mode archive sur tusmo.xyz (~60 jours glissants).
    One-shot local — ne PAS appeler depuis le run quotidien (une requête + définition par jour)."""
    dates = [date.fromisoformat(s) for s in get_archive_dates()]
    missing = [d for d in dates if not (TUSMO_ARCHIVE / f"{d.isoformat()}.json").exists()]
    success = failed = 0
    for i, d in enumerate(missing):
        word = get_archive_solution(d)
        if not word:
            failed += 1
            print(f"[Tusmo backfill] ({i + 1}/{len(missing)}) {d} ⚠ indisponible")
            continue
        definition = fetch_tusmo_definition(word)
        generate_archive_json(d, {
            "date": d.isoformat(),
            "puzzle_num": puzzle_num_for(d),
            "word": word,
            "letter_count": len(word),
            "first_letter": word[0],
            "definition": definition,
        })
        success += 1
        print(f"[Tusmo backfill] ({i + 1}/{len(missing)}) {d} ✅ {word}")
        if i + 1 < len(missing):
            time.sleep(delay)
    result = {"total": len(missing), "success": success, "failed": failed}
    print(f"[Tusmo backfill] Terminé : {result}")
    return result
