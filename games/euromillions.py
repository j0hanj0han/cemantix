"""
games/euromillions.py — Résultats du tirage EuroMillions.

Données : scraping de euro-millions.com/fr/resultats (site officiel multi-pays)
Format EuroMillions : 5 boules (1–50) + 2 étoiles (1–12)
Tirages : mardi et vendredi soir (~21h05)

Génère :
  docs/euromillions/solution.json          ← dernier tirage
  docs/euromillions/index.html             ← résultats du dernier tirage
  docs/euromillions/archive/YYYY-MM-DD.json
  docs/euromillions/archive/YYYY-MM-DD.html
  docs/euromillions/archive/index.html
  docs/euromillions/stats/index.html       ← statistiques
"""

import csv
import io
import json
import re
import urllib.request
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from core import (
    SITE_URL, DOCS_DIR, _session, date_fr, atomic_write,
    fetch_static_html, jackpot_html,
    load_all_archives as _load_archives,
)

# ── Configuration ─────────────────────────────────────────────────────────────

EM_DIR = DOCS_DIR / "euromillions"
EM_ARCHIVE = EM_DIR / "archive"
EM_SITE_URL = f"{SITE_URL}/euromillions"

_EM_RESULTS_URL = "https://www.euro-millions.com/fr/resultats"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
_MONTHS_FR = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}


# ── Scraping ──────────────────────────────────────────────────────────────────

def get_euromillions_latest() -> dict | None:
    """
    Scrape le dernier tirage EuroMillions depuis euro-millions.com/fr/resultats.
    Retourne {date, balls, stars} ou None si indisponible.
    Les boules et étoiles sont triées par ordre croissant.
    """
    try:
        resp = _session.get(_EM_RESULTS_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"   ⚠ EuroMillions : erreur réseau : {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Trouver le bloc du dernier tirage : remonter depuis la première .ball
    # jusqu'au bloc parent qui contient aussi une date en français
    first_ball = soup.find(class_="ball")
    if not first_ball:
        print("   ⚠ EuroMillions : aucune boule trouvée dans la page")
        return None

    node = first_ball.parent
    for _ in range(12):
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        m = re.search(
            r"(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|juillet|août|"
            r"septembre|octobre|novembre|décembre)\s+(20\d{2})",
            text, re.I,
        )
        if m:
            day, month_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            draw_date = date(year, _MONTHS_FR[month_str], day)
            balls = sorted(int(b.text.strip()) for b in node.find_all(class_="ball")[:5] if b.text.strip().isdigit())
            stars = sorted(int(s.text.strip()) for s in node.find_all(class_="lucky-star")[:2] if s.text.strip().isdigit())
            if len(balls) == 5 and len(stars) == 2:
                # Code My Million : dans div.raffleCodeBox > span.raffle
                code = ""
                raffle_box = soup.find(class_="raffleCodeBox")
                if raffle_box:
                    raffle_span = raffle_box.find(class_="raffle")
                    if raffle_span:
                        code = raffle_span.get_text(strip=True)
                return {
                    "date": draw_date.isoformat(),
                    "balls": balls,
                    "stars": stars,
                    "code": code,
                }
            break
        node = node.parent

    print("   ⚠ EuroMillions : impossible d'extraire les résultats du dernier tirage")
    return None


# ── Helpers HTML ──────────────────────────────────────────────────────────────

def _em_code_html(code: str) -> str:
    """Retourne le bloc HTML du code My Million, ou '' si absent."""
    if not code:
        return ""
    return f"""      <div class="draw-codes">
        <p class="draw-codes-title">Code My Million</p>
        <span class="draw-code-single">{code}</span>
        <p class="draw-codes-note">1&nbsp;million&nbsp;€ garanti si votre ticket porte ce code.</p>
      </div>"""


def _em_balls_html(balls: list[int], stars: list[int], small: bool = False) -> str:
    """Retourne le bloc HTML des boules + étoiles EuroMillions."""
    cls = "loto-ball loto-ball-sm" if small else "loto-ball"
    cls_s = f"{cls} em-star"
    wrap = "loto-balls loto-balls-sm" if small else "loto-balls"
    inner = "".join(f'<span class="{cls}">{b}</span>' for b in balls)
    inner += "".join(f'<span class="{cls_s}">&#9733;{s}</span>' for s in stars)
    return f'<div class="{wrap}">{inner}</div>'


# ── Fichiers JSON ─────────────────────────────────────────────────────────────

def generate_solution_json(draw: dict) -> dict:
    data = {**draw, "generated_at": datetime.now(timezone.utc).isoformat()}
    EM_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(EM_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(data: dict) -> None:
    EM_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(EM_ARCHIVE / f"{data['date']}.json",
                 json.dumps(data, ensure_ascii=False, indent=2))


def load_all_archives() -> list[dict]:
    return _load_archives(EM_ARCHIVE, required_keys=["date", "balls", "stars"])


# ── Génération HTML ───────────────────────────────────────────────────────────

def generate_archive_html(
    draw_date: date,
    balls: list[int],
    stars: list[int],
    prev_date,
    next_date,
    jackpot_amount=None,
    jackpot_winners=0,
    jackpot_won=False,
    code: str = "",
) -> None:
    """Génère docs/euromillions/archive/YYYY-MM-DD.html."""
    EM_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = draw_date.isoformat()
    date_display = date_fr(draw_date)
    balls_str = " · ".join(str(b) for b in balls)
    stars_str = " · ".join(str(s) for s in stars)

    if prev_date is not None:
        nav_prev = f'<a class="nav-link" href="{prev_date.isoformat()}.html">&#8592; {date_fr(prev_date)}</a>'
    else:
        nav_prev = '<span class="nav-disabled">&#8592; Plus ancien</span>'

    if next_date is not None:
        nav_next = f'<a class="nav-link" href="{next_date.isoformat()}.html">{date_fr(next_date)} &#8594;</a>'
    else:
        nav_next = '<a class="nav-link" href="../index.html">Dernier tirage &#8594;</a>'

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Résultats EuroMillions {date_display} — Numéros gagnants · Archive</title>
  <meta name="description" content="Résultats du tirage EuroMillions du {date_display}. Numéros : {balls_str}. Étoiles : {stars_str}.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{EM_SITE_URL}/archive/{date_str}">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta property="og:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{EM_SITE_URL}/archive/{date_str}">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta name="twitter:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="article:published_time" content="{date_str}T21:30:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Résultats EuroMillions {date_display}",
    "datePublished": "{date_str}T21:30:00+01:00",
    "dateModified": "{date_str}T21:30:00+01:00",
    "description": "Numéros gagnants EuroMillions du {date_display} : {balls_str} — étoiles : {stars_str}.",
    "url": "{EM_SITE_URL}/archive/{date_str}",
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
        "name": "Quels sont les numéros EuroMillions du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Les numéros gagnants de l'EuroMillions du {date_display} sont : {balls_str}, avec les étoiles {stars_str}."
        }}
      }}
    ]
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "EuroMillions", "item": "https://solution-du-jour.fr/euromillions/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "https://solution-du-jour.fr/euromillions/archive/"}},
      {{"@type": "ListItem", "position": 4, "name": "Tirage du {date_display}"}}
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
  <h1>EuroMillions — Archive</h1>
  <p class="subtitle">Résultats du {date_display}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../index.html">EuroMillions</a> &rsaquo;
  <a href="index.html">Archives</a> &rsaquo;
  <span>Tirage du {date_display}</span>
