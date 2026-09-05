"""
games/cemantix.py — Logique complète du jeu Cémantix.

Génère :
  docs/cemantix/solution.json
  docs/cemantix/index.html
  docs/cemantix/archive/YYYY-MM-DD.json
  docs/cemantix/archive/YYYY-MM-DD.html
  docs/cemantix/archive/index.html
"""

import json
import re
from datetime import date, datetime, timezone
from html import escape as _html_escape
from pathlib import Path

from core import (
    SITE_URL, DOCS_DIR, _session, date_fr, date_fr_short, atomic_write, load_all_archives as _load_archives,
    iso_paris, FEED_LINK_TAG, updated_block, utc_iso_to_paris,
    hint_levels_html, solution_box_html, faq_jsonld, faq_html,
    fetch_definition, render_page,
)

# ── Configuration Cémantix ────────────────────────────────────────────────────

BASE_URL = "https://cemantix.certitudes.org"
CEMANTIX_DIR = DOCS_DIR / "cemantix"
CEMANTIX_ARCHIVE = CEMANTIX_DIR / "archive"
CEMANTIX_SITE_URL = f"{SITE_URL}/cemantix"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}

# Point de référence pour calculer le numéro de puzzle par la date
_REF_DATE = date(2026, 2, 28)
_REF_PUZZLE = 1459


# ── API Cémantix ──────────────────────────────────────────────────────────────

def get_puzzle_number() -> int:
    """
    Récupère le numéro du puzzle depuis le HTML du site.
    Fallback : calcul à partir d'un point de référence connu.
    """
    from bs4 import BeautifulSoup
    try:
        resp = _session.get(BASE_URL, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="script")
        if script and "data-puzzle-number" in script.attrs:
            return int(script["data-puzzle-number"])
        print("   ⚠ Tag <script id='script'> non trouvé — utilisation du fallback date")
    except Exception as e:
        print(f"   ⚠ Erreur lors de la récupération du puzzle : {e}")

    delta = (date.today() - _REF_DATE).days
    puzzle_num = _REF_PUZZLE + delta
    print(f"   Fallback : puzzle #{puzzle_num} (calculé à partir du {_REF_DATE.isoformat()} = #{_REF_PUZZLE})")
    return puzzle_num


def get_nearby(word: str, puzzle_num: int) -> list[dict]:
    """
    Appelle /nearby (POST) pour récupérer les voisins de la solution.
    Retourne une liste triée par percentile ASC.
    """
    try:
        resp = _session.post(
            f"{BASE_URL}/nearby?n={puzzle_num}",
            data=f"word={word}",
            headers=HEADERS,
            timeout=15,
        )
        data = resp.json()
        if isinstance(data, dict):
            results = []
            for w, val in data.items():
                if isinstance(val, (list, tuple)) and len(val) >= 2:
                    results.append({
                        "word": str(w),
                        "percentile": int(val[0]),
                        "similarity": float(val[1]),
                    })
            return sorted(results, key=lambda x: x["percentile"])
    except Exception as e:
        print(f"   ⚠ Erreur /nearby : {e}")
    return []


# ── Sélection des indices ─────────────────────────────────────────────────────

def select_hints(nearby: list[dict]) -> dict:
    """
    Sélectionne 3 niveaux d'indices depuis la liste des voisins triés.

    Niveau 1 (vague)       : percentile ~200-400
    Niveau 2 (proche)      : percentile ~500-700
    Niveau 3 (très proche) : percentile ~800-950
    """
    def pick(lo: int, hi: int, count: int = 3) -> list[dict]:
        candidates = [
            {"word": item["word"], "percentile": item["percentile"]}
            for item in nearby
            if lo <= item["percentile"] <= hi
        ]
        if len(candidates) <= count:
            return candidates
        step = len(candidates) // count
        return [candidates[i * step] for i in range(count)]

    return {
        "level1": pick(200, 400),
        "level2": pick(500, 700),
        "level3": pick(800, 950),
    }


def enrich_hints_with_definitions(hints: dict) -> dict:
    """Ajoute la définition Wikipedia à chaque mot-indice (si non déjà présente)."""
    for level in ("level1", "level2", "level3"):
        for item in hints.get(level, []):
            if isinstance(item, dict) and "definition" not in item:
                item["definition"] = fetch_definition(item["word"])
    return hints


# ── Chargement des archives ───────────────────────────────────────────────────

def load_all_archives() -> list[dict]:
    return _load_archives(CEMANTIX_ARCHIVE, required_keys=["date", "word", "puzzle_num"])


# ── Génération des fichiers ───────────────────────────────────────────────────

def _compute_nearby_top(nearby: list[dict], count: int = 20) -> list[dict]:
    """Les `count` voisins les plus proches (percentile le plus élevé) triés DESC."""
    return [
        {"word": item["word"], "percentile": item["percentile"], "similarity": item["similarity"]}
        for item in sorted(nearby, key=lambda x: x["percentile"], reverse=True)[:count]
    ]


