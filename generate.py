"""
generate.py — Orchestrateur quotidien pour le site statique Cémantix.

Usage :
  python generate.py
  python generate.py --model /chemin/vers/modele.bin --puzzle 1458

Produit :
  docs/solution.json
  docs/index.html
  docs/archive/YYYY-MM-DD.json
  docs/sitemap.xml
"""

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

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
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
}

MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


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
    from bs4 import BeautifulSoup
    resp = requests.get(BASE_URL, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", id="script")
    if not script:
        raise RuntimeError("Impossible de trouver le numéro du puzzle.")
    return int(script["data-puzzle-number"])


def get_nearby(word: str, puzzle_num: int) -> list[dict]:
    """
    Appelle l'endpoint /nearby (POST) pour récupérer les voisins de la solution.
    Retourne une liste de dicts triée par percentile ASC.
    Chaque dict : {"word": str, "percentile": int, "similarity": float}
    Réponse API : {"mot": [percentile, similarity], ...}
    """
    try:
        resp = requests.post(
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


def generate_index_html(today: date, puzzle_num: int, word: str, hints: dict) -> None:
    date_str = today.isoformat()
    date_display = date_fr(today)

    # Hints formatted for display
    def words_html(words: list[str]) -> str:
        return "".join(f'<span class="hint-tag">{w}</span>' for w in words)

    hints_l1 = words_html(hints.get("level1", []))
    hints_l2 = words_html(hints.get("level2", []))
    hints_l3 = words_html(hints.get("level3", []))

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cémantix Solution du {date_display} — Réponse #{puzzle_num}</title>
  <meta name="description" content="Solution et indices du Cémantix #{puzzle_num} du {date_display}. Trouvez la réponse et des indices progressifs pour le mot du jour. Ne spoilez pas vos amis !">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/">
  <link rel="stylesheet" href="css/style.css">

  <!-- Open Graph -->
  <meta property="og:title" content="Cémantix Solution du {date_display} — #{puzzle_num}">
  <meta property="og:description" content="La réponse et des indices progressifs pour le Cémantix du {date_display}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{SITE_URL}/">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <!-- JSON-LD Article (fraîcheur pour Google) -->
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Cémantix Solution du {date_display} — Réponse #{puzzle_num}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et indices progressifs du jeu Cémantix #{puzzle_num}.",
    "url": "{SITE_URL}/",
    "author": {{"@type": "Organization", "name": "Cémantix Solution"}}
  }}
  </script>

  <!-- JSON-LD FAQPage (Featured Snippet) -->
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
          "text": "Cémantix est un jeu de mots quotidien basé sur la similarité sémantique. Chaque jour, les joueurs doivent deviner un mot secret en soumettant des propositions et en recevant un score de proximité sémantique."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Où trouver des indices pour Cémantix ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Cette page propose 3 niveaux d'indices progressifs pour le Cémantix du {date_display} : des mots vagues, proches, puis très proches de la solution. Déverrouillez chaque niveau selon votre besoin."
        }}
      }}
    ]
  }}
  </script>
</head>
<body>

<header class="site-header">
  <h1>Cémantix — Solution du jour</h1>
  <p class="subtitle">Réponse &amp; indices progressifs — #{puzzle_num}</p>
</header>

