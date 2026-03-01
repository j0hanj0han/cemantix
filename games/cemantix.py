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
from datetime import date, datetime, timezone
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
    return _load_archives(CEMANTIX_ARCHIVE, required_keys=["date", "word", "puzzle_num"])


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
    CEMANTIX_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(CEMANTIX_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(today: date, data: dict) -> None:
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(CEMANTIX_ARCHIVE / f"{today.isoformat()}.json",
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
    """Génère docs/cemantix/archive/YYYY-MM-DD.html."""
    CEMANTIX_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    date_display = date_fr(d)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)

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
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/archive/{date_str}.html">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix {date_display} — Solution #{puzzle_num}">
  <meta property="og:description" content="Réponse et indices du Cémantix du {date_display} (puzzle #{puzzle_num}).">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/archive/{date_str}.html">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices du Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{CEMANTIX_SITE_URL}/archive/{date_str}.html",
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

  <link rel="stylesheet" href="../../css/style.css">
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

  <title>Archives Cémantix — Toutes les solutions du jour</title>
  <meta name="description" content="Retrouvez toutes les solutions passées de Cémantix : réponses et indices de chaque puzzle depuis le début.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/archive/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives Cémantix — Toutes les solutions">
  <meta property="og:description" content="Toutes les solutions passées du jeu Cémantix avec indices progressifs.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/archive/">

  <link rel="stylesheet" href="../../css/style.css">
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

    atomic_write(CEMANTIX_ARCHIVE / "index.html", html)


def generate_index_html(
    today: date,
    puzzle_num: int,
    word: str,
    hints: dict,
    recent_archives: list | None = None,
) -> None:
    """Génère docs/cemantix/index.html."""
    date_str = today.isoformat()
    date_display = date_fr(today)
    hints_l1, hints_l2, hints_l3 = _hints_html(hints)

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

  <title>Cémantix {date_display} — Solution #{puzzle_num} · Réponse du Jour</title>
  <meta name="description" content="Solution du Cémantix #{puzzle_num} du {date_display}. Indices progressifs en 3 niveaux pour trouver la réponse au mot secret du jour sans spoiler immédiat.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{CEMANTIX_SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Cémantix {date_display} — Solution #{puzzle_num}">
  <meta property="og:description" content="Réponse et indices progressifs du Cémantix du {date_display}. Trouvez le mot secret sans vous faire spoiler.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{CEMANTIX_SITE_URL}/">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Solution Cémantix #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices progressifs du jeu Cémantix #{puzzle_num} pour le {date_display}.",
    "url": "{CEMANTIX_SITE_URL}/",
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

  <link rel="stylesheet" href="../css/style.css">
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

    atomic_write(CEMANTIX_DIR / "index.html", html)


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(today: date, puzzle_num: int, word: str, hints: dict) -> None:
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
        generate_archive_html(d, entry["puzzle_num"], entry["word"], entry_hints, prev_date, next_date)

    print("[Cémantix] Génération de docs/cemantix/archive/index.html…")
    generate_archive_index(past_archives)

    recent_archives = past_archives[:7]
    print("[Cémantix] Génération de docs/cemantix/index.html…")
    generate_index_html(today, puzzle_num, word, hints, recent_archives)


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
            print(f"[Cémantix] ℹ Solution déjà présente : {word!r} — régénération HTML uniquement.")
            generate_archive_json(today, existing)
            _generate_all_html(today, puzzle_num, word, hints)
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
    from solver import solve
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
    print(f"[Cémantix]    Indices niveau 1 : {hints['level1']}")
    print(f"[Cémantix]    Indices niveau 2 : {hints['level2']}")
    print(f"[Cémantix]    Indices niveau 3 : {hints['level3']}")

    # Fichiers JSON
    data = generate_solution_json(today, puzzle_num, word, hints, len(tried))
    generate_archive_json(today, data)

    # HTML
    _generate_all_html(today, puzzle_num, word, hints)

    print(f"[Cémantix] 🎉 Site généré ({today.isoformat()}, #{puzzle_num}, {word!r})")
    return data