def generate_solution_json(
    today: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    tried_count: int,
    definition: str = "",
    nearby: list[dict] | None = None,
) -> dict:
    data = {
        "date": today.isoformat(),
        "puzzle_num": puzzle_num,
        "word": word,
        "first_letter": word[0].upper() if word else "",
        "word_length": len(word),
        "definition": definition,
        "hints": hints,
        "tried_count": tried_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if nearby:
        data["nearby_top"] = _compute_nearby_top(nearby)
    CEMANTIX_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(CEMANTIX_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(today: date, data: dict) -> None:
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(CEMANTIX_ARCHIVE / f"{today.isoformat()}.json",
                 json.dumps(data, ensure_ascii=False, indent=2))


def _mask_word(word: str, text: str) -> str:
    """Remplace le mot (insensible à la casse) par ___ dans le texte."""
    return re.sub(re.escape(word), "___", text, flags=re.IGNORECASE)


def _word_hints_card_html(word: str, definition: str, reveal: bool = False) -> str:
    """Card 'Indices du mot' / 'Le mot en détail' : nb lettres, première lettre, définition.
    reveal=False (page du jour) : chaque valeur masquée derrière un bouton JS (anti-spoiler).
    reveal=True (archive passée) : tout affiché directement, sans JS."""
    first_letter = word[0].upper() if word else "?"
    n = len(word)
    letters_label = f"{n} lettre{'s' if n > 1 else ''}"

    if reveal:
        def_row = ""
        if definition:
            def_row = (
                '\n        <div class="word-hint-item">'
                '\n          <span class="word-hint-icon">&#128218;</span>'
                '\n          <span class="word-hint-label">D&#233;finition</span>'
                f'\n          <span class="word-hint-value visible definition">{_html_escape(definition)}</span>'
                '\n        </div>'
            )
        return (
            '\n    <div class="card">'
            '\n      <h2>Le mot en d&#233;tail</h2>'
            '\n      <div class="word-hints">'
            '\n        <div class="word-hint-item">'
            '\n          <span class="word-hint-icon">&#128207;</span>'
            '\n          <span class="word-hint-label">Nombre de lettres</span>'
            f'\n          <span class="word-hint-value visible">{letters_label}</span>'
            '\n        </div>'
            '\n        <div class="word-hint-item">'
            '\n          <span class="word-hint-icon">&#128288;</span>'
            '\n          <span class="word-hint-label">Premi&#232;re lettre</span>'
            f'\n          <span class="word-hint-value visible">{first_letter}</span>'
            '\n        </div>'
            f'{def_row}'
            '\n      </div>'
            '\n    </div>'
        )

    def_row = ""
    if definition:
        masked = _html_escape(_mask_word(word, definition))
        def_row = (
            '\n        <div class="word-hint-item">'
            '\n          <span class="word-hint-icon">&#128218;</span>'
            '\n          <span class="word-hint-label">D&#233;finition</span>'
            f'\n          <span class="word-hint-value definition" id="wh-def">{masked}</span>'
            "\n          <button class=\"word-hint-btn\" id=\"wh-def-btn\" onclick=\"revealWordHint('def')\">R&#233;v&#233;ler</button>"
            '\n        </div>'
        )
    return (
        '\n    <div class="card">'
        '\n      <h2>Indices du mot</h2>'
        '\n      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">'
        '\n        R&#233;v&#233;lez chaque indice s&#233;par&#233;ment — du moins au plus spoiler.'
        '\n      </p>'
        '\n      <div class="word-hints">'
        '\n        <div class="word-hint-item">'
        '\n          <span class="word-hint-icon">&#128207;</span>'
        '\n          <span class="word-hint-label">Nombre de lettres</span>'
        f'\n          <span class="word-hint-value" id="wh-length">{letters_label}</span>'
        "\n          <button class=\"word-hint-btn\" id=\"wh-length-btn\" onclick=\"revealWordHint('length')\">R&#233;v&#233;ler</button>"
        '\n        </div>'
        '\n        <div class="word-hint-item">'
        '\n          <span class="word-hint-icon">&#128288;</span>'
        '\n          <span class="word-hint-label">Premi&#232;re lettre</span>'
        f'\n          <span class="word-hint-value" id="wh-letter">{first_letter}</span>'
        "\n          <button class=\"word-hint-btn\" id=\"wh-letter-btn\" onclick=\"revealWordHint('letter')\">R&#233;v&#233;ler</button>"
        '\n        </div>'
        f'{def_row}'
        '\n      </div>'
        '\n    </div>'
    )


def _hints_html(hints: dict) -> tuple:
    """Retourne (hints_l1, hints_l2, hints_l3) comme chaînes HTML.
    Gère les deux formats : liste de str (archives anciennes) et liste de dicts (nouveau).
    """
    def words_html(words: list) -> str:
        tags = []
        for item in words:
            if isinstance(item, dict):
                w = item["word"]
                defn = item.get("definition", "")
                if defn:
                    safe_defn = _html_escape(defn.replace('\n', ' ').replace('\r', ''))
                    attr = (
                        f' data-def="{safe_defn}"'
                        f' onclick="toggleDef(this)"'
                    )
                else:
                    attr = ""
                tags.append(f'<span class="hint-tag"{attr}>{w}</span>')
            else:
                tags.append(f'<span class="hint-tag">{item}</span>')
        return "".join(tags)
    return (
        words_html(hints.get("level1", [])),
        words_html(hints.get("level2", [])),
        words_html(hints.get("level3", [])),
    )


def _faq_items(word: str, puzzle_num: int, date_display: str, definition: str, *, is_index: bool) -> list[tuple[str, str]]:
    first_letter = word[0].upper() if word else "?"
    word_length = len(word)
    plural = "s" if word_length > 1 else ""
    items = [(
        f"Quelle est la solution du Cémantix du {date_display} ?",
        f"La réponse du Cémantix #{puzzle_num} du {date_display} est : {word}.",
    )]
    if is_index:
        items.append((
            "Qu'est-ce que Cémantix ?",
            "Cémantix est un jeu de mots quotidien basé sur la similarité sémantique. Chaque jour, "
            "un mot secret est à deviner en soumettant des propositions et en recevant un score de "
            "proximité sémantique sous forme de température.",
        ))
        items.append((
            "Comment avoir des indices pour Cémantix ?",
            f"Cette page propose 3 niveaux d'indices progressifs : des mots sémantiquement tièdes, "
            f"chauds, puis brûlants. Déverrouillez chaque niveau selon votre besoin pour le Cémantix "
            f"du {date_display}.",
        ))
    items.append((
        f"Quelle est la première lettre du Cémantix du {date_display} ?",
        f"La première lettre du Cémantix #{puzzle_num} du {date_display} est : {first_letter}.",
    ))
    items.append((
        f"Combien de lettres contient le mot du Cémantix du {date_display} ?",
        f"Le mot du Cémantix #{puzzle_num} du {date_display} contient {word_length} lettre{plural}.",
    ))
    if definition:
        items.append((
            f"Quelle est la définition du mot du Cémantix du {date_display} ?",
            definition,
        ))
    return items


def _nearby_table_html(nearby_top: list | None, hints: dict) -> str:
    """Tableau des mots les plus proches sémantiquement de la solution (contenu unique par archive).
    Utilise `nearby_top` si présent (post-PR3), sinon un repli sur les mots-indices déjà stockés
    (percentile seul, pas de similarité — archives générées avant ce champ)."""
    if nearby_top:
        rows = "\n            ".join(
            f'<tr><td class="nearby-rank">#{1000 - item["percentile"]}</td>'
            f'<td>{_html_escape(item["word"])}</td>'
            f'<td class="nearby-temp">{item["similarity"] * 100:.1f}°C</td></tr>'
            for item in nearby_top
        )
        intro = f"Les {len(nearby_top)} mots les plus proches sémantiquement de la solution, du plus chaud au moins chaud."
        heading = "Les mots les plus proches"
    else:
        fallback_items = [
            item for level in ("level3", "level2", "level1")
            for item in (hints or {}).get(level, [])
            if isinstance(item, dict) and "word" in item and "percentile" in item
        ]
        if not fallback_items:
            return ""
        rows = "\n            ".join(
            f'<tr><td class="nearby-rank">#{1000 - item["percentile"]}</td>'
            f'<td>{_html_escape(item["word"])}</td>'
            f'<td class="nearby-temp">—</td></tr>'
            for item in fallback_items
        )
        intro = "Les mots-indices utilisés ce jour-là, du plus proche au moins proche de la solution."
        heading = "Mots-indices proches de la solution"
    return f"""
    <div class="card">
      <h2>{heading}</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        {intro}
      </p>
      <div style="overflow-x:auto;">
        <table class="nearby-table">
          <thead><tr><th>Rang</th><th>Mot</th><th style="text-align:right;">Température</th></tr></thead>
          <tbody>
            {rows}
          </tbody>
        </table>
      </div>
    </div>"""


def generate_archive_html(
    d: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    prev_date,  # date | None — plus ancienne
    next_date,  # date | None — plus récente (None → lien vers index.html)
    definition: str = "",
    nearby_top: list | None = None,
) -> None:
    """Génère docs/cemantix/archive/YYYY-MM-DD.html — solution, indices et mots proches en clair."""
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    date_display = date_fr(d)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    word_hints_card = _word_hints_card_html(word, definition, reveal=True)
    hint_levels = hint_levels_html(
        [
            ("Niveau 1 — Indices vagues", "Ces mots étaient sémantiquement proches de la solution (zone tiède) :", hints_l1),
            ("Niveau 2 — Indices proches", "Ces mots étaient très proches de la solution (zone chaude) :", hints_l2),
            ("Niveau 3 — Indices très proches", "Ces mots étaient extrêmement proches de la solution (zone brûlante) :", hints_l3),
        ],
        mode="plain",
    )
    solution_box = solution_box_html(word, reveal=True)
    faq_items = _faq_items(word, puzzle_num, date_display, definition, is_index=False)
    faq_visible = faq_html(faq_items, open_first=True)
    nearby_table = _nearby_table_html(nearby_top, hints)

    ym = d.strftime("%Y-%m")
    has_month_page = (CEMANTIX_ARCHIVE / f"{ym}.html").exists()
    month_label = _month_fr(ym)
    month_de = _de_month_fr(ym)
    month_breadcrumb_item = (
        f',\n      {{"@type": "ListItem", "position": 4, "name": "{month_label.capitalize()}", '
        f'"item": "{CEMANTIX_SITE_URL}/archive/{ym}"}}'
        if has_month_page else ""
    )
    date_breadcrumb_position = 5 if has_month_page else 4
    month_link_html = (
        f'\n      <p style="margin-top:.75rem;font-size:.9rem;">'
        f'<a href="{ym}">Toutes les solutions {month_de} &#8594;</a></p>'
        if has_month_page else ""
    )

    if prev_date is not None:
        nav_prev = f'<a class="nav-link" href="{prev_date.isoformat()}">&#8592; {date_fr(prev_date)}</a>'
    else:
        nav_prev = '<span class="nav-disabled">&#8592; Plus ancien</span>'

    if next_date is not None:
        nav_next = f'<a class="nav-link" href="{next_date.isoformat()}">{date_fr(next_date)} &#8594;</a>'
    else:
        nav_next = '<a class="nav-link" href="../">Solution du jour &#8594;</a>'

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Cémantix #{puzzle_num} du {date_fr_short(d)} : solution et indices</title>
  <meta name="description" content="Solution du Cémantix #{puzzle_num} du {date_display}. Première lettre, nombre de lettres, définition et indices progressifs pour trouver le mot.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/archive/{date_str}">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix #{puzzle_num} du {date_fr_short(d)} : solution et indices">
  <meta property="og:description" content="Première lettre, nombre de lettres, définition et indices du Cémantix #{puzzle_num} du {date_display}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/archive/{date_str}">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Cémantix #{puzzle_num} du {date_fr_short(d)} : solution et indices">
  <meta name="twitter:description" content="Première lettre, nombre de lettres, définition et indices du Cémantix #{puzzle_num} du {date_display}.">
  <meta name="twitter:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="article:published_time" content="{iso_paris(d, 8, 0)}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{iso_paris(d, 8, 0)}",
    "dateModified": "{iso_paris(d, 8, 0)}",
    "description": "Solution et indices du Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{CEMANTIX_SITE_URL}/archive/{date_str}",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{CEMANTIX_SITE_URL}/archive/{date_str}"}},
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "https://solution-du-jour.fr/"}}
  }}
  </script>

