"""
generate.py — Orchestrateur quotidien pour le site statique Cémantix.

Usage :
  python generate.py
  python generate.py --model /chemin/vers/modele.bin --puzzle 1458

Produit :
  docs/solution.json
  docs/index.html
  docs/archive/YYYY-MM-DD.json
  docs/archive/YYYY-MM-DD.html
  docs/archive/index.html
  docs/sitemap.xml
"""

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import cloudscraper

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL = "https://cemantix.certitudes.org"
SITE_URL = "https://j0hanj0han.github.io/cemantix"
DOCS_DIR = Path("docs")
ARCHIVE_DIR = DOCS_DIR / "archive"
MODEL_PATH_DEFAULT = "frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}

# Session cloudscraper partagée (gère les défis Cloudflare JS)
_session = cloudscraper.create_scraper()

MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

# Point de référence pour calculer le numéro de puzzle par la date
# (fallback si le site bloque la requête HTML)
_REF_DATE = date(2026, 2, 28)
_REF_PUZZLE = 1459


# ── Helpers ───────────────────────────────────────────────────────────────────

def date_fr(d: date) -> str:
    """Retourne une date en français : '28 février 2026'."""
    return f"{d.day} {MONTHS_FR[d.month]} {d.year}"


def atomic_write(path: Path, content: str) -> None:
    """Écriture atomique : écrit dans .tmp puis renomme."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ── API Cémantix ──────────────────────────────────────────────────────────────

def get_puzzle_number() -> int:
    """
    Récupère le numéro du puzzle depuis le HTML du site.
    Fallback : calcul à partir d'un point de référence connu si le site
    bloque la requête (ex : IP GitHub Actions).
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

    # Fallback : le puzzle avance d'1 par jour
    delta = (date.today() - _REF_DATE).days
    puzzle_num = _REF_PUZZLE + delta
    print(f"   Fallback : puzzle #{puzzle_num} (calculé à partir du {_REF_DATE.isoformat()} = #{_REF_PUZZLE})")
    return puzzle_num


def get_nearby(word: str, puzzle_num: int) -> list[dict]:
    """
    Appelle l'endpoint /nearby (POST) pour récupérer les voisins de la solution.
    Retourne une liste de dicts triée par percentile ASC.
    Chaque dict : {"word": str, "percentile": int, "similarity": float}
    Réponse API : {"mot": [percentile, similarity], ...}
    """
    try:
        resp = _session.post(
            f"{BASE_URL}/nearby?n={puzzle_num}",
            data=f"word={word}",
            headers=HEADERS,
            timeout=15,
        )
        data = resp.json()
        # Format : {"mot": [percentile, similarity], ...}
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
    def pick(lo: int, hi: int, count: int = 3) -> list[str]:
        candidates = [
            item["word"] for item in nearby
            if lo <= item["percentile"] <= hi
        ]
        # Prendre des mots régulièrement espacés dans l'intervalle
        if len(candidates) <= count:
            return candidates
        step = len(candidates) // count
        return [candidates[i * step] for i in range(count)]

    return {
        "level1": pick(200, 400),
        "level2": pick(500, 700),
        "level3": pick(800, 950),
    }


# ── Chargement des archives ───────────────────────────────────────────────────

