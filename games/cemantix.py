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

from core import SITE_URL, DOCS_DIR, _session, date_fr, atomic_write, load_all_archives as _load_archives

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


def fetch_definition(word: str) -> str:
    """Récupère la première phrase de définition via l'API REST de Wikipédia (fr)."""
    try:
        resp = _session.get(
            f"https://fr.wikipedia.org/api/rest_v1/page/summary/{word}",
            timeout=10,
        )
        if resp.status_code == 200:
            extract = resp.json().get("extract", "").strip()
            if extract:
                idx = extract.find(". ")
                return extract[:idx + 1] if idx != -1 else extract[:300]
    except Exception as e:
        print(f"   ⚠ Définition Wikipedia : {e}")
    return ""


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

def generate_solution_json(
    today: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    tried_count: int,
    definition: str = "",
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


def _word_hints_card_html(word: str, definition: str) -> str:
    """Card 'Indices du mot' : nb lettres → première lettre → définition (escalade de spoiler)."""
    first_letter = word[0].upper() if word else "?"
    n = len(word)
    letters_label = f"{n} lettre{'s' if n > 1 else ''}"
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


def generate_archive_html(
    d: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    prev_date,  # date | None — plus ancienne
    next_date,  # date | None — plus récente (None → lien vers index.html)
    definition: str = "",
) -> None:
    """Génère docs/cemantix/archive/YYYY-MM-DD.html."""
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    date_display = date_fr(d)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    word_hints_card = _word_hints_card_html(word, definition)

    if prev_date is not None:
        nav_prev = f'<a class="nav-link" href="{prev_date.isoformat()}.html">&#8592; {date_fr(prev_date)}</a>'
    else:
        nav_prev = '<span class="nav-disabled">&#8592; Plus ancien</span>'

    if next_date is not None:
        nav_next = f'<a class="nav-link" href="{next_date.isoformat()}.html">{date_fr(next_date)} &#8594;</a>'
    else:
        nav_next = '<a class="nav-link" href="../index.html">Solution du jour &#8594;</a>'

    first_letter = word[0].upper() if word else "?"
    word_length = len(word)
    letters_plural = "s" if word_length > 1 else ""
    faq_extra = f""",
      {{
        "@type": "Question",
        "name": "Quelle est la premi\u00e8re lettre du C\u00e9mantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La premi\u00e8re lettre du C\u00e9mantix #{puzzle_num} du {date_display} est : {first_letter}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Combien de lettres contient le mot du C\u00e9mantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le mot du C\u00e9mantix #{puzzle_num} du {date_display} contient {word_length} lettre{letters_plural}."
        }}
      }}"""
    if definition:
        safe_def = definition.replace('"', "'")
        faq_extra += f""",
      {{
        "@type": "Question",
        "name": "Quelle est la d\u00e9finition du mot du C\u00e9mantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "{safe_def}"
        }}
      }}"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Cémantix #{puzzle_num} — Solution du {date_display}</title>
  <meta name="description" content="Solution du Cémantix #{puzzle_num} du {date_display}. Première lettre, nombre de lettres, définition et indices progressifs pour trouver le mot.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/archive/{date_str}.html">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix #{puzzle_num} — Solution du {date_display}">
  <meta property="og:description" content="Première lettre, nombre de lettres, définition et indices du Cémantix #{puzzle_num} du {date_display}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/archive/{date_str}.html">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Cémantix #{puzzle_num} — Solution du {date_display}">
  <meta name="twitter:description" content="Première lettre, nombre de lettres, définition et indices du Cémantix #{puzzle_num} du {date_display}.">
  <meta name="twitter:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices du Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{CEMANTIX_SITE_URL}/archive/{date_str}.html",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{CEMANTIX_SITE_URL}/archive/{date_str}.html"}},
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
        "name": "Quelle est la solution du Cémantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La réponse du Cémantix #{puzzle_num} du {date_display} est : {word}."
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
      {{"@type": "ListItem", "position": 2, "name": "Cémantix", "item": "https://solution-du-jour.fr/cemantix/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "https://solution-du-jour.fr/cemantix/archive/"}},
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
  <h1>Cémantix #{puzzle_num} — Solution du {date_display}</h1>
  <p class="subtitle">Archive · indices &amp; définition</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../index.html">Cémantix</a> &rsaquo;
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
        Déverrouillez les indices niveau par niveau. Chaque niveau est plus précis que le précédent.
      </p>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l1" onclick="revealHint(1)">
          &#127777; Niveau 1 — Indices vagues (cliquer pour révéler)
        </button>
        <div class="hint-content" id="content-l1">
          <p>Ces mots sont <strong>sémantiquement proches</strong> de la solution (zone tiède) :</p>
          <div class="hint-words">{hints_l1 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l2" onclick="revealHint(2)" disabled>
          &#128293; Niveau 2 — Indices proches (déverrouillé après niveau 1)
        </button>
        <div class="hint-content" id="content-l2">
          <p>Ces mots sont <strong>très proches</strong> de la solution (zone chaude) :</p>
          <div class="hint-words">{hints_l2 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l3" onclick="revealHint(3)" disabled>
          &#128561; Niveau 3 — Indices très proches (déverrouillé après niveau 2)
        </button>
        <div class="hint-content" id="content-l3">
          <p>Ces mots sont <strong>extrêmement proches</strong> de la solution (zone brûlante) :</p>
          <div class="hint-words">{hints_l3 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="solution-blur" id="solution-wrap">
          <span class="solution-word">{word}</span>
        </div>
        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">
          Cliquer pour révéler la réponse
        </button>
      </div>
      <p class="puzzle-meta">Puzzle #{puzzle_num} · {date_display}</p>
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
    <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">Jouer à Cémantix</a>
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


def generate_archive_index(entries: list[dict]) -> None:
    """Génère docs/cemantix/archive/index.html — liste de toutes les solutions."""
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">#{e["puzzle_num"]}</span>'
            f'<a class="arch-link" href="{e["date"]}.html">{e["word"].upper()}</a>'
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

  <title>Archives Cémantix — Toutes les solutions du jour</title>
  <meta name="description" content="Retrouvez toutes les solutions passées de Cémantix : réponses et indices de chaque puzzle depuis le début.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/archive/">
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
  <a href="../index.html">Cémantix</a> &rsaquo;
  <span>Archives</span>
</nav>
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
    <a class="reveal-btn" href="../index.html">Solution du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../index.html">Solution du jour</a> ·
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
) -> None:
    """Génère docs/cemantix/index.html."""
    date_str = today.isoformat()
    date_display = date_fr(today)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)
    word_hints_card = _word_hints_card_html(word, definition)
    first_letter = word[0].upper() if word else "?"
    word_length = len(word)
    letters_plural = "s" if word_length > 1 else ""
    faq_extra = f""",
      {{
        "@type": "Question",
        "name": "Quelle est la premi\u00e8re lettre du C\u00e9mantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La premi\u00e8re lettre du C\u00e9mantix #{puzzle_num} du {date_display} est : {first_letter}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Combien de lettres contient le mot du C\u00e9mantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le mot du C\u00e9mantix #{puzzle_num} du {date_display} contient {word_length} lettre{letters_plural}."
        }}
      }}"""
    if definition:
        safe_def = definition.replace('"', "'")
        faq_extra += f""",
      {{
        "@type": "Question",
        "name": "Quelle est la d\u00e9finition du mot du C\u00e9mantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "{safe_def}"
        }}
      }}"""

    recent_archives_card = ""
    if recent_archives:
        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">#{e["puzzle_num"]}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}.html">{e["word"].upper()}</a>'
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

  <title>🧠 Cémantix #{puzzle_num} — Solution du {date_display}</title>
  <meta name="description" content="Solution du Cémantix #{puzzle_num} du {date_display}. Première lettre, nombre de lettres, définition et indices progressifs pour trouver le mot secret.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix #{puzzle_num} — Solution du {date_display}">
  <meta property="og:description" content="Première lettre, nombre de lettres, définition et indices progressifs du Cémantix du {date_display}. Trouvez le mot secret !">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Cémantix #{puzzle_num} — Solution du {date_display}">
  <meta name="twitter:description" content="Première lettre, nombre de lettres, définition et indices progressifs du Cémantix du {date_display}.">
  <meta name="twitter:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices progressifs du jeu Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{CEMANTIX_SITE_URL}/",
    "mainEntityOfPage": {{"@type": "WebPage", "@id": "{CEMANTIX_SITE_URL}/"}},
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
        "name": "Quelle est la solution du Cémantix du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La réponse du Cémantix #{puzzle_num} du {date_display} est : {word}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Qu'est-ce que Cémantix ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Cémantix est un jeu de mots quotidien basé sur la similarité sémantique. Chaque jour, un mot secret est à deviner en soumettant des propositions et en recevant un score de proximité sous forme de température."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Comment avoir des indices pour Cémantix ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Cette page propose 3 niveaux d'indices progressifs : des mots sémantiquement tièdes, chauds, puis brûlants. Déverrouillez chaque niveau selon votre besoin pour le Cémantix du {date_display}."
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
  <h1>Cémantix — Solution du jour</h1>
  <p class="subtitle">Réponse &amp; indices progressifs — #{puzzle_num}</p>
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

      <div class="hint-level">
        <button class="hint-btn" id="btn-l1" onclick="revealHint(1)">
          &#127777; Niveau 1 — Indices vagues (cliquer pour révéler)
        </button>
        <div class="hint-content" id="content-l1">
          <p>Ces mots sont <strong>sémantiquement proches</strong> de la solution (zone tiède) :</p>
          <div class="hint-words">{hints_l1 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l2" onclick="revealHint(2)" disabled>
          &#128293; Niveau 2 — Indices proches (déverrouillé après niveau 1)
        </button>
        <div class="hint-content" id="content-l2">
          <p>Ces mots sont <strong>très proches</strong> de la solution (zone chaude) :</p>
          <div class="hint-words">{hints_l2 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn" id="btn-l3" onclick="revealHint(3)" disabled>
          &#128561; Niveau 3 — Indices très proches (déverrouillé après niveau 2)
        </button>
        <div class="hint-content" id="content-l3">
          <p>Ces mots sont <strong>extrêmement proches</strong> de la solution (zone brûlante) :</p>
          <div class="hint-words">{hints_l3 or "<em>Aucun indice disponible</em>"}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="solution-blur" id="solution-wrap">
          <span class="solution-word">{word}</span>
        </div>
        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">
          Cliquer pour révéler la réponse
        </button>
      </div>
      <p class="puzzle-meta">Puzzle #{puzzle_num} · Généré automatiquement le {date_display}</p>
    </div>

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


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(today: date, puzzle_num: int, word: str, hints: dict, definition: str = "") -> None:
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
        generate_archive_html(d, entry["puzzle_num"], entry["word"], entry_hints, prev_date, next_date, entry_definition)

    print("[Cémantix] Génération de docs/cemantix/archive/index.html…")
    generate_archive_index(past_archives)

    recent_archives = [e for e in past_archives[:7] if (CEMANTIX_ARCHIVE / f"{e['date']}.html").exists()]
    print("[Cémantix] Génération de docs/cemantix/index.html…")
    generate_index_html(today, puzzle_num, word, hints, definition, recent_archives)


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
            _generate_all_html(today, puzzle_num, word, updated, definition)
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
    data = generate_solution_json(today, puzzle_num, word, hints, len(tried), definition)
    generate_archive_json(today, data)

    # HTML
    _generate_all_html(today, puzzle_num, word, hints, definition)

    print(f"[Cémantix] 🎉 Site généré ({today.isoformat()}, #{puzzle_num}, {word!r})")
    return data