{faq_jsonld(faq_items)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "Cémantix", "item": "https://solution-du-jour.fr/cemantix/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "https://solution-du-jour.fr/cemantix/archive/"}}{month_breadcrumb_item},
      {{"@type": "ListItem", "position": {date_breadcrumb_position}, "name": "Solution du {date_display}"}}
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
  <h1>Solution Cémantix #{puzzle_num} du {date_display}</h1>
  <p class="subtitle">Archive · indices &amp; définition</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../">Cémantix</a> &rsaquo;
  <a href="./">Archives</a> &rsaquo;
  <span>Solution du {date_display}</span>
</nav>
  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="./">Toutes les archives</a>
    {nav_next}
  </nav>

  <article>

    <div class="card">
      <h2>Cémantix #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez la <strong>solution du Cémantix du {date_display}</strong> (puzzle #{puzzle_num}).
        Consultez la <strong>première lettre</strong>, le <strong>nombre de lettres</strong>
        et la <strong>définition</strong> du mot, ainsi que les <strong>indices progressifs</strong>
        pour trouver le mot sans spoiler immédiat.
      </p>
    </div>
{word_hints_card}
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
    </div>
{nearby_table}
{faq_visible}
{month_link_html}
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
    <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">Jouer à Cémantix</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

<script>
  function toggleDef(el) {{
    var wasActive = el.classList.contains('active');
    document.querySelectorAll('.hint-tag.active').forEach(function(t) {{ t.classList.remove('active'); }});
    var popup = document.getElementById('hd-popup');
    if (!popup) {{
      popup = document.createElement('div');
      popup.id = 'hd-popup';
      popup.className = 'hint-def-popup';
      document.body.appendChild(popup);
    }}
    if (wasActive) {{ popup.style.display = 'none'; return; }}
    var def = el.getAttribute('data-def');
    if (!def) return;
    el.classList.add('active');
    popup.textContent = def;
    popup.style.display = 'block';
    var rect = el.getBoundingClientRect();
    popup.style.left = Math.max(8, Math.min(rect.left + window.scrollX, window.innerWidth - 275)) + 'px';
    popup.style.top = (rect.bottom + window.scrollY + 6) + 'px';
  }}
  document.addEventListener('click', function(e) {{
    if (!e.target.classList.contains('hint-tag')) {{
      var p = document.getElementById('hd-popup');
      if (p) p.style.display = 'none';
      document.querySelectorAll('.hint-tag.active').forEach(function(t) {{ t.classList.remove('active'); }});
    }}
  }});
</script>

</body>
</html>"""

    atomic_write(CEMANTIX_ARCHIVE / f"{date_str}.html", html)


_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _month_fr(ym: str) -> str:
    """'2026-06' → 'juin 2026'."""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{_MOIS_FR[m]} {y}"


def _de_month_fr(ym: str) -> str:
    """'2026-08' -> "d’août 2026" ; '2026-06' -> 'de juin 2026'."""
    label = _month_fr(ym)
    return f"d’{label}" if label[0] in "ao" else f"de {label}"


def generate_month_html(ym: str, entries: list[dict], prev_ym, next_ym) -> None:
    """Génère docs/cemantix/archive/YYYY-MM.html — récap de toutes les solutions du mois."""
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    month_label = _month_fr(ym)
    month_de = _de_month_fr(ym)
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

    nav_prev = (
        f'<a class="nav-link" href="{prev_ym}">&#8592; {_month_fr(prev_ym)}</a>'
        if prev_ym else '<span class="nav-disabled">&#8592; Mois précédent</span>'
    )
    nav_next = (
        f'<a class="nav-link" href="{next_ym}">{_month_fr(next_ym)} &#8594;</a>'
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

  <title>Cémantix — Toutes les solutions {month_de}</title>
  <meta name="description" content="Liste complète des solutions du Cémantix {month_de} : les {count} mots du jour avec leur date, leur numéro de puzzle et leur définition.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/archive/{ym}">
{FEED_LINK_TAG}
  {link_prev}
  {link_next}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix — Solutions {month_de}">
  <meta property="og:description" content="Toutes les réponses du Cémantix {month_de} ({count} mots du jour) avec définitions.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/archive/{ym}">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    "name": "Cémantix — Solutions {month_de}",
    "description": "Liste complète des solutions du Cémantix {month_de} ({count} mots du jour) avec leur définition.",
    "url": "{CEMANTIX_SITE_URL}/archive/{ym}",
    "isPartOf": {{"@type": "WebSite", "name": "Solutions du Jour", "url": "{SITE_URL}/"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Cémantix", "item": "{CEMANTIX_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "{CEMANTIX_SITE_URL}/archive/"}},
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
  <h1>Cémantix — Solutions {month_de}</h1>
  <p class="subtitle">{count} mot{"s" if count > 1 else ""} du jour</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Cémantix</a> &rsaquo;
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
      <h2>Toutes les solutions Cémantix {month_de}</h2>
      <p>
        Retrouvez la <strong>liste complète des solutions du Cémantix {month_de}</strong> :
        {count} mots du jour ({words_preview}…), chacun avec sa <strong>date</strong>, son
        <strong>numéro de puzzle</strong> et sa <strong>définition</strong>. Cliquez sur un mot
        pour ouvrir la page détaillée du jour avec ses indices progressifs.
      </p>
      <div style="overflow-x:auto;">
        <table class="month-table" style="width:100%;border-collapse:collapse;">
          <thead>
            <tr>
              <th style="text-align:left;">Date</th>
              <th style="text-align:left;">Puzzle</th>
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
    <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">Jouer à Cémantix</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solutions générées automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(CEMANTIX_ARCHIVE / f"{ym}.html", html)


def generate_archive_index(entries: list[dict], months: dict[str, list] | None = None) -> None:
    """Génère docs/cemantix/archive/index.html — liste de toutes les solutions."""
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)

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

    # Section « Par mois » : liens vers les récaps mensuels
    months_card = ""
    if months:
        month_links = "\n".join(
            f'        <li><a class="arch-link" href="{ym}">{_month_fr(ym)}</a> '
            f'<span class="arch-num">{len(months[ym])} mot{"s" if len(months[ym]) > 1 else ""}</span></li>'
            for ym in months
        )
        months_card = f"""
  <div class="card">
    <h2>Par mois</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Parcourez toutes les solutions Cémantix regroupées par mois.
    </p>
    <ul class="arch-list month-list">
{month_links}
    </ul>
  </div>
"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Archives Cémantix — Toutes les solutions du jour</title>
  <meta name="description" content="Retrouvez toutes les solutions passées de Cémantix : réponses et indices de chaque puzzle depuis le début.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/archive/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives Cémantix — Toutes les solutions">
  <meta property="og:description" content="Toutes les solutions passées du jeu Cémantix avec indices progressifs.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/archive/">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Cémantix", "item": "{CEMANTIX_SITE_URL}/"}},
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
  <h1>Archives Cémantix</h1>
  <p class="subtitle">{count} solution{"s" if count > 1 else ""} enregistrée{"s" if count > 1 else ""}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../">Cémantix</a> &rsaquo;
  <span>Archives</span>
</nav>
{months_card}
  <div class="card">
    <h2>Toutes les solutions Cémantix ({count})</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Cliquez sur un mot pour voir la solution complète et les indices de ce jour.
    </p>
    <ul class="arch-list">
{items_html}
    </ul>
  </div>

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../">Solution du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../">Solution du jour</a> ·
    <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">Jouer à Cémantix</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(CEMANTIX_ARCHIVE / "index.html", html)


def generate_index_html(
    today: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    definition: str = "",
    recent_archives: list | None = None,
    generated_at: str | None = None,
) -> None:
    """Génère docs/cemantix/index.html."""
    date_str = today.isoformat()
    date_display = date_fr(today)
    modified_iso = utc_iso_to_paris(generated_at) if generated_at else iso_paris(today, 8, 0)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    word_hints_card = _word_hints_card_html(word, definition)
    hint_levels = hint_levels_html(
        [
            ("&#127777; Niveau 1 \u2014 Indices vagues", "Ces mots sont <strong>s\u00e9mantiquement proches</strong> de la solution (zone ti\u00e8de) :", hints_l1),
            ("&#128293; Niveau 2 \u2014 Indices proches", "Ces mots sont <strong>tr\u00e8s proches</strong> de la solution (zone chaude) :", hints_l2),
            ("&#128561; Niveau 3 \u2014 Indices tr\u00e8s proches", "Ces mots sont <strong>extr\u00eamement proches</strong> de la solution (zone br\u00fblante) :", hints_l3),
        ],
        mode="details",
    )
    solution_box = solution_box_html(word, reveal=False)
    faq_items = _faq_items(word, puzzle_num, date_display, definition, is_index=True)
    faq_visible = faq_html(faq_items, open_first=False)

    recent_archives_card = ""
    if recent_archives:
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

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Cémantix #{puzzle_num} du {date_fr_short(today)} : solution et indices</title>
  <meta name="description" content="Bloqué sur le Cémantix #{puzzle_num} du {date_display} ? Indices progressifs (1ère lettre, longueur, définition) puis la solution complète. Mis à jour chaque matin à 8h.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix #{puzzle_num} du {date_fr_short(today)} : solution et indices">
  <meta property="og:description" content="Bloqué sur le Cémantix #{puzzle_num} du {date_display} ? Indices progressifs puis la solution complète, mis à jour chaque matin.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Cémantix #{puzzle_num} du {date_fr_short(today)} : solution et indices">
  <meta name="twitter:description" content="Bloqué sur le Cémantix #{puzzle_num} du {date_display} ? Indices progressifs puis la solution complète.">
  <meta name="twitter:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="article:published_time" content="{iso_paris(today, 8, 0)}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{iso_paris(today, 8, 0)}",
    "dateModified": "{modified_iso}",
    "description": "Solution et indices progressifs du jeu Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{CEMANTIX_SITE_URL}/",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{CEMANTIX_SITE_URL}/"}},
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "https://solution-du-jour.fr/"}}
  }}
  </script>

