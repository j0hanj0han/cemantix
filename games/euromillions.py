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
"""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from core import SITE_URL, DOCS_DIR, _session, date_fr, atomic_write, load_all_archives as _load_archives

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
            balls = sorted(int(b.text.strip()) for b in node.find_all(class_="ball")[:5])
            stars = sorted(int(s.text.strip()) for s in node.find_all(class_="lucky-star")[:2])
            if len(balls) == 5 and len(stars) == 2:
                return {
                    "date": draw_date.isoformat(),
                    "balls": balls,
                    "stars": stars,
                }
            break
        node = node.parent

    print("   ⚠ EuroMillions : impossible d'extraire les résultats du dernier tirage")
    return None


# ── Helpers HTML ──────────────────────────────────────────────────────────────

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

  <title>Résultats EuroMillions {date_display} — Numéros gagnants · Archive</title>
  <meta name="description" content="Résultats du tirage EuroMillions du {date_display}. Numéros : {balls_str}. Étoiles : {stars_str}.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{EM_SITE_URL}/archive/{date_str}.html">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta property="og:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{EM_SITE_URL}/archive/{date_str}.html">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta name="twitter:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="article:published_time" content="{date_str}T21:30:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Résultats EuroMillions {date_display}",
    "datePublished": "{date_str}T21:30:00+01:00",
    "dateModified": "{date_str}T21:30:00+01:00",
    "description": "Numéros gagnants EuroMillions du {date_display} : {balls_str} — étoiles : {stars_str}.",
    "url": "{EM_SITE_URL}/archive/{date_str}.html",
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

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>Résultats EuroMillions {date_display} — Numéros gagnants du dernier tirage</title>
  <meta name="description" content="Résultats du tirage EuroMillions du {date_display}. Numéros gagnants : {balls_str}. Étoiles : {stars_str}. Mis à jour automatiquement après chaque tirage.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{EM_SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta property="og:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{EM_SITE_URL}/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="EuroMillions {date_display} — Numéros gagnants">
  <meta name="twitter:description" content="Résultats EuroMillions du {date_display} : {balls_str} + étoiles {stars_str}.">
  <meta property="article:published_time" content="{date_str}T21:30:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
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
      <h2>Numéros gagnants du {date_display}</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        5 boules (1–50) + 2 étoiles &#9733; (1–12, fond doré)
      </p>
      {_em_balls_html(balls, stars)}
      <p class="puzzle-meta">
        Boules : <strong>{balls_str}</strong> · Étoiles : <strong>{stars_str}</strong>
      </p>
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
{recent_archives_card}
  </article>
</main>

<footer>
  <p>Site non officiel — Résultats récupérés automatiquement · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a></p>
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
        generate_archive_html(d, entry["balls"], entry["stars"], prev_date, next_date)

    print("[EuroMillions] Génération de docs/euromillions/archive/index.html…")
    generate_archive_index(past_archives)

    recent_archives = past_archives[:7]
    print("[EuroMillions] Génération de docs/euromillions/index.html…")
    generate_index_html(draw_date, data["balls"], data["stars"], recent_archives)


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
        print("[EuroMillions] ⚠ Tirage non disponible — génération page indisponible.")
        generate_unavailable_html(today)
        return None

    draw_date_str = draw["date"]
    print(f"[EuroMillions] ✅ Dernier tirage : {draw_date_str} — {draw['balls']} ★ {draw['stars']}")

    # Si ce tirage est déjà sauvegardé → régénérer HTML seulement
    solution_path = EM_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == draw_date_str:
            print(f"[EuroMillions] ℹ Tirage déjà présent ({draw_date_str}) — régénération HTML uniquement.")
            _generate_all_html(date.fromisoformat(draw_date_str), existing)
            return existing

    # Nouveau tirage → sauvegarder
    data = generate_solution_json(draw)
    generate_archive_json(data)
    _generate_all_html(date.fromisoformat(draw_date_str), data)

    print(f"[EuroMillions] 🎉 Tirage sauvegardé et HTML généré ({draw_date_str})")
    return data