</nav>
  <nav class="nav-archive" aria-label="Navigation entre les tirages">
    {nav_prev}
    <a class="nav-center" href="index.html">Tous les tirages</a>
    {nav_next}
  </nav>

  <article>

    <div class="card">
      <h2>Tirage EuroMillions — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez les <strong>résultats de l'EuroMillions du {date_display}</strong>.
        Les numéros sont affichés dans l'ordre croissant.
      </p>
    </div>

    <div class="card">
      <h2>Numéros gagnants</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        5 boules (fond gris) + 2 étoiles &#9733; (fond doré)
      </p>
      {_em_balls_html(balls, stars)}
      <p class="puzzle-meta">
        Boules : <strong>{balls_str}</strong> · Étoiles : <strong>{stars_str}</strong>
      </p>
{jackpot_html(jackpot_won, jackpot_winners, jackpot_amount)}
{_em_code_html(code)}
    </div>

    <div class="card">
      <h2>À propos de ce tirage</h2>
      <p>
        Ce tirage EuroMillions a eu lieu le <strong>{date_display}</strong>.
        Les tirages EuroMillions ont lieu le mardi et le vendredi soir à partir de 21h05.
        5 numéros sont tirés parmi les boules 1 à 50, et 2 étoiles parmi 1 à 12.
        Pour vérifier vos gains ou rejouer, rendez-vous sur
        <a href="https://www.fdj.fr/jeux-de-tirage/euromillions-my-million" rel="noopener" target="_blank">fdj.fr</a>.
      </p>
    </div>

  </article>

  <nav class="nav-archive" aria-label="Navigation entre les tirages">
    {nav_prev}
    <a class="nav-center" href="index.html">Tous les tirages</a>
    {nav_next}
  </nav>
</main>

<footer>
  <p>
    <a href="../index.html">Dernier tirage</a> ·
    <a href="index.html">Tous les tirages</a> ·
    <a href="../stats/">Statistiques</a> ·
    <a href="https://www.euro-millions.com/fr" rel="noopener" target="_blank">euro-millions.com</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Résultats récupérés automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(EM_ARCHIVE / f"{date_str}.html", html)


def generate_archive_index(entries: list[dict]) -> None:
    """Génère docs/euromillions/archive/index.html."""
    EM_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        balls_str = " · ".join(str(b) for b in e["balls"])
        stars_str = " · ".join(str(s) for s in e["stars"])
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">&#9733; {stars_str}</span>'
            f'<a class="arch-link" href="{e["date"]}.html">{balls_str}</a>'
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

  <title>Archives EuroMillions — Tous les tirages et numéros gagnants</title>
  <meta name="description" content="Retrouvez tous les résultats des tirages EuroMillions : numéros gagnants et étoiles pour chaque tirage du mardi et vendredi.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{EM_SITE_URL}/archive/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives EuroMillions — Tous les numéros gagnants">
  <meta property="og:description" content="Tous les résultats des tirages EuroMillions avec numéros et étoiles.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{EM_SITE_URL}/archive/">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta property="og:image" content="{SITE_URL}/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Archives EuroMillions — Tous les numéros gagnants">
  <meta name="twitter:description" content="Tous les résultats des tirages EuroMillions avec numéros et étoiles depuis 2004.">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "EuroMillions", "item": "{EM_SITE_URL}/"}},
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
  <h1>Archives EuroMillions</h1>
  <p class="subtitle">{count} tirage{"s" if count > 1 else ""} enregistré{"s" if count > 1 else ""}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../index.html">EuroMillions</a> &rsaquo;
  <span>Archives</span>
</nav>
  <div class="card">
    <h2>Tous les tirages EuroMillions ({count})</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Cliquez sur un tirage pour voir les détails. Format : boules | &#9733; étoiles.
    </p>
    <ul class="arch-list">
{items_html}
    </ul>
  </div>

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../index.html">Dernier tirage &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../index.html">Dernier tirage</a> ·
    <a href="../stats/">Statistiques</a> ·
    <a href="https://www.euro-millions.com/fr" rel="noopener" target="_blank">euro-millions.com</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Résultats récupérés automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(EM_ARCHIVE / "index.html", html)


def generate_index_html(
    draw_date: date,
    balls: list[int],
    stars: list[int],
    recent_archives: list | None = None,
    total_archives: int = 0,
    jackpot_amount=None,
    jackpot_winners=0,
    jackpot_won=False,
    next_jackpot: float | None = None,
    code: str = "",
) -> None:
    """Génère docs/euromillions/index.html — dernier tirage."""
    date_str = draw_date.isoformat()
    date_display = date_fr(draw_date)
    balls_str = " · ".join(str(b) for b in balls)
    stars_str = " · ".join(str(s) for s in stars)

    recent_archives_card = ""
    if recent_archives:
        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            b_str = " · ".join(str(b) for b in e["balls"])
            s_str = " · ".join(str(s) for s in e["stars"])
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">&#9733; {s_str}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}.html">{b_str}</a>'
                f'</li>'
            )
        items = "\n".join(arch_item(e) for e in recent_archives[:7])
        recent_archives_card = f"""
    <div class="card">
      <h2>Tirages précédents</h2>
      <ul class="arch-list">
{items}
      </ul>
      <p style="margin-top:.75rem;font-size:.9rem;">
        <a href="archive/">Voir tous les tirages &#8594;</a>
      </p>
    </div>"""

    if next_jackpot:
        nj_str = f"{next_jackpot:,.0f}".replace(",", "\u202f")
        next_jackpot_card = f"""    <div class="card" style="text-align:center;">
      <h2 style="font-size:1rem;margin-bottom:.4rem;">Prochain jackpot EuroMillions</h2>
      <p style="font-size:1.4rem;font-weight:700;color:#7c3aed;margin:.4rem 0;">
        {nj_str} \u20ac
      </p>
      <p style="font-size:.85rem;color:#6b7280;">Prochain tirage : mardi ou vendredi à 21h05</p>
    </div>"""
    else:
        next_jackpot_card = ""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>⭐ Résultats EuroMillions {date_display} — Numéros gagnants du dernier tirage</title>
  <meta name="description" content="Résultats du tirage EuroMillions du {date_display}. Numéros gagnants : {balls_str}. Étoiles : {stars_str}. Mis à jour automatiquement après chaque tirage.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{EM_SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta property="og:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{EM_SITE_URL}/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta name="twitter:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="article:published_time" content="{date_str}T21:30:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Résultats EuroMillions {date_display}",
    "datePublished": "{date_str}T21:30:00+01:00",
    "dateModified": "{date_str}T21:30:00+01:00",
    "description": "Numéros gagnants EuroMillions du {date_display} : {balls_str} — étoiles : {stars_str}.",
    "url": "{EM_SITE_URL}/",
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
        "name": "Quels sont les numéros gagnants de l'EuroMillions du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Les numéros gagnants de l'EuroMillions du {date_display} sont : {balls_str}, avec les étoiles {stars_str}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quand a lieu le prochain tirage EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "L'EuroMillions tire le mardi et le vendredi soir (vers 21h05). Cette page est mise à jour automatiquement après chaque tirage."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quelles sont les étoiles de l'EuroMillions du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Les étoiles (numéros étoiles) du tirage EuroMillions du {date_display} sont le {stars_str}."
        }}
      }}
    ]
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "EuroMillions", "item": "https://solution-du-jour.fr/euromillions/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Résultats EuroMillions</h1>
  <p class="subtitle">Numéros gagnants du dernier tirage</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <span>EuroMillions</span>