{faq_jsonld(faq_items)}

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "Cémantix", "item": "https://solution-du-jour.fr/cemantix/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Solution Cémantix #{puzzle_num} du {date_display}</h1>
  <p class="subtitle">Réponse &amp; indices progressifs</p>
{updated_block(modified_iso)}
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <span>Cémantix</span>
</nav>
  <article>

    <div class="card">
      <h2>Cémantix #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Vous cherchez la <strong>solution du Cémantix du {date_display}</strong> (puzzle #{puzzle_num}) ?
        Cette page vous propose d'abord des <strong>indices progressifs</strong> pour ne pas
        vous spoiler, puis la <strong>réponse complète</strong> si vous êtes bloqué.
        La réponse au <em>mot du jour</em> et à la <em>réponse sémantix</em> est disponible ci-dessous.
      </p>
    </div>
{word_hints_card}
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
      <p style="font-size:.85rem;margin-top:.5rem;">
        Juste des indices, pas encore prêt pour la solution ? <a href="indice/">Voir seulement les indices &#8594;</a>
      </p>
    </div>
{faq_visible}

    <div class="card">
      <h2>Comment jouer à Cémantix ?</h2>
      <p>
        <strong>Cémantix</strong> est un jeu de devinettes sémantiques quotidien disponible sur
        <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">cemantix.certitudes.org</a>.
        Chaque jour, un nouveau mot secret est à deviner. Les joueurs soumettent des propositions
        et reçoivent un <em>score de température</em> indiquant la proximité sémantique avec la solution.
        Plus le mot est proche, plus la température est élevée.
      </p>
      <p style="margin-top:.75rem;">
        Cette page est mise à jour automatiquement chaque matin avec la <strong>solution du jour</strong>
        et des <strong>indices cémantix</strong> pour vous aider si vous êtes bloqué.
        Revenez chaque jour pour la nouvelle <em>réponse cémantix</em> !
        Vous cherchez la <em>réponse sémantix</em> ou le <em>mot du jour cémantix</em> ?
        Vous êtes au bon endroit.
      </p>
    </div>

    <div class="card" style="margin-top:.5rem;">
      <h2 style="font-size:1rem;margin-bottom:.75rem;">Autres jeux du jour</h2>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
        <a href="../sutom/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🔤 Sutom</a>
        <a href="../pedantix/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">📖 Pédantix</a>
        <a href="../loto/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🎱 Loto FDJ</a>
        <a href="../euromillions/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">⭐ EuroMillions</a>
      </div>
    </div>
{recent_archives_card}
  </article>