<main>

  <!-- Intro SEO -->
  <div class="card">
    <h2>Cémantix #{puzzle_num} — {date_display}</h2>
    <p>
      Vous cherchez la <strong>solution du Cémantix du {date_display}</strong> (puzzle #{puzzle_num}) ?
      Cette page vous propose d'abord des <strong>indices progressifs</strong> pour ne pas
      vous spoiler, puis la <strong>réponse complète</strong> si vous êtes bloqué.
      La réponse au <em>mot du jour</em> et à la réponse <em>sémantix</em> est disponible ci-dessous.
    </p>
  </div>

  <!-- Hints -->
  <div class="card">
    <h2>Indices progressifs</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Déverrouillez les indices niveau par niveau. Chaque niveau est plus précis que le précédent.
    </p>

    <div class="hint-level" id="hint-level-1">
      <button class="hint-btn" id="btn-l1" onclick="revealHint(1)">
        🌡 Niveau 1 — Indices vagues (cliquer pour révéler)
      </button>
      <div class="hint-content" id="content-l1">
        <p>Ces mots sont <strong>sémantiquement proches</strong> de la solution (zone tiède) :</p>
        <div class="hint-words">{hints_l1 or "<em>Aucun indice disponible</em>"}</div>
      </div>
    </div>

    <div class="hint-level" id="hint-level-2">
      <button class="hint-btn" id="btn-l2" onclick="revealHint(2)" disabled>
        🔥 Niveau 2 — Indices proches (déverrouillé après niveau 1)
      </button>
      <div class="hint-content" id="content-l2">
        <p>Ces mots sont <strong>très proches</strong> de la solution (zone chaude) :</p>
        <div class="hint-words">{hints_l2 or "<em>Aucun indice disponible</em>"}</div>
      </div>
    </div>

    <div class="hint-level" id="hint-level-3">
      <button class="hint-btn" id="btn-l3" onclick="revealHint(3)" disabled>
        😱 Niveau 3 — Indices très proches (déverrouillé après niveau 2)
      </button>
      <div class="hint-content" id="content-l3">
        <p>Ces mots sont <strong>extrêmement proches</strong> de la solution (zone brûlante) :</p>
        <div class="hint-words">{hints_l3 or "<em>Aucun indice disponible</em>"}</div>
      </div>
    </div>
  </div>

  <!-- Solution -->
  <div class="card">
    <h2>La solution du {date_display}</h2>
    <div class="solution-wrapper">
      <button class="solution-hidden" id="solution-btn" onclick="revealSolution()" aria-label="Révéler la solution du Cémantix">
        Cliquer pour révéler la réponse
      </button>
    </div>
    <!-- Texte indexable par Google mais invisible visuellement avant clic -->
    <p id="solution-text" style="display:none;text-align:center;font-size:1.1rem;margin-top:.5rem;">
      La solution du Cémantix #{puzzle_num} du {date_display} est :
      <span class="solution-word">{word}</span>
    </p>
    <p class="puzzle-meta">Puzzle #{puzzle_num} · Généré automatiquement le {date_display}</p>
  </div>

  <!-- Explication SEO -->
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
    </p>
  </div>

</main>

<footer>
  <p>Site non officiel — Solution générée automatiquement · <a href="{SITE_URL}/">Accueil</a></p>
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
    // Débloquer le niveau suivant
    var next = level + 1;
    if (next <= 3) {{
      var nextBtn = document.getElementById('btn-l' + next);
      if (nextBtn) nextBtn.disabled = false;
    }}
  }}

  function revealSolution() {{
    document.getElementById('solution-btn').style.display = 'none';
    document.getElementById('solution-text').style.display = 'block';
  }}
</script>

</body>
</html>"""

    atomic_write(DOCS_DIR / "index.html", html)


def update_sitemap(today: date, archive_dates: list[date]) -> None:
    """Génère un sitemap.xml avec la page principale et les archives."""
    urls = [f"""  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{today.isoformat()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>"""]

    for d in sorted(archive_dates, reverse=True)[:30]:  # 30 dernières archives
        urls.append(f"""  <url>
    <loc>{SITE_URL}/archive/{d.isoformat()}.json</loc>
    <lastmod>{d.isoformat()}</lastmod>
    <changefreq>never</changefreq>
    <priority>0.5</priority>
  </url>""")

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += "\n".join(urls)
    sitemap += "\n</urlset>\n"

    atomic_write(DOCS_DIR / "sitemap.xml", sitemap)


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

    # 2. Résolution via solver.py
    print(f"\nRésolution du puzzle #{puzzle_num}…")
    from solver import solve
    word, tried = solve(puzzle_num, args.model)

    if not word:
        print("❌ Le solveur n'a pas trouvé la solution. Abandon.")
        raise SystemExit(1)

    print(f"\n✅ Solution trouvée : {word!r} ({len(tried)} mots testés)\n")

    # 3. Récupération des voisins depuis l'API
    print("Récupération des 1000 voisins proches via /nearby…")
    nearby = get_nearby(word, puzzle_num)
    print(f"   {len(nearby)} voisins récupérés")

    # 4. Sélection des indices
    hints = select_hints(nearby)
    print(f"   Indices niveau 1 : {hints['level1']}")
    print(f"   Indices niveau 2 : {hints['level2']}")
    print(f"   Indices niveau 3 : {hints['level3']}")

    # 5. solution.json
    print("\nGénération de docs/solution.json…")
    data = generate_solution_json(today, puzzle_num, word, hints, len(tried))

    # 6. index.html
    print("Génération de docs/index.html…")
    generate_index_html(today, puzzle_num, word, hints)

    # 7. archive
    print(f"Génération de docs/archive/{today.isoformat()}.json…")
    generate_archive_json(today, data)

    # 8. sitemap
    print("Mise à jour de docs/sitemap.xml…")
    archive_dates = collect_archive_dates()
    update_sitemap(today, archive_dates)

    print(f"\n🎉 Site généré avec succès pour le {date_fr(today)} (puzzle #{puzzle_num} — {word!r})")
    print(f"   docs/index.html      ✓")
    print(f"   docs/solution.json   ✓")
    print(f"   docs/archive/        ✓")
    print(f"   docs/sitemap.xml     ✓\n")


if __name__ == "__main__":
    main()
