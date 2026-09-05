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
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from core import SITE_URL, DOCS_DIR, date_fr, atomic_write, iso_paris, FEED_LINK_TAG, ping_indexnow

MODEL_PATH_DEFAULT = "frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin"

# Heure de publication par jeu (heure locale Paris) : (heure, minute)
GAMES_CFG = {
    "cemantix":     {"pub_time": (8, 5),   "title_prefix": "Solution Cémantix du"},
    "sutom":        {"pub_time": (8, 5),   "title_prefix": "Solution Sutom du"},
    "pedantix":     {"pub_time": (8, 5),   "title_prefix": "Solution Pédantix du"},
    "loto":         {"pub_time": (22, 0),  "title_prefix": "Résultats Loto du"},
    "euromillions": {"pub_time": (21, 30), "title_prefix": "Résultats EuroMillions du"},
}


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
    pedantix = game_data.get("pedantix")

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

    # ── Carte Pédantix ──
    if pedantix:
        title_p = pedantix.get("title_display") or pedantix.get("word", "?")
        puzzle_p = pedantix["puzzle_num"]
        pedantix_card = f"""
    <a class="game-card" href="pedantix/">
      <div class="game-card-header">
        <h2 class="game-card-title">Pédantix</h2>
        <span class="game-badge game-badge-semantix">Wikipedia</span>
      </div>
      <p class="game-card-desc">Devinez l'article Wikipedia secret par similarité sémantique.</p>
      <div class="game-card-solution">
        <span class="game-label">Solution #{puzzle_p}</span>
        <div class="solution-blur solution-blur-sm" id="sol-pedantix">
          <span class="solution-word solution-word-sm">{title_p}</span>
        </div>
        <button class="reveal-btn-sm" onclick="reveal(event,'sol-pedantix')">Révéler</button>
      </div>
      <span class="game-link-arrow">Voir la solution &amp; indices &#8594;</span>
    </a>"""
    else:
        pedantix_card = """
    <a class="game-card game-card-unavailable" href="pedantix/">
      <div class="game-card-header">
        <h2 class="game-card-title">Pédantix</h2>
        <span class="game-badge game-badge-semantix">Wikipedia</span>
      </div>
      <p class="game-card-desc">Devinez l'article Wikipedia secret par similarité sémantique.</p>
      <p class="game-unavailable">Solution en cours de génération…</p>
      <span class="game-link-arrow">Aller sur Pédantix &#8594;</span>
    </a>"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>🎯 Solutions du jour : Cémantix, Sutom, Loto, EuroMillions</title>
  <meta name="description" content="Toutes les solutions du jour au même endroit : Cémantix, Sutom, résultats Loto et EuroMillions + simulateurs de gains gratuits. Mis à jour chaque matin à 8h.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SITE_URL}/">
{FEED_LINK_TAG}
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Solutions du jour : Cémantix, Sutom, Loto, EuroMillions">
  <meta property="og:description" content="Toutes les solutions du jour au même endroit, mises à jour chaque matin : Cémantix, Sutom, résultats Loto, EuroMillions.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{SITE_URL}/">
  <meta property="og:image" content="{SITE_URL}/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Solutions du jour : Cémantix, Sutom, Loto, EuroMillions">
  <meta name="twitter:description" content="Toutes les solutions du jour au même endroit, mises à jour chaque matin : Cémantix, Sutom, résultats Loto, EuroMillions.">

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

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "ItemList",
    "name": "Jeux du jour",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Cémantix — Solution du jour", "url": "{SITE_URL}/cemantix/"}},
      {{"@type": "ListItem", "position": 2, "name": "Sutom — Solution du jour", "url": "{SITE_URL}/sutom/"}},
      {{"@type": "ListItem", "position": 3, "name": "P\u00e9dantix — Solution du jour", "url": "{SITE_URL}/pedantix/"}},
      {{"@type": "ListItem", "position": 4, "name": "Loto FDJ — Résultats", "url": "{SITE_URL}/loto/"}},
      {{"@type": "ListItem", "position": 5, "name": "EuroMillions — Résultats", "url": "{SITE_URL}/euromillions/"}}
    ]
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {{
        "@type": "Question",
        "name": "À quelle heure est publiée la solution Cémantix ?",
        "acceptedAnswer": {{"@type": "Answer", "text": "La solution Cémantix est publiée automatiquement chaque matin vers 8h05, dès que le nouveau puzzle est disponible."}}
      }},
      {{
        "@type": "Question",
        "name": "Quand sont tirés les numéros du Loto FDJ ?",
        "acceptedAnswer": {{"@type": "Answer", "text": "Le Loto FDJ tire ses numéros le lundi, mercredi et samedi soir vers 20h20. Les résultats sont publiés automatiquement sur ce site après chaque tirage."}}
      }},
      {{
        "@type": "Question",
        "name": "Quand a lieu le tirage EuroMillions ?",
        "acceptedAnswer": {{"@type": "Answer", "text": "L'EuroMillions tire ses numéros le mardi et vendredi soir vers 21h30. Les résultats sont disponibles sur ce site après chaque tirage."}}
      }},
      {{
        "@type": "Question",
        "name": "Comment simuler ses gains au Loto FDJ ?",
        "acceptedAnswer": {{"@type": "Answer", "text": "Notre simulateur Loto gratuit vous permet d'entrer vos 5 numéros + numéro chance et de voir combien vous auriez gagné sur plus de 2 600 tirages depuis 2019."}}
      }}
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
{pedantix_card}
{loto_card}
{em_card}
  </div>

  <section style="margin-top:1.5rem;">
    <h2 style="font-size:1.05rem;margin-bottom:.75rem;">Outils</h2>
    <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
      <a href="loto/simulateur/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🎯 Simulateur Loto</a>
      <a href="loto/stats/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">📊 Statistiques Loto</a>
      <a href="euromillions/simulateur/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🎯 Simulateur EuroMillions</a>
      <a href="euromillions/stats/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">📊 Statistiques EuroMillions</a>
    </div>
  </section>

  <section style="margin-top:1.5rem;">
    <h2 style="font-size:1.05rem;margin-bottom:1rem;">Guides &amp; astuces</h2>
    <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
      <a href="cemantix/comment-jouer/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">📘 Comment jouer à Cémantix</a>
      <a href="cemantix/astuces/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">💡 Astuces Cémantix</a>
      <a href="cemantix/statistiques/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">📊 Statistiques Cémantix</a>
      <a href="sutom/comment-jouer/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">📘 Comment jouer à Sutom</a>
      <a href="sutom/meilleurs-mots/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">💡 Meilleurs mots Sutom</a>
    </div>
  </section>

  <section style="margin-top:2rem;padding:1.25rem;background:#f9fafb;border-radius:.5rem;">
    <h2 style="font-size:1.05rem;margin-bottom:.75rem;">À propos de ce site</h2>
    <p style="font-size:.92rem;color:#374151;line-height:1.6;">
      <strong>Solutions du Jour</strong> est un site non officiel qui publie chaque jour les solutions
      et indices des jeux <strong>Cémantix</strong> et <strong>Sutom</strong>, ainsi que les
      <strong>résultats Loto et EuroMillions</strong>.
      Tout est généré automatiquement — aucune intervention humaine.
      Les solutions Cémantix et Sutom sont publiées vers <strong>8h05</strong> chaque matin.
      Les résultats Loto sont mis à jour après chaque tirage (lundi, mercredi, samedi).
      Les résultats EuroMillions sont mis à jour après chaque tirage (mardi, vendredi).
    </p>
  </section>

  <section style="margin-top:1.5rem;">
    <h2 style="font-size:1.05rem;margin-bottom:1rem;">Questions fréquentes</h2>
    <div style="display:flex;flex-direction:column;gap:.75rem;">
      <details style="background:#f9fafb;border-radius:.5rem;padding:.85rem 1rem;">
        <summary style="font-weight:600;cursor:pointer;font-size:.92rem;">À quelle heure est publiée la solution Cémantix ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;color:#374151;">Chaque matin vers <strong>8h05</strong>, dès que le nouveau puzzle Cémantix est disponible.</p>
      </details>
      <details style="background:#f9fafb;border-radius:.5rem;padding:.85rem 1rem;">
        <summary style="font-weight:600;cursor:pointer;font-size:.92rem;">Quand sont tirés les numéros du Loto FDJ ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;color:#374151;">Le Loto FDJ tire ses numéros le <strong>lundi, mercredi et samedi</strong> soir vers 20h20.</p>
      </details>
      <details style="background:#f9fafb;border-radius:.5rem;padding:.85rem 1rem;">
        <summary style="font-weight:600;cursor:pointer;font-size:.92rem;">Quand a lieu le tirage EuroMillions ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;color:#374151;">L'EuroMillions tire ses numéros le <strong>mardi et vendredi</strong> soir vers 21h30.</p>
      </details>
      <details style="background:#f9fafb;border-radius:.5rem;padding:.85rem 1rem;">
        <summary style="font-weight:600;cursor:pointer;font-size:.92rem;">Comment simuler ses gains au Loto FDJ ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;color:#374151;">Utilisez notre <a href="loto/simulateur/">simulateur Loto gratuit</a> : entrez vos 5 numéros + numéro chance et découvrez vos résultats sur 2 600+ tirages depuis 2019.</p>
      </details>
    </div>
  </section>

</main>

<footer>
  <p>Site non officiel — Solutions générées automatiquement</p>
  <p style="margin-top:.4rem;">
    <a href="cemantix/">Cémantix</a> ·
    <a href="sutom/">Sutom</a> ·
    <a href="pedantix/">Pédantix</a> ·
    <a href="loto/">Loto</a> ·
    <a href="euromillions/">EuroMillions</a> ·
    <a href="a-propos/">À propos</a>
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


# ── Google News Sitemap ───────────────────────────────────────────────────────

def generate_news_sitemap(today: date, game_data: dict) -> None:
    """Génère docs/news-sitemap.xml — Google News sitemap (fenêtre 48h).

    Inclut les pages du jour (et d'hier si disponible) pour chaque jeu.
    À soumettre dans GSC → Sitemaps pour accélérer l'indexation des pages fraîches.
    """
    from games.cemantix import CEMANTIX_ARCHIVE, CEMANTIX_SITE_URL
    from games.sutom import SUTOM_ARCHIVE, SUTOM_SITE_URL
    from games.loto import LOTO_ARCHIVE, LOTO_SITE_URL
    from games.euromillions import EM_ARCHIVE, EM_SITE_URL
    from games.pedantix import PEDANTIX_ARCHIVE, PEDANTIX_SITE_URL

    yesterday = today - timedelta(days=1)

    games_dirs = {
        "cemantix":     (CEMANTIX_SITE_URL, CEMANTIX_ARCHIVE),
        "sutom":        (SUTOM_SITE_URL,    SUTOM_ARCHIVE),
        "pedantix":     (PEDANTIX_SITE_URL, PEDANTIX_ARCHIVE),
        "loto":         (LOTO_SITE_URL,     LOTO_ARCHIVE),
        "euromillions": (EM_SITE_URL,       EM_ARCHIVE),
    }

    entries = []
    for key, (base_url, archive_dir) in games_dirs.items():
        cfg = GAMES_CFG[key]
        hh, mm = cfg["pub_time"]
        title_prefix = cfg["title_prefix"]

        # Jour courant : depuis game_data (déjà résolu)
        data = game_data.get(key)
        if data:
            try:
                data_date = date.fromisoformat(data["date"])
            except (KeyError, ValueError):
                data_date = None
            if data_date == today:
                d_str = today.isoformat()
                pub_dt = iso_paris(today, hh, mm)
                label = date_fr(today)
                entries.append((f"{base_url}/", pub_dt, f"{title_prefix} {label}"))
                archive_html = archive_dir / f"{d_str}.html"
                if archive_html.exists():
                    entries.append((f"{base_url}/archive/{d_str}", pub_dt, f"{title_prefix} {label}"))
                if key == "cemantix" and (DOCS_DIR / "cemantix" / "indice" / "index.html").exists():
                    entries.append((f"{base_url}/indice/", pub_dt, f"Indices Cémantix du {label}"))

        # Hier : depuis les fichiers JSON d'archive
        y_str = yesterday.isoformat()
        y_json = archive_dir / f"{y_str}.json"
        y_html = archive_dir / f"{y_str}.html"
        if y_json.exists() and y_html.exists():
            pub_dt = iso_paris(yesterday, hh, mm)
            label = date_fr(yesterday)
            entries.append((f"{base_url}/archive/{y_str}", pub_dt, f"{title_prefix} {label}"))

    if not entries:
        return

    news_entries = []
    for loc, pub_dt, title in entries:
        news_entries.append(f"""  <url>
    <loc>{loc}</loc>
    <news:news>
      <news:publication>
        <news:name>Solutions du Jour</news:name>
        <news:language>fr</news:language>
      </news:publication>
      <news:publication_date>{pub_dt}</news:publication_date>
      <news:title>{title}</news:title>
    </news:news>
  </url>""")

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
    sitemap += '        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">\n'
    sitemap += "\n".join(news_entries)
    sitemap += "\n</urlset>\n"

    atomic_write(DOCS_DIR / "news-sitemap.xml", sitemap)


# ── Sitemap global (sitemapindex) ─────────────────────────────────────────────

def _url_entry(loc: str, lastmod: str) -> str:
    return f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{lastmod}</lastmod>\n  </url>"


def _write_urlset(path: Path, urls: list[str]) -> None:
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += "\n".join(urls)
    sitemap += "\n</urlset>\n"
    atomic_write(path, sitemap)


def _archive_dates(archive_dir: Path) -> list[date]:
    return sorted(
        [date.fromisoformat(f.stem) for f in archive_dir.glob("????-??-??.json")]
        if archive_dir.exists() else [],
        reverse=True,
    )


def _game_sitemap(
    slug: str, site_url: str, archive_dir: Path, index_lastmod: str,
    *, month_pages: bool = False, year_pages: bool = False,
) -> list[str]:
    """Construit les URLs d'un jeu : index, archive index, mois/années éventuels, dates."""
    urls = [_url_entry(f"{site_url}/", index_lastmod)]
    dates = _archive_dates(archive_dir)
    if not dates:
        return urls
    urls.append(_url_entry(f"{site_url}/archive/", dates[0].isoformat()))

    if month_pages:
        seen_months: set[str] = set()
        for d in dates:  # DESC → 1re occurrence d'un mois = sa date la plus récente
            ym = d.strftime("%Y-%m")
            if ym in seen_months:
                continue
            seen_months.add(ym)
            if not (archive_dir / f"{ym}.html").exists():
                continue
            urls.append(_url_entry(f"{site_url}/archive/{ym}", d.isoformat()))

    if year_pages:
        seen_years: set[str] = set()
        for d in dates:
            y = str(d.year)
            if y in seen_years:
                continue
            seen_years.add(y)
            if not (archive_dir / f"{y}.html").exists():
                continue
            urls.append(_url_entry(f"{site_url}/archive/{y}", d.isoformat()))

    for d in dates:
        d_str = d.isoformat()
        if not (archive_dir / f"{d_str}.html").exists():
            continue
        urls.append(_url_entry(f"{site_url}/archive/{d_str}", d_str))

    return urls


def _pages_sitemap(today_str: str, loto_dates: list[date], em_dates: list[date]) -> list[str]:
    """URLs hors jeux : hub, simulateurs, stats, pages evergreen, à-propos."""
    urls = [_url_entry(f"{SITE_URL}/", today_str)]
    if loto_dates:
        loto_lastmod = loto_dates[0].isoformat()
        urls.append(_url_entry(f"{SITE_URL}/loto/simulateur/", loto_lastmod))
        urls.append(_url_entry(f"{SITE_URL}/loto/stats/", loto_lastmod))
    if em_dates:
        em_lastmod = em_dates[0].isoformat()
        urls.append(_url_entry(f"{SITE_URL}/euromillions/simulateur/", em_lastmod))
        urls.append(_url_entry(f"{SITE_URL}/euromillions/stats/", em_lastmod))

    from games.evergreen import PAGES as EVERGREEN_PAGES
    for page in EVERGREEN_PAGES:
        if (DOCS_DIR / page.path / "index.html").exists():
            urls.append(_url_entry(f"{SITE_URL}/{page.path}/", today_str))
    if (DOCS_DIR / "a-propos" / "index.html").exists():
        urls.append(_url_entry(f"{SITE_URL}/a-propos/", today_str))
    return urls


_SUB_SITEMAPS = [
    "sitemap-cemantix.xml", "sitemap-sutom.xml", "sitemap-pedantix.xml",
    "sitemap-loto.xml", "sitemap-euromillions.xml", "sitemap-pages.xml",
]


def _write_sitemap_index(today_str: str) -> None:
    entries = []
    for name in _SUB_SITEMAPS:
        if not (DOCS_DIR / name).exists():
            continue
        entries.append(
            f"  <sitemap>\n    <loc>{SITE_URL}/{name}</loc>\n    <lastmod>{today_str}</lastmod>\n  </sitemap>"
        )
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += "\n".join(entries)
    sitemap += "\n</sitemapindex>\n"
    atomic_write(DOCS_DIR / "sitemap.xml", sitemap)


def generate_global_sitemap(today: date, game_data: dict | None = None) -> None:
    """Génère docs/sitemap.xml (sitemapindex) + un sous-sitemap par jeu + sitemap-pages.xml."""
    from games.cemantix import CEMANTIX_ARCHIVE, CEMANTIX_SITE_URL
    from games.sutom import SUTOM_ARCHIVE, SUTOM_SITE_URL
    from games.loto import LOTO_ARCHIVE, LOTO_SITE_URL
    from games.euromillions import EM_ARCHIVE, EM_SITE_URL
    from games.pedantix import PEDANTIX_ARCHIVE, PEDANTIX_SITE_URL

    today_str = today.isoformat()
    game_data = game_data or {}

    def _index_lastmod(key: str, archive_dir: Path) -> str:
        data = game_data.get(key)
        if data:
            try:
                return date.fromisoformat(data["date"]).isoformat()
            except (KeyError, ValueError):
                pass
        dates = _archive_dates(archive_dir)
        return dates[0].isoformat() if dates else today_str

    cemantix_urls = _game_sitemap(
        "cemantix", CEMANTIX_SITE_URL, CEMANTIX_ARCHIVE,
        _index_lastmod("cemantix", CEMANTIX_ARCHIVE), month_pages=True,
    )
    if (DOCS_DIR / "cemantix" / "indice" / "index.html").exists():
        cemantix_urls.insert(1, _url_entry(f"{CEMANTIX_SITE_URL}/indice/", _index_lastmod("cemantix", CEMANTIX_ARCHIVE)))
    _write_urlset(DOCS_DIR / "sitemap-cemantix.xml", cemantix_urls)
    _write_urlset(DOCS_DIR / "sitemap-sutom.xml", _game_sitemap(
        "sutom", SUTOM_SITE_URL, SUTOM_ARCHIVE, _index_lastmod("sutom", SUTOM_ARCHIVE),
    ))
    _write_urlset(DOCS_DIR / "sitemap-pedantix.xml", _game_sitemap(
        "pedantix", PEDANTIX_SITE_URL, PEDANTIX_ARCHIVE, _index_lastmod("pedantix", PEDANTIX_ARCHIVE),
    ))

    loto_dates = _archive_dates(LOTO_ARCHIVE)
    _write_urlset(DOCS_DIR / "sitemap-loto.xml", _game_sitemap(
        "loto", LOTO_SITE_URL, LOTO_ARCHIVE, _index_lastmod("loto", LOTO_ARCHIVE),
    ))

    em_dates = _archive_dates(EM_ARCHIVE)
    _write_urlset(DOCS_DIR / "sitemap-euromillions.xml", _game_sitemap(
        "euromillions", EM_SITE_URL, EM_ARCHIVE, _index_lastmod("euromillions", EM_ARCHIVE),
    ))

    _write_urlset(DOCS_DIR / "sitemap-pages.xml", _pages_sitemap(today_str, loto_dates, em_dates))

    _write_sitemap_index(today_str)


# ── Flux Atom ─────────────────────────────────────────────────────────────────

def _feed_content(key: str, data: dict) -> str:
    """Résumé HTML (avec solution) pour le flux Atom — pas indexé comme une page web."""
    if key in ("cemantix", "sutom"):
        return f"Mot : <strong>{_xml_escape(data.get('word', '').upper())}</strong>"
    if key == "pedantix":
        title = data.get("title_display") or data.get("word", "")
        return f"Article : <strong>{_xml_escape(title)}</strong>"
    if key == "loto":
        balls = " · ".join(str(b) for b in data.get("balls", []))
        return f"Numéros : <strong>{_xml_escape(balls)}</strong> + chance {_xml_escape(str(data.get('lucky_ball', '')))}"
    if key == "euromillions":
        balls = " · ".join(str(b) for b in data.get("balls", []))
        stars = " · ".join(str(s) for s in data.get("stars", []))
        return f"Numéros : <strong>{_xml_escape(balls)}</strong> — Étoiles : {_xml_escape(stars)}"
    return ""


def generate_atom_feed(today: date, game_data: dict, days: int = 30) -> None:
    """Génère docs/feed.xml — flux Atom des `days` derniers jours, tous jeux confondus."""
    from games.cemantix import CEMANTIX_ARCHIVE, CEMANTIX_SITE_URL
    from games.sutom import SUTOM_ARCHIVE, SUTOM_SITE_URL
    from games.loto import LOTO_ARCHIVE, LOTO_SITE_URL
    from games.euromillions import EM_ARCHIVE, EM_SITE_URL
    from games.pedantix import PEDANTIX_ARCHIVE, PEDANTIX_SITE_URL

    games_dirs = {
        "cemantix":     (CEMANTIX_SITE_URL, CEMANTIX_ARCHIVE),
        "sutom":        (SUTOM_SITE_URL,    SUTOM_ARCHIVE),
        "pedantix":     (PEDANTIX_SITE_URL, PEDANTIX_ARCHIVE),
        "loto":         (LOTO_SITE_URL,     LOTO_ARCHIVE),
        "euromillions": (EM_SITE_URL,       EM_ARCHIVE),
    }

    raw_entries = []
    for key, (base_url, archive_dir) in games_dirs.items():
        if not archive_dir.exists():
            continue
        for f in archive_dir.glob("????-??-??.json"):
            try:
                d = date.fromisoformat(f.stem)
            except ValueError:
                continue
            if d > today or (today - d).days > days:
                continue
            raw_entries.append((d, key, base_url, archive_dir, f))

    raw_entries.sort(key=lambda t: t[0], reverse=True)

    feed_entries = []
    for d, key, base_url, archive_dir, json_path in raw_entries:
        cfg = GAMES_CFG[key]
        hh, mm = cfg["pub_time"]
        d_str = d.isoformat()
        html_path = archive_dir / f"{d_str}.html"
        link = f"{base_url}/archive/{d_str}" if html_path.exists() else f"{base_url}/"
        entry_id = f"{base_url}/archive/{d_str}"
        label = date_fr(d)
        title = f"{cfg['title_prefix']} {label}"
        updated = iso_paris(d, hh, mm)
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        content = _feed_content(key, data)
        feed_entries.append(f"""  <entry>
    <id>{_xml_escape(entry_id)}</id>
    <link href="{_xml_escape(link)}"/>
    <title>{_xml_escape(title)}</title>
    <updated>{updated}</updated>
    <summary>Solution et détails pour le {_xml_escape(label)}.</summary>
    <content type="html">{_xml_escape(content)}</content>
  </entry>""")

    if not feed_entries:
        return

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Solutions du Jour</title>
  <link href="{SITE_URL}/feed.xml" rel="self"/>
  <link href="{SITE_URL}/"/>
  <id>{SITE_URL}/</id>
  <updated>{iso_paris(today, 8, 5)}</updated>
{chr(10).join(feed_entries)}
</feed>
"""
    atomic_write(DOCS_DIR / "feed.xml", feed)


# ── Régénération quotidienne (daily.yml + seed_archives.py) ──────────────────

def regenerate_all(today: date | None = None) -> dict:
    """Relit les 5 solution.json et régénère tout le HTML + hub + sitemaps + flux Atom.

    Reprend la logique du script inline historique de .github/workflows/daily.yml —
    seul point d'entrée à faire évoluer désormais si les signatures des jeux changent.
    """
    if today is None:
        today = date.today()
    today_str = today.isoformat()

    cemantix_data = sutom_data = pedantix_data = loto_data = em_data = None

    cemantix_json = DOCS_DIR / "cemantix" / "solution.json"
    if cemantix_json.exists():
        data = json.loads(cemantix_json.read_text(encoding="utf-8"))
        if data.get("date") == today_str:
            cemantix_data = data
            hints = data.get("hints", {"level1": [], "level2": [], "level3": []})
            definition = data.get("definition", "")
            from games import cemantix as c
            print(f"Régénération HTML Cémantix #{data['puzzle_num']} '{data['word']}'")
            c._generate_all_html(today, data["puzzle_num"], data["word"], hints, definition, data.get("generated_at"))
            print("✅ HTML Cémantix régénéré")

    sutom_json = DOCS_DIR / "sutom" / "solution.json"
    if sutom_json.exists():
        data = json.loads(sutom_json.read_text(encoding="utf-8"))
        if data.get("date") == today_str:
            sutom_data = data
            from games import sutom as s
            print(f"Régénération HTML Sutom #{data['puzzle_num']} '{data['word']}'")
            s._generate_all_html(
                today, data["puzzle_num"], data["word"],
                data.get("definition", ""), data.get("generated_at"),
            )
            print("✅ HTML Sutom régénéré")

    pedantix_json = DOCS_DIR / "pedantix" / "solution.json"
    if pedantix_json.exists():
        data = json.loads(pedantix_json.read_text(encoding="utf-8"))
        if data.get("date") == today_str:
            pedantix_data = data
            from games import pedantix as pd
            title_display = data.get("title_display") or data.get("word", "?")
            title_slug = data.get("title_slug", title_display)
            hints = data.get("hints", {"level1": [], "level2": [], "level3": []})
            extract = data.get("extract", "")
            print(f"Régénération HTML Pédantix #{data['puzzle_num']} '{title_display}'")
            pd._generate_all_html(today, data["puzzle_num"], title_display, title_slug, hints, extract, data.get("generated_at"))
            print("✅ HTML Pédantix régénéré")

    loto_json = DOCS_DIR / "loto" / "solution.json"
    if loto_json.exists():
        data = json.loads(loto_json.read_text(encoding="utf-8"))
        loto_data = data
        from games import loto as lt
        draw_date = date.fromisoformat(data["date"])
        print(f"Régénération HTML Loto n°{data['draw_num']} {data['date']}")
        lt._generate_all_html(draw_date, data)
        print("✅ HTML Loto régénéré")

    em_json = DOCS_DIR / "euromillions" / "solution.json"
    if em_json.exists():
        data = json.loads(em_json.read_text(encoding="utf-8"))
        em_data = data
        from games import euromillions as em
        draw_date_em = date.fromisoformat(data["date"])
        print(f"Régénération HTML EuroMillions {data['date']}")
        em._generate_all_html(draw_date_em, data)
        print("✅ HTML EuroMillions régénéré")

    game_data_all = {
        "cemantix": cemantix_data, "sutom": sutom_data,
        "pedantix": pedantix_data,
        "loto": loto_data, "euromillions": em_data,
    }
    from games.evergreen import generate_evergreen, generate_about_page
    generate_evergreen(today)
    generate_about_page()
    generate_hub_html(today, game_data_all)
    generate_global_sitemap(today, game_data_all)
    generate_news_sitemap(today, game_data_all)
    generate_atom_feed(today, game_data_all)
    print("✅ Hub + evergreen + à-propos + sitemaps + flux Atom régénérés")
    return game_data_all


def daily_urls(today: date, game_data: dict) -> list[str]:
    """URLs à notifier à IndexNow chaque jour (hub, index par jeu, archive d'hier, mois courant)."""
    from games.cemantix import CEMANTIX_ARCHIVE, CEMANTIX_SITE_URL
    from games.sutom import SUTOM_ARCHIVE, SUTOM_SITE_URL
    from games.loto import LOTO_ARCHIVE, LOTO_SITE_URL
    from games.euromillions import EM_ARCHIVE, EM_SITE_URL
    from games.pedantix import PEDANTIX_ARCHIVE, PEDANTIX_SITE_URL

    yesterday = today - timedelta(days=1)
    games_dirs = {
        "cemantix":     (CEMANTIX_SITE_URL, CEMANTIX_ARCHIVE),
        "sutom":        (SUTOM_SITE_URL,    SUTOM_ARCHIVE),
        "pedantix":     (PEDANTIX_SITE_URL, PEDANTIX_ARCHIVE),
        "loto":         (LOTO_SITE_URL,     LOTO_ARCHIVE),
        "euromillions": (EM_SITE_URL,       EM_ARCHIVE),
    }

    urls = [f"{SITE_URL}/"]
    for key, (base_url, archive_dir) in games_dirs.items():
        urls.append(f"{base_url}/")
        urls.append(f"{base_url}/archive/")
        y_str = yesterday.isoformat()
        if (archive_dir / f"{y_str}.html").exists():
            urls.append(f"{base_url}/archive/{y_str}")
        ym = today.strftime("%Y-%m")
        if (archive_dir / f"{ym}.html").exists():
            urls.append(f"{base_url}/archive/{ym}")
        if key == "cemantix" and (DOCS_DIR / "cemantix" / "indice" / "index.html").exists():
            urls.append(f"{base_url}/indice/")
    return urls


def ping_daily_indexnow(today: date | None = None, game_data: dict | None = None) -> bool:
    """Notifie IndexNow des URLs fraîches du jour. Best-effort, appelé après le push (daily.yml)."""
    if today is None:
        today = date.today()
    urls = daily_urls(today, game_data or {})
    return ping_indexnow(urls)


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

    # 5. Pédantix
    print("\n─── Pédantix ───────────────────────────────────────────")
    from games.pedantix import run as run_pedantix
    pedantix_data = run_pedantix(today)

    game_data_all = {
        "cemantix": cemantix_data, "sutom": sutom_data,
        "loto": loto_data, "euromillions": em_data,
        "pedantix": pedantix_data,
    }

    # 6. Pages evergreen + à-propos
    print("\n─── Evergreen ──────────────────────────────────────────")
    from games.evergreen import generate_evergreen, generate_about_page
    generate_evergreen(today)
    generate_about_page()
    print("Génération des pages evergreen + à-propos…")

    # 6bis. Hub page
    print("\n─── Hub ────────────────────────────────────────────────")
    print("Génération de docs/index.html (hub)…")
    generate_hub_html(today, game_data_all)

    # 7. Sitemap global (sitemapindex + sous-sitemaps)
    print("Génération de docs/sitemap.xml (sitemapindex)…")
    generate_global_sitemap(today, game_data_all)

    # 8. Google News sitemap
    print("Génération de docs/news-sitemap.xml (Google News)…")
    generate_news_sitemap(today, game_data_all)

    # 9. Flux Atom
    print("Génération de docs/feed.xml (Atom)…")
    generate_atom_feed(today, game_data_all)

    print(f"\n🎉 Site complet généré pour le {date_fr(today)}")
    print(f"   docs/index.html                          ✓ (hub)")
    print(f"   docs/cemantix/index.html                 {'✓' if cemantix_data else '⚠ indisponible'}")
    print(f"   docs/sutom/index.html                    {'✓' if sutom_data else '⚠ indisponible'}")
    print(f"   docs/pedantix/index.html                 {'✓' if pedantix_data else '⚠ indisponible'}")
    print(f"   docs/loto/index.html                     {'✓' if loto_data else '⚠ indisponible'}")
    print(f"   docs/loto/simulateur/                    ✓")
    print(f"   docs/euromillions/index.html             {'✓' if em_data else '⚠ indisponible'}")
    print(f"   docs/euromillions/simulateur/            ✓")
    print(f"   docs/sitemap.xml                         ✓")
    print(f"   docs/news-sitemap.xml                    ✓\n")


if __name__ == "__main__":
    main()