</nav>
  <article>

    <div class="card">
      <h2>Tirage EuroMillions — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez les <strong>résultats de l'EuroMillions du {date_display}</strong>.
        Les numéros sont affichés dans l'ordre croissant. Les tirages ont lieu le mardi et le vendredi soir.
      </p>
    </div>

    <div class="card">
      <h2>Vérifiez votre grille</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Sélectionnez vos 5 numéros + 2 étoiles pour savoir si vous avez gagné.
      </p>
      <script>
      (function(){{
        const DRAW_BALLS = {balls};
        const DRAW_STARS = {stars};
        const EM_GAINS = {{1:null,2:600000,3:40000,4:3500,5:200,6:150,7:100,8:20,9:15,10:12,11:10,12:6,13:4}};
        const RANK_LABELS = {{
          1:'1er rang — Jackpot',2:'2e rang',3:'3e rang',4:'4e rang',5:'5e rang',
          6:'6e rang',7:'7e rang',8:'8e rang',9:'9e rang',10:'10e rang',
          11:'11e rang',12:'12e rang',13:'13e rang'
        }};
        const RANK_DESC = {{
          1:'5+2',2:'5+1',3:'5+0',4:'4+2',5:'4+1',6:'3+2',7:'4+0',
          8:'2+2',9:'3+1',10:'3+0',11:'1+2',12:'2+1',13:'2+0'
        }};
        function emRank(ub,us,db,ds){{
          const mb=ub.filter(n=>db.includes(n)).length,ms=us.filter(n=>ds.includes(n)).length;
          if(mb===5&&ms===2)return 1;if(mb===5&&ms===1)return 2;
          if(mb===5&&ms===0)return 3;if(mb===4&&ms===2)return 4;
          if(mb===4&&ms===1)return 5;if(mb===3&&ms===2)return 6;
          if(mb===4&&ms===0)return 7;if(mb===2&&ms===2)return 8;
          if(mb===3&&ms===1)return 9;if(mb===3&&ms===0)return 10;
          if(mb===1&&ms===2)return 11;if(mb===2&&ms===1)return 12;
          if(mb===2&&ms===0)return 13;return 0;
        }}
        let userBalls=[], userStars=[];
        function render(){{
          const res=document.getElementById('em-checker-result');
          if(!res)return;
          if(userBalls.length<5||userStars.length<2){{res.style.display='none';return;}}
          const rank=emRank(userBalls,userStars,DRAW_BALLS,DRAW_STARS);
          res.style.display='block';
          if(rank===0){{
            res.className='sim-result';
            res.innerHTML='<div class="sim-result-rank">Pas de gain</div>'
              +'<div class="sim-result-detail">Aucune combinaison gagnante.</div>';
          }}else{{
            const gain=EM_GAINS[rank];
            res.className='sim-result win';
            res.innerHTML='<div class="sim-result-rank">'+RANK_LABELS[rank]+' — '+RANK_DESC[rank]+'</div>'
              +(gain?'<div class="sim-result-gain">≈ '+gain.toLocaleString('fr-FR')+'&nbsp;€</div>':'<div class="sim-result-gain">Jackpot !</div>');
          }}
        }}
        function buildPicker(){{
          const bg=document.getElementById('em-checker-balls');
          const sg=document.getElementById('em-checker-stars');
          if(!bg||!sg)return;
          for(let n=1;n<=50;n++){{
            const btn=document.createElement('button');
            btn.className='sim-ball em-type';btn.type='button';btn.textContent=n;
            btn.addEventListener('click',()=>{{
              const i=userBalls.indexOf(n);
              if(i>=0){{userBalls.splice(i,1);btn.classList.remove('selected');}}
              else if(userBalls.length<5){{userBalls.push(n);btn.classList.add('selected');}}
              bg.querySelectorAll('.sim-ball').forEach(b=>{{
                b.classList.toggle('dimmed',userBalls.length>=5&&!userBalls.includes(parseInt(b.textContent)));
              }});
              document.getElementById('em-cnt-balls').textContent=userBalls.length;
              render();
            }});
            bg.appendChild(btn);
          }}
          for(let n=1;n<=12;n++){{
            const btn=document.createElement('button');
            btn.className='sim-ball star-type';btn.type='button';btn.textContent=n;
            btn.addEventListener('click',()=>{{
              const i=userStars.indexOf(n);
              if(i>=0){{userStars.splice(i,1);btn.classList.remove('selected');}}
              else if(userStars.length<2){{userStars.push(n);btn.classList.add('selected');}}
              sg.querySelectorAll('.sim-ball').forEach(b=>{{
                b.classList.toggle('dimmed',userStars.length>=2&&!userStars.includes(parseInt(b.textContent)));
              }});
              document.getElementById('em-cnt-stars').textContent=userStars.length;
              render();
            }});
            sg.appendChild(btn);
          }}
        }}
        document.addEventListener('DOMContentLoaded',buildPicker);
      }})();
      </script>
      <p class="sim-label">Vos 5 numéros <span id="em-cnt-balls">0</span>/5</p>
      <div class="sim-grid" id="em-checker-balls"></div>
      <p class="sim-label">Vos 2 étoiles <span id="em-cnt-stars">0</span>/2</p>
      <div class="sim-grid" id="em-checker-stars"></div>
      <div id="em-checker-result" style="display:none;"></div>
      <p style="margin-top:1rem;font-size:.85rem;color:#6b7280;">
        Testez vos numéros sur tout l'historique : <a href="simulateur/">Simulateur EuroMillions depuis 2004 &#8594;</a>
      </p>
    </div>

    <div class="card">
      <h2>Numéros gagnants du {date_display}</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        5 boules (1–50) + 2 étoiles &#9733; (1–12, fond doré)
      </p>
      <div class="solution-blur" id="em-balls-blur">
        {_em_balls_html(balls, stars)}
        <p class="puzzle-meta">
          Boules : <strong>{balls_str}</strong> · Étoiles : <strong>{stars_str}</strong>
        </p>
{jackpot_html(jackpot_won, jackpot_winners, jackpot_amount)}
{_em_code_html(code)}
      </div>
      <p style="text-align:center;margin-top:.75rem;">
        <button class="reveal-btn" id="em-reveal-btn"
          onclick="document.getElementById('em-balls-blur').classList.add('revealed');this.parentElement.style.display='none';">
          Voir les numéros gagnants
        </button>
      </p>
    </div>

{next_jackpot_card}
{recent_archives_card}
    <div class="card" style="text-align:center;">
      <h2 style="font-size:1rem;margin-bottom:.4rem;">Statistiques EuroMillions depuis 2004</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:.75rem;">
        Numéros les plus sortis, étoiles fréquentes et retardataires sur {total_archives} tirages analysés.
      </p>
      <a class="reveal-btn" href="stats/">Voir les statistiques &#8594;</a>
    </div>
    <div class="card">
      <h2>Comment fonctionne l'EuroMillions ?</h2>
      <p>
        L'<strong>EuroMillions</strong> est une loterie multi-pays organisée notamment par
        la <a href="https://www.fdj.fr" rel="noopener" target="_blank">FDJ</a> en France.
        Chaque tirage, 5 numéros (de 1 à 50) et 2 étoiles (de 1 à 12) sont tirés au sort.
        Les tirages ont lieu le <strong>mardi et vendredi</strong> à partir de 21h05.
      </p>
      <p style="margin-top:.75rem;">
        Pour vérifier vos gains ou pour jouer, rendez-vous sur
        <a href="https://www.fdj.fr/jeux-de-tirage/euromillions-my-million" rel="noopener" target="_blank">fdj.fr</a>.
        Cette page est mise à jour automatiquement après chaque tirage.
      </p>
    </div>

    <div class="card" style="margin-top:.5rem;">
      <h2 style="font-size:1rem;margin-bottom:.75rem;">Autres jeux du jour</h2>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
        <a href="simulateur/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🎯 Simulateur de gains</a>
        <a href="../cemantix/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🧠 Cémantix</a>
        <a href="../sutom/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🔤 Sutom</a>
        <a href="../loto/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🎱 Loto FDJ</a>
      </div>
    </div>
  </article>
</main>

<footer>
  <p>Site non officiel — Résultats récupérés automatiquement · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a> · <a href="stats/">Statistiques</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://www.fdj.fr/jeux-de-tirage/euromillions-my-million" rel="noopener" target="_blank">fdj.fr</a></p>
</footer>

</body>
</html>"""

    atomic_write(EM_DIR / "index.html", html)


def generate_unavailable_html(today: date) -> None:
    """Génère une page 'résultats non disponibles' si le scraping échoue."""
    EM_DIR.mkdir(parents=True, exist_ok=True)
    date_display = date_fr(today)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <title>Résultats EuroMillions — Non disponibles</title>
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="../css/style.css">
</head>
<body>

<header class="site-header">
  <h1>Résultats EuroMillions</h1>
  <p class="subtitle">{date_display}</p>
</header>

<main>
  <div class="card">
    <h2>Résultats non disponibles</h2>
    <p>
      Les résultats de l'EuroMillions ne sont pas encore disponibles ou n'ont pas pu être récupérés.
      Le prochain tirage a lieu mardi ou vendredi soir à partir de 21h05.
      Rendez-vous directement sur
      <a href="https://www.euro-millions.com/fr/resultats" rel="noopener" target="_blank">euro-millions.com</a>.
    </p>
  </div>
</main>

<footer>
  <p><a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives EuroMillions</a></p>
</footer>

</body>
</html>"""

    atomic_write(EM_DIR / "index.html", html)


# ── Backfill historique ────────────────────────────────────────────────────────

