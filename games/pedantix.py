"""
games/pedantix.py — Logique complète du jeu Pédantix.

Génère :
  docs/pedantix/solution.json
  docs/pedantix/index.html
  docs/pedantix/archive/YYYY-MM-DD.json
  docs/pedantix/archive/YYYY-MM-DD.html
  docs/pedantix/archive/index.html
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from html import escape as _html_escape
from pathlib import Path

from core import (
    SITE_URL, DOCS_DIR, PARIS_TZ, _session, date_fr, date_fr_short, atomic_write,
    load_all_archives as _load_archives,
    published_iso, og_image_url, FEED_LINK_TAG, updated_block,
    hint_levels_html, solution_box_html, faq_jsonld, faq_html,
    month_fr, de_month_fr, group_by_month,
)
import og_images

# ── Configuration Pédantix ────────────────────────────────────────────────────

BASE_URL = "https://pedantix.certitudes.org"
PEDANTIX_DIR = DOCS_DIR / "pedantix"
PEDANTIX_ARCHIVE = PEDANTIX_DIR / "archive"
PEDANTIX_SITE_URL = f"{SITE_URL}/pedantix"

_HEADERS_FORM = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}

# Pédantix change de page à MIDI heure de Paris (data-utc-time="39600" sur la page),
# pas à minuit comme Cémantix. Convention de date (celle de gamewave.fr, mobi.gg…) :
# un puzzle porte la date du jour où il COMMENCE. #1444 a commencé le 26/04/2026 à midi.
_REF_DATE = date(2026, 4, 26)
_REF_PUZZLE = 1444
RESET_HOUR = 12
PUB_TIME = (12, 5)   # heure de publication de repli (run launchd de 12h05)


def puzzle_date(puzzle_num: int) -> date:
    """Date d'un puzzle = jour où il commence (à midi). Ne jamais la déduire de `today`."""
    return _REF_DATE + timedelta(days=puzzle_num - _REF_PUZZLE)


def current_puzzle_num(now: datetime | None = None) -> int:
    """Numéro du puzzle en cours d'après l'horloge (repli si la page est injoignable)."""
    now = (now or datetime.now(PARIS_TZ)).astimezone(PARIS_TZ)
    start = now.date() if now.hour >= RESET_HOUR else now.date() - timedelta(days=1)
    return _REF_PUZZLE + (start - _REF_DATE).days


# ── API Pédantix ──────────────────────────────────────────────────────────────

def get_page_info() -> tuple[int, str | None, str | None, str | None]:
    """
    Scrape la page Pédantix.
    Retourne (puzzle_num, yesterday_slug, yesterday_name, html).
    yesterday_slug/name/html = None si non trouvé.
    """
    from bs4 import BeautifulSoup
    puzzle_num = current_puzzle_num()
    yesterday_slug = yesterday_name = html = None
    try:
        resp = _session.get(BASE_URL, headers=_HEADERS_FORM, timeout=15)
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", id="script")
        if script and "data-puzzle-number" in script.attrs:
            puzzle_num = int(script["data-puzzle-number"])
        else:
            html = None
        # Réponse du puzzle précédent : <a id="yesterday">
        yesterday_el = soup.find("a", id="yesterday")
        if yesterday_el:
            href = yesterday_el.get("href", "")
            if "/wiki/" in href:
                yesterday_slug = href.split("/wiki/")[-1]
            yesterday_name = yesterday_el.get_text(strip=True)
    except Exception as e:
        print(f"   ⚠ Erreur scraping page Pédantix : {e}")
    return puzzle_num, yesterday_slug, yesterday_name, html


def get_puzzle_number() -> int:
    return get_page_info()[0]


def _get_page(article_slug: str) -> dict | None:
    try:
        resp = _session.post(
            f"{BASE_URL}/page",
            data=f"answer={article_slug}",
            headers=_HEADERS_FORM,
            timeout=15,
        )
        return resp.json()
    except Exception as e:
        print(f"   ⚠ /page ({article_slug!r}) : {e}")
    return None


