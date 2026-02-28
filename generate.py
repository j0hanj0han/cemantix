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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>

  <title>Cémantix {date_display} — Solution #{puzzle_num} · Réponse du Jour</title>
  <meta name="description" content="Solution du Cémantix #{puzzle_num} du {date_display}. Indices progressifs en 3 niveaux pour trouver la réponse au mot secret du jour sans spoiler immédiat.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/">

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

  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,700;1,400&family=Outfit:wght@400;500;600&display=swap">
  <link rel="stylesheet" href="css/style.css">
</head>
<body>

<header class="site-header">
  <div class="site-header-inner">
    <a class="site-logo" href="{SITE_URL}/">Céman<span class="logo-accent">tix</span></a>
    <span class="site-date"><time datetime="{date_str}">{date_display}</time></span>
  </div>
</header>

<div class="hero">
  <p class="hero-eyebrow">Solution du jour &middot; Puzzle #{puzzle_num}</p>
  <h1>Cémantix du <em>{date_display}</em><br>— Réponse &amp; Indices</h1>
  <p class="hero-intro">
    Vous cherchez la <strong>solution du Cémantix #{puzzle_num}</strong> ?
    Déverrouillez les indices progressivement ou révélez directement la
    <strong>réponse du jour</strong> si vous êtes bloqué.
    Mis à jour automatiquement chaque matin.
  </p>
</div>

<main>
  <article>

    <section aria-label="Indices progressifs">
      <h2 class="section-label">Indices progressifs</h2>

      <div class="hint-level">
        <button class="hint-btn lvl1" id="btn-l1" onclick="revealHint(1)">
          <span class="temp-pip"></span>
          <span class="btn-label">Niveau 1 — Indices tièdes</span>
          <span class="btn-action">Révéler</span>
        </button>
        <div class="hint-content lvl1" id="content-l1">
          <p>Ces mots sont <strong>sémantiquement proches</strong> de la solution (zone tiède) :</p>
          <div class="hint-words">{hints_l1 or "<em>Indices indisponibles</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn lvl2" id="btn-l2" onclick="revealHint(2)" disabled>
          <span class="temp-pip"></span>
          <span class="btn-label">Niveau 2 — Indices chauds</span>
          <span class="btn-action">Après niveau 1</span>
        </button>
        <div class="hint-content lvl2" id="content-l2">
          <p>Ces mots sont <strong>très proches</strong> de la solution (zone chaude) :</p>
          <div class="hint-words">{hints_l2 or "<em>Indices indisponibles</em>"}</div>
        </div>
      </div>

      <div class="hint-level">
        <button class="hint-btn lvl3" id="btn-l3" onclick="revealHint(3)" disabled>
          <span class="temp-pip"></span>
          <span class="btn-label">Niveau 3 — Indices brûlants</span>
          <span class="btn-action">Après niveau 2</span>
        </button>
        <div class="hint-content lvl3" id="content-l3">
          <p>Ces mots sont <strong>extrêmement proches</strong> de la solution (zone brûlante) :</p>
          <div class="hint-words">{hints_l3 or "<em>Indices indisponibles</em>"}</div>
        </div>
      </div>
    </section>

    <section aria-label="Solution du jour">
      <h2 class="section-label">La réponse du {date_display}</h2>
      <div class="solution-card">
        <!-- Toujours dans le DOM pour les crawlers — visuellement flouté avant clic -->
        <div class="solution-blur" id="solution-wrap">
          <span class="solution-word">{word}</span>
        </div>
        <p class="puzzle-meta">Puzzle #{puzzle_num} &middot; {date_display}</p>
        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">
          Révéler la solution
        </button>
      </div>
    </section>

    <section class="seo-section" aria-label="À propos de Cémantix">
      <h2 class="section-label">À propos de Cémantix</h2>
      <p>
        <strong>Cémantix</strong> est un jeu de devinettes sémantiques quotidien disponible sur
        <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">cemantix.certitudes.org</a>.
        Chaque jour, un nouveau <strong>mot secret</strong> est à deviner en soumettant des propositions.
        Vous recevez un <em>score de température</em> : plus votre mot est sémantiquement proche de la
        solution, plus la température monte — jusqu'à 100 °C pour la bonne réponse.
      </p>
      <p>
        Cette page publie chaque matin la <strong>réponse cémantix du jour</strong> ainsi que des
        <strong>indices cémantix</strong> progressifs pour vous aider sans spoiler immédiat.
        Revenez chaque jour pour la nouvelle <em>solution cémantix</em>.
        Vous cherchez la <em>réponse sémantix</em> ou le <em>mot du jour cémantix</em> ?
        Vous êtes au bon endroit.
      </p>
    </section>

  </article>
</main>

<footer>
  <p>Site non officiel &middot; Solution générée automatiquement &middot;
  <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">Jouer à Cémantix</a></p>
</footer>

<script>
  var revealed = [false, false, false];

  function revealHint(level) {{
    if (level > 1 && !revealed[level - 2]) return;
    document.getElementById('content-l' + level).classList.add('visible');
    var btn = document.getElementById('btn-l' + level);
    btn.disabled = true;
    var action = btn.querySelector('.btn-action');
    if (action) action.textContent = 'Révélé';
    revealed[level - 1] = true;
    var next = level + 1;
    if (next <= 3) {{
      var nb = document.getElementById('btn-l' + next);
      if (nb) {{
        nb.disabled = false;
        var na = nb.querySelector('.btn-action');
        if (na) na.textContent = 'Révéler';
      }}
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

    # 2. Vérifier si la solution est déjà générée pour aujourd'hui
    solution_path = DOCS_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == today.isoformat() and existing.get("word"):
            word = existing["word"]
            hints = existing.get("hints", {"level1": [], "level2": [], "level3": []})
            print(f"ℹ Solution déjà présente pour aujourd'hui : {word!r} — régénération HTML uniquement.")
            generate_index_html(today, puzzle_num, word, hints)
            generate_archive_json(today, existing)
            archive_dates = collect_archive_dates()
            update_sitemap(today, archive_dates)
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