def load_all_archives() -> list[dict]:
    """
    Charge tous les fichiers JSON du dossier archive/.
    Retourne une liste triée par date DESC (plus récent en premier).
    """
    entries = []
    if ARCHIVE_DIR.exists():
        for f in ARCHIVE_DIR.glob("????-??-??.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if "date" in data and "word" in data and "puzzle_num" in data:
                    entries.append(data)
            except Exception:
                pass
    entries.sort(key=lambda x: x["date"], reverse=True)
    return entries


def collect_archive_dates() -> list[date]:
    """Retourne les dates des fichiers JSON déjà dans le dossier archive."""
    dates = []
    if ARCHIVE_DIR.exists():
        for f in ARCHIVE_DIR.glob("????-??-??.json"):
            try:
                dates.append(date.fromisoformat(f.stem))
            except ValueError:
                pass
    return dates


# ── Génération des fichiers ───────────────────────────────────────────────────

def generate_solution_json(
    today: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    tried_count: int,
) -> dict:
    data = {
        "date": today.isoformat(),
        "puzzle_num": puzzle_num,
        "word": word,
        "hints": hints,
        "tried_count": tried_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(DOCS_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(today: date, data: dict) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(ARCHIVE_DIR / f"{today.isoformat()}.json",
                 json.dumps(data, ensure_ascii=False, indent=2))


def _hints_html(hints: dict) -> tuple:
    """Retourne (hints_l1, hints_l2, hints_l3) comme chaînes HTML."""
    def words_html(words: list) -> str:
        return "".join(f'<span class="hint-tag">{w}</span>' for w in words)
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
) -> None:
    """
    Génère docs/archive/YYYY-MM-DD.html pour une archive individuelle.
    prev_date : date plus ancienne (ou None si c'est la plus ancienne connue)
    next_date : date plus récente (ou None → bouton "Aujourd'hui" vers index)
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    date_display = date_fr(d)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)

    # Navigation prev/next
    if prev_date is not None:
        nav_prev = f'<a class="nav-link" href="{prev_date.isoformat()}.html">&#8592; {date_fr(prev_date)}</a>'
    else:
        nav_prev = '<span class="nav-disabled">&#8592; Plus ancien</span>'

    if next_date is not None:
        nav_next = f'<a class="nav-link" href="{next_date.isoformat()}.html">{date_fr(next_date)} &#8594;</a>'
    else:
        nav_next = '<a class="nav-link" href="../index.html">Solution du jour &#8594;</a>'

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>Cémantix {date_display} — Solution #{puzzle_num} · Archive</title>
  <meta name="description" content="Solution du Cémantix #{puzzle_num} du {date_display}. Retrouvez la réponse et les indices progressifs de ce puzzle.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/archive/{date_str}.html">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix {date_display} — Solution #{puzzle_num}">
  <meta property="og:description" content="Réponse et indices du Cémantix du {date_display} (puzzle #{puzzle_num}).">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{SITE_URL}/archive/{date_str}.html">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices du Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{SITE_URL}/archive/{date_str}.html",
    "author": {{"@type": "Organization", "name": "Cémantix Solution"}}
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
      }}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="//gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Cémantix — Archive</h1>
  <p class="subtitle">Solution du {date_display} — #{puzzle_num}</p>
</header>

<main>
  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="index.html">Toutes les archives</a>
    {nav_next}
  </nav>

  <article>

    <div class="card">
      <h2>Cémantix #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez la <strong>solution du Cémantix du {date_display}</strong> (puzzle #{puzzle_num})
        et les <strong>indices progressifs</strong> pour ce puzzle.
      </p>
    </div>

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
        <!-- Toujours dans le DOM pour les crawlers — visuellement flouté avant clic -->
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
</script>

</body>
</html>"""

    atomic_write(ARCHIVE_DIR / f"{date_str}.html", html)


def generate_archive_index(entries: list[dict]) -> None:
    """
    Génère docs/archive/index.html — liste de toutes les solutions passées.
    entries : liste triée par date DESC (plus récent en premier).
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

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

  <title>Archives Cémantix — Toutes les solutions du jour</title>
  <meta name="description" content="Retrouvez toutes les solutions passées de Cémantix : réponses et indices de chaque puzzle depuis le début.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/archive/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives Cémantix — Toutes les solutions">
  <meta property="og:description" content="Toutes les solutions passées du jeu Cémantix avec indices progressifs.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{SITE_URL}/archive/">

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="//gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Archives Cémantix</h1>
  <p class="subtitle">{count} solution{"s" if count > 1 else ""} enregistrée{"s" if count > 1 else ""}</p>
</header>

<main>
  <div class="card">
    <h2>Toutes les solutions Cémantix</h2>
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

    atomic_write(ARCHIVE_DIR / "index.html", html)


def generate_index_html(
    today: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    recent_archives: list | None = None,
) -> None:
    date_str = today.isoformat()
    date_display = date_fr(today)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)

    # Section "Solutions précédentes"
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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>

  <title>Cémantix {date_display} — Solution #{puzzle_num} · Réponse du Jour</title>
  <meta name="description" content="Solution du Cémantix #{puzzle_num} du {date_display}. Indices progressifs en 3 niveaux pour trouver la réponse au mot secret du jour sans spoiler immédiat.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix {date_display} — Solution #{puzzle_num}">
  <meta property="og:description" content="Réponse et indices progressifs du Cémantix du {date_display}. Trouvez le mot secret sans vous faire spoiler.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{SITE_URL}/">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices progressifs du jeu Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{SITE_URL}/",
    "author": {{"@type": "Organization", "name": "Cémantix Solution"}}
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
      }}
    ]
  }}
  </script>

  <link rel="stylesheet" href="css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="//gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Cémantix — Solution du jour</h1>
  <p class="subtitle">Réponse &amp; indices progressifs — #{puzzle_num}</p>
</header>

<main>
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
        <!-- Toujours dans le DOM pour les crawlers — visuellement flouté avant clic -->
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
</script>

</body>
</html>"""

    atomic_write(DOCS_DIR / "index.html", html)


def update_sitemap(today: date, archive_dates: list) -> None:
    """Génère un sitemap.xml avec la page principale, l'index et les pages HTML d'archive."""
    urls = [f"""  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{today.isoformat()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>"""]

    if archive_dates:
        latest = max(archive_dates).isoformat() if archive_dates else today.isoformat()
        urls.append(f"""  <url>
    <loc>{SITE_URL}/archive/</loc>
    <lastmod>{latest}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")

    for d in sorted(archive_dates, reverse=True)[:60]:
        d_str = d.isoformat()
        urls.append(f"""  <url>
    <loc>{SITE_URL}/archive/{d_str}.html</loc>
    <lastmod>{d_str}</lastmod>
    <changefreq>never</changefreq>
    <priority>0.7</priority>
  </url>""")

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += "\n".join(urls)
    sitemap += "\n</urlset>\n"

    atomic_write(DOCS_DIR / "sitemap.xml", sitemap)


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(today: date, puzzle_num: int, word: str, hints: dict) -> None:
    """
    Génère tous les fichiers HTML à partir des JSON déjà en place :
    - docs/archive/YYYY-MM-DD.html pour chaque archive passée
    - docs/archive/index.html
    - docs/index.html (avec section "Solutions précédentes")
    - docs/sitemap.xml
    """
    # Charger toutes les archives (JSON), trier par date DESC
    all_archives = load_all_archives()

    # Séparer les archives passées (tout sauf aujourd'hui)
    today_str = today.isoformat()
    past_archives = [e for e in all_archives if e["date"] != today_str]

    # Pages HTML individuelles pour chaque archive passée
    print(f"Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        d = date.fromisoformat(entry["date"])
        # past_archives trié DESC : [0]=plus récent, [-1]=plus ancien
        # prev = plus ancienne = past_archives[i+1]
        # next = plus récente  = past_archives[i-1]
        prev_date = date.fromisoformat(past_archives[i + 1]["date"]) if i + 1 < len(past_archives) else None
        next_date = date.fromisoformat(past_archives[i - 1]["date"]) if i > 0 else None
        entry_hints = entry.get("hints", {"level1": [], "level2": [], "level3": []})
        generate_archive_html(d, entry["puzzle_num"], entry["word"], entry_hints, prev_date, next_date)

    # Index des archives
    print("Génération de docs/archive/index.html…")
    generate_archive_index(past_archives)

    # Page principale avec les 7 dernières archives
    recent_archives = past_archives[:7]
    print("Génération de docs/index.html…")
    generate_index_html(today, puzzle_num, word, hints, recent_archives)

    # Sitemap incluant les pages HTML d'archive
    print("Mise à jour de docs/sitemap.xml…")
    archive_dates = [date.fromisoformat(e["date"]) for e in past_archives]
    update_sitemap(today, archive_dates)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Générateur de site statique Cémantix")
    parser.add_argument("--model", default=MODEL_PATH_DEFAULT,
                        help="Chemin vers le modèle word2vec .bin")
    parser.add_argument("--puzzle", type=int, default=None,
                        help="Forcer un numéro de puzzle (debug)")
    args = parser.parse_args()

    today = date.today()
    print(f"\n=== Cémantix Site Generator — {today.isoformat()} ===\n")

    # 1. Numéro du puzzle
    if args.puzzle:
        puzzle_num = args.puzzle
        print(f"Puzzle forcé : #{puzzle_num}")
    else:
        print("Récupération du numéro du puzzle…")
        puzzle_num = get_puzzle_number()
        print(f"Puzzle du jour : #{puzzle_num}")

    # 2. Vérifier si la solution est déjà générée pour aujourd'hui
    solution_path = DOCS_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == today.isoformat() and existing.get("word"):
            word = existing["word"]
            hints = existing.get("hints", {"level1": [], "level2": [], "level3": []})
            print(f"ℹ Solution déjà présente pour aujourd'hui : {word!r} — régénération HTML uniquement.")
            generate_archive_json(today, existing)
            _generate_all_html(today, puzzle_num, word, hints)
            print(f"🎉 HTML régénéré ({today.isoformat()}, #{puzzle_num}, {word!r})\n")
            return

    # 3. Résolution via solver.py
    print(f"\nRésolution du puzzle #{puzzle_num}…")
    from solver import solve
    word, tried = solve(puzzle_num, args.model)

    if not word:
        print("❌ Le solveur n'a pas trouvé la solution. Abandon.")
        raise SystemExit(1)

    print(f"\n✅ Solution trouvée : {word!r} ({len(tried)} mots testés)\n")

    # 4. Récupération des voisins depuis l'API
    print("Récupération des 1000 voisins proches via /nearby…")
    nearby = get_nearby(word, puzzle_num)
    print(f"   {len(nearby)} voisins récupérés")

    # 5. Sélection des indices
    hints = select_hints(nearby)
    print(f"   Indices niveau 1 : {hints['level1']}")
    print(f"   Indices niveau 2 : {hints['level2']}")
    print(f"   Indices niveau 3 : {hints['level3']}")

    # 6. solution.json
    print("\nGénération de docs/solution.json…")
    data = generate_solution_json(today, puzzle_num, word, hints, len(tried))

    # 7. archive JSON
    print(f"Génération de docs/archive/{today.isoformat()}.json…")
    generate_archive_json(today, data)

    # 8. Tout le HTML (index, archives, sitemap)
    _generate_all_html(today, puzzle_num, word, hints)

    print(f"\n🎉 Site généré avec succès pour le {date_fr(today)} (puzzle #{puzzle_num} — {word!r})")
    print(f"   docs/index.html           ✓")
    print(f"   docs/solution.json        ✓")
    print(f"   docs/archive/*.json       ✓")
    print(f"   docs/archive/*.html       ✓")
    print(f"   docs/archive/index.html   ✓")
    print(f"   docs/sitemap.xml          ✓\n")


if __name__ == "__main__":
    main()
