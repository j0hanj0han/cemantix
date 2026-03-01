"""
generate.py — Orchestrateur multi-jeux pour le site statique.

Usage :
  python generate.py
  python generate.py --model /chemin/vers/modele.bin --puzzle 1458

Produit :
  docs/index.html              ← hub multi-jeux
  docs/sitemap.xml             ← sitemap global
  docs/cemantix/...            ← délégué à games/cemantix.py
  docs/sutom/...               ← délégué à games/sutom.py
"""

import argparse
import json
from datetime import date
from pathlib import Path

from core import SITE_URL, DOCS_DIR, date_fr, atomic_write

MODEL_PATH_DEFAULT = "frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin"


# ── Hub page ──────────────────────────────────────────────────────────────────

def generate_hub_html(today: date, game_data: dict) -> None:
    """
    Génère docs/index.html — page d'accueil listant tous les jeux.
    game_data : {"cemantix": dict|None, "sutom": dict|None}
    """
    date_display = date_fr(today)
    date_str = today.isoformat()

    cemantix = game_data.get("cemantix")
    sutom = game_data.get("sutom")

    # ── Carte Cémantix ──
    if cemantix:
        word_c = cemantix["word"]
        puzzle_c = cemantix["puzzle_num"]
        cemantix_card = f"""
    <a class="game-card" href="cemantix/">
      <div class="game-card-header">
        <h2 class="game-card-title">Cémantix</h2>
        <span class="game-badge game-badge-semantix">Sémantique</span>
      </div>
      <p class="game-card-desc">Devinez le mot secret grâce à la proximité sémantique.</p>
      <div class="game-card-solution">
        <span class="game-label">Solution #{puzzle_c}</span>
        <div class="solution-blur solution-blur-sm" id="sol-cemantix">
          <span class="solution-word solution-word-sm">{word_c}</span>
        </div>
        <button class="reveal-btn-sm" onclick="reveal(event,'sol-cemantix')">Révéler</button>
      </div>
      <span class="game-link-arrow">Voir la solution &amp; indices &#8594;</span>
    </a>"""
    else:
        cemantix_card = """
    <a class="game-card game-card-unavailable" href="cemantix/">
      <div class="game-card-header">
        <h2 class="game-card-title">Cémantix</h2>
        <span class="game-badge game-badge-semantix">Sémantique</span>
      </div>
      <p class="game-card-desc">Devinez le mot secret grâce à la proximité sémantique.</p>
      <p class="game-unavailable">Solution en cours de génération…</p>
      <span class="game-link-arrow">Aller sur Cémantix &#8594;</span>
    </a>"""

    # ── Carte Sutom ──
    if sutom:
        word_s = sutom["word"]
        puzzle_s = sutom["puzzle_num"]
        letter_count = sutom.get("letter_count", len(word_s))
        first_letter = sutom.get("first_letter", word_s[0])
        sutom_card = f"""
    <a class="game-card" href="sutom/">
      <div class="game-card-header">
        <h2 class="game-card-title">Sutom</h2>
        <span class="game-badge game-badge-sutom">Wordle FR</span>
      </div>
      <p class="game-card-desc">Devinez le mot en {letter_count} lettres (commence par {first_letter}).</p>
      <div class="game-card-solution">
        <span class="game-label">Solution #{puzzle_s}</span>
        <div class="solution-blur solution-blur-sm" id="sol-sutom">
          <span class="solution-word solution-word-sm">{word_s}</span>
        </div>
        <button class="reveal-btn-sm" onclick="reveal(event,'sol-sutom')">Révéler</button>
      </div>
      <span class="game-link-arrow">Voir la solution &#8594;</span>
    </a>"""
    else:
        sutom_card = """
    <a class="game-card game-card-unavailable" href="sutom/">
      <div class="game-card-header">
        <h2 class="game-card-title">Sutom</h2>
        <span class="game-badge game-badge-sutom">Wordle FR</span>
      </div>
      <p class="game-card-desc">Devinez le mot du jour en 6 tentatives.</p>
      <p class="game-unavailable">Solution en cours de génération…</p>
      <span class="game-link-arrow">Aller sur Sutom &#8594;</span>
    </a>"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>Solutions du Jour — Cémantix, Sutom · {date_display}</title>
  <meta name="description" content="Solutions du jour de Cémantix et Sutom pour le {date_display}. Réponses et indices pour tous les jeux de mots quotidiens francophones.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Solutions du Jour — {date_display}">
  <meta property="og:description" content="Solutions et indices pour Cémantix et Sutom du {date_display}.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{SITE_URL}/">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "Solutions du Jour",
    "url": "{SITE_URL}/",
    "description": "Solutions quotidiennes pour Cémantix, Sutom et autres jeux de mots francophones.",
    "inLanguage": "fr"
  }}
  </script>

  <link rel="stylesheet" href="css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="//gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Solutions du Jour</h1>
  <p class="subtitle">Cémantix · Sutom · <time datetime="{date_str}">{date_display}</time></p>
</header>

<main class="hub-main">
  <p class="hub-intro">
    Retrouvez chaque jour les <strong>solutions et indices</strong> des meilleurs jeux de mots francophones.
    Mises à jour automatiquement chaque matin.
  </p>

  <div class="games-grid">
{cemantix_card}
{sutom_card}
  </div>
</main>

<footer>
  <p>Site non officiel — Solutions générées automatiquement</p>
  <p style="margin-top:.4rem;">
    <a href="cemantix/">Cémantix</a> ·
    <a href="sutom/">Sutom</a>
  </p>
</footer>

<script>
  function reveal(e, id) {{
    e.preventDefault();
    e.stopPropagation();
    var el = document.getElementById(id);
    if (el) el.classList.add('revealed');
    e.target.style.display = 'none';
  }}
</script>

</body>
</html>"""

    atomic_write(DOCS_DIR / "index.html", html)