_FDJ_ZIPS = [
    "https://media.fdj.fr/static-draws/csv/euromillions/euromillions_200402.zip",
    "https://media.fdj.fr/static-draws/csv/euromillions/euromillions_201105.zip",
    "https://media.fdj.fr/static-draws/csv/euromillions/euromillions_201402.zip",
    "https://media.fdj.fr/static-draws/csv/euromillions/euromillions_201609.zip",
    "https://media.fdj.fr/static-draws/csv/euromillions/euromillions_201902.zip",
    "https://media.fdj.fr/static-draws/csv/euromillions/euromillions_202002.zip",
]
_PEDRO_API = "https://euromillions.api.pedromealha.dev/v1/draws"


def get_em_next_jackpot() -> float | None:
    """Récupère le montant du prochain jackpot EuroMillions depuis tirage-gagnant.com."""
    html = fetch_static_html("https://www.tirage-gagnant.com/euromillions/resultats-euromillions/")
    if html is None:
        return None
    soup = BeautifulSoup(html, "html.parser")
    montant = soup.find(class_="montant")
    if not montant:
        return None
    try:
        raw = montant.get_text(strip=True).replace("€", "").replace(" ", "").replace(".", "").replace("\xa0", "").replace(",", "")
        return float(raw)
    except ValueError:
        return None


def fetch_jackpot_data() -> dict:
    """
    Récupère les données jackpot de tous les tirages EuroMillions via pedro-mealha.
    Retourne {date_str: {jackpot_amount, jackpot_winners, jackpot_won}}.
    """
    try:
        resp = _session.get(_PEDRO_API, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"   ⚠ pedro-mealha jackpot : {e}")
        return {}
    result = {}
    for draw in resp.json():
        d = draw.get("date", "")
        if not d:
            continue
        jackpot_prize = next(
            (p for p in draw.get("prizes", [])
             if p["matched_numbers"] == 5 and p["matched_stars"] == 2),
            None,
        )
        result[d] = {
            "jackpot_amount": jackpot_prize["prize"] if jackpot_prize else None,
            "jackpot_winners": jackpot_prize["winners"] if jackpot_prize else 0,
            "jackpot_won": draw.get("has_winner", False),
        }
    return result


