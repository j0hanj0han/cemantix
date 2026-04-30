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

from core import SITE_URL, DOCS_DIR, _session, date_fr, atomic_write, load_all_archives as _load_archives

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
_HEADERS_JSON = {
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}

# Point de référence pour calculer le numéro de puzzle par la date
_REF_DATE = date(2026, 4, 26)
_REF_PUZZLE = 1444



# ── API Pédantix ──────────────────────────────────────────────────────────────

def get_page_info() -> tuple[int, str | None, str | None]:
    """
    Scrape la page Pédantix.
    Retourne (puzzle_num, yesterday_slug, yesterday_name).
    yesterday_slug/name = None si non trouvé.
    """
    from bs4 import BeautifulSoup
    puzzle_num = _REF_PUZZLE + (date.today() - _REF_DATE).days
    yesterday_slug = yesterday_name = None
    try:
        resp = _session.get(BASE_URL, headers=_HEADERS_FORM, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="script")
        if script and "data-puzzle-number" in script.attrs:
            puzzle_num = int(script["data-puzzle-number"])
        # Extraire la réponse d'hier depuis l'élément <a id="yesterday">
        yesterday_el = soup.find("a", id="yesterday")
        if yesterday_el:
            href = yesterday_el.get("href", "")
            if "/wiki/" in href:
                yesterday_slug = href.split("/wiki/")[-1]
            yesterday_name = yesterday_el.get_text(strip=True)
    except Exception as e:
        print(f"   ⚠ Erreur scraping page Pédantix : {e}")
    return puzzle_num, yesterday_slug, yesterday_name


def get_puzzle_number() -> int:
    puzzle_num, _, _ = get_page_info()
    return puzzle_num


