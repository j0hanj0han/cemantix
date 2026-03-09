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
    game_data : {"cemantix": dict|None, "sutom": dict|None, "loto": dict|None}
    """
    date_display = date_fr(today)
    date_str = today.isoformat()

    cemantix = game_data.get("cemantix")
    sutom = game_data.get("sutom")
    loto = game_data.get("loto")
    em = game_data.get("euromillions")

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

    # ── Carte Loto ──
    if loto:
        from games.loto import _balls_html as _loto_balls
        draw_date_display = date_fr(date.fromisoformat(loto["date"]))
        balls = loto["balls"]
        lucky = loto["lucky_ball"]
        balls_str = " · ".join(str(b) for b in balls)
        loto_card = f"""
    <a class="game-card" href="loto/">
      <div class="game-card-header">
        <h2 class="game-card-title">Loto</h2>
        <span class="game-badge game-badge-loto">FDJ</span>
      </div>
      <p class="game-card-desc">Résultats du tirage n°{loto["draw_num"]} du {draw_date_display}.</p>
      <div class="game-card-solution" style="flex-direction:column;align-items:flex-start;gap:.4rem;">
        <span class="game-label">Numéros gagnants</span>
        <div class="solution-blur solution-blur-sm" id="sol-loto">
          {_loto_balls(balls, lucky, small=True)}
        </div>
        <button class="reveal-btn-sm" onclick="reveal(event,'sol-loto')">Révéler</button>
      </div>
      <span class="game-link-arrow">Voir tous les résultats &#8594;</span>
    </a>"""
    else:
        loto_card = """
    <a class="game-card game-card-unavailable" href="loto/">
      <div class="game-card-header">
        <h2 class="game-card-title">Loto</h2>
        <span class="game-badge game-badge-loto">FDJ</span>
      </div>
      <p class="game-card-desc">Résultats du tirage Loto (lun/mer/sam).</p>
      <p class="game-unavailable">Résultats en cours de récupération…</p>
      <span class="game-link-arrow">Aller sur Loto &#8594;</span>
    </a>"""

    # ── Carte EuroMillions ──
    if em:
        from games.euromillions import _em_balls_html
        draw_date_em = date.fromisoformat(em["date"])
        em_date_display = date_fr(draw_date_em)
        em_balls_str = " · ".join(str(b) for b in em["balls"])
        em_stars_str = " · ".join(str(s) for s in em["stars"])
        em_card = f"""
    <a class="game-card" href="euromillions/">
      <div class="game-card-header">
        <h2 class="game-card-title">EuroMillions</h2>
        <span class="game-badge game-badge-em">&#9733; Multi-pays</span>
      </div>
      <p class="game-card-desc">Tirage du {em_date_display} — 5 boules + 2 étoiles.</p>
      <div class="game-card-solution" style="flex-direction:column;align-items:flex-start;gap:.4rem;">
        <span class="game-label">Numéros gagnants</span>
        <div class="solution-blur solution-blur-sm" id="sol-em">
          {_em_balls_html(em["balls"], em["stars"], small=True)}
        </div>
        <button class="reveal-btn-sm" onclick="reveal(event,'sol-em')">Révéler</button>
      </div>
      <span class="game-link-arrow">Voir tous les résultats &#8594;</span>
    </a>"""
    else:
        em_card = """
    <a class="game-card game-card-unavailable" href="euromillions/">
      <div class="game-card-header">
        <h2 class="game-card-title">EuroMillions</h2>
        <span class="game-badge game-badge-em">&#9733; Multi-pays</span>
      </div>
      <p class="game-card-desc">Résultats du tirage EuroMillions (mar/ven).</p>
      <p class="game-unavailable">Résultats en cours de récupération…</p>
      <span class="game-link-arrow">Aller sur EuroMillions &#8594;</span>
    </a>"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>Solutions du Jour — Cémantix, Sutom, Loto, EuroMillions · {date_display}</title>
  <meta name="description" content="Solutions du jour de Cémantix et Sutom pour le {date_display}. Résultats Loto et EuroMillions. Mis à jour automatiquement chaque jour.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Solutions du Jour — {date_display}">
  <meta property="og:description" content="Solutions Cémantix, Sutom et résultats Loto, EuroMillions du {date_display}.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{SITE_URL}/">
  <meta property="og:image" content="{SITE_URL}/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Solutions du Jour — {date_display}">
  <meta name="twitter:description" content="Solutions Cémantix, Sutom et résultats Loto, EuroMillions du {date_display}.">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "Solutions du Jour",
    "url": "{SITE_URL}/",
    "description": "Solutions quotidiennes pour Cémantix, Sutom, Loto et EuroMillions.",
    "inLanguage": "fr"
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Solutions du Jour — Cémantix, Sutom, Loto, EuroMillions</h1>
  <p class="subtitle"><time datetime="{date_str}">{date_display}</time></p>
</header>

<main class="hub-main">
  <p class="hub-intro">
    Retrouvez chaque jour les <strong>solutions Cémantix et Sutom</strong> ainsi que les
    <strong>résultats Loto et EuroMillions</strong>.
    Mis à jour automatiquement après chaque tirage et chaque nouveau puzzle.
  </p>

  <div class="games-grid">
{cemantix_card}
{sutom_card}
{loto_card}
{em_card}
  </div>
</main>