def fetch_wikipedia_data(slug: str) -> tuple[str, list[str]]:
    """Retourne (extrait, tags_descriptifs) depuis l'API Wikipedia FR."""
    extract = ""
    categories = []
    try:
        resp = _session.get(
            f"https://fr.wikipedia.org/api/rest_v1/page/summary/{slug}",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data.get("extract", "").strip()
            if raw:
                idx = raw.find(". ")
                extract = raw[:idx + 1] if idx != -1 else raw[:400]
            # La description courte est un bon indice de niveau 2
            description = data.get("description", "").strip()
            if description and description.lower() != slug.lower():
                categories.append(description)
    except Exception as e:
        print(f"   ⚠ Wikipedia summary ({slug!r}) : {e}")

    # Catégories via parse (moins de maintenance que query/categories)
    if len(categories) < 3:
        try:
            resp2 = _session.get(
                f"https://fr.wikipedia.org/w/api.php?action=parse&page={slug}"
                f"&prop=categories&format=json",
                timeout=10,
            )
            if resp2.status_code == 200:
                cats = resp2.json().get("parse", {}).get("categories", [])
                skip_words = ["article", "portail", "page", "fichier", "maintenance",
                              "wikipedia", "ébauche", "admissibilité", "neutralité",
                              "qualité", "traduction", "liens", "mort", "vide",
                              "wikidata", "commons", "catégorie", "identifiant",
                              "lien", "import", "export", "modèle", "projet"]
                for c in cats:
                    title = c.get("*", "")
                    title_lower = title.lower()
                    title_clean = title.replace("_", " ")
                    slug_normalized = slug.replace("_", " ").lower()
                    if (not any(skip in title_lower for skip in skip_words)
                            and title_clean not in categories
                            and title_clean.lower() != slug_normalized):
                        categories.append(title_clean)
                        if len(categories) >= 4:
                            break
        except Exception as e:
            print(f"   ⚠ Wikipedia parse categories ({slug!r}) : {e}")

    return extract, categories


def fetch_history() -> list[tuple[int, str, str]]:
    """
    Récupère l'historique Pédantix via /history.
    Retourne une liste de (puzzle_num, slug, display_name) pour les puzzles résolus seulement.
    Le puzzle actif retourne ['', ''] et est filtré.
    """
    try:
        resp = _session.get(f"{BASE_URL}/history", timeout=10)
        if resp.status_code == 200:
            raw = resp.json()
            result = []
            for entry in raw:
                if isinstance(entry, list) and len(entry) >= 3:
                    num = entry[0]
                    info = entry[2]
                    if isinstance(info, list) and len(info) >= 2 and info[0]:
                        result.append((num, info[0], info[1]))
            return result
    except Exception as e:
        print(f"   ⚠ /history : {e}")
    return []


# ── Construction des indices ───────────────────────────────────────────────────

def build_hints(title_display: str, title_slug: str) -> dict:
    """
    Construit les 3 niveaux d'indices pour un article Pédantix.

    Niveau 1 : nombre de mots, première lettre de chaque mot
    Niveau 2 : catégories Wikipedia (max 4)
    Niveau 3 : premier extrait Wikipedia avec titre masqué
    """
    words = title_display.split()
    first_letters = " · ".join(w[0].upper() for w in words if w)
    n_words = len(words)
    n_chars = len(title_display.replace(" ", ""))
    level1 = [
        f"{n_words} mot{'s' if n_words > 1 else ''} · {n_chars} lettre{'s' if n_chars > 1 else ''}",
        f"Première{'s lettres' if n_words > 1 else ' lettre'} : {first_letters}",
    ]

    extract, categories = fetch_wikipedia_data(title_slug)

    level2 = categories if categories else ["Catégories non disponibles"]

    if extract:
        masked = re.sub(re.escape(title_display), "___", extract, flags=re.IGNORECASE)
        for word in words:
            if len(word) > 3:
                masked = re.sub(re.escape(word), "___", masked, flags=re.IGNORECASE)
        level3 = [masked]
    else:
        level3 = ["Extrait non disponible"]

    return {
        "level1": level1,
        "level2": level2,
        "level3": level3,
        "extract": extract,
        "categories": categories,
    }


# ── Chargement des archives ───────────────────────────────────────────────────

def load_all_archives() -> list[dict]:
    return _load_archives(
        PEDANTIX_ARCHIVE,
        required_keys=["date", "word", "puzzle_num"],
    )


# ── Génération des fichiers ───────────────────────────────────────────────────

def generate_solution_json(
    today: date,
    puzzle_num: int,
    title_slug: str,
    title_display: str,
    hints: dict,
) -> dict:
    data = {
        "date": today.isoformat(),
        "puzzle_num": puzzle_num,
        "word": title_display,          # champ standard pour hub + archives
        "title_slug": title_slug,
        "title_display": title_display,
        "wikipedia_url": f"https://fr.wikipedia.org/wiki/{title_slug}",
        "hints": {
            "level1": hints["level1"],
            "level2": hints["level2"],
            "level3": hints["level3"],
        },
        "extract": hints.get("extract", ""),
        "categories": hints.get("categories", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    PEDANTIX_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(PEDANTIX_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(today: date, data: dict) -> None:
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(
        PEDANTIX_ARCHIVE / f"{today.isoformat()}.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )


# ── Blocs HTML réutilisables ──────────────────────────────────────────────────

def _hints_html(hints: dict) -> tuple[str, str, str]:
    def items_html(items: list) -> str:
        tags = []
        for item in items:
            safe = _html_escape(str(item))
            tags.append(f'<span class="hint-tag">{safe}</span>')
        return "".join(tags)
    return (
        items_html(hints.get("level1", [])),
        items_html(hints.get("level2", [])),
        items_html(hints.get("level3", [])),
    )


def _title_hints_card_html(title_display: str, puzzle_num: int, date_display: str, reveal: bool = False) -> str:
    words = title_display.split()
    n_words = len(words)
    n_chars = len(title_display.replace(" ", ""))
    first_letters = " · ".join(w[0].upper() for w in words if w)
    count_label = f'{n_words}\u202fmot{"s" if n_words > 1 else ""} · {n_chars}\u202flettre{"s" if n_chars > 1 else ""}'

    if reveal:
        return (
            '\n    <div class="card">'
            '\n      <h2>Le titre en d&#233;tail</h2>'
            '\n      <div class="word-hints">'
            '\n        <div class="word-hint-item">'
            '\n          <span class="word-hint-icon">&#128207;</span>'
            '\n          <span class="word-hint-label">Nombre de mots &amp; lettres</span>'
            f'\n          <span class="word-hint-value visible">{count_label}</span>'
            '\n        </div>'
            '\n        <div class="word-hint-item">'
            '\n          <span class="word-hint-icon">&#128288;</span>'
            '\n          <span class="word-hint-label">Premi&#232;re lettre de chaque mot</span>'
            f'\n          <span class="word-hint-value visible">{first_letters}</span>'
            '\n        </div>'
            '\n      </div>'
            '\n    </div>'
        )
    return (
        '\n    <div class="card">'
        '\n      <h2>Indices du titre</h2>'
        '\n      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">'
        f'\n        Article Wikipedia du Pédantix #{puzzle_num} du {date_display}.'
        '\n        Révélez chaque indice selon votre besoin.'
        '\n      </p>'
        '\n      <div class="word-hints">'
        '\n        <div class="word-hint-item">'
        '\n          <span class="word-hint-icon">&#128207;</span>'
        '\n          <span class="word-hint-label">Nombre de mots &amp; lettres</span>'
        f'\n          <span class="word-hint-value" id="wh-count">{count_label}</span>'
        "\n          <button class=\"word-hint-btn\" id=\"wh-count-btn\" onclick=\"revealWordHint('count')\">R&#233;v&#233;ler</button>"
        '\n        </div>'
        '\n        <div class="word-hint-item">'
        '\n          <span class="word-hint-icon">&#128288;</span>'
        '\n          <span class="word-hint-label">Premi&#232;re lettre de chaque mot</span>'
        f'\n          <span class="word-hint-value" id="wh-letters">{first_letters}</span>'
        "\n          <button class=\"word-hint-btn\" id=\"wh-letters-btn\" onclick=\"revealWordHint('letters')\">R&#233;v&#233;ler</button>"
        '\n        </div>'
        '\n      </div>'
        '\n    </div>'
    )


def _faq_items(title_display: str, puzzle_num: int, date_display: str, *, is_index: bool) -> list[tuple[str, str]]:
    n_words = len(title_display.split())
    n_chars = len(title_display.replace(" ", ""))
    items = [(
        f"Quelle est la solution du Pédantix du {date_display} ?",
        f"La réponse du Pédantix #{puzzle_num} du {date_display} est : {title_display}.",
    )]
    if is_index:
        items.append((
            "Qu'est-ce que Pédantix ?",
            "Pédantix est un jeu quotidien basé sur les articles Wikipedia. Il faut deviner un "
            "article secret en proposant des mots qui révèlent les mots de l'article selon leur "
            "similarité sémantique.",
        ))
        items.append((
            "Comment avoir des indices pour Pédantix ?",
            "Cette page propose 3 niveaux d'indices : métadonnées du titre, catégories Wikipedia, "
            "et un extrait masqué de l'article.",
        ))
        items.append((
            f"Combien de mots a le titre du Pédantix du {date_display} ?",
            f"Le titre du Pédantix #{puzzle_num} du {date_display} contient {n_words} mot(s) et {n_chars} lettre(s).",
        ))
    else:
        items.append((
            f"Combien de mots contient le titre du Pédantix du {date_display} ?",
            f"Le titre du Pédantix #{puzzle_num} du {date_display} contient {n_words} mot(s).",
        ))
    return items

def generate_archive_html(
    d: date,
    puzzle_num: int,
    title_display: str,
    title_slug: str,
    hints: dict,
    prev_date,
    next_date,
    extract: str = "",
    categories: list | None = None,
    generated_at: str | None = None,
) -> None:
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    pub_iso = published_iso(d, generated_at, *PUB_TIME)
    og_img = og_image_url("pedantix", d)
    date_display = date_fr(d)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    title_card = _title_hints_card_html(title_display, puzzle_num, date_display, reveal=True)
    wiki_url = f"https://fr.wikipedia.org/wiki/{title_slug}"
    hint_levels = hint_levels_html(
        [
            ("Niveau 1 — Métadonnées du titre", "Informations générales sur le titre de l'article :", hints_l1),
            ("Niveau 2 — Catégories Wikipedia", "L'article appartenait aux catégories :", hints_l2),
            ("Niveau 3 — Extrait Wikipedia", "Premier extrait de l'article (titre masqué) :", hints_l3),
        ],
        mode="plain",
    )
    solution_box = solution_box_html(title_display, reveal=True)
    faq_items = _faq_items(title_display, puzzle_num, date_display, is_index=False)
    faq_visible = faq_html(faq_items, open_first=True)

    nav_prev = (
        f'<a class="nav-link" href="{prev_date.isoformat()}">&#8592; {date_fr(prev_date)}</a>'
        if prev_date else '<span class="nav-disabled">&#8592; Plus ancien</span>'
    )
    nav_next = (
        f'<a class="nav-link" href="{next_date.isoformat()}">{date_fr(next_date)} &#8594;</a>'
        if next_date else '<a class="nav-link" href="../">Solution du jour &#8594;</a>'
    )

    cats_html = ""
    if categories:
        cats_html = " · ".join(_html_escape(c) for c in categories[:3])

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Pédantix #{puzzle_num} du {date_fr_short(d)} : solution et indices</title>
  <meta name="description" content="Solution du Pédantix #{puzzle_num} du {date_display}. Article Wikipedia, indices progressifs et réponse complète.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{PEDANTIX_SITE_URL}/archive/{date_str}">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Pédantix #{puzzle_num} du {date_fr_short(d)} : solution et indices">
  <meta property="og:description" content="Indices et réponse du Pédantix #{puzzle_num} du {date_display}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{PEDANTIX_SITE_URL}/archive/{date_str}">
  <meta property="og:image" content="{og_img}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary_large_image">
  <meta property="article:published_time" content="{pub_iso}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution P\u00e9dantix #{puzzle_num} du {date_display}",
    "datePublished": "{pub_iso}",
    "dateModified": "{pub_iso}",
    "description": "Solution et indices du P\u00e9dantix #{puzzle_num} pour le {date_display}.",
    "url": "{PEDANTIX_SITE_URL}/archive/{date_str}",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{PEDANTIX_SITE_URL}/archive/{date_str}"}},
    "image": ["{og_img}"],
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "https://solution-du-jour.fr/", "logo": {{"@type": "ImageObject", "url": "https://solution-du-jour.fr/logo.png", "width": 512, "height": 512}}}}
  }}
  </script>

{faq_jsonld(faq_items)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "P\u00e9dantix", "item": "https://solution-du-jour.fr/pedantix/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "https://solution-du-jour.fr/pedantix/archive/"}},
      {{"@type": "ListItem", "position": 4, "name": "{month_fr(date_str[:7])}", "item": "{PEDANTIX_SITE_URL}/archive/{date_str[:7]}"}},
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
  <h1>Solution Pédantix #{puzzle_num} du {date_display}</h1>
  <p class="subtitle">Archive · indices &amp; article Wikipedia</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../">Pédantix</a> &rsaquo;
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
      <h2>Pédantix #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez la <strong>solution du Pédantix du {date_display}</strong> (puzzle #{puzzle_num}).
        Consultez les <strong>indices progressifs</strong> avant de révéler l'article Wikipedia secret.
        {f'<span style="font-size:.9rem;color:#6b7280;">{cats_html}</span>' if cats_html else ''}
      </p>
    </div>
{title_card}
    <div class="card">
      <h2>Indices progressifs</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Les indices qui accompagnaient ce puzzle, en clair.
      </p>
{hint_levels}
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
{solution_box}
      <p class="puzzle-meta">Puzzle #{puzzle_num} · {date_display}</p>
      <p style="text-align:center;margin-top:.5rem;">
        <a href="{wiki_url}" rel="noopener" target="_blank"
           style="font-size:.9rem;color:#6b7280;">Voir l'article Wikipedia &#8594;</a>
      </p>
    </div>
{faq_visible}

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
    <a href="https://pedantix.certitudes.org" rel="noopener" target="_blank">Jouer à Pédantix</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""
    atomic_write(PEDANTIX_ARCHIVE / f"{date_str}.html", html)


def generate_month_html(ym: str, entries: list[dict], prev_ym, next_ym) -> None:
    """Génère docs/pedantix/archive/YYYY-MM.html — récap de tous les articles du mois."""
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    month_label = month_fr(ym)
    month_de = de_month_fr(ym)
    count = len(entries)

    def row_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        title = e.get("title_display") or e.get("word", "?")
        return (
            '        <tr>'
            f'<td class="arch-date">{date_fr(d)}</td>'
            f'<td class="arch-num">#{e["puzzle_num"]}</td>'
            f'<td><a class="arch-link" href="{e["date"]}">{_html_escape(title)}</a></td>'
            '</tr>'
        )

    rows_html = "\n".join(row_html(e) for e in entries)
    titles_preview = ", ".join((e.get("title_display") or e.get("word", "?")) for e in entries[:6])

    nav_prev = (
        f'<a class="nav-link" href="{prev_ym}">&#8592; {month_fr(prev_ym)}</a>'
        if prev_ym else '<span class="nav-disabled">&#8592; Mois précédent</span>'
    )
    nav_next = (
        f'<a class="nav-link" href="{next_ym}">{month_fr(next_ym)} &#8594;</a>'
        if next_ym else '<a class="nav-link" href="./">Toutes les archives &#8594;</a>'
    )
    link_prev = f'<link rel="prev" href="{prev_ym}">' if prev_ym else ''
    link_next = f'<link rel="next" href="{next_ym}">' if next_ym else ''

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Pédantix — Tous les articles {month_de}</title>
  <meta name="description" content="Liste complète des articles Wikipedia du Pédantix {month_de} : les {count} articles du jour avec leur date et leur numéro de puzzle.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{PEDANTIX_SITE_URL}/archive/{ym}">
{FEED_LINK_TAG}
  {link_prev}
  {link_next}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Pédantix — Articles {month_de}">
  <meta property="og:description" content="Tous les articles Wikipedia du Pédantix {month_de} ({count} articles).">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{PEDANTIX_SITE_URL}/archive/{ym}">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    "name": "Pédantix — Articles {month_de}",
    "description": "Liste complète des articles Wikipedia du Pédantix {month_de} ({count} articles).",
    "url": "{PEDANTIX_SITE_URL}/archive/{ym}",
    "isPartOf": {{"@type": "WebSite", "name": "Solutions du Jour", "url": "{SITE_URL}/"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Pédantix", "item": "{PEDANTIX_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "{PEDANTIX_SITE_URL}/archive/"}},
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
  <h1>Pédantix — Articles {month_de}</h1>
  <p class="subtitle">{count} article{"s" if count > 1 else ""} du jour</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Pédantix</a> &rsaquo;
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
      <h2>Tous les articles Pédantix {month_de}</h2>
      <p>
        Retrouvez la <strong>liste complète des articles Wikipedia du Pédantix {month_de}</strong> :
        {count} articles du jour ({titles_preview}…), chacun avec sa <strong>date</strong> et son
        <strong>numéro de puzzle</strong>. Cliquez sur un titre pour ouvrir la page détaillée du jour.
      </p>
      <div style="overflow-x:auto;">
        <table class="month-table" style="width:100%;border-collapse:collapse;">
          <thead>
            <tr>
              <th style="text-align:left;">Date</th>
              <th style="text-align:left;">Puzzle</th>
              <th style="text-align:left;">Article</th>
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
    <a href="https://pedantix.certitudes.org" rel="noopener" target="_blank">Jouer à Pédantix</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(PEDANTIX_ARCHIVE / f"{ym}.html", html)


def generate_archive_index(entries: list[dict], months: dict[str, list] | None = None) -> None:
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        title = e.get("title_display") or e.get("word", "?")
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">#{e["puzzle_num"]}</span>'
            f'<a class="arch-link" href="{e["date"]}">{_html_escape(title.upper())}</a>'
            f'</li>'
        )

    items_html = "\n".join(item_html(e) for e in entries)
    count = len(entries)

    months_card = ""
    if months:
        month_links = "\n".join(
            f'        <li><a class="arch-link" href="{ym}">{month_fr(ym)}</a> '
            f'<span class="arch-num">{len(months[ym])} article{"s" if len(months[ym]) > 1 else ""}</span></li>'
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

  <title>Archives Pédantix — Toutes les solutions du jour</title>
  <meta name="description" content="Retrouvez toutes les solutions passées de Pédantix : articles Wikipedia et indices de chaque puzzle.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{PEDANTIX_SITE_URL}/archive/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "P\u00e9dantix", "item": "{PEDANTIX_SITE_URL}/"}},
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
  <h1>Archives Pédantix</h1>
  <p class="subtitle">{count} solution{"s" if count > 1 else ""} enregistrée{"s" if count > 1 else ""}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Pédantix</a> &rsaquo;
  <span>Archives</span>
</nav>
  <div class="card">
    <h2>Toutes les solutions Pédantix ({count})</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Cliquez sur un titre pour voir la solution complète et les indices de ce jour.
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
    <a href="https://pedantix.certitudes.org" rel="noopener" target="_blank">Jouer à Pédantix</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""
    atomic_write(PEDANTIX_ARCHIVE / "index.html", html)


def generate_index_html(
    today: date,
    puzzle_num: int,
    title_display: str,
    title_slug: str,
    hints: dict,
    extract: str = "",
    recent_archives: list | None = None,
    generated_at: str | None = None,
) -> None:
    date_str = today.isoformat()
    date_display = date_fr(today)
    pub_iso = published_iso(today, generated_at, *PUB_TIME)
    og_img = og_image_url("pedantix", today)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    title_card = _title_hints_card_html(title_display, puzzle_num, date_display)
    wiki_url = f"https://fr.wikipedia.org/wiki/{title_slug}"
    hint_levels = hint_levels_html(
        [
            ("&#127777; Niveau 1 — Métadonnées du titre", "Informations générales sur le titre de l'article :", hints_l1),
            ("&#128293; Niveau 2 — Catégories Wikipedia", "L'article appartient aux catégories :", hints_l2),
            ("&#128561; Niveau 3 — Extrait Wikipedia masqué", "Premier extrait de l'article (titre masqué) :", hints_l3),
        ],
        mode="details",
    )
    solution_box = solution_box_html(title_display, reveal=False)
    faq_items = _faq_items(title_display, puzzle_num, date_display, is_index=True)
    faq_visible = faq_html(faq_items, open_first=False)

    recent_archives_card = ""
    if recent_archives:
        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            title = e.get("title_display") or e.get("word", "?")
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">#{e["puzzle_num"]}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}">{_html_escape(title.upper())}</a>'
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

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Pédantix #{puzzle_num} du {date_fr_short(today)} : solution et indices</title>
  <meta name="description" content="Solution du Pédantix #{puzzle_num} du {date_display}. Article Wikipedia secret, indices progressifs et réponse complète.">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
  <link rel="canonical" href="{PEDANTIX_SITE_URL}/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Pédantix #{puzzle_num} du {date_fr_short(today)} : solution et indices">
  <meta property="og:description" content="Article Wikipedia secret et indices progressifs du Pédantix du {date_display}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{PEDANTIX_SITE_URL}/">
  <meta property="og:image" content="{og_img}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary_large_image">
  <meta property="article:published_time" content="{pub_iso}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution P\u00e9dantix #{puzzle_num} du {date_display}",
    "datePublished": "{pub_iso}",
    "dateModified": "{pub_iso}",
    "description": "Solution et indices du P\u00e9dantix #{puzzle_num} pour le {date_display}.",
    "url": "{PEDANTIX_SITE_URL}/",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{PEDANTIX_SITE_URL}/"}},
    "image": ["{og_img}"],
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "https://solution-du-jour.fr/", "logo": {{"@type": "ImageObject", "url": "https://solution-du-jour.fr/logo.png", "width": 512, "height": 512}}}}
  }}
  </script>