def enrich_archives_with_jackpot() -> int:
    """
    Enrichit les archives EuroMillions avec les données jackpot de pedro-mealha.
    Retourne le nombre de fichiers mis à jour.
    """
    print("[EuroMillions] Récupération données jackpot (pedro-mealha)…")
    jackpot_map = fetch_jackpot_data()
    if not jackpot_map:
        return 0
    updated = 0
    for f in EM_ARCHIVE.glob("????-??-??.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        d = data.get("date", "")
        if d not in jackpot_map:
            continue
        jk = jackpot_map[d]
        if "jackpot_amount" not in data and jk["jackpot_amount"] is not None:
            data.update(jk)
            atomic_write(f, json.dumps(data, ensure_ascii=False, indent=2))
            updated += 1
    print(f"[EuroMillions] {updated} archives enrichies avec données jackpot")
    return updated


def _parse_em_date(s: str) -> date | None:
    """Parse une date FDJ en format YYYYMMDD, DD/MM/YYYY ou DD/MM/YY."""
    s = s.strip()
    if re.match(r"^\d{8}$", s):
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def backfill_euromillions() -> int:
    """
    Télécharge tout l'historique EuroMillions depuis les ZIPs FDJ (2004→2024)
    puis comble le gap via l'API pedro-mealha jusqu'à aujourd'hui.
    Retourne le nombre de tirages ajoutés.
    """
    EM_ARCHIVE.mkdir(parents=True, exist_ok=True)
    added = 0
    max_fdj_date = date(2000, 1, 1)

    # ── Phase 1 : 6 ZIPs FDJ ──
    for url in _FDJ_ZIPS:
        print(f"   → {url.split('/')[-1]}…", end=" ", flush=True)
        try:
            raw = urllib.request.urlopen(url, timeout=30).read()
        except Exception as e:
            print(f"erreur : {e}")
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            fname = z.namelist()[0]
            content = z.read(fname).decode("latin-1").splitlines()
        reader = csv.DictReader(content, delimiter=";")
        n = 0
        for row in reader:
            draw_date = _parse_em_date(row.get("date_de_tirage", ""))
            if not draw_date:
                continue
            out = EM_ARCHIVE / f"{draw_date.isoformat()}.json"
            if out.exists():
                max_fdj_date = max(max_fdj_date, draw_date)
                continue
            try:
                balls = sorted(int(row[f"boule_{i}"]) for i in range(1, 6))
                stars = sorted(int(row[f"etoile_{i}"]) for i in range(1, 3))
            except (KeyError, ValueError):
                continue
            data = {"date": draw_date.isoformat(), "balls": balls, "stars": stars}
            atomic_write(out, json.dumps(data, ensure_ascii=False, indent=2))
            max_fdj_date = max(max_fdj_date, draw_date)
            added += 1
            n += 1
        print(f"{n} nouveaux")

    # ── Phase 2 : gap via API pedro-mealha ──
    print(f"   → API pedro-mealha (après {max_fdj_date})…", end=" ", flush=True)
    try:
        resp = _session.get(_PEDRO_API, timeout=30)
        resp.raise_for_status()
        draws = resp.json()
    except Exception as e:
        print(f"erreur : {e}")
        return added

    n = 0
    for draw in draws:
        draw_date_str = draw.get("date", "")
        if not draw_date_str:
            continue
        try:
            draw_date = date.fromisoformat(draw_date_str)
        except ValueError:
            continue
        if draw_date <= max_fdj_date:
            continue
        out = EM_ARCHIVE / f"{draw_date_str}.json"
        if out.exists():
            continue
        try:
            balls = sorted(int(x) for x in draw["numbers"])
            stars = sorted(int(x) for x in draw["stars"])
        except (KeyError, ValueError):
            continue
        data = {"date": draw_date_str, "balls": balls, "stars": stars}
        atomic_write(out, json.dumps(data, ensure_ascii=False, indent=2))
        added += 1
        n += 1
    print(f"{n} nouveaux")
    return added


# ── Statistiques ───────────────────────────────────────────────────────────────

def compute_em_stats(archives: list[dict]) -> dict:
    """Calcule les statistiques EuroMillions à partir des archives JSON."""
    ball_counts = Counter()
    star_counts = Counter()
    last_seen_ball = {}
    last_seen_star = {}

    for i, draw in enumerate(archives):
        for b in draw["balls"]:
            ball_counts[b] += 1
            if b not in last_seen_ball:
                last_seen_ball[b] = i
        for s in draw["stars"]:
            star_counts[s] += 1
            if s not in last_seen_star:
                last_seen_star[s] = i

    for b in range(1, 51):
        if b not in last_seen_ball:
            last_seen_ball[b] = len(archives)
    for s in range(1, 13):
        if s not in last_seen_star:
            last_seen_star[s] = len(archives)

    sorted_balls = sorted(ball_counts.items(), key=lambda x: -x[1])
    sorted_stars = sorted(star_counts.items(), key=lambda x: -x[1])
    sorted_retard_balls = sorted(last_seen_ball.items(), key=lambda x: -x[1])
    sorted_retard_stars = sorted(last_seen_star.items(), key=lambda x: -x[1])

    recent_balls = Counter()
    recent_stars = Counter()
    for draw in archives[:50]:
        for b in draw["balls"]:
            recent_balls[b] += 1
        for s in draw["stars"]:
            recent_stars[s] += 1
    recent_balls_sorted = sorted(recent_balls.items(), key=lambda x: -x[1])
    recent_stars_sorted = sorted(recent_stars.items(), key=lambda x: -x[1])

    return {
        "total_draws": len(archives),
        "date_from": archives[-1]["date"] if archives else "",
        "date_to": archives[0]["date"] if archives else "",
        "top_balls": sorted_balls[:10],
        "bottom_balls": sorted_balls[-5:],
        "top_stars": sorted_stars[:6],
        "retardataires_balls": sorted_retard_balls[:5],
        "retardataires_stars": sorted_retard_stars[:3],
        "recent_top_balls": recent_balls_sorted[:5],
        "recent_bottom_balls": recent_balls_sorted[-5:],
        "recent_top_stars": recent_stars_sorted[:3],
    }


def generate_em_stats_html(stats: dict) -> None:
    """Génère docs/euromillions/stats/index.html."""
    stats_dir = EM_DIR / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)

    n = stats["total_draws"]
    year_from = stats["date_from"][:4] if stats["date_from"] else "2004"
    date_from = date_fr(date.fromisoformat(stats["date_from"])) if stats["date_from"] else "—"
    date_to   = date_fr(date.fromisoformat(stats["date_to"]))   if stats["date_to"]   else "—"

    top_ball   = stats["top_balls"][0][0]   if stats["top_balls"]   else "—"
    top_star   = stats["top_stars"][0][0]   if stats["top_stars"]   else "—"
    retard_ball  = stats["retardataires_balls"][0][0]  if stats["retardataires_balls"]  else "—"
    retard_draws = stats["retardataires_balls"][0][1]  if stats["retardataires_balls"]  else 0
    bottom_balls_list = ", ".join(str(b) for b, _ in stats["bottom_balls"])

    def ball_row(b, count):
        pct = round(count / n * 100, 1) if n else 0
        return (
            f'<tr><td><span class="loto-ball" style="width:2rem;height:2rem;font-size:.85rem;'
            f'display:inline-flex;align-items:center;justify-content:center;">{b}</span></td>'
            f'<td>{count}</td><td>{pct}%</td></tr>'
        )

    def star_row(s, count):
        pct = round(count / n * 100, 1) if n else 0
        return (
            f'<tr><td><span class="loto-ball em-star" style="width:2rem;height:2rem;font-size:.85rem;'
            f'display:inline-flex;align-items:center;justify-content:center;">&#9733;{s}</span></td>'
            f'<td>{count}</td><td>{pct}%</td></tr>'
        )

    top_rows    = "\n".join(ball_row(b, c) for b, c in stats["top_balls"])
    bottom_rows = "\n".join(ball_row(b, c) for b, c in stats["bottom_balls"])
    star_rows   = "\n".join(star_row(s, c) for s, c in stats["top_stars"])
    retard_ball_rows = "\n".join(
        f'<tr><td><span class="loto-ball" style="width:2rem;height:2rem;font-size:.85rem;'
        f'display:inline-flex;align-items:center;justify-content:center;">{b}</span></td>'
        f'<td>{d} tirage{"s" if d > 1 else ""}</td></tr>'
        for b, d in stats["retardataires_balls"]
    )
    retard_star_rows = "\n".join(
        f'<tr><td><span class="loto-ball em-star" style="width:2rem;height:2rem;font-size:.85rem;'
        f'display:inline-flex;align-items:center;justify-content:center;">&#9733;{s}</span></td>'
        f'<td>{d} tirage{"s" if d > 1 else ""}</td></tr>'
        for s, d in stats["retardataires_stars"]
    )
    recent_top_rows = "\n".join(
        f'<tr><td><span class="loto-ball" style="width:2rem;height:2rem;font-size:.85rem;'
        f'display:inline-flex;align-items:center;justify-content:center;">{b}</span></td>'
        f'<td>{c}</td></tr>'
        for b, c in stats["recent_top_balls"]
    )
    recent_bottom_rows = "\n".join(
        f'<tr><td><span class="loto-ball" style="width:2rem;height:2rem;font-size:.85rem;'
        f'display:inline-flex;align-items:center;justify-content:center;">{b}</span></td>'
        f'<td>{c}</td></tr>'
        for b, c in stats["recent_bottom_balls"]
    )

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>Statistiques EuroMillions depuis {year_from} — Numéros les plus sortis | Solution du Jour</title>
  <meta name="description" content="Numéros et étoiles les plus sortis à l'EuroMillions depuis {year_from} ({n} tirages). Retardataires, tendances récentes. Mis à jour.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{EM_SITE_URL}/stats/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Statistiques EuroMillions depuis {year_from} — Numéros les plus sortis">
  <meta property="og:description" content="Fréquence des numéros sur {n} tirages EuroMillions depuis {year_from}. Mis à jour automatiquement.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{EM_SITE_URL}/stats/">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta property="og:image" content="{SITE_URL}/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Statistiques EuroMillions depuis {year_from} — Numéros les plus sortis">
  <meta name="twitter:description" content="Fréquence des numéros sur {n} tirages EuroMillions depuis {year_from}. Mis à jour automatiquement.">
  <meta property="article:modified_time" content="{stats['date_to']}T22:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Dataset",
    "name": "Statistiques EuroMillions depuis {year_from}",
    "description": "Fréquence des numéros sur {n} tirages EuroMillions depuis {year_from}",
    "temporalCoverage": "{stats['date_from']}/{stats['date_to']}",
    "url": "{EM_SITE_URL}/stats/",
    "license": "https://creativecommons.org/publicdomain/zero/1.0/",
    "creator": {{"@type": "Organization", "name": "Solutions du Jour", "url": "{SITE_URL}/"}},
    "publisher": {{"@type": "Organization", "name": "Solutions du Jour", "url": "{SITE_URL}/"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {{
        "@type": "Question",
        "name": "Quel numéro sort le plus souvent à l'EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sur les {n} tirages EuroMillions analysés depuis {year_from}, le numéro {top_ball} est le plus fréquemment sorti."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quelle étoile sort le plus souvent à l'EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sur les {n} tirages analysés depuis {year_from}, l'étoile {top_star} est sortie le plus souvent."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quel est le numéro retardataire de l'EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le numéro {retard_ball} est absent depuis {retard_draws} tirages consécutifs — c'est le plus grand retard actuel."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Combien de tirages EuroMillions ont eu lieu depuis {year_from} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "{n} tirages EuroMillions ont eu lieu depuis {year_from}, à raison de 2 tirages par semaine (mardi et vendredi)."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quels sont les numéros les moins sortis à l'EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sur {n} tirages depuis {year_from}, les numéros les moins fréquents sont : {bottom_balls_list}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Ces statistiques garantissent-elles de gagner à l'EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Non. L'EuroMillions est un jeu de hasard : chaque numéro a la même probabilité de sortir à chaque tirage. Les statistiques historiques sont descriptives, pas prédictives."
        }}
      }}
    ]
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "EuroMillions", "item": "{EM_SITE_URL}/"}},
      {{"@type": "ListItem", "position": 3, "name": "Statistiques"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Statistiques EuroMillions depuis {year_from}</h1>
  <p class="subtitle">Numéros les plus sortis sur {n} tirages — Mis à jour le {date_to}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../index.html">EuroMillions</a> &rsaquo;
  <span>Statistiques</span>
</nav>

  <div class="card">
    <h2>À propos de ces statistiques</h2>
    <p>
      Ces statistiques sont calculées automatiquement à partir des <strong>{n} tirages EuroMillions depuis {year_from}</strong>
      ({date_from} au {date_to}). Elles sont mises à jour après chaque tirage.
    </p>
    <p style="margin-top:.6rem;font-size:.9rem;color:#6b7280;">
      ⚠️ L'EuroMillions est un jeu de hasard : chaque numéro a la même probabilité de sortir à chaque tirage.
      Ces statistiques sont descriptives, pas prédictives.
    </p>
  </div>

  <div class="card">
    <h2>Numéros les plus sortis depuis {year_from}</h2>
    <p style="font-size:.85rem;color:#6b7280;margin-bottom:.75rem;">
      Période : {date_from} → {date_to} ({n} tirages)
    </p>
    <div class="table-scroll">
    <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
      <thead>
        <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
          <th style="padding:.4rem .5rem;">Numéro</th>
          <th style="padding:.4rem .5rem;">Sorties</th>
          <th style="padding:.4rem .5rem;">Fréquence</th>
        </tr>
      </thead>
      <tbody>
{top_rows}
      </tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <h2>Numéros les moins sortis depuis {year_from}</h2>
    <p style="font-size:.85rem;color:#6b7280;margin-bottom:.75rem;">
      Les 5 numéros les plus rares sur l'ensemble des {n} tirages.
    </p>
    <div class="table-scroll">
    <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
      <thead>
        <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
          <th style="padding:.4rem .5rem;">Numéro</th>
          <th style="padding:.4rem .5rem;">Sorties</th>
          <th style="padding:.4rem .5rem;">Fréquence</th>
        </tr>
      </thead>
      <tbody>
{bottom_rows}
      </tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <h2>Tendances récentes — 50 derniers tirages</h2>
    <p style="font-size:.85rem;color:#6b7280;margin-bottom:.75rem;">
      Numéros chauds et froids sur les 50 derniers tirages uniquement.
    </p>
    <div class="recent-grid">
      <div>
        <h3 style="font-size:.95rem;margin-bottom:.5rem;">🔥 Numéros chauds</h3>
        <div class="table-scroll">
        <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
          <thead>
            <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
              <th style="padding:.3rem .4rem;">Numéro</th>
              <th style="padding:.3rem .4rem;">Sorties</th>
            </tr>
          </thead>
          <tbody>
{recent_top_rows}
          </tbody>
        </table>
        </div>
      </div>
      <div>
        <h3 style="font-size:.95rem;margin-bottom:.5rem;">❄️ Numéros froids</h3>
        <div class="table-scroll">
        <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
          <thead>
            <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
              <th style="padding:.3rem .4rem;">Numéro</th>
              <th style="padding:.3rem .4rem;">Sorties</th>
            </tr>
          </thead>
          <tbody>
{recent_bottom_rows}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Étoiles les plus fréquentes</h2>
    <div class="table-scroll">
    <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
      <thead>
        <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
          <th style="padding:.4rem .5rem;">Étoile</th>
          <th style="padding:.4rem .5rem;">Sorties</th>
          <th style="padding:.4rem .5rem;">Fréquence</th>
        </tr>
      </thead>
      <tbody>
{star_rows}
      </tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <h2>Numéros retardataires</h2>
    <p style="font-size:.85rem;color:#6b7280;margin-bottom:.75rem;">
      Numéros et étoiles absents depuis le plus grand nombre de tirages consécutifs.
    </p>
    <div class="recent-grid">
      <div>
        <h3 style="font-size:.9rem;margin-bottom:.5rem;">Numéros</h3>
        <div class="table-scroll">
        <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
          <thead>
            <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
              <th style="padding:.3rem .4rem;">Numéro</th>
              <th style="padding:.3rem .4rem;">Absent depuis</th>
            </tr>
          </thead>
          <tbody>
{retard_ball_rows}
          </tbody>
        </table>
        </div>
      </div>
      <div>
        <h3 style="font-size:.9rem;margin-bottom:.5rem;">Étoiles</h3>
        <div class="table-scroll">
        <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
          <thead>
            <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
              <th style="padding:.3rem .4rem;">Étoile</th>
              <th style="padding:.3rem .4rem;">Absent depuis</th>
            </tr>
          </thead>
          <tbody>
{retard_star_rows}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Questions fréquentes</h2>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Quel numéro sort le plus souvent à l'EuroMillions ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      Sur les {n} tirages analysés depuis {year_from}, le numéro <strong>{top_ball}</strong> est le plus fréquent.
      L'étoile la plus fréquente est la <strong>{top_star}</strong>.
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Quels sont les numéros les moins sortis ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      Sur {n} tirages depuis {year_from}, les numéros les plus rares sont : <strong>{bottom_balls_list}</strong>.
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Combien de tirages depuis {year_from} ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      <strong>{n} tirages</strong> ont eu lieu depuis {year_from},
      à raison de 2 tirages par semaine (mardi et vendredi).
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Quel est le numéro retardataire ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      Le numéro <strong>{retard_ball}</strong> est absent depuis <strong>{retard_draws} tirages</strong> consécutifs.
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Ces stats garantissent-elles de gagner ?</h3>
    <p style="font-size:.9rem;">
      Non — l'EuroMillions est un jeu d'équiprobabilité. Chaque numéro a exactement 1 chance sur 50 de sortir
      à chaque tirage, indépendamment de l'historique.
    </p>
  </div>

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../index.html">Dernier tirage EuroMillions &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="{SITE_URL}/">Accueil</a> ·
    <a href="../index.html">Dernier tirage</a> ·
    <a href="../archive/">Archives</a> ·
    <a href="https://www.fdj.fr/jeux-de-tirage/euromillions-my-million" rel="noopener" target="_blank">Jouer à l'EuroMillions</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Statistiques calculées automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(stats_dir / "index.html", html)
    print("[EuroMillions] Génération de docs/euromillions/stats/index.html…")


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(draw_date: date, data: dict) -> None:
    """Génère tous les fichiers HTML EuroMillions à partir des JSON déjà en place."""
    all_archives = load_all_archives()
    draw_str = draw_date.isoformat()
    past_archives = [e for e in all_archives if e["date"] != draw_str]

    print(f"[EuroMillions] Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        d = date.fromisoformat(entry["date"])
        prev_date = date.fromisoformat(past_archives[i + 1]["date"]) if i + 1 < len(past_archives) else None
        next_date = date.fromisoformat(past_archives[i - 1]["date"]) if i > 0 else None
        generate_archive_html(
            d, entry["balls"], entry["stars"], prev_date, next_date,
            jackpot_amount=entry.get("jackpot_amount"),
            jackpot_winners=entry.get("jackpot_winners", 0),
            jackpot_won=entry.get("jackpot_won", False),
            code=entry.get("code", ""),
        )

    print("[EuroMillions] Génération de docs/euromillions/archive/index.html…")
    generate_archive_index(past_archives)

    print("[EuroMillions] Calcul des statistiques…")
    em_stats = compute_em_stats(all_archives)
    generate_em_stats_html(em_stats)

    recent_archives = [e for e in past_archives[:7] if (EM_ARCHIVE / f"{e['date']}.html").exists()]
    print("[EuroMillions] Génération de docs/euromillions/index.html…")
    generate_index_html(
        draw_date, data["balls"], data["stars"], recent_archives,
        total_archives=len(all_archives),
        jackpot_amount=data.get("jackpot_amount"),
        jackpot_winners=data.get("jackpot_winners", 0),
        jackpot_won=data.get("jackpot_won", False),
        next_jackpot=data.get("next_jackpot"),
        code=data.get("code", ""),
    )


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run(today: date) -> dict | None:
    """
    Scrape le dernier tirage EuroMillions et génère tous les fichiers.
    Retourne le dict data ou None si indisponible.
    Note : le tirage peut dater d'avant aujourd'hui (mar/ven soir — mise à jour le lendemain matin).
    """
    EM_DIR.mkdir(parents=True, exist_ok=True)
    EM_ARCHIVE.mkdir(parents=True, exist_ok=True)

    print("[EuroMillions] Récupération du dernier tirage…")
    draw = get_euromillions_latest()

    if not draw:
        # Fallback : si solution.json existe déjà, régénérer HTML depuis les archives
        solution_path = EM_DIR / "solution.json"
        if solution_path.exists():
            existing = json.loads(solution_path.read_text(encoding="utf-8"))
            print(f"[EuroMillions] ℹ Fallback sur tirage existant ({existing.get('date')}) — régénération HTML.")
            _generate_all_html(date.fromisoformat(existing["date"]), existing)
            return existing
        print("[EuroMillions] ⚠ Tirage non disponible — génération page indisponible.")
        generate_unavailable_html(today)
        return None

    draw_date_str = draw["date"]
    print(f"[EuroMillions] ✅ Dernier tirage : {draw_date_str} — {draw['balls']} ★ {draw['stars']}")

    # Si ce tirage est déjà sauvegardé → mettre à jour jackpot si besoin + régénérer HTML
    solution_path = EM_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == draw_date_str:
            updated = False
            # Re-fetch si jackpot absent OU si non remporté et tirage récent (API lag la nuit du tirage)
            from datetime import date as _date
            draw_age_days = (_date.today() - _date.fromisoformat(draw_date_str)).days
            need_jackpot_refresh = (
                "jackpot_amount" not in existing
                or (not existing.get("jackpot_won") and draw_age_days <= 3)
            )
            if need_jackpot_refresh:
                print("[EuroMillions] Récupération données jackpot…")
                jackpot_map = fetch_jackpot_data()
                if draw_date_str in jackpot_map:
                    jk = jackpot_map[draw_date_str]
                    existing.update({k: jk[k] for k in ("jackpot_amount", "jackpot_winners", "jackpot_won")})
                    won_str = f"Jackpot {'remporté' if jk['jackpot_won'] else 'non remporté'}"
                    print(f"   {won_str} — {jk['jackpot_amount']:,.0f} €".replace(",", "\u202f"))
                    updated = True
            next_jk = get_em_next_jackpot()
            if next_jk:
                print(f"[EuroMillions] Prochain jackpot : {next_jk:,.0f} €".replace(",", "\u202f"))
            if next_jk and existing.get("next_jackpot") != next_jk:
                existing["next_jackpot"] = next_jk
                updated = True
            # Ajouter le code My Million s'il est absent et qu'on vient de le scraper
            if "code" not in existing and draw.get("code"):
                existing["code"] = draw["code"]
                updated = True
                print(f"[EuroMillions] Code My Million ajouté : {draw['code']}")
            if updated:
                atomic_write(solution_path, json.dumps(existing, ensure_ascii=False, indent=2))
            print(f"[EuroMillions] ℹ Tirage déjà présent ({draw_date_str}) — régénération HTML uniquement.")
            _generate_all_html(date.fromisoformat(draw_date_str), existing)
            return existing

    # Nouveau tirage → enrichir avec jackpot puis sauvegarder
    print("[EuroMillions] Récupération données jackpot…")
    jackpot_map = fetch_jackpot_data()
    if draw_date_str in jackpot_map:
        draw.update(jackpot_map[draw_date_str])
        jk = jackpot_map[draw_date_str]
        won_str = f"Jackpot {'remporté' if jk['jackpot_won'] else 'non remporté'}"
        print(f"   {won_str} — {jk['jackpot_amount']:,.0f} €".replace(",", "\u202f"))

    next_jk = get_em_next_jackpot()
    if next_jk:
        draw["next_jackpot"] = next_jk
        print(f"[EuroMillions] Prochain jackpot : {next_jk:,.0f} €".replace(",", "\u202f"))

    data = generate_solution_json(draw)
    generate_archive_json(data)
    _generate_all_html(date.fromisoformat(draw_date_str), data)

    print(f"[EuroMillions] 🎉 Tirage sauvegardé et HTML généré ({draw_date_str})")
    return data


# ── Simulateur historique ──────────────────────────────────────────────────────

def generate_simulator_data() -> None:
    """Génère docs/euromillions/simulateur/data.json — tous tirages [date, balls, stars]."""
    archives = _load_archives(EM_ARCHIVE, required_keys=["date", "balls", "stars"])
    if not archives:
        print("[EuroMillions] ⚠ Simulateur : aucune archive trouvée")
        return
    archives_sorted = sorted(archives, key=lambda e: e["date"])
    data = [[e["date"], e["balls"], e["stars"]] for e in archives_sorted]
    out_dir = EM_DIR / "simulateur"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "data.json",
                 json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"[EuroMillions] Simulateur data.json : {len(data)} tirages")


def generate_simulator_html() -> None:
    """Génère docs/euromillions/simulateur/index.html — simulateur historique interactif."""
    out_dir = EM_DIR / "simulateur"
    out_dir.mkdir(parents=True, exist_ok=True)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>⭐ Simulateur EuroMillions — Calculez vos gains sur 1 900 tirages</title>
  <meta name="description" content="Simulez vos gains EuroMillions : entrez vos 5 numéros + 2 étoiles et obtenez vos résultats sur 1\u202f900+ tirages depuis 2004. Gratuit et instantané.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{EM_SITE_URL}/simulateur/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Simulateur EuroMillions — Calculez vos gains sur 1 900 tirages">
  <meta property="og:description" content="Simulez vos gains EuroMillions : entrez vos 5 numéros + 2 étoiles et obtenez vos résultats sur 1\u202f900+ tirages depuis 2004.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{EM_SITE_URL}/simulateur/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Simulateur EuroMillions — Calculez vos gains sur 1 900 tirages">
  <meta name="twitter:description" content="Simulez vos gains EuroMillions : entrez vos 5 numéros + 2 étoiles et obtenez vos résultats sur 1\u202f900+ tirages depuis 2004.">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "WebApplication",
    "name": "Simulateur de gains EuroMillions",
    "description": "Simulez vos gains EuroMillions sur l'ensemble des tirages depuis 2004. Entrez vos 5 numéros et 2 étoiles.",
    "url": "{EM_SITE_URL}/simulateur/",
    "applicationCategory": "UtilityApplication",
    "operatingSystem": "Web",
    "offers": {{"@type": "Offer", "price": "0", "priceCurrency": "EUR"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {{
        "@type": "Question",
        "name": "Comment fonctionne le simulateur EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sélectionnez vos 5 numéros (1–50) et 2 étoiles (1–12), puis cliquez sur Simuler. L'outil vérifie vos numéros sur tous les tirages EuroMillions depuis 2004 et calcule vos gains cumulés."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Les gains affichés sont-ils exacts ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Les gains sont approximatifs. Le jackpot (1er rang) varie énormément selon les tirages. Les autres rangs reflètent les montants indicatifs officiels."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Combien de tirages EuroMillions sont analysés ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le simulateur couvre tous les tirages disponibles depuis février 2004, mis à jour après chaque tirage (mardi et vendredi)."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quelle est la probabilité de gagner le jackpot EuroMillions ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La probabilité de décrocher le jackpot EuroMillions (5+2) est d'environ 1 sur 139\u202f838\u202f160. Ce simulateur est un outil ludique illustrant l'espérance mathématique."
        }}
      }}
    ]
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "https://solution-du-jour.fr/"}},
      {{"@type": "ListItem", "position": 2, "name": "EuroMillions", "item": "https://solution-du-jour.fr/euromillions/"}},
      {{"@type": "ListItem", "position": 3, "name": "Simulateur", "item": "{EM_SITE_URL}/simulateur/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Simulateur EuroMillions — Calculez vos gains</h1>
  <p class="subtitle">Simulez vos résultats sur 1 900+ tirages depuis 2004</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../">EuroMillions</a> &rsaquo;
  <span>Simulateur</span>
</nav>
  <article>

    <div class="card">
      <h2>Comment ça marche ?</h2>
      <p>
        Choisissez <strong>5 numéros</strong> (1–50) et <strong>2 étoiles</strong> (1–12),
        puis lancez la simulation. L'outil vérifie votre grille sur l'ensemble des tirages EuroMillions
        depuis 2004 et calcule vos gains cumulés.
      </p>
      <p style="margin-top:.5rem;font-size:.9rem;color:#6b7280;">
        Mise par tirage : <strong>2,50&nbsp;€</strong>. Les gains affichés sont approximatifs
        (hors jackpot variable).
      </p>
    </div>

    <div class="card">
      <h2>Vos numéros</h2>

      <p class="sim-label">5 numéros (<span id="cnt-balls">0</span>/5)</p>
      <div class="sim-grid" id="picker-balls"></div>

      <p class="sim-label">2 étoiles &#9733; (<span id="cnt-stars">0</span>/2)</p>
      <div class="sim-grid" id="picker-stars"></div>

      <button class="sim-btn" id="sim-btn" disabled>Simuler depuis 2004</button>
    </div>

    <div class="card" id="sim-results" style="display:none;">
      <h2>Résultats de la simulation</h2>
      <div class="sim-summary" id="sim-summary"></div>
      <p id="sim-best" style="font-size:.9rem;color:#374151;margin-bottom:.5rem;"></p>
      <table class="sim-rank-table">
        <thead><tr><th>Rang</th><th>Combinaison</th><th>Fois</th><th>Gain approx.</th></tr></thead>
        <tbody id="sim-rank-body"></tbody>
      </table>
    </div>

    <div class="card">
      <h2>Questions fréquentes</h2>
      <details style="margin-bottom:.75rem;">
        <summary style="cursor:pointer;font-weight:600;">Comment fonctionne le simulateur EuroMillions ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          Sélectionnez vos 5 numéros (1–50) et 2 étoiles (1–12), puis cliquez sur Simuler.
          L'outil vérifie votre grille sur tous les tirages depuis 2004 et calcule vos gains.
        </p>
      </details>
      <details style="margin-bottom:.75rem;">
        <summary style="cursor:pointer;font-weight:600;">Les gains sont-ils exacts ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          Les gains sont approximatifs. Le jackpot (1er rang) varie très fortement selon les tirages.
          Les autres rangs reflètent les montants indicatifs officiels.
        </p>
      </details>
      <details style="margin-bottom:.75rem;">
        <summary style="cursor:pointer;font-weight:600;">Combien de tirages sont analysés ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          Le simulateur couvre tous les tirages depuis février 2004, mis à jour après chaque tirage
          (mardi et vendredi).
        </p>
      </details>
      <details>
        <summary style="cursor:pointer;font-weight:600;">Quelle est la probabilité de gagner le jackpot ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          La probabilité de décrocher le jackpot EuroMillions (5+2) est d'environ 1/139\u202f838\u202f160.
          Ce simulateur est un outil ludique illustrant l'espérance mathématique.
        </p>
      </details>
    </div>

    <div class="card" style="text-align:center;">
      <p style="font-size:.9rem;">
        <a href="../">← Résultats du dernier tirage</a> &nbsp;|&nbsp;
        <a href="../stats/">Statistiques EuroMillions</a> &nbsp;|&nbsp;
        <a href="../archive/">Archives</a>
      </p>
    </div>

  </article>
</main>

<footer>
  <p>Site non officiel · <a href="{SITE_URL}/">Accueil</a> · <a href="../">EuroMillions</a> · <a href="../archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://www.fdj.fr/jeux-de-tirage/euromillions-my-million" rel="noopener" target="_blank">fdj.fr</a></p>
</footer>

<script>
(function() {{
  const EM_GAINS = {{1:null,2:600000,3:40000,4:3500,5:200,6:150,7:100,8:20,9:15,10:12,11:10,12:6,13:4}};
  const EM_MISE = 2.50;
  const RANK_LABELS = {{
    1:'1er rang (Jackpot)',2:'2e rang',3:'3e rang',4:'4e rang',5:'5e rang',
    6:'6e rang',7:'7e rang',8:'8e rang',9:'9e rang',10:'10e rang',
    11:'11e rang',12:'12e rang',13:'13e rang'
  }};
  const RANK_DESC = {{
    1:'5+2',2:'5+1',3:'5+0',4:'4+2',5:'4+1',6:'3+2',7:'4+0',
    8:'2+2',9:'3+1',10:'3+0',11:'1+2',12:'2+1',13:'2+0'
  }};

  function emRank(ub, us, db, ds) {{
    const mb = ub.filter(n => db.includes(n)).length, ms = us.filter(n => ds.includes(n)).length;
    if (mb===5&&ms===2) return 1;  if (mb===5&&ms===1) return 2;
    if (mb===5&&ms===0) return 3;  if (mb===4&&ms===2) return 4;
    if (mb===4&&ms===1) return 5;  if (mb===3&&ms===2) return 6;
    if (mb===4&&ms===0) return 7;  if (mb===2&&ms===2) return 8;
    if (mb===3&&ms===1) return 9;  if (mb===3&&ms===0) return 10;
    if (mb===1&&ms===2) return 11; if (mb===2&&ms===1) return 12;
    if (mb===2&&ms===0) return 13; return 0;
  }}

  let userBalls = [], userStars = [];

  function updateBtn() {{
    document.getElementById('sim-btn').disabled = !(userBalls.length === 5 && userStars.length === 2);
  }}

  // Build ball picker (1-50)
  const bg = document.getElementById('picker-balls');
  for (let n = 1; n <= 50; n++) {{
    const btn = document.createElement('button');
    btn.className = 'sim-ball em-type'; btn.type = 'button'; btn.textContent = n;
    btn.addEventListener('click', () => {{
      const i = userBalls.indexOf(n);
      if (i >= 0) {{ userBalls.splice(i, 1); btn.classList.remove('selected'); }}
      else if (userBalls.length < 5) {{ userBalls.push(n); btn.classList.add('selected'); }}
      bg.querySelectorAll('.sim-ball').forEach(b => {{
        b.classList.toggle('dimmed', userBalls.length >= 5 && !userBalls.includes(parseInt(b.textContent)));
      }});
      document.getElementById('cnt-balls').textContent = userBalls.length;
      updateBtn();
    }});
    bg.appendChild(btn);
  }}

  // Build star picker (1-12)
  const sg = document.getElementById('picker-stars');
  for (let n = 1; n <= 12; n++) {{
    const btn = document.createElement('button');
    btn.className = 'sim-ball star-type'; btn.type = 'button'; btn.textContent = n;
    btn.addEventListener('click', () => {{
      const i = userStars.indexOf(n);
      if (i >= 0) {{ userStars.splice(i, 1); btn.classList.remove('selected'); }}
      else if (userStars.length < 2) {{ userStars.push(n); btn.classList.add('selected'); }}
      sg.querySelectorAll('.sim-ball').forEach(b => {{
        b.classList.toggle('dimmed', userStars.length >= 2 && !userStars.includes(parseInt(b.textContent)));
      }});
      document.getElementById('cnt-stars').textContent = userStars.length;
      updateBtn();
    }});
    sg.appendChild(btn);
  }}

  document.getElementById('sim-btn').addEventListener('click', () => {{
    fetch('data.json')
      .then(r => r.json())
      .then(draws => {{
        let totalGain = 0, rankCounts = {{}}, bestRank = 0, bestDate = null;
        for (const [dateStr, balls, stars] of draws) {{
          const rank = emRank(userBalls, userStars, balls, stars);
          if (rank > 0) {{
            rankCounts[rank] = (rankCounts[rank] || 0) + 1;
            const gain = EM_GAINS[rank] || 0;
            totalGain += gain;
            if (bestRank === 0 || rank < bestRank) {{ bestRank = rank; bestDate = dateStr; }}
          }}
        }}
        const mise = draws.length * EM_MISE;
        const bilan = totalGain - mise;
        const bilanCls = bilan >= 0 ? 'bilan-pos' : 'bilan-neg';
        const fmt = n => n.toLocaleString('fr-FR', {{maximumFractionDigits:2}});

        document.getElementById('sim-summary').innerHTML =
          `<div class="sim-stat"><div class="sim-stat-value">${{draws.length.toLocaleString('fr-FR')}}</div><div class="sim-stat-label">Tirages analysés</div></div>`+
          `<div class="sim-stat"><div class="sim-stat-value">${{fmt(mise)}}\u00a0€</div><div class="sim-stat-label">Mise totale</div></div>`+
          `<div class="sim-stat"><div class="sim-stat-value">${{fmt(totalGain)}}\u00a0€</div><div class="sim-stat-label">Gains totaux</div></div>`+
          `<div class="sim-stat ${{bilanCls}}"><div class="sim-stat-value">${{bilan>=0?'+':''}}${{fmt(bilan)}}\u00a0€</div><div class="sim-stat-label">Bilan</div></div>`;

        const bestEl = document.getElementById('sim-best');
        if (bestRank > 0) {{
          const [y,m,d] = bestDate.split('-');
          bestEl.innerHTML = `<strong>Votre meilleur gain :</strong> ${{RANK_LABELS[bestRank]}} le ${{d}}/${{m}}/${{y}}`;
        }} else {{
          bestEl.textContent = 'Aucun gain sur cet historique.';
        }}

        const tbody = document.getElementById('sim-rank-body');
        tbody.innerHTML = '';
        for (let r = 1; r <= 13; r++) {{
          const cnt = rankCounts[r] || 0;
          if (cnt === 0) continue;
          const gain = EM_GAINS[r];
          const gainStr = gain ? (gain.toLocaleString('fr-FR') + '\u00a0€') : 'Jackpot';
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${{RANK_LABELS[r]}}</td><td>${{RANK_DESC[r]}}</td><td>${{cnt}}</td><td>${{gainStr}}</td>`;
          tbody.appendChild(tr);
        }}
        if (tbody.children.length === 0) {{
          tbody.innerHTML = '<tr><td colspan="4" style="color:#6b7280;text-align:center;">Aucun rang gagné</td></tr>';
        }}

        document.getElementById('sim-results').style.display = 'block';
        document.getElementById('sim-results').scrollIntoView({{behavior:'smooth',block:'start'}});
      }})
      .catch(() => alert('Erreur lors du chargement des données.'));
  }});
}})();
</script>

</body>
</html>"""

    atomic_write(out_dir / "index.html", html)
    print("[EuroMillions] Simulateur index.html généré")