def _score_candidate(word: str, puzzle_num: int) -> dict | None:
    time.sleep(0.3)
    try:
        resp = _session.post(
            f"{BASE_URL}/score?n={puzzle_num}",
            data=json.dumps({"num": puzzle_num, "word": word, "answer": []}),
            headers=_HEADERS_JSON,
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        print(f"   ⚠ /score ({word!r}) : {e}")
    return None


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


def solve(puzzle_num: int) -> tuple[str | None, str | None]:
    """
    Trouve le titre de l'article Pédantix via l'endpoint /history.
    Retourne (slug, display_name) ou (None, None) si échec.
    """
    print(f"[Pédantix] Résolution puzzle #{puzzle_num} via /history…")
    history = fetch_history()
    for num, slug, name in history:
        if num == puzzle_num:
            print(f"[Pédantix] ✅ Trouvé dans /history : {name!r}")
            return slug, name
    print(f"[Pédantix] ❌ Puzzle #{puzzle_num} introuvable dans /history ({len(history)} entrées).")
    return None, None


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


def _title_hints_card_html(title_display: str, puzzle_num: int, date_display: str) -> str:
    words = title_display.split()
    n_words = len(words)
    n_chars = len(title_display.replace(" ", ""))
    first_letters = " · ".join(w[0].upper() for w in words if w)
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
        f'\n          <span class="word-hint-value" id="wh-count">'
        f'{n_words}\u202fmot{"s" if n_words > 1 else ""} · {n_chars}\u202flettre{"s" if n_chars > 1 else ""}'
        '\n          </span>'
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


# ── Pages HTML ────────────────────────────────────────────────────────────────

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
) -> None:
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    date_display = date_fr(d)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    title_card = _title_hints_card_html(title_display, puzzle_num, date_display)
    wiki_url = f"https://fr.wikipedia.org/wiki/{title_slug}"

    nav_prev = (
        f'<a class="nav-link" href="{prev_date.isoformat()}.html">&#8592; {date_fr(prev_date)}</a>'
        if prev_date else '<span class="nav-disabled">&#8592; Plus ancien</span>'
    )
    nav_next = (
        f'<a class="nav-link" href="{next_date.isoformat()}.html">{date_fr(next_date)} &#8594;</a>'
        if next_date else '<a class="nav-link" href="../index.html">Solution du jour &#8594;</a>'
    )

    cats_html = ""
    if categories:
        cats_html = " · ".join(_html_escape(c) for c in categories[:3])

    faq_extra = f""",
      {{
        "@type": "Question",
        "name": "Combien de mots contient le titre du P\u00e9dantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le titre du P\u00e9dantix #{puzzle_num} du {date_display} contient {len(title_display.split())} mot(s)."
        }}
      }}"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Pédantix #{puzzle_num} — Solution du {date_display}</title>
  <meta name="description" content="Solution du Pédantix #{puzzle_num} du {date_display}. Article Wikipedia, indices progressifs et réponse complète.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{PEDANTIX_SITE_URL}/archive/{date_str}">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Pédantix #{puzzle_num} — Solution du {date_display}">
  <meta property="og:description" content="Indices et réponse du Pédantix #{puzzle_num} du {date_display}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{PEDANTIX_SITE_URL}/archive/{date_str}">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution P\u00e9dantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices du P\u00e9dantix #{puzzle_num} pour le {date_display}.",
    "url": "{PEDANTIX_SITE_URL}/archive/{date_str}",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{PEDANTIX_SITE_URL}/archive/{date_str}"}},
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "https://solution-du-jour.fr/"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {{
        "@type": "Question",
        "name": "Quelle est la solution du P\u00e9dantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La r\u00e9ponse du P\u00e9dantix #{puzzle_num} du {date_display} est : {title_display}."
        }}
      }}{faq_extra}
    ]
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "P\u00e9dantix", "item": "https://solution-du-jour.fr/pedantix/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "https://solution-du-jour.fr/pedantix/archive/"}},
      {{"@type": "ListItem", "position": 4, "name": "Solution du {date_display}"}}
    ]
  }}
  </script>
  {f'<link rel="prev" href="{prev_date.isoformat()}.html">' if prev_date else ''}
  {f'<link rel="next" href="{next_date.isoformat()}.html">' if next_date else ''}

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Pédantix #{puzzle_num} — Solution du {date_display}</h1>
  <p class="subtitle">Archive · indices &amp; article Wikipedia</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../index.html">Pédantix</a> &rsaquo;
  <a href="index.html">Archives</a> &rsaquo;
  <span>Solution du {date_display}</span>
</nav>
  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="index.html">Toutes les archives</a>
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
        Déverrouillez les indices niveau par niveau. Chaque niveau est plus précis que le précédent.
      </p>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l1" onclick="revealHint(1)">
          &#127777; Niveau 1 — Métadonnées du titre (cliquer pour révéler)
        </button>
        <div class="hint-content" id="content-l1">
          <p>Informations générales sur le titre de l'article :</p>
          <div class="hint-words">{hints_l1 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l2" onclick="revealHint(2)" disabled>
          &#128293; Niveau 2 — Catégories Wikipedia (déverrouillé après niveau 1)
        </button>
        <div class="hint-content" id="content-l2">
          <p>L'article appartient aux catégories :</p>
          <div class="hint-words">{hints_l2 or "<em>Aucune catégorie disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l3" onclick="revealHint(3)" disabled>
          &#128561; Niveau 3 — Extrait Wikipedia masqué (déverrouillé après niveau 2)
        </button>
        <div class="hint-content" id="content-l3">
          <p>Premier extrait de l'article (titre masqué) :</p>
          <div class="hint-words">{hints_l3 or "<em>Extrait non disponible</em>"}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="solution-blur" id="solution-wrap">
          <span class="solution-word">{title_display}</span>
        </div>
        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">
          Cliquer pour révéler la réponse
        </button>
      </div>
      <p class="puzzle-meta">Puzzle #{puzzle_num} · {date_display}</p>
      <p style="text-align:center;margin-top:.5rem;">
        <a href="{wiki_url}" rel="noopener" target="_blank"
           style="font-size:.9rem;color:#6b7280;">Voir l'article Wikipedia &#8594;</a>
      </p>
    </div>

  </article>

  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="index.html">Toutes les archives</a>
    {nav_next}
  </nav>
</main>

<footer>
  <p>
    <a href="../index.html">Solution du jour</a> ·
    <a href="index.html">Archives</a> ·
    <a href="https://pedantix.certitudes.org" rel="noopener" target="_blank">Jouer à Pédantix</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

<script>
  var revealed = [false, false, false];

  function revealHint(level) {{
    if (level > 1 && !revealed[level - 2]) return;
    var btn = document.getElementById('btn-l' + level);
    var content = document.getElementById('content-l' + level);
    content.classList.add('visible');
    btn.disabled = true;
    revealed[level - 1] = true;
    var next = level + 1;
    if (next <= 3) {{
      var nextBtn = document.getElementById('btn-l' + next);
      if (nextBtn) nextBtn.disabled = false;
    }}
  }}

  function revealSolution() {{
    document.getElementById('solution-wrap').classList.add('revealed');
    document.getElementById('reveal-btn').style.display = 'none';
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
    atomic_write(PEDANTIX_ARCHIVE / f"{date_str}.html", html)


def generate_archive_index(entries: list[dict]) -> None:
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        title = e.get("title_display") or e.get("word", "?")
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">#{e["puzzle_num"]}</span>'
            f'<a class="arch-link" href="{e["date"]}.html">{_html_escape(title.upper())}</a>'
            f'</li>'
        )

    items_html = "\n".join(item_html(e) for e in entries)
    count = len(entries)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Archives Pédantix — Toutes les solutions du jour</title>
  <meta name="description" content="Retrouvez toutes les solutions passées de Pédantix : articles Wikipedia et indices de chaque puzzle.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{PEDANTIX_SITE_URL}/archive/">
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
  <a href="../index.html">Pédantix</a> &rsaquo;
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

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../index.html">Solution du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../index.html">Solution du jour</a> ·
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
) -> None:
    date_str = today.isoformat()
    date_display = date_fr(today)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    title_card = _title_hints_card_html(title_display, puzzle_num, date_display)
    wiki_url = f"https://fr.wikipedia.org/wiki/{title_slug}"
    n_words = len(title_display.split())
    n_chars = len(title_display.replace(" ", ""))

    faq_extra = f""",
      {{
        "@type": "Question",
        "name": "Combien de mots a le titre du P\u00e9dantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le titre du P\u00e9dantix #{puzzle_num} du {date_display} contient {n_words} mot(s) et {n_chars} lettre(s)."
        }}
      }}"""

    recent_archives_card = ""
    if recent_archives:
        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            title = e.get("title_display") or e.get("word", "?")
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">#{e["puzzle_num"]}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}.html">{_html_escape(title.upper())}</a>'
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

  <title>&#129504; Pédantix #{puzzle_num} — Solution du {date_display}</title>
  <meta name="description" content="Solution du Pédantix #{puzzle_num} du {date_display}. Article Wikipedia secret, indices progressifs et réponse complète.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{PEDANTIX_SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Pédantix #{puzzle_num} — Solution du {date_display}">
  <meta property="og:description" content="Article Wikipedia secret et indices progressifs du Pédantix du {date_display}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{PEDANTIX_SITE_URL}/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution P\u00e9dantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices du P\u00e9dantix #{puzzle_num} pour le {date_display}.",
    "url": "{PEDANTIX_SITE_URL}/",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{PEDANTIX_SITE_URL}/"}},
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "https://solution-du-jour.fr/"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {{
        "@type": "Question",
        "name": "Quelle est la solution du P\u00e9dantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La r\u00e9ponse du P\u00e9dantix #{puzzle_num} du {date_display} est : {title_display}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Qu'est-ce que P\u00e9dantix ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "P\u00e9dantix est un jeu quotidien bas\u00e9 sur les articles Wikipedia. Il faut deviner un article secret en proposant des mots qui r\u00e9v\u00e8lent les mots de l'article selon leur similarit\u00e9 s\u00e9mantique."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Comment avoir des indices pour P\u00e9dantix ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Cette page propose 3 niveaux d'indices : m\u00e9tadonn\u00e9es du titre, cat\u00e9gories Wikipedia, et un extrait masqu\u00e9 de l'article."
        }}
      }}{faq_extra}
    ]
  }}
  </script>

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
  <h1>Pédantix — Solution du jour</h1>
  <p class="subtitle">Article Wikipedia secret &amp; indices — #{puzzle_num}</p>
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

      <div class="hint-level">
        <button class="hint-btn" id="btn-l1" onclick="revealHint(1)">
          &#127777; Niveau 1 — Métadonnées du titre (cliquer pour révéler)
        </button>
        <div class="hint-content" id="content-l1">
          <p>Informations sur le titre de l'article Wikipedia :</p>
          <div class="hint-words">{hints_l1 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l2" onclick="revealHint(2)" disabled>
          &#128293; Niveau 2 — Catégories Wikipedia (déverrouillé après niveau 1)
        </button>
        <div class="hint-content" id="content-l2">
          <p>L'article secret appartient aux catégories :</p>
          <div class="hint-words">{hints_l2 or "<em>Aucune catégorie disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l3" onclick="revealHint(3)" disabled>
          &#128561; Niveau 3 — Extrait masqué (déverrouillé après niveau 2)
        </button>
        <div class="hint-content" id="content-l3">
          <p>Début de l'article Wikipedia (titre masqué) :</p>
          <div class="hint-words">{hints_l3 or "<em>Extrait non disponible</em>"}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="solution-blur" id="solution-wrap">
          <span class="solution-word">{title_display}</span>
        </div>
        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">
          Cliquer pour révéler la réponse
        </button>
      </div>
      <p class="puzzle-meta">Puzzle #{puzzle_num} · Généré automatiquement le {date_display}</p>
      <p style="text-align:center;margin-top:.5rem;" id="wiki-link" style="display:none;">
        <a href="{wiki_url}" rel="noopener" target="_blank"
           style="font-size:.9rem;color:#6b7280;">Voir l'article Wikipedia &#8594;</a>
      </p>
    </div>

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
        Cette page est mise à jour automatiquement chaque matin avec la <strong>solution du jour</strong>
        et des <strong>indices pédantix</strong> pour vous aider sans trop vous spoiler.
      </p>
    </div>

    <div class="card" style="margin-top:.5rem;">
      <h2 style="font-size:1rem;margin-bottom:.75rem;">Autres jeux du jour</h2>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
        <a href="../cemantix/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">&#129504; Cémantix</a>
        <a href="../sutom/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">&#128292; Sutom</a>
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
  var revealed = [false, false, false];

  function revealHint(level) {{
    if (level > 1 && !revealed[level - 2]) return;
    var btn = document.getElementById('btn-l' + level);
    var content = document.getElementById('content-l' + level);
    content.classList.add('visible');
    btn.disabled = true;
    revealed[level - 1] = true;
    var next = level + 1;
    if (next <= 3) {{
      var nextBtn = document.getElementById('btn-l' + next);
      if (nextBtn) nextBtn.disabled = false;
    }}
  }}

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
) -> None:
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
        )

    print("[Pédantix] Génération de docs/pedantix/archive/index.html…")
    generate_archive_index(past_archives)

    recent_archives = [e for e in past_archives[:7] if (PEDANTIX_ARCHIVE / f"{e['date']}.html").exists()]
    print("[Pédantix] Génération de docs/pedantix/index.html…")
    generate_index_html(today, puzzle_num, title_display, title_slug, hints, extract, recent_archives)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def _archive_yesterday(today: date, puzzle_num: int, yesterday_slug: str, yesterday_name: str) -> None:
    """
    Archive la solution d'hier si elle n'est pas déjà en base.
    Appelle les endpoints Wikipedia pour obtenir l'extrait et les catégories.
    """
    yesterday = today - timedelta(days=1)
    yesterday_str = yesterday.isoformat()
    archive_json_path = PEDANTIX_ARCHIVE / f"{yesterday_str}.json"
    if archive_json_path.exists():
        return  # déjà archivé

    print(f"[Pédantix] Archivage réponse d'hier : {yesterday_name!r} ({yesterday_str})…")
    hints = build_hints(yesterday_name, yesterday_slug)
    yesterday_data = {
        "date": yesterday_str,
        "puzzle_num": puzzle_num - 1,
        "word": yesterday_name,
        "title_slug": yesterday_slug,
        "title_display": yesterday_name,
        "wikipedia_url": f"https://fr.wikipedia.org/wiki/{yesterday_slug}",
        "hints": {
            "level1": hints["level1"],
            "level2": hints["level2"],
            "level3": hints["level3"],
        },
        "extract": hints.get("extract", ""),
        "categories": hints.get("categories", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(archive_json_path, json.dumps(yesterday_data, ensure_ascii=False, indent=2))
    print(f"[Pédantix] ✅ Archive d'hier créée : {yesterday_str}.json")


def run(today: date) -> dict | None:
    """Lance solve + génère tous les fichiers Pédantix. Retourne le dict data ou None."""
    PEDANTIX_DIR.mkdir(parents=True, exist_ok=True)
    PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)

    # Scraper le puzzle number ET la réponse d'hier en une seule requête
    print("[Pédantix] Scraping de la page…")
    puzzle_num, yesterday_slug, yesterday_name = get_page_info()
    print(f"[Pédantix] Puzzle du jour : #{puzzle_num}")
    if yesterday_name:
        print(f"[Pédantix] Réponse d'hier : {yesterday_name!r}")

    # Récupérer l'historique complet pour backfill + solve
    print("[Pédantix] Récupération de l'historique /history…")
    history = fetch_history()
    print(f"[Pédantix] {len(history)} entrées dans /history")

    # Backfill des archives passées depuis /history (max 20 par run pour ne pas surcharger Wikipedia)
    history_map = {num: (slug, name) for num, slug, name in history if slug}
    backfilled = 0
    BACKFILL_LIMIT = 20
    for num, slug, name in sorted(history, key=lambda x: x[0], reverse=True):
        if backfilled >= BACKFILL_LIMIT:
            break
        if num >= puzzle_num or not slug or not name:
            continue  # puzzle du jour / futur ou réponse non révélée
        days_ago = puzzle_num - num
        past_date = today - timedelta(days=days_ago)
        archive_path = PEDANTIX_ARCHIVE / f"{past_date.isoformat()}.json"
        if not archive_path.exists():
            print(f"[Pédantix] Backfill #{num} ({past_date.isoformat()}) : {name!r}…")
            hints = build_hints(name, slug)
            past_data = {
                "date": past_date.isoformat(),
                "puzzle_num": num,
                "word": name,
                "title_slug": slug,
                "title_display": name,
                "wikipedia_url": f"https://fr.wikipedia.org/wiki/{slug}",
                "hints": {
                    "level1": hints["level1"],
                    "level2": hints["level2"],
                    "level3": hints["level3"],
                },
                "extract": hints.get("extract", ""),
                "categories": hints.get("categories", []),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            PEDANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
            atomic_write(archive_path, json.dumps(past_data, ensure_ascii=False, indent=2))
            backfilled += 1
    if backfilled:
        print(f"[Pédantix] ✅ {backfilled} archives backfillées depuis /history")

    # Archiver la réponse d'hier si disponible (via scraping HTML — priorité sur /history)
    if yesterday_slug and yesterday_name:
        _archive_yesterday(today, puzzle_num, yesterday_slug, yesterday_name)

    # Vérifier si la solution est déjà générée pour aujourd'hui
    solution_path = PEDANTIX_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == today.isoformat() and existing.get("word"):
            title_display = existing["word"]
            title_slug = existing.get("title_slug", title_display)
            hints = existing.get("hints", {"level1": [], "level2": [], "level3": []})
            extract = existing.get("extract", "")
            print(f"[Pédantix] ℹ Solution déjà présente : {title_display!r} — régénération HTML uniquement.")
            generate_archive_json(today, existing)
            _generate_all_html(today, puzzle_num, title_display, title_slug, hints, extract)
            return existing

    print(f"[Pédantix] Résolution du puzzle #{puzzle_num}…")
    if puzzle_num in history_map:
        title_slug, title_display = history_map[puzzle_num]
        print(f"[Pédantix] ✅ Trouvé dans /history : {title_display!r}")
    else:
        title_slug, title_display = None, None
        print(f"[Pédantix] ❌ Puzzle #{puzzle_num} absent de /history.")

    if not title_display:
        # /history ne révèle pas encore le puzzle actif — utiliser la dernière archive connue
        print("[Pédantix] ⏳ Solution du jour non disponible dans /history (puzzle actif).")
        all_archives = load_all_archives()
        if all_archives:
            latest = all_archives[0]  # archives triées DESC
            title_display = latest.get("title_display") or latest.get("word", "")
            title_slug = latest.get("title_slug", title_display)
            hints = latest.get("hints", {"level1": [], "level2": [], "level3": []})
            extract = latest.get("extract", "")
            latest_puzzle_num = latest.get("puzzle_num", puzzle_num - 1)
            print(f"[Pédantix] ℹ Affichage de la dernière solution connue : #{latest_puzzle_num} {title_display!r}")
            _generate_all_html(today, latest_puzzle_num, title_display, title_slug, hints, extract)
        return None

    print(f"[Pédantix] ✅ Solution : {title_display!r}")

    print("[Pédantix] Récupération des indices Wikipedia…")
    hints = build_hints(title_display, title_slug)
    extract = hints.get("extract", "")
    print(f"[Pédantix]    Catégories : {hints.get('categories', [])}")
    if extract:
        print(f"[Pédantix]    Extrait : {extract[:60]}…")

    data = generate_solution_json(today, puzzle_num, title_slug, title_display, hints)
    generate_archive_json(today, data)
    _generate_all_html(today, puzzle_num, title_display, title_slug, hints, extract)

    print(f"[Pédantix] 🎉 Site généré ({today.isoformat()}, #{puzzle_num}, {title_display!r})")
    return data