# ── Sitemap global ────────────────────────────────────────────────────────────

def generate_global_sitemap(today: date) -> None:
    """Génère docs/sitemap.xml — toutes les URLs de tous les jeux."""
    from games.cemantix import CEMANTIX_ARCHIVE
    from games.sutom import SUTOM_ARCHIVE

    today_str = today.isoformat()
    urls = []

    # Hub
    urls.append(f"""  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{today_str}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""")

    # ── Cémantix ──
    urls.append(f"""  <url>
    <loc>{SITE_URL}/cemantix/</loc>
    <lastmod>{today_str}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>""")

    cemantix_dates = sorted(
        [date.fromisoformat(f.stem) for f in CEMANTIX_ARCHIVE.glob("????-??-??.json")]
        if CEMANTIX_ARCHIVE.exists() else [],
        reverse=True,
    )
    if cemantix_dates:
        urls.append(f"""  <url>
    <loc>{SITE_URL}/cemantix/archive/</loc>
    <lastmod>{cemantix_dates[0].isoformat()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")
        for d in cemantix_dates[:60]:
            d_str = d.isoformat()
            urls.append(f"""  <url>
    <loc>{SITE_URL}/cemantix/archive/{d_str}.html</loc>
    <lastmod>{d_str}</lastmod>
    <changefreq>never</changefreq>
    <priority>0.7</priority>
  </url>""")

    # ── Sutom ──
    urls.append(f"""  <url>
    <loc>{SITE_URL}/sutom/</loc>
    <lastmod>{today_str}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>""")

    sutom_dates = sorted(
        [date.fromisoformat(f.stem) for f in SUTOM_ARCHIVE.glob("????-??-??.json")]
        if SUTOM_ARCHIVE.exists() else [],
        reverse=True,
    )
    if sutom_dates:
        urls.append(f"""  <url>
    <loc>{SITE_URL}/sutom/archive/</loc>
    <lastmod>{sutom_dates[0].isoformat()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")
        for d in sutom_dates[:60]:
            d_str = d.isoformat()
            urls.append(f"""  <url>
    <loc>{SITE_URL}/sutom/archive/{d_str}.html</loc>
    <lastmod>{d_str}</lastmod>
    <changefreq>never</changefreq>
    <priority>0.7</priority>
  </url>""")

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += "\n".join(urls)
    sitemap += "\n</urlset>\n"

    atomic_write(DOCS_DIR / "sitemap.xml", sitemap)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Générateur de site statique multi-jeux")
    parser.add_argument("--model", default=MODEL_PATH_DEFAULT,
                        help="Chemin vers le modèle word2vec .bin (pour Cémantix)")
    parser.add_argument("--puzzle", type=int, default=None,
                        help="Forcer un numéro de puzzle Cémantix (debug)")
    args = parser.parse_args()

    today = date.today()
    print(f"\n=== Site Generator — {today.isoformat()} ===\n")

    # 1. Cémantix
    print("─── Cémantix ───────────────────────────────────────────")
    from games.cemantix import run as run_cemantix
    cemantix_data = run_cemantix(today, args.model, args.puzzle)

    # 2. Sutom
    print("\n─── Sutom ──────────────────────────────────────────────")
    from games.sutom import run as run_sutom
    sutom_data = run_sutom(today)

    # 3. Hub page
    print("\n─── Hub ────────────────────────────────────────────────")
    print("Génération de docs/index.html (hub)…")
    generate_hub_html(today, {"cemantix": cemantix_data, "sutom": sutom_data})

    # 4. Sitemap global
    print("Génération de docs/sitemap.xml (global)…")
    generate_global_sitemap(today)

    print(f"\n🎉 Site complet généré pour le {date_fr(today)}")
    print(f"   docs/index.html              ✓ (hub)")
    print(f"   docs/cemantix/index.html     {'✓' if cemantix_data else '⚠ indisponible'}")
    print(f"   docs/sutom/index.html        {'✓' if sutom_data else '⚠ indisponible'}")
    print(f"   docs/sitemap.xml             ✓\n")


if __name__ == "__main__":
    main()