</main>

<footer>
  <p>Site non officiel — Solution générée automatiquement · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">cemantix.certitudes.org</a></p>
</footer>

<script>
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

  function toggleDef(el) {{
    var wasActive = el.classList.contains('active');
    document.querySelectorAll('.hint-tag.active').forEach(function(t) {{ t.classList.remove('active'); }});
    var popup = document.getElementById('hd-popup');
    if (!popup) {{
      popup = document.createElement('div');
      popup.id = 'hd-popup';
      popup.className = 'hint-def-popup';
      document.body.appendChild(popup);
    }}
    if (wasActive) {{ popup.style.display = 'none'; return; }}
    var def = el.getAttribute('data-def');
    if (!def) return;
    el.classList.add('active');
    popup.textContent = def;
    popup.style.display = 'block';
    var rect = el.getBoundingClientRect();
    popup.style.left = Math.max(8, Math.min(rect.left + window.scrollX, window.innerWidth - 275)) + 'px';
    popup.style.top = (rect.bottom + window.scrollY + 6) + 'px';
  }}
  document.addEventListener('click', function(e) {{
    if (!e.target.classList.contains('hint-tag')) {{
      var p = document.getElementById('hd-popup');
      if (p) p.style.display = 'none';
      document.querySelectorAll('.hint-tag.active').forEach(function(t) {{ t.classList.remove('active'); }});
    }}
  }});