{faq_jsonld(faq_items)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "P\u00e9dantix", "item": "https://solution-du-jour.fr/pedantix/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Solution Pédantix #{puzzle_num} du {date_display}</h1>
  <p class="subtitle">Article Wikipedia secret &amp; indices</p>
{updated_block(pub_iso)}
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <span>Pédantix</span>
</nav>
  <article>

    <div class="card">
      <h2>Pédantix #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Vous cherchez la <strong>solution du Pédantix du {date_display}</strong> (puzzle #{puzzle_num}) ?
        Cette page vous propose d'abord des <strong>indices progressifs</strong> pour ne pas
        vous spoiler, puis la <strong>réponse complète</strong> si vous êtes bloqué.
      </p>
    </div>
{title_card}
    <div class="card">
      <h2>Indices progressifs</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Déverrouillez les indices niveau par niveau. Chaque niveau est plus précis que le précédent.
      </p>
{hint_levels}
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
{solution_box}
      <p class="puzzle-meta">Puzzle #{puzzle_num} · Généré automatiquement le {date_display}</p>
      <p style="text-align:center;margin-top:.5rem;" id="wiki-link" style="display:none;">
        <a href="{wiki_url}" rel="noopener" target="_blank"
           style="font-size:.9rem;color:#6b7280;">Voir l'article Wikipedia &#8594;</a>
      </p>
    </div>
{faq_visible}

    <div class="card">
      <h2>Comment jouer à Pédantix ?</h2>
      <p>
        <strong>Pédantix</strong> est un jeu quotidien disponible sur
        <a href="https://pedantix.certitudes.org" rel="noopener" target="_blank">pedantix.certitudes.org</a>.
        Chaque jour, un article Wikipedia secret est à deviner. Les joueurs soumettent des mots
        qui révèlent progressivement les mots de l'article selon leur proximité sémantique.
        Le but est de retrouver le titre de l'article.
      </p>
      <p style="margin-top:.75rem;">
        Pédantix change d'article chaque jour à midi. Cette page est mise à jour automatiquement
        vers 12h05 avec la <strong>solution du jour</strong> et des <strong>indices pédantix</strong>
        pour vous aider sans trop vous spoiler.
      </p>
    </div>

    <div class="card" style="margin-top:.5rem;">
      <h2 style="font-size:1rem;margin-bottom:.75rem;">Autres jeux du jour</h2>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
        <a href="../cemantix/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">&#129504; Cémantix</a>
        <a href="../sutom/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">&#128292; Sutom</a>
        <a href="../tusmo/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">&#129001; Tusmo</a>
        <a href="../loto/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">&#127921; Loto FDJ</a>
        <a href="../euromillions/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">&#11088; EuroMillions</a>
      </div>
    </div>
{recent_archives_card}
  </article>
</main>

<footer>
  <p>Site non officiel — Solution générée automatiquement · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://pedantix.certitudes.org" rel="noopener" target="_blank">pedantix.certitudes.org</a></p>
</footer>

<script>
  function revealSolution() {{
    document.getElementById('solution-wrap').classList.add('revealed');
    document.getElementById('reveal-btn').style.display = 'none';
    var wl = document.getElementById('wiki-link');
    if (wl) wl.style.display = 'block';
  }}

  function revealWordHint(key) {{
    var el = document.getElementById('wh-' + key);
    if (el) el.classList.add('visible');
    var btn = document.getElementById('wh-' + key + '-btn');
    if (btn) btn.style.display = 'none';
  }}
</script>

</body>
</html>"""
    atomic_write(PEDANTIX_DIR / "index.html", html)


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(
    today: date,
    puzzle_num: int,
    title_display: str,
    title_slug: str,
    hints: dict,
    extract: str = "",
    generated_at: str | None = None,
) -> None:
    og_images.word_game("pedantix", today, puzzle_num)
    all_archives = load_all_archives()
    today_str = today.isoformat()
    past_archives = [e for e in all_archives if e["date"] != today_str]

    print(f"[Pédantix] Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        d = date.fromisoformat(entry["date"])
        prev_date = date.fromisoformat(past_archives[i + 1]["date"]) if i + 1 < len(past_archives) else None
        next_date = date.fromisoformat(past_archives[i - 1]["date"]) if i > 0 else None
        e_title = entry.get("title_display") or entry.get("word", "?")
        e_slug = entry.get("title_slug", e_title)
        e_hints = entry.get("hints", {"level1": [], "level2": [], "level3": []})
        e_extract = entry.get("extract", "")
        e_cats = entry.get("categories", [])
        generate_archive_html(
            d, entry["puzzle_num"], e_title, e_slug, e_hints,
            prev_date, next_date, e_extract, e_cats,
            generated_at=entry.get("generated_at"),
        )

    months = group_by_month(past_archives)
    month_keys = list(months.keys())
    print(f"[Pédantix] Génération des pages mensuelles ({len(month_keys)} mois)…")
    for i, ym in enumerate(month_keys):
        next_ym = month_keys[i - 1] if i > 0 else None
        prev_ym = month_keys[i + 1] if i + 1 < len(month_keys) else None
        generate_month_html(ym, months[ym], prev_ym, next_ym)

    print("[Pédantix] Génération de docs/pedantix/archive/index.html…")
    generate_archive_index(past_archives, months)

    recent_archives = [e for e in past_archives[:7] if (PEDANTIX_ARCHIVE / f"{e['date']}.html").exists()]
    print("[Pédantix] Génération de docs/pedantix/index.html…")
    generate_index_html(today, puzzle_num, title_display, title_slug, hints, extract, recent_archives, generated_at)


# ── Point d'entrée ────────────────────────────────────────────────────────────

BACKFILL_LIMIT = 20   # archives écrites max par run (chacune coûte 2 appels Wikipédia)


def _archive_entry(num: int, slug: str, name: str, generated_at: str | None = None) -> dict:
    """Archive d'un puzzle terminé, datée par son numéro (indices via Wikipédia)."""
    hints = build_hints(name, slug)
    return {
        "date": puzzle_date(num).isoformat(),
        "puzzle_num": num,
        "word": name,
        "title_slug": slug,
        "title_display": name,
        "wikipedia_url": f"https://fr.wikipedia.org/wiki/{slug}",
        "hints": {k: hints[k] for k in ("level1", "level2", "level3")},
        "extract": hints.get("extract", ""),
        "categories": hints.get("categories", []),
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
    }


def _sync_finished(finished: list[tuple[int, str, str]], active_num: int) -> int:
    """
    Archive les puzzles terminés (source : /history + lien « page d'hier »).
    Crée les archives manquantes et corrige celles qui ne correspondent pas à la
    source (mauvais puzzle à cette date, ou titre trouvé par le solveur différent).
    Retourne le nombre d'archives écrites.
    """
    written = 0
    for num, slug, name in sorted(finished, reverse=True):
        if written >= BACKFILL_LIMIT:
            break
        if num >= active_num or not slug or not name:
            continue
        path = PEDANTIX_ARCHIVE / f"{puzzle_date(num).isoformat()}.json"
        generated_at = None
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                existing = {}
            if existing.get("puzzle_num") == num and existing.get("word") == name:
                continue
            if existing.get("puzzle_num") == num:
                generated_at = existing.get("generated_at")   # garder l'heure de publication d'origine
            print(f"[Pédantix] ⚠ Archive {path.name} corrigée : "
                  f"#{existing.get('puzzle_num')} {existing.get('word')!r} → #{num} {name!r}")
        else:
            print(f"[Pédantix] Archivage #{num} ({path.stem}) : {name!r}…")
        atomic_write(path, json.dumps(_archive_entry(num, slug, name, generated_at), ensure_ascii=False, indent=2))
        written += 1
    return written


def _redate_archives() -> int:
    """
    Remet chaque archive à la date de son numéro de puzzle (`puzzle_date`).

    Corrige le décalage d'un jour hérité de l'ancien code (qui supposait un changement
    à minuit) : fichiers mal datés, doublons du même puzzle, pages et images sociales
    périmées. Idempotent et sans réseau. Retourne le nombre de dates modifiées.
    """
    by_num: dict[int, dict] = {}
    current: dict[str, int] = {}          # date du fichier → puzzle qu'il contient
    for f in sorted(PEDANTIX_ARCHIVE.glob("????-??-??.json")):
        try:
            entry = json.loads(f.read_text(encoding="utf-8"))
            num = int(entry["puzzle_num"])
        except (ValueError, KeyError, TypeError):
            continue
        current[f.stem] = num
        kept = by_num.get(num)
        # doublon : garder la première publication (heure d'origine pour datePublished)
        if kept is None or (entry.get("generated_at") or "") < (kept.get("generated_at") or "~"):
            by_num[num] = entry
    target = {puzzle_date(n).isoformat(): e for n, e in by_num.items()}

    changed = 0
    for ds in sorted(set(current) | set(target)):
        want = target.get(ds)
        if want is not None and current.get(ds) == want["puzzle_num"] and want.get("date") == ds:
            continue
        changed += 1
        (PEDANTIX_ARCHIVE / f"{ds}.html").unlink(missing_ok=True)   # régénérée si la date reste valide
        img = PEDANTIX_DIR / "img" / f"{ds}.png"
        had_img = img.exists()
        img.unlink(missing_ok=True)                                  # le n° de puzzle y est imprimé
        if want is None:
            (PEDANTIX_ARCHIVE / f"{ds}.json").unlink(missing_ok=True)
            continue
        atomic_write(PEDANTIX_ARCHIVE / f"{ds}.json",
                     json.dumps({**want, "date": ds}, ensure_ascii=False, indent=2))
        if had_img:
            og_images.word_game("pedantix", date.fromisoformat(ds), want["puzzle_num"])
    if changed:
        print(f"[Pédantix] 🔧 {changed} date(s) d'archive recalée(s) sur le numéro de puzzle")
    return changed


def _show_latest_archive(active_num: int) -> None:
    """Repli : afficher la dernière solution connue tant que le puzzle en cours n'est pas résolu."""
    all_archives = load_all_archives()
    if not all_archives:
        return
    latest = all_archives[0]  # archives triées DESC
    title_display = latest.get("title_display") or latest.get("word", "")
    title_slug = latest.get("title_slug", title_display)
    hints = latest.get("hints", {"level1": [], "level2": [], "level3": []})
    print(f"[Pédantix] ℹ Affichage de la dernière solution connue : #{latest.get('puzzle_num')} {title_display!r}")
    _generate_all_html(
        date.fromisoformat(latest["date"]), latest.get("puzzle_num", active_num - 1),
        title_display, title_slug, hints, latest.get("extract", ""), latest.get("generated_at"),
    )


def run(today: date, model=None) -> dict | None:
    """
    Résout le puzzle Pédantix EN COURS et génère toutes les pages.

    La date d'un puzzle vient de son numéro (`puzzle_date`), jamais de `today` :
    Pédantix change à midi, donc à 00h15 le puzzle en cours est daté de la veille.
    `model` : KeyedVectors déjà chargé (sinon le solveur le charge).
    Retourne le dict solution du puzzle en cours, ou None si non résolu.
    """
    from urllib.parse import unquote

    PEDANTIX_DIR.mkdir(parents=True, exist_ok=True)
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    _redate_archives()

    print("[Pédantix] Scraping de la page…")
    active, y_slug, y_name, html = get_page_info()
    active_date = puzzle_date(active)
    print(f"[Pédantix] Puzzle en cours : #{active} (commencé le {date_fr(active_date)} à midi)")

    print("[Pédantix] Récupération de l'historique /history…")
    finished = [(n, s, nm) for n, s, nm in fetch_history() if s]
    print(f"[Pédantix] {len(finished)} puzzles terminés dans /history")
    if y_slug and y_name and all(n != active - 1 for n, _, _ in finished):
        finished.append((active - 1, unquote(y_slug), y_name))
    written = _sync_finished(finished, active)
    if written:
        print(f"[Pédantix] ✅ {written} archive(s) écrite(s)")

    solution_path = PEDANTIX_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("puzzle_num") == active and existing.get("word"):
            if existing.get("date") != active_date.isoformat():   # daté par l'ancien code
                existing["date"] = active_date.isoformat()
                atomic_write(solution_path, json.dumps(existing, ensure_ascii=False, indent=2))
            title_display = existing["word"]
            title_slug = existing.get("title_slug", title_display)
            hints = existing.get("hints", {"level1": [], "level2": [], "level3": []})
            print(f"[Pédantix] ℹ Solution #{active} déjà trouvée : {title_display!r} — régénération HTML uniquement.")
            generate_archive_json(active_date, existing)
            _generate_all_html(active_date, active, title_display, title_slug, hints,
                               existing.get("extract", ""), existing.get("generated_at"))
            return existing

    solved = None
    if html:
        try:
            from games.pedantix_solver import solve as solve_live
            solved = solve_live(html=html, model=model, expected_puzzle=active)
        except Exception as e:
            print(f"[Pédantix] ❌ Solveur en erreur : {e}")
    if not solved or solved[0] != active:
        print(f"[Pédantix] ⏳ Puzzle #{active} non résolu.")
        _show_latest_archive(active)
        return None

    _, title_display, title_slug = solved
    print(f"[Pédantix] ✅ Solution #{active} : {title_display!r} ({title_slug})")
    print("[Pédantix] Récupération des indices Wikipedia…")
    hints = build_hints(title_display, title_slug)
    extract = hints.get("extract", "")
    print(f"[Pédantix]    Catégories : {hints.get('categories', [])}")

    data = generate_solution_json(active_date, active, title_slug, title_display, hints)
    generate_archive_json(active_date, data)
    _generate_all_html(active_date, active, title_display, title_slug, hints, extract, data.get("generated_at"))

    print(f"[Pédantix] 🎉 Site généré ({active_date.isoformat()}, #{active}, {title_display!r})")
    return data