<footer>
  <p>Site non officiel — Solutions générées automatiquement</p>
  <p style="margin-top:.4rem;">
    <a href="cemantix/">Cémantix</a> ·
    <a href="sutom/">Sutom</a> ·
    <a href="loto/">Loto</a> ·
    <a href="euromillions/">EuroMillions</a>
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
    from games.loto import LOTO_ARCHIVE
    from games.euromillions import EM_ARCHIVE

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
        for d in cemantix_dates:
            d_str = d.isoformat()
            if not (CEMANTIX_ARCHIVE / f"{d_str}.html").exists():
                continue
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
        for d in sutom_dates:
            d_str = d.isoformat()
            if not (SUTOM_ARCHIVE / f"{d_str}.html").exists():
                continue
            urls.append(f"""  <url>
    <loc>{SITE_URL}/sutom/archive/{d_str}.html</loc>
    <lastmod>{d_str}</lastmod>
    <changefreq>never</changefreq>
    <priority>0.7</priority>
  </url>""")

    # ── Loto ──
    loto_dates = sorted(
        [date.fromisoformat(f.stem) for f in LOTO_ARCHIVE.glob("????-??-??.json")]
        if LOTO_ARCHIVE.exists() else [],
        reverse=True,
    )
    loto_lastmod = loto_dates[0].isoformat() if loto_dates else today_str
    urls.append(f"""  <url>
    <loc>{SITE_URL}/loto/</loc>
    <lastmod>{loto_lastmod}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>""")

    if loto_dates:
        urls.append(f"""  <url>
    <loc>{SITE_URL}/loto/simulateur/</loc>
    <lastmod>{loto_dates[0].isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.85</priority>
  </url>""")
        urls.append(f"""  <url>
    <loc>{SITE_URL}/loto/stats/</loc>
    <lastmod>{loto_dates[0].isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")
        urls.append(f"""  <url>
    <loc>{SITE_URL}/loto/archive/</loc>
    <lastmod>{loto_dates[0].isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")
        for d in loto_dates:
            d_str = d.isoformat()
            if not (LOTO_ARCHIVE / f"{d_str}.html").exists():
                continue
            urls.append(f"""  <url>
    <loc>{SITE_URL}/loto/archive/{d_str}.html</loc>
    <lastmod>{d_str}</lastmod>
    <changefreq>never</changefreq>
    <priority>0.7</priority>
  </url>""")

    # ── EuroMillions ──
    em_dates = sorted(
        [date.fromisoformat(f.stem) for f in EM_ARCHIVE.glob("????-??-??.json")]
        if EM_ARCHIVE.exists() else [],
        reverse=True,
    )
    em_lastmod = em_dates[0].isoformat() if em_dates else today_str
    urls.append(f"""  <url>
    <loc>{SITE_URL}/euromillions/</loc>
    <lastmod>{em_lastmod}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>""")

    if em_dates:
        urls.append(f"""  <url>
    <loc>{SITE_URL}/euromillions/simulateur/</loc>
    <lastmod>{em_dates[0].isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.85</priority>
  </url>""")
        urls.append(f"""  <url>
    <loc>{SITE_URL}/euromillions/stats/</loc>
    <lastmod>{em_dates[0].isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")
        urls.append(f"""  <url>
    <loc>{SITE_URL}/euromillions/archive/</loc>
    <lastmod>{em_dates[0].isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")
        for d in em_dates:
            d_str = d.isoformat()
            if not (EM_ARCHIVE / f"{d_str}.html").exists():
                continue
            urls.append(f"""  <url>
    <loc>{SITE_URL}/euromillions/archive/{d_str}.html</loc>
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

    # 3. Loto
    print("\n─── Loto ───────────────────────────────────────────────")
    from games.loto import run as run_loto
    from games.loto import generate_simulator_data as loto_sim_data
    from games.loto import generate_simulator_html as loto_sim_html
    loto_data = run_loto(today)
    loto_sim_data()
    loto_sim_html()

    # 4. EuroMillions
    print("\n─── EuroMillions ───────────────────────────────────────")
    from games.euromillions import run as run_em
    from games.euromillions import generate_simulator_data as em_sim_data
    from games.euromillions import generate_simulator_html as em_sim_html
    em_data = run_em(today)
    em_sim_data()
    em_sim_html()

    # 5. Hub page
    print("\n─── Hub ────────────────────────────────────────────────")
    print("Génération de docs/index.html (hub)…")
    generate_hub_html(today, {
        "cemantix": cemantix_data, "sutom": sutom_data,
        "loto": loto_data, "euromillions": em_data,
    })

    # 6. Sitemap global
    print("Génération de docs/sitemap.xml (global)…")
    generate_global_sitemap(today)

    print(f"\n🎉 Site complet généré pour le {date_fr(today)}")
    print(f"   docs/index.html                          ✓ (hub)")
    print(f"   docs/cemantix/index.html                 {'✓' if cemantix_data else '⚠ indisponible'}")
    print(f"   docs/sutom/index.html                    {'✓' if sutom_data else '⚠ indisponible'}")
    print(f"   docs/loto/index.html                     {'✓' if loto_data else '⚠ indisponible'}")
    print(f"   docs/loto/simulateur/                    ✓")
    print(f"   docs/euromillions/index.html             {'✓' if em_data else '⚠ indisponible'}")
    print(f"   docs/euromillions/simulateur/            ✓")
    print(f"   docs/sitemap.xml                         ✓\n")


if __name__ == "__main__":
    main()