</script>

</body>
</html>"""

    atomic_write(CEMANTIX_DIR / "index.html", html)


def generate_indice_html(
    today: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    definition: str = "",
    generated_at: str | None = None,
) -> None:
    """Génère docs/cemantix/indice/index.html — indices visibles, sans la solution.
    Cible la requête « indice cemantix » (fort volume, CTR très faible sur la page
    principale car elle affiche solution + indices masqués derrière la même page)."""
    date_str = today.isoformat()
    date_display = date_fr(today)
    date_short = date_fr_short(today)
    modified_iso = utc_iso_to_paris(generated_at) if generated_at else iso_paris(today, 8, 0)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    hint_levels = hint_levels_html(
        [
            ("&#127777; Niveau 1 — Indices vagues", "Ces mots sont <strong>sémantiquement proches</strong> de la solution (zone tiède) :", hints_l1),
            ("&#128293; Niveau 2 — Indices proches", "Ces mots sont <strong>très proches</strong> de la solution (zone chaude) :", hints_l2),
            ("&#128561; Niveau 3 — Indices très proches", "Ces mots sont <strong>extrêmement proches</strong> de la solution (zone brûlante) :", hints_l3),
        ],
        mode="details",
    )
    # Contrairement à l'index principal : les 3 niveaux sont ouverts par défaut.
    hint_levels = hint_levels.replace("<details>", "<details open>")

    first_letter = word[0].upper() if word else "?"
    word_length = len(word)
    letters_label = f"{word_length} lettre{'s' if word_length > 1 else ''}"

    title = f"Indices Cémantix #{puzzle_num} du {date_short} (sans solution)"
    description = (
        f"Indices progressifs pour le Cémantix #{puzzle_num} du {date_display}, sans la solution : "
        f"1ère lettre, longueur du mot, définition masquée et 3 niveaux d'indices sémantiques."
    )
    canonical = f"{CEMANTIX_SITE_URL}/indice/"

    def_row = ""
    if definition:
        masked = _html_escape(_mask_word(word, definition))
        def_row = (
            '\n        <div class="word-hint-item">'
            '\n          <span class="word-hint-icon">&#128218;</span>'
            '\n          <span class="word-hint-label">Définition</span>'
            f'\n          <span class="word-hint-value definition" id="wh-def">{masked}</span>'
            "\n          <button class=\"word-hint-btn\" id=\"wh-def-btn\" onclick=\"revealWordHint('def')\">Révéler</button>"
            '\n        </div>'
        )

    faq_items = [
        (
            "Comment avoir des indices pour le Cémantix du jour sans voir la solution ?",
            f"Cette page affiche directement les 3 niveaux d'indices progressifs du Cémantix #{puzzle_num} "
            f"du {date_display} (mots sémantiquement proches, 1ère lettre, longueur du mot), sans révéler la réponse.",
        ),
        (
            "Où trouver la solution complète du Cémantix du jour ?",
            f"La solution complète et les indices du Cémantix #{puzzle_num} du {date_display} sont "
            f"disponibles sur la page principale : solution-du-jour.fr/cemantix/.",
        ),
    ]

    body_html = f"""    <div class="card">
      <h2>Indices Cémantix #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Vous voulez des <strong>indices pour le Cémantix du {date_display}</strong> (puzzle #{puzzle_num})
        sans voir tout de suite la solution ? Cette page affiche directement les 3 niveaux d'indices
        progressifs ainsi que la première lettre et la longueur du mot.
      </p>
    </div>
    <div class="card">
      <h2>Le mot en détail</h2>
      <div class="word-hints">
        <div class="word-hint-item">
          <span class="word-hint-icon">&#128207;</span>
          <span class="word-hint-label">Nombre de lettres</span>
          <span class="word-hint-value visible">{letters_label}</span>
        </div>
        <div class="word-hint-item">
          <span class="word-hint-icon">&#128288;</span>
          <span class="word-hint-label">Première lettre</span>
          <span class="word-hint-value visible">{first_letter}</span>
        </div>{def_row}
      </div>
    </div>
    <div class="card">
      <h2>Indices progressifs</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Les 3 niveaux sont déjà ouverts ci-dessous — aucune solution n'est révélée sur cette page.
      </p>
{hint_levels}
    </div>
{faq_html(faq_items, open_first=False)}
    <div class="card" style="margin-top:.5rem;text-align:center;">
      <a href="../" style="font-weight:600;">Voir la solution complète du Cémantix #{puzzle_num} &#8594;</a>
    </div>"""

    scripts = ""
    if definition:
        scripts = (
            "<script>\n"
            "  function revealWordHint(key) {\n"
            "    var el = document.getElementById('wh-' + key);\n"
            "    if (el) el.classList.add('visible');\n"
            "    var btn = document.getElementById('wh-' + key + '-btn');\n"
            "    if (btn) btn.style.display = 'none';\n"
            "  }\n"
            "</script>"
        )

    news_article = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": f"Indices Cémantix #{puzzle_num} du {date_display} (sans la solution)",
        "datePublished": iso_paris(today, 8, 0),
        "dateModified": modified_iso,
        "description": description,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "author": {"@type": "Organization", "name": "Solutions du Jour"},
        "publisher": {"@type": "Organization", "name": "Solutions du Jour", "url": f"{SITE_URL}/"},
    }

    html = render_page(
        title=title,
        description=description,
        canonical=canonical,
        h1=f"Indices Cémantix #{puzzle_num} du {date_display} (sans la solution)",
        subtitle="Indices progressifs — pas de spoiler",
        body_html=body_html,
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("Cémantix", f"{CEMANTIX_SITE_URL}/"), ("Indices", canonical)],
        css_rel="../../css/style.css",
        og_type="article",
        jsonld=[news_article],
        footer_links='<a href="../">Cémantix</a> · <a href="../../">Accueil</a>',
        scripts=scripts,
    )
    indice_dir = CEMANTIX_DIR / "indice"
    indice_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(indice_dir / "index.html", html)


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(
    today: date, puzzle_num: int, word: str, hints: dict, definition: str = "",
    generated_at: str | None = None,
) -> None:
    """
    Génère tous les fichiers HTML Cémantix à partir des JSON déjà en place.
    """
    all_archives = load_all_archives()
    today_str = today.isoformat()
    past_archives = [e for e in all_archives if e["date"] != today_str]

    print(f"[Cémantix] Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        d = date.fromisoformat(entry["date"])
        prev_date = date.fromisoformat(past_archives[i + 1]["date"]) if i + 1 < len(past_archives) else None
        next_date = date.fromisoformat(past_archives[i - 1]["date"]) if i > 0 else None
        entry_hints = entry.get("hints", {"level1": [], "level2": [], "level3": []})
        entry_definition = entry.get("definition", "")
        generate_archive_html(
            d, entry["puzzle_num"], entry["word"], entry_hints, prev_date, next_date,
            entry_definition, entry.get("nearby_top"),
        )

    # Pages récapitulatives mensuelles (past_archives est trié DESC)
    months: dict[str, list] = {}
    for e in past_archives:
        months.setdefault(e["date"][:7], []).append(e)
    month_keys = list(months.keys())
    print(f"[Cémantix] Génération des pages mensuelles ({len(month_keys)} mois)…")
    for i, ym in enumerate(month_keys):
        next_ym = month_keys[i - 1] if i > 0 else None                    # mois plus récent
        prev_ym = month_keys[i + 1] if i + 1 < len(month_keys) else None  # mois plus ancien
        generate_month_html(ym, months[ym], prev_ym, next_ym)

    print("[Cémantix] Génération de docs/cemantix/archive/index.html…")
    generate_archive_index(past_archives, months)

    recent_archives = [e for e in past_archives[:7] if (CEMANTIX_ARCHIVE / f"{e['date']}.html").exists()]
    print("[Cémantix] Génération de docs/cemantix/index.html…")
    generate_index_html(today, puzzle_num, word, hints, definition, recent_archives, generated_at)

    print("[Cémantix] Génération de docs/cemantix/indice/index.html…")
    generate_indice_html(today, puzzle_num, word, hints, definition, generated_at)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run(today: date, model_path: str, forced_puzzle: int | None = None) -> dict | None:
    """
    Lance solve + génère tous les fichiers Cémantix.
    Retourne le dict data ou None en cas d'échec.
    """
    CEMANTIX_DIR.mkdir(parents=True, exist_ok=True)
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)

    # Vérifier si la solution est déjà générée pour aujourd'hui
    solution_path = CEMANTIX_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == today.isoformat() and existing.get("word"):
            word = existing["word"]
            puzzle_num = existing.get("puzzle_num", forced_puzzle or get_puzzle_number())
            hints = existing.get("hints", {"level1": [], "level2": [], "level3": []})
            definition = existing.get("definition", "")
            # Enrichir les définitions des mots-indices si absentes
            updated = enrich_hints_with_definitions(hints)
            if updated is not hints or any(
                isinstance(i, dict) and "definition" in i
                for lvl in updated.values() for i in lvl
            ):
                existing["hints"] = updated
                atomic_write(solution_path, json.dumps(existing, ensure_ascii=False, indent=2))
            print(f"[Cémantix] ℹ Solution déjà présente : {word!r} — régénération HTML uniquement.")
            generate_archive_json(today, existing)
            _generate_all_html(today, puzzle_num, word, updated, definition, existing.get("generated_at"))
            return existing

    # Numéro du puzzle
    if forced_puzzle:
        puzzle_num = forced_puzzle
        print(f"[Cémantix] Puzzle forcé : #{puzzle_num}")
    else:
        print("[Cémantix] Récupération du numéro du puzzle…")
        puzzle_num = get_puzzle_number()
        print(f"[Cémantix] Puzzle du jour : #{puzzle_num}")

    # Résolution via solver.py
    print(f"[Cémantix] Résolution du puzzle #{puzzle_num}…")
    from games.solver import solve
    word, tried = solve(puzzle_num, model_path)

    if not word:
        print("[Cémantix] ❌ Le solveur n'a pas trouvé la solution.")
        return None

    print(f"[Cémantix] ✅ Solution : {word!r} ({len(tried)} mots testés)")

    # Voisins et indices
    print("[Cémantix] Récupération des voisins via /nearby…")
    nearby = get_nearby(word, puzzle_num)
    print(f"[Cémantix]    {len(nearby)} voisins récupérés")

    hints = select_hints(nearby)
    print(f"[Cémantix]    Indices niveau 1 : {[i['word'] for i in hints['level1']]}")
    print(f"[Cémantix]    Indices niveau 2 : {[i['word'] for i in hints['level2']]}")
    print(f"[Cémantix]    Indices niveau 3 : {[i['word'] for i in hints['level3']]}")

    # Définitions des mots-indices
    print("[Cémantix] Récupération des définitions des mots-indices…")
    hints = enrich_hints_with_definitions(hints)

    # Définition via Wiktionnaire
    print("[Cémantix] Récupération de la définition…")
    definition = fetch_definition(word)
    if definition:
        print(f"[Cémantix]    Définition : {definition[:80]}…")
    else:
        print("[Cémantix]    Aucune définition trouvée.")

    # Fichiers JSON
    data = generate_solution_json(today, puzzle_num, word, hints, len(tried), definition, nearby)
    generate_archive_json(today, data)

    # HTML
    _generate_all_html(today, puzzle_num, word, hints, definition, data.get("generated_at"))

    print(f"[Cémantix] 🎉 Site généré ({today.isoformat()}, #{puzzle_num}, {word!r})")
    return data
