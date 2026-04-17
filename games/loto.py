"""
games/loto.py — Résultats du tirage Loto (FDJ).

Données : API OpenDataSoft (publique, sans auth)
  https://data.opendatasoft.com — dataset resultats-loto-2019-a-aujourd-hui@agrall

Génère :
  docs/loto/solution.json          ← dernier tirage {date, draw_num, balls, lucky_ball}
  docs/loto/index.html             ← résultats du dernier tirage
  docs/loto/archive/YYYY-MM-DD.json
  docs/loto/archive/YYYY-MM-DD.html
  docs/loto/archive/index.html
  docs/loto/stats/index.html       ← statistiques des N derniers tirages
"""

import json
import re
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

LOTO_DIR = DOCS_DIR / "loto"
LOTO_ARCHIVE = LOTO_DIR / "archive"
LOTO_SITE_URL = f"{SITE_URL}/loto"

_LOTO_API = (
    "https://data.opendatasoft.com/api/records/1.0/search/"
    "?dataset=resultats-loto-2019-a-aujourd-hui%40agrall"
    "&rows=1&sort=date_de_tirage"
)

_REDUCMIZ_URL = "https://www.reducmiz.com/resultat_fdj.php?jeu=loto&nb={nb}"


# ── API ───────────────────────────────────────────────────────────────────────

def backfill_archives() -> int:
    """Télécharge tout l'historique Loto FDJ depuis 2019 et sauvegarde
    les tirages manquants dans LOTO_ARCHIVE. Retourne le nombre ajoutés."""
    LOTO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    added = 0
    start = 0
    batch = 100
    while True:
        url = (
            "https://data.opendatasoft.com/api/records/1.0/search/"
            "?dataset=resultats-loto-2019-a-aujourd-hui%40agrall"
            f"&rows={batch}&start={start}&sort=date_de_tirage"
        )
        resp = _session.get(url, timeout=15)
        resp.raise_for_status()
        records = resp.json().get("records", [])
        if not records:
            break
        for rec in records:
            f = rec["fields"]
            draw_date = f.get("date_de_tirage", "")[:10]
            if not draw_date:
                continue
            out = LOTO_ARCHIVE / f"{draw_date}.json"
            if out.exists():
                start += 1  # count but skip
                continue
            balls = sorted([
                f["boule_1"], f["boule_2"], f["boule_3"],
                f["boule_4"], f["boule_5"]
            ])
            lucky = f["numero_chance"]
            draw_num = f.get("annee_numero_de_tirage", "")
            data = {"date": draw_date, "draw_num": draw_num,
                    "balls": balls, "lucky_ball": lucky}
            atomic_write(out, json.dumps(data, ensure_ascii=False, indent=2))
            added += 1
        if len(records) < batch:
            break
        start += batch
    return added


def get_loto_latest() -> dict | None:
    """
    Récupère le dernier tirage Loto depuis l'API OpenDataSoft.
    Retourne {date, draw_num, balls, lucky_ball} ou None si indisponible.
    Les boules sont triées par ordre croissant (comme l'affichage officiel FDJ).
    """
    try:
        resp = _session.get(_LOTO_API, timeout=15)
        resp.raise_for_status()
        records = resp.json().get("records", [])
        if not records:
            return None
        fields = records[0]["fields"]
        balls = sorted(fields[f"boule_{i}"] for i in range(1, 6))
        return {
            "date": fields["date_de_tirage"],
            "draw_num": fields.get("annee_numero_de_tirage", ""),
            "balls": balls,
            "lucky_ball": int(fields["numero_chance"]),
        }
    except Exception as e:
        print(f"   ⚠ Loto API : {e}")
        return None


# ── Jackpot (reducmiz.com) ────────────────────────────────────────────────────

def _parse_reducmiz_jackpot(text: str) -> dict[str, dict]:
    """Parse le texte brut de reducmiz.com. Retourne {date_iso: jackpot_info + codes}."""
    result = {}
    blocks = re.split(r'date du tirage\s*\n', text)
    for block in blocks[1:]:
        date_m = re.search(r'(\d{2})/(\d{2})/(\d{4})', block)
        if not date_m:
            continue
        day, month, year = date_m.group(1), date_m.group(2), date_m.group(3)
        date_iso = f"{year}-{month}-{day}"
        jackpot_m = re.search(
            r'5 bons nums\s*\n\+\s*num[eé]ro chance\s*\n(\d+)\s*gagnant\s*\n([^\n]+)',
            block,
        )
        if not jackpot_m:
            continue
        winners = int(jackpot_m.group(1))
        amount_str = jackpot_m.group(2).strip()
        if winners == 0 or amount_str.startswith('-'):
            entry = {"jackpot_won": False, "jackpot_winners": 0, "jackpot_amount": None}
        else:
            amt = amount_str.replace('\xa0', '').replace(' €', '').replace(' ', '').replace(',', '.')
            try:
                entry = {"jackpot_won": True, "jackpot_winners": winners, "jackpot_amount": float(amt)}
            except ValueError:
                entry = {"jackpot_won": False, "jackpot_winners": 0, "jackpot_amount": None}
        # Codes gagnants (20 000 €) : lettre + 8 chiffres, séparés par " ; "
        codes_m = re.search(r"Codes?\s+à\s+20[,\u202f\s]000\s*€\s*\n([^\n]+)", block)
        if codes_m:
            raw = codes_m.group(1)
            entry["codes"] = [c.strip() for c in raw.split(";") if c.strip()]
        result[date_iso] = entry
    return result


def get_loto_current_jackpot(draw_date_str: str | None = None) -> dict | None:
    """Récupère le jackpot exact du dernier tirage + prochain jackpot depuis tirage-gagnant.com.
    Retourne {jackpot_amount, jackpot_won, jackpot_winners, next_jackpot} ou None si indisponible.
    draw_date_str : date ISO attendue (ex: '2026-03-04') pour vérifier qu'on lit le bon tirage.
    """
    html = fetch_static_html("https://www.tirage-gagnant.com/loto/resultats-loto/")
    if html is None:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Vérifier la date du dernier tirage affiché
    date_el = soup.find(class_="date_min")
    if date_el:
        raw = date_el.get_text(strip=True)  # "04/03/2026"
        try:
            d, m, y = raw.split("/")
            page_date = f"{y}-{m}-{d}"
        except Exception:
            page_date = None
        if draw_date_str and page_date and page_date != draw_date_str:
            print(f"   ⚠ tirage-gagnant.com : date page={page_date}, attendue={draw_date_str}")
            return None

    # Jackpot du tirage courant : "<p>7 000.000€ (non remporté)</p>"
    jackpot_m = re.search(r"([\d][\d\s.]*)\s*€\s*\(((?:non\s+)?remport[eé])\)", html)
    if not jackpot_m:
        return None

    amount_raw = jackpot_m.group(1).strip()
    won = "non" not in jackpot_m.group(2).lower()
    try:
        jackpot_amount = float(amount_raw.replace(" ", "").replace(".", ""))
    except ValueError:
        return None

    # Prochain jackpot : <p class="montant">8 000 000 €</p>
    next_jackpot = None
    montant_el = soup.find(class_="montant")
    if montant_el:
        try:
            next_raw = montant_el.get_text(strip=True).replace("€", "").replace(" ", "").replace(".", "").replace("\xa0", "")
            next_jackpot = float(next_raw)
        except ValueError:
            pass

    return {
        "jackpot_amount": jackpot_amount,
        "jackpot_won": won,
        "jackpot_winners": 0,  # overridden by reducmiz if won
        "next_jackpot": next_jackpot,
    }


def get_loto_jackpot_latest(nb: int = 50) -> dict[str, dict]:
    """Récupère les infos jackpot des N derniers tirages depuis reducmiz.com."""
    try:
        resp = _session.get(_REDUCMIZ_URL.format(nb=nb), timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return _parse_reducmiz_jackpot(soup.get_text(separator="\n", strip=True))
    except Exception as e:
        print(f"   ⚠ reducmiz.com : {e}")
        return {}


def backfill_loto_jackpot() -> int:
    """Enrichit tous les archives JSON Loto avec les données jackpot depuis reducmiz.com.
    Retourne le nombre de fichiers mis à jour."""
    print("[Loto] Backfill jackpot depuis reducmiz.com (nb=2415)…")
    jackpot_data = get_loto_jackpot_latest(nb=2415)
    if not jackpot_data:
        print("   ⚠ Aucune donnée jackpot récupérée.")
        return 0
    updated = 0
    for f in sorted(LOTO_ARCHIVE.glob("????-??-??.json")):
        d = f.stem
        if d not in jackpot_data:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("jackpot_won") is not None:
                continue  # déjà enrichi
            data.update(jackpot_data[d])
            atomic_write(f, json.dumps(data, ensure_ascii=False, indent=2))
            updated += 1
        except Exception:
            pass
    print(f"   ✅ {updated} archive(s) enrichie(s) avec données jackpot.")
    return updated


# ── Helpers HTML ──────────────────────────────────────────────────────────────

def _codes_html(codes: list[str] | None) -> str:
    """Retourne le bloc HTML des codes gagnants Loto (20 000 €), ou '' si absent."""
    if not codes:
        return ""
    items = "".join(f'<span class="draw-code-item">{c}</span>' for c in codes)
    return f"""      <div class="draw-codes">
        <p class="draw-codes-title">Codes Gagnants — 20&nbsp;000&nbsp;€</p>
        <div class="draw-codes-grid">{items}</div>
        <p class="draw-codes-note">Si votre ticket porte l'un de ces codes, vous gagnez 20&nbsp;000&nbsp;€.</p>
      </div>"""


def _balls_html(balls: list[int], lucky: int, small: bool = False) -> str:
    """Retourne le bloc HTML des boules + boule chance."""
    cls = "loto-ball loto-ball-sm" if small else "loto-ball"
    cls_c = f"{cls} loto-ball-chance"
    wrap = "loto-balls loto-balls-sm" if small else "loto-balls"
    inner = "".join(f'<span class="{cls}">{b}</span>' for b in balls)
    inner += f'<span class="{cls_c}">{lucky}</span>'
    return f'<div class="{wrap}">{inner}</div>'


# ── Fichiers JSON ─────────────────────────────────────────────────────────────

def generate_solution_json(draw: dict) -> dict:
    data = {**draw, "generated_at": datetime.now(timezone.utc).isoformat()}
    LOTO_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(LOTO_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(data: dict) -> None:
    LOTO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(LOTO_ARCHIVE / f"{data['date']}.json",
                 json.dumps(data, ensure_ascii=False, indent=2))


def load_all_archives() -> list[dict]:
    return _load_archives(LOTO_ARCHIVE, required_keys=["date", "balls", "lucky_ball"])


# ── Génération HTML ───────────────────────────────────────────────────────────

def generate_archive_html(
    draw_date: date,
    draw_num: str,
    balls: list[int],
    lucky: int,
    prev_date,
    next_date,
    jackpot_won: bool | None = None,
    jackpot_winners: int = 0,
    jackpot_amount: float | None = None,
    codes: list[str] | None = None,
) -> None:
    """Génère docs/loto/archive/YYYY-MM-DD.html."""
    LOTO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = draw_date.isoformat()
    date_display = date_fr(draw_date)
    balls_str = " · ".join(str(b) for b in balls)

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

  <title>Résultats Loto {date_display} — Numéros gagnants · Archive</title>
  <meta name="description" content="Résultats du tirage Loto du {date_display} (tirage n°{draw_num}). Numéros gagnants : {balls_str} + chance {lucky}.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{LOTO_SITE_URL}/archive/{date_str}.html">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Loto {date_display} — Numéros gagnants">
  <meta property="og:description" content="Résultats du Loto du {date_display} : {balls_str} + chance {lucky}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{LOTO_SITE_URL}/archive/{date_str}.html">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Loto {date_display} — Numéros gagnants">
  <meta name="twitter:description" content="Résultats du Loto du {date_display} : {balls_str} + chance {lucky}.">
  <meta property="article:published_time" content="{date_str}T22:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Résultats Loto {date_display} — Tirage n°{draw_num}",
    "datePublished": "{date_str}T22:00:00+01:00",
    "dateModified": "{date_str}T22:00:00+01:00",
    "description": "Numéros gagnants du tirage Loto du {date_display} : {balls_str} + numéro chance {lucky}.",
    "url": "{LOTO_SITE_URL}/archive/{date_str}.html",
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
        "name": "Quels sont les numéros du Loto du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Les numéros gagnants du Loto du {date_display} (tirage n°{draw_num}) sont : {balls_str} avec le numéro chance {lucky}."
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
      {{"@type": "ListItem", "position": 2, "name": "Loto", "item": "https://solution-du-jour.fr/loto/"}},
      {{"@type": "ListItem", "position": 3, "name": "Archives", "item": "https://solution-du-jour.fr/loto/archive/"}},
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
  <h1>Loto — Archive</h1>
  <p class="subtitle">Résultats du {date_display} — tirage n°{draw_num}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../index.html">Loto</a> &rsaquo;
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
      <h2>Tirage Loto n°{draw_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez les <strong>résultats du tirage Loto du {date_display}</strong> (tirage n°{draw_num}).
        Les numéros sont affichés dans l'ordre croissant.
      </p>
    </div>

    <div class="card">
      <h2>Numéros gagnants</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        5 boules + 1 numéro chance (en doré)
      </p>
      {_balls_html(balls, lucky)}
      <p class="puzzle-meta">{balls_str} + chance <strong>{lucky}</strong></p>
{jackpot_html(jackpot_won, jackpot_winners, jackpot_amount)}
{_codes_html(codes)}
    </div>

    <div class="card">
      <h2>À propos de ce tirage</h2>
      <p>
        Ce tirage Loto n°{draw_num} a eu lieu le <strong>{date_display}</strong>.
        Les tirages Loto ont lieu le lundi, le mercredi et le samedi soir à partir de 20h20.
        Les 5 numéros sont tirés parmi les boules 1 à 49, et le numéro chance parmi 1 à 10.
        Pour vérifier vos gains ou rejouer, rendez-vous sur
        <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">fdj.fr</a>.
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
    <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">Jouer au Loto</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Résultats récupérés automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(LOTO_ARCHIVE / f"{date_str}.html", html)


def generate_archive_index(entries: list[dict]) -> None:
    """Génère docs/loto/archive/index.html."""
    LOTO_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        balls_str = " · ".join(str(b) for b in e["balls"])
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">n°{e["draw_num"]}</span>'
            f'<a class="arch-link" href="{e["date"]}.html">{balls_str} + {e["lucky_ball"]}</a>'
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

  <title>Archives Loto — Tous les tirages et numéros gagnants depuis 2008</title>
  <meta name="description" content="Retrouvez tous les résultats des tirages Loto depuis 2008 : numéros gagnants et numéros chance pour chaque tirage.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{LOTO_SITE_URL}/archive/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives Loto — Tous les numéros gagnants depuis 2008">
  <meta property="og:description" content="Tous les résultats des tirages Loto depuis 2008 avec numéros et numéros chance.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{LOTO_SITE_URL}/archive/">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Accueil", "item": "{SITE_URL}/"}},
      {{"@type": "ListItem", "position": 2, "name": "Loto", "item": "{LOTO_SITE_URL}/"}},
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
  <h1>Archives Loto</h1>
  <p class="subtitle">{count} tirage{"s" if count > 1 else ""} enregistré{"s" if count > 1 else ""}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../index.html">Loto</a> &rsaquo;
  <span>Archives</span>
</nav>
  <div class="card">
    <h2>Tous les tirages Loto ({count})</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Cliquez sur un tirage pour voir les détails. Format : boules (croissant) + numéro chance.
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
    <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">Jouer au Loto</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Résultats récupérés automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(LOTO_ARCHIVE / "index.html", html)


def generate_index_html(
    draw_date: date,
    draw_num: str,
    balls: list[int],
    lucky: int,
    recent_archives: list | None = None,
    total_archives: int = 0,
    jackpot_won: bool | None = None,
    jackpot_winners: int = 0,
    jackpot_amount: float | None = None,
    next_jackpot: float | None = None,
    codes: list[str] | None = None,
) -> None:
    """Génère docs/loto/index.html — dernier tirage."""
    date_str = draw_date.isoformat()
    date_display = date_fr(draw_date)
    balls_str = " · ".join(str(b) for b in balls)

    recent_archives_card = ""
    if recent_archives:
        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            b_str = " · ".join(str(b) for b in e["balls"])
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">n°{e["draw_num"]}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}.html">{b_str} + {e["lucky_ball"]}</a>'
                f'</li>'
            )
        items = "\n".join(arch_item(e) for e in recent_archives[:7])
        n_label = f"{total_archives} tirages analysés" if total_archives else "tous les tirages"
        recent_archives_card = f"""
    <div class="card">
      <h2>Tirages précédents</h2>
      <ul class="arch-list">
{items}
      </ul>
      <p style="margin-top:.75rem;font-size:.9rem;">
        <a href="archive/">Voir tous les tirages &#8594;</a>
      </p>
    </div>
    <div class="card" style="text-align:center;">
      <h2 style="font-size:1rem;margin-bottom:.4rem;">Statistiques Loto depuis 2008</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:.75rem;">
        Numéros les plus sortis, retardataires et tendances récentes sur {n_label}.
      </p>
      <a class="reveal-btn" href="stats/">Voir les statistiques &#8594;</a>
    </div>"""

    if next_jackpot:
        nj_str = f"{next_jackpot:,.0f}".replace(",", "\u202f")
        next_jackpot_card = f"""    <div class="card" style="text-align:center;">
      <h2 style="font-size:1rem;margin-bottom:.4rem;">Prochain jackpot Loto</h2>
      <p style="font-size:1.4rem;font-weight:700;color:#ca8a04;margin:.4rem 0;">
        {nj_str} \u20ac
      </p>
      <p style="font-size:.85rem;color:#6b7280;">Prochain tirage : lundi, mercredi ou samedi à 20h20</p>
    </div>"""
    else:
        next_jackpot_card = ""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>🍀 Résultats Loto {date_display} — Numéros gagnants tirage n°{draw_num}</title>
  <meta name="description" content="Résultats du tirage Loto du {date_display} (n°{draw_num}). Numéros gagnants : {balls_str} + numéro chance {lucky}. Mis à jour automatiquement après chaque tirage.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{LOTO_SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Loto {date_display} — Numéros gagnants">
  <meta property="og:description" content="Résultats Loto du {date_display} : {balls_str} + chance {lucky}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{LOTO_SITE_URL}/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Loto {date_display} — Numéros gagnants">
  <meta name="twitter:description" content="Résultats Loto du {date_display} : {balls_str} + chance {lucky}.">
  <meta property="article:published_time" content="{date_str}T22:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Résultats Loto {date_display} — Tirage n°{draw_num}",
    "datePublished": "{date_str}T22:00:00+01:00",
    "dateModified": "{date_str}T22:00:00+01:00",
    "description": "Numéros gagnants du tirage Loto du {date_display} : {balls_str} + numéro chance {lucky}.",
    "url": "{LOTO_SITE_URL}/",
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
        "name": "Quels sont les numéros gagnants du Loto du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Les numéros gagnants du tirage Loto n°{draw_num} du {date_display} sont : {balls_str}, avec le numéro chance {lucky}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quand a lieu le prochain tirage Loto ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le Loto tire le lundi, le mercredi et le samedi soir (vers 20h20). Cette page est mise à jour automatiquement après chaque tirage."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quel est le numéro chance du Loto du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le numéro chance du tirage Loto du {date_display} est le {lucky}."
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
      {{"@type": "ListItem", "position": 2, "name": "Loto", "item": "https://solution-du-jour.fr/loto/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Résultats Loto</h1>
  <p class="subtitle">Numéros gagnants — tirage n°{draw_num}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <span>Loto</span>
</nav>
  <article>

    <div class="card">
      <h2>Tirage Loto n°{draw_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez les <strong>résultats du tirage Loto du {date_display}</strong> (tirage n°{draw_num}).
        Les numéros sont affichés dans l'ordre croissant, comme sur le site officiel FDJ.
      </p>
    </div>

    <div class="card">
      <h2>Vérifiez votre grille</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Sélectionnez vos 5 numéros + votre numéro chance pour savoir si vous avez gagné.
      </p>
      <script>
      (function(){{
        const DRAW_BALLS = {balls};
        const DRAW_LUCKY = {lucky};
        const LOTO_GAINS = {{1:null,2:100000,3:1500,4:250,5:50,6:10,7:5,8:2,9:2}};
        const RANK_LABELS = {{
          1:'1er rang — Jackpot',2:'2e rang',3:'3e rang',4:'4e rang',
          5:'5e rang',6:'6e rang',7:'7e rang',8:'8e rang',9:'9e rang'
        }};
        const RANK_DESC = {{
          1:'5 numéros + chance',2:'5 numéros',3:'4 numéros + chance',4:'4 numéros',
          5:'3 numéros + chance',6:'3 numéros',7:'2 numéros + chance',
          8:'1 numéro + chance',9:'2 numéros'
        }};
        function lotoRank(ub,ul,db,dl){{
          const mb=ub.filter(n=>db.includes(n)).length,ml=ul===dl;
          if(mb===5&&ml)return 1;if(mb===5)return 2;
          if(mb===4&&ml)return 3;if(mb===4)return 4;
          if(mb===3&&ml)return 5;if(mb===3)return 6;
          if(mb===2&&ml)return 7;if(mb===1&&ml)return 8;
          if(mb===2)return 9;return 0;
        }}
        let userBalls=[], userLucky=null;
        function render(){{
          const rank=userBalls.length===5&&userLucky!==null?lotoRank(userBalls,userLucky,DRAW_BALLS,DRAW_LUCKY):0;
          const res=document.getElementById('loto-checker-result');
          if(!res)return;
          if(userBalls.length<5||userLucky===null){{res.style.display='none';return;}}
          res.style.display='block';
          if(rank===0){{
            res.className='sim-result';
            res.innerHTML='<div class="sim-result-rank">Pas de gain</div>'
              +'<div class="sim-result-detail">Aucune combinaison gagnante.</div>';
          }}else{{
            const gain=LOTO_GAINS[rank];
            res.className='sim-result win';
            res.innerHTML='<div class="sim-result-rank">'+RANK_LABELS[rank]+'</div>'
              +(gain?'<div class="sim-result-gain">≈ '+gain.toLocaleString('fr-FR')+'&nbsp;€</div>':'<div class="sim-result-gain">Jackpot !</div>')
              +'<div class="sim-result-detail">'+RANK_DESC[rank]+'</div>';
          }}
        }}
        function buildBalls(){{
          const bg=document.getElementById('loto-checker-balls');
          const lg=document.getElementById('loto-checker-lucky');
          if(!bg||!lg)return;
          for(let n=1;n<=49;n++){{
            const btn=document.createElement('button');
            btn.className='sim-ball';btn.type='button';btn.textContent=n;
            btn.addEventListener('click',()=>{{
              const i=userBalls.indexOf(n);
              if(i>=0){{userBalls.splice(i,1);btn.classList.remove('selected');}}
              else if(userBalls.length<5){{userBalls.push(n);btn.classList.add('selected');}}
              bg.querySelectorAll('.sim-ball').forEach(b=>{{
                b.classList.toggle('dimmed',userBalls.length>=5&&!userBalls.includes(parseInt(b.textContent)));
              }});
              document.getElementById('loto-cnt-balls').textContent=userBalls.length;
              render();
            }});
            bg.appendChild(btn);
          }}
          for(let n=1;n<=10;n++){{
            const btn=document.createElement('button');
            btn.className='sim-ball';btn.type='button';btn.textContent=n;
            btn.style.background='#fef3c7';btn.style.borderColor='#fbbf24';
            btn.addEventListener('click',()=>{{
              if(userLucky===n){{userLucky=null;btn.classList.remove('selected');}}
              else{{
                lg.querySelectorAll('.sim-ball').forEach(b=>b.classList.remove('selected'));
                userLucky=n;btn.classList.add('selected');
              }}
              render();
            }});
            lg.appendChild(btn);
          }}
        }}
        document.addEventListener('DOMContentLoaded',buildBalls);
      }})();
      </script>
      <p class="sim-label">Vos 5 numéros <span id="loto-cnt-balls">0</span>/5</p>
      <div class="sim-grid" id="loto-checker-balls"></div>
      <p class="sim-label">Votre numéro chance</p>
      <div class="sim-grid" id="loto-checker-lucky"></div>
      <div id="loto-checker-result" style="display:none;"></div>
      <p style="margin-top:1rem;font-size:.85rem;color:#6b7280;">
        Testez vos numéros sur tout l'historique : <a href="simulateur/">Simulateur Loto depuis 2019 &#8594;</a>
      </p>
    </div>

    <div class="card">
      <h2>Numéros gagnants du {date_display}</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        5 boules numérotées + 1 numéro chance (en doré)
      </p>
      <div class="solution-blur" id="loto-balls-blur">
        {_balls_html(balls, lucky)}
        <p class="puzzle-meta">
          Boules : <strong>{balls_str}</strong> · Numéro chance : <strong>{lucky}</strong>
        </p>
{jackpot_html(jackpot_won, jackpot_winners, jackpot_amount)}
{_codes_html(codes)}
      </div>
      <p style="text-align:center;margin-top:.75rem;">
        <button class="reveal-btn" id="loto-reveal-btn"
          onclick="document.getElementById('loto-balls-blur').classList.add('revealed');this.parentElement.style.display='none';">
          Voir les numéros gagnants
        </button>
      </p>
    </div>

{next_jackpot_card}
{recent_archives_card}
    <div class="card">
      <h2>Rappel des règles du Loto</h2>
      <p>
        Le <strong>Loto</strong> est le jeu de tirage de la <a href="https://www.fdj.fr" rel="noopener" target="_blank">FDJ</a>.
        Chaque tirage, 5 boules (de 1 à 49) et 1 numéro chance (de 1 à 10) sont tirés au sort.
        Les tirages ont lieu le <strong>lundi, mercredi et samedi</strong> à partir de 20h20.
      </p>
      <p style="margin-top:.75rem;">
        Pour vérifier si vous avez gagné ou pour jouer, rendez-vous sur
        <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">fdj.fr</a>.
        Cette page est mise à jour automatiquement après chaque tirage.
      </p>
    </div>

    <div class="card" style="margin-top:.5rem;">
      <h2 style="font-size:1rem;margin-bottom:.75rem;">Autres jeux du jour</h2>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;">
        <a href="simulateur/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🎯 Simulateur de gains</a>
        <a href="../cemantix/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🧠 Cémantix</a>
        <a href="../sutom/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">🔤 Sutom</a>
        <a href="../euromillions/" style="padding:.4rem .85rem;background:#f3f4f6;border-radius:.375rem;text-decoration:none;color:#374151;font-weight:500;">⭐ EuroMillions</a>
      </div>
    </div>
  </article>
</main>

<footer>
  <p>Site non officiel — Résultats récupérés automatiquement · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">fdj.fr</a></p>
</footer>

</body>
</html>"""

    atomic_write(LOTO_DIR / "index.html", html)


def generate_unavailable_html(today: date) -> None:
    """Génère une page 'résultats non disponibles' si l'API est inaccessible."""
    LOTO_DIR.mkdir(parents=True, exist_ok=True)
    date_display = date_fr(today)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <title>Résultats Loto — Non disponibles</title>
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="../css/style.css">
</head>
<body>

<header class="site-header">
  <h1>Résultats Loto</h1>
  <p class="subtitle">{date_display}</p>
</header>

<main>
  <div class="card">
    <h2>Résultats non disponibles</h2>
    <p>
      Les résultats du Loto ne sont pas encore disponibles ou n'ont pas pu être récupérés.
      Le prochain tirage a lieu lundi, mercredi ou samedi soir à partir de 20h20.
      Rendez-vous directement sur
      <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">fdj.fr</a>.
    </p>
  </div>
</main>

<footer>
  <p><a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives Loto</a></p>
</footer>

</body>
</html>"""

    atomic_write(LOTO_DIR / "index.html", html)


# ── Statistiques ──────────────────────────────────────────────────────────────

def compute_loto_stats(archives: list[dict]) -> dict:
    """Calcule les statistiques des tirages à partir des archives JSON."""
    ball_counts = Counter()
    lucky_counts = Counter()
    last_seen = {}  # ball → index du dernier tirage où elle est apparue (0 = plus récent)

    for i, draw in enumerate(archives):
        for b in draw["balls"]:
            ball_counts[b] += 1
            if b not in last_seen:
                last_seen[b] = i
        lucky_counts[draw["lucky_ball"]] += 1

    # Retardataires : boules absentes depuis le plus de tirages
    all_balls = set(range(1, 50))
    for b in all_balls:
        if b not in last_seen:
            last_seen[b] = len(archives)  # jamais vu dans nos archives

    sorted_by_freq = sorted(ball_counts.items(), key=lambda x: -x[1])
    sorted_lucky = sorted(lucky_counts.items(), key=lambda x: -x[1])
    sorted_retard = sorted(last_seen.items(), key=lambda x: -x[1])

    # Fenêtre "50 derniers tirages"
    recent_counts = Counter()
    for draw in archives[:50]:
        for b in draw["balls"]:
            recent_counts[b] += 1
    recent_sorted = sorted(recent_counts.items(), key=lambda x: -x[1])

    return {
        "total_draws": len(archives),
        "date_from": archives[-1]["date"] if archives else "",
        "date_to": archives[0]["date"] if archives else "",
        "top_balls": sorted_by_freq[:10],        # 10 boules les plus fréquentes
        "bottom_balls": sorted_by_freq[-5:],     # 5 boules les moins fréquentes
        "top_lucky": sorted_lucky[:5],           # 5 numéros chance les plus fréquents
        "retardataires": sorted_retard[:5],      # 5 boules absentes depuis le plus longtemps
        "all_balls": dict(ball_counts),
        "recent_top_balls": recent_sorted[:5],   # 5 boules les plus fréquentes sur 50 derniers
        "recent_bottom_balls": recent_sorted[-5:],  # 5 boules les moins fréquentes sur 50 derniers
    }


def generate_stats_html(stats: dict) -> None:
    """Génère docs/loto/stats/index.html."""
    stats_dir = LOTO_DIR / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)

    n = stats["total_draws"]
    year_from = stats["date_from"][:4] if stats["date_from"] else "2008"
    date_from = date_fr(date.fromisoformat(stats["date_from"])) if stats["date_from"] else "—"
    date_to = date_fr(date.fromisoformat(stats["date_to"])) if stats["date_to"] else "—"

    def ball_row(b, count):
        pct = round(count / n * 100, 1) if n else 0
        return (
            f'<tr><td><span class="loto-ball" style="width:2rem;height:2rem;font-size:.85rem;'
            f'display:inline-flex;align-items:center;justify-content:center;">{b}</span></td>'
            f'<td>{count}</td><td>{pct}%</td></tr>'
        )

    top_rows = "\n".join(ball_row(b, c) for b, c in stats["top_balls"])
    lucky_rows = "\n".join(
        f'<tr><td><span class="loto-ball loto-ball-chance" style="width:2rem;height:2rem;'
        f'font-size:.85rem;display:inline-flex;align-items:center;justify-content:center;">'
        f'{b}</span></td><td>{c}</td><td>{round(c/n*100,1) if n else 0}%</td></tr>'
        for b, c in stats["top_lucky"]
    )
    retard_rows = "\n".join(
        f'<tr><td><span class="loto-ball" style="width:2rem;height:2rem;font-size:.85rem;'
        f'display:inline-flex;align-items:center;justify-content:center;">{b}</span></td>'
        f'<td>{d} tirage{"s" if d > 1 else ""}</td></tr>'
        for b, d in stats["retardataires"]
    )

    top_ball = stats["top_balls"][0][0] if stats["top_balls"] else "—"
    top_lucky = stats["top_lucky"][0][0] if stats["top_lucky"] else "—"
    retard_ball = stats["retardataires"][0][0] if stats["retardataires"] else "—"
    retard_draws = stats["retardataires"][0][1] if stats["retardataires"] else 0
    bottom_balls_list = ", ".join(str(b) for b, _ in stats["bottom_balls"])

    bottom_rows = "\n".join(ball_row(b, c) for b, c in stats["bottom_balls"])

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

  <title>Statistiques Loto FDJ depuis {year_from} — Numéros les plus sortis | Solution du Jour</title>
  <meta name="description" content="Numéros les plus sortis au Loto FDJ depuis {year_from} ({n} tirages). Retardataires, tendances récentes. Mis à jour.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{LOTO_SITE_URL}/stats/">

  <meta property="og:title" content="Statistiques Loto FDJ depuis {year_from} — Numéros les plus sortis">
  <meta property="og:description" content="Fréquence des numéros sur {n} tirages Loto FDJ depuis {year_from}. Mis à jour automatiquement.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{LOTO_SITE_URL}/stats/">
  <meta property="og:image" content="{SITE_URL}/og-image.png">
  <meta name="twitter:card" content="summary">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Dataset",
    "name": "Statistiques Loto FDJ depuis {year_from}",
    "description": "Fréquence des numéros sur {n} tirages Loto FDJ depuis {year_from}",
    "temporalCoverage": "{stats['date_from']}/{stats['date_to']}",
    "url": "{LOTO_SITE_URL}/stats/",
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
        "name": "Quel numéro sort le plus souvent au Loto ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sur les {n} tirages Loto analysés depuis {year_from} ({date_from} au {date_to}), le numéro {top_ball} est le plus fréquemment sorti."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quel est le numéro chance le plus sorti au Loto ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sur les {n} tirages analysés depuis {year_from}, le numéro chance {top_lucky} est sorti le plus souvent."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quel est le numéro retardataire du Loto ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le numéro {retard_ball} est absent depuis {retard_draws} tirages consécutifs — c'est le plus grand retard actuel."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Ces statistiques garantissent-elles de gagner au Loto ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Non. Le Loto est un jeu de hasard : chaque boule a exactement la même probabilité de sortir à chaque tirage (1 chance sur 49). Les statistiques historiques sont un indicateur descriptif, pas prédictif."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Combien de tirages Loto ont eu lieu depuis {year_from} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "{n} tirages Loto ont eu lieu depuis {year_from}, à raison de 3 tirages par semaine (lundi, mercredi, samedi)."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Quels sont les numéros les moins sortis au Loto ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sur {n} tirages depuis {year_from}, les numéros les moins sortis sont : {bottom_balls_list}."
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
      {{"@type": "ListItem", "position": 2, "name": "Loto", "item": "{LOTO_SITE_URL}/"}},
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
  <h1>Statistiques Loto FDJ depuis {year_from}</h1>
  <p class="subtitle">Numéros les plus sortis sur {n} tirages — Mis à jour le {date_to}</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="{SITE_URL}/">Accueil</a> &rsaquo;
  <a href="../index.html">Loto</a> &rsaquo;
  <span>Statistiques</span>
</nav>

  <div class="card">
    <h2>À propos de ces statistiques</h2>
    <p>
      Ces statistiques sont calculées automatiquement à partir des <strong>{n} tirages Loto FDJ depuis {year_from}</strong>
      ({date_from} au {date_to}). Elles sont mises à jour après chaque tirage.
    </p>
    <p style="margin-top:.6rem;font-size:.9rem;color:#6b7280;">
      ⚠️ Le Loto est un jeu de hasard : chaque boule a la même probabilité de sortir à chaque tirage.
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
          <th style="padding:.4rem .5rem;">Boule</th>
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
      Les 5 boules les plus rares sur l'ensemble des {n} tirages.
    </p>
    <div class="table-scroll">
    <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
      <thead>
        <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
          <th style="padding:.4rem .5rem;">Boule</th>
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
              <th style="padding:.3rem .4rem;">Boule</th>
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
              <th style="padding:.3rem .4rem;">Boule</th>
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
    <h2>Numéro Chance le plus fréquent</h2>
    <div class="table-scroll">
    <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
      <thead>
        <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
          <th style="padding:.4rem .5rem;">N° Chance</th>
          <th style="padding:.4rem .5rem;">Sorties</th>
          <th style="padding:.4rem .5rem;">Fréquence</th>
        </tr>
      </thead>
      <tbody>
{lucky_rows}
      </tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <h2>Numéros retardataires</h2>
    <p style="font-size:.85rem;color:#6b7280;margin-bottom:.75rem;">
      Boules absentes depuis le plus grand nombre de tirages consécutifs.
    </p>
    <div class="table-scroll">
    <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
      <thead>
        <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
          <th style="padding:.4rem .5rem;">Boule</th>
          <th style="padding:.4rem .5rem;">Absent depuis</th>
        </tr>
      </thead>
      <tbody>
{retard_rows}
      </tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <h2>Questions fréquentes</h2>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Quel numéro sort le plus souvent au Loto ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      Sur les {n} tirages analysés depuis {year_from}, le numéro <strong>{top_ball}</strong> est le plus fréquemment sorti.
      Le numéro chance le plus fréquent est le <strong>{top_lucky}</strong>.
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Quels sont les numéros les moins sortis au Loto ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      Sur {n} tirages depuis {year_from}, les numéros les moins fréquents sont : <strong>{bottom_balls_list}</strong>.
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Combien de tirages Loto ont eu lieu depuis {year_from} ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      <strong>{n} tirages</strong> ont eu lieu depuis {year_from},
      à raison de 3 tirages par semaine (lundi, mercredi, samedi).
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Quel est le numéro retardataire du Loto ?</h3>
    <p style="font-size:.9rem;margin-bottom:.75rem;">
      Le numéro <strong>{retard_ball}</strong> est absent depuis <strong>{retard_draws} tirages</strong> consécutifs.
    </p>

    <h3 style="font-size:1rem;margin-bottom:.3rem;">Ces stats garantissent-elles de gagner ?</h3>
    <p style="font-size:.9rem;">
      Non — le Loto est un jeu d'équiprobabilité. Chaque boule a exactement 1 chance sur 49 de sortir
      à chaque tirage, indépendamment de l'historique.
    </p>
  </div>

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../index.html">Dernier tirage Loto &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="{SITE_URL}/">Accueil</a> ·
    <a href="../index.html">Dernier tirage</a> ·
    <a href="../archive/">Archives</a> ·
    <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">Jouer au Loto</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Statistiques calculées automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(stats_dir / "index.html", html)
    print("[Loto] Génération de docs/loto/stats/index.html…")


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(draw_date: date, data: dict) -> None:
    """Génère tous les fichiers HTML Loto à partir des JSON déjà en place."""
    all_archives = load_all_archives()
    draw_str = draw_date.isoformat()
    past_archives = [e for e in all_archives if e["date"] != draw_str]

    print(f"[Loto] Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        d = date.fromisoformat(entry["date"])
        prev_date = date.fromisoformat(past_archives[i + 1]["date"]) if i + 1 < len(past_archives) else None
        next_date = date.fromisoformat(past_archives[i - 1]["date"]) if i > 0 else None
        generate_archive_html(
            d, entry["draw_num"], entry["balls"], entry["lucky_ball"],
            prev_date, next_date,
            jackpot_won=entry.get("jackpot_won"),
            jackpot_winners=entry.get("jackpot_winners", 0),
            jackpot_amount=entry.get("jackpot_amount"),
            codes=entry.get("codes"),
        )

    print("[Loto] Génération de docs/loto/archive/index.html…")
    generate_archive_index(past_archives)

    recent_archives = [e for e in past_archives[:7] if (LOTO_ARCHIVE / f"{e['date']}.html").exists()]
    print("[Loto] Génération de docs/loto/index.html…")
    generate_index_html(
        draw_date, data["draw_num"], data["balls"], data["lucky_ball"],
        recent_archives,
        total_archives=len(all_archives),
        jackpot_won=data.get("jackpot_won"),
        jackpot_winners=data.get("jackpot_winners", 0),
        jackpot_amount=data.get("jackpot_amount"),
        next_jackpot=data.get("next_jackpot"),
        codes=data.get("codes"),
    )

    stats = compute_loto_stats(all_archives)
    generate_stats_html(stats)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run(today: date) -> dict | None:
    """
    Récupère le dernier tirage Loto et génère tous les fichiers.
    Retourne le dict data ou None si le tirage est indisponible.
    Note : contrairement aux jeux de mots, le tirage peut dater d'avant aujourd'hui
    (le Loto tire lun/mer/sam soir — la mise à jour a lieu le lendemain matin).
    """
    LOTO_DIR.mkdir(parents=True, exist_ok=True)
    LOTO_ARCHIVE.mkdir(parents=True, exist_ok=True)

    print("[Loto] Récupération du dernier tirage…")
    draw = get_loto_latest()

    if not draw:
        print("[Loto] ⚠ Tirage non disponible — génération page indisponible.")
        generate_unavailable_html(today)
        return None

    draw_date_str = draw["date"]
    print(f"[Loto] ✅ Dernier tirage : {draw_date_str} — {draw['balls']} + chance {draw['lucky_ball']}")

    # Si ce tirage est déjà sauvegardé → régénérer HTML seulement
    solution_path = LOTO_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == draw_date_str:
            # Ajouter les codes gagnants s'ils sont absents du JSON
            if "codes" not in existing:
                reducmiz_info = get_loto_jackpot_latest(nb=5)
                if reducmiz_info and draw_date_str in reducmiz_info:
                    codes = reducmiz_info[draw_date_str].get("codes")
                    if codes:
                        existing["codes"] = codes
                        atomic_write(solution_path, json.dumps(existing, ensure_ascii=False, indent=2))
                        print(f"[Loto] Codes ajoutés : {len(codes)} codes gagnants")
            print(f"[Loto] ℹ Tirage déjà présent ({draw_date_str}) — régénération HTML uniquement.")
            _generate_all_html(date.fromisoformat(draw_date_str), existing)
            return existing

    # Nouveau tirage → enrichir avec jackpot
    reducmiz_info = get_loto_jackpot_latest(nb=10)
    tg_info = get_loto_current_jackpot(draw_date_str)
    if tg_info:
        draw.update(tg_info)
        print(f"[Loto] Jackpot tirage-gagnant : {tg_info.get('jackpot_amount')} € (won={tg_info.get('jackpot_won')}), prochain={tg_info.get('next_jackpot')}")
        # Compléter avec nombre de gagnants depuis reducmiz
        if reducmiz_info and draw_date_str in reducmiz_info:
            draw["jackpot_winners"] = reducmiz_info[draw_date_str].get("jackpot_winners", 0)
    elif reducmiz_info and draw_date_str in reducmiz_info:
        draw.update(reducmiz_info[draw_date_str])
        print(f"[Loto] Jackpot reducmiz : won={draw.get('jackpot_won')}, winners={draw.get('jackpot_winners')}")
    else:
        print(f"[Loto] ⚠ Jackpot non disponible pour {draw_date_str}")

    data = generate_solution_json(draw)
    generate_archive_json(data)
    _generate_all_html(date.fromisoformat(draw_date_str), data)

    print(f"[Loto] 🎉 Tirage sauvegardé et HTML généré ({draw_date_str}, n°{data['draw_num']})")
    return data


# ── Simulateur historique ──────────────────────────────────────────────────────

def generate_simulator_data() -> None:
    """Génère docs/loto/simulateur/data.json — tous tirages [date, balls, lucky_ball]."""
    archives = _load_archives(LOTO_ARCHIVE, required_keys=["date", "balls", "lucky_ball"])
    if not archives:
        print("[Loto] ⚠ Simulateur : aucune archive trouvée")
        return
    # Tri chronologique (oldest first) pour faciliter la lecture JS
    archives_sorted = sorted(archives, key=lambda e: e["date"])
    data = [[e["date"], e["balls"], e["lucky_ball"]] for e in archives_sorted]
    out_dir = LOTO_DIR / "simulateur"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "data.json",
                 json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"[Loto] Simulateur data.json : {len(data)} tirages")


def generate_simulator_html() -> None:
    """Génère docs/loto/simulateur/index.html — simulateur historique interactif."""
    out_dir = LOTO_DIR / "simulateur"
    out_dir.mkdir(parents=True, exist_ok=True)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">

  <title>🍀 Simulateur Loto FDJ — Calculez vos gains sur 2 600 tirages</title>
  <meta name="description" content="Simulez vos gains Loto FDJ : entrez vos 5 numéros + numéro chance et obtenez vos résultats sur 2\u202f600+ tirages depuis 2019. Gratuit et instantané.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{LOTO_SITE_URL}/simulateur/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Simulateur Loto FDJ — Calculez vos gains sur 2 600 tirages">
  <meta property="og:description" content="Simulez vos gains Loto FDJ : entrez vos 5 numéros + numéro chance et obtenez vos résultats sur 2\u202f600+ tirages depuis 2019.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{LOTO_SITE_URL}/simulateur/">
  <meta property="og:image" content="https://solution-du-jour.fr/og-image.png">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:site_name" content="Solutions du Jour">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Simulateur Loto FDJ — Calculez vos gains sur 2 600 tirages">
  <meta name="twitter:description" content="Simulez vos gains Loto FDJ : entrez vos 5 numéros + numéro chance et obtenez vos résultats sur 2\u202f600+ tirages depuis 2019.">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "WebApplication",
    "name": "Simulateur de gains Loto FDJ",
    "description": "Simulez vos gains Loto sur l'ensemble des tirages depuis 2019. Entrez vos 5 numéros et votre numéro chance.",
    "url": "{LOTO_SITE_URL}/simulateur/",
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
        "name": "Comment fonctionne le simulateur Loto ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sélectionnez vos 5 numéros (1–49) et votre numéro chance (1–10), puis cliquez sur Simuler. L'outil vérifie vos numéros sur tous les tirages Loto depuis 2019 et calcule vos gains cumulés."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Les résultats sont-ils officiels ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Les tirages sont issus des données officielles FDJ (OpenDataSoft). Les gains affichés sont approximatifs car le jackpot varie."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Combien de tirages Loto sont analysés ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le simulateur couvre tous les tirages disponibles depuis 2019 (environ 2\u202f600 tirages). Il est mis à jour après chaque nouveau tirage."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Peut-on vraiment gagner en jouant toujours les mêmes numéros ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Non — chaque tirage est indépendant. La probabilité de gagner le jackpot est d'environ 1 sur 19 millions. Ce simulateur est un outil ludique pour illustrer l'espérance mathématique."
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
      {{"@type": "ListItem", "position": 2, "name": "Loto", "item": "https://solution-du-jour.fr/loto/"}},
      {{"@type": "ListItem", "position": 3, "name": "Simulateur", "item": "{LOTO_SITE_URL}/simulateur/"}}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="https://gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Simulateur Loto FDJ — Calculez vos gains</h1>
  <p class="subtitle">Simulez vos résultats sur 2 600+ tirages depuis 2019</p>
</header>

<main>
<nav class="breadcrumb" aria-label="Fil d'Ariane">
  <a href="https://solution-du-jour.fr/">Accueil</a> &rsaquo;
  <a href="../">Loto</a> &rsaquo;
  <span>Simulateur</span>
</nav>
  <article>

    <div class="card">
      <h2>Comment ça marche ?</h2>
      <p>
        Choisissez <strong>5 numéros</strong> (1–49) et <strong>1 numéro chance</strong> (1–10),
        puis lancez la simulation. L'outil vérifie votre grille sur l'ensemble des tirages Loto
        depuis 2019 et calcule vos gains cumulés.
      </p>
      <p style="margin-top:.5rem;font-size:.9rem;color:#6b7280;">
        Mise par tirage : <strong>2,20&nbsp;€</strong>. Les gains affichés sont approximatifs
        (hors jackpot variable).
      </p>
    </div>

    <div class="card">
      <h2>Vos numéros</h2>

      <p class="sim-label">5 numéros (<span id="cnt-balls">0</span>/5)</p>
      <div class="sim-grid" id="picker-balls"></div>

      <p class="sim-label">Numéro chance (<span id="cnt-lucky">0</span>/1)</p>
      <div class="sim-grid" id="picker-lucky"></div>

      <button class="sim-btn" id="sim-btn" disabled>Simuler depuis 2019</button>
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
        <summary style="cursor:pointer;font-weight:600;">Comment fonctionne le simulateur Loto ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          Sélectionnez vos 5 numéros (1–49) et votre numéro chance (1–10), puis cliquez sur Simuler.
          L'outil vérifie vos numéros sur tous les tirages Loto depuis 2019 et calcule vos gains cumulés.
        </p>
      </details>
      <details style="margin-bottom:.75rem;">
        <summary style="cursor:pointer;font-weight:600;">Les gains sont-ils exacts ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          Les gains sont approximatifs. Le jackpot (rang 1) varie à chaque tirage.
          Les autres rangs reflètent les montants moyens officiels FDJ.
        </p>
      </details>
      <details style="margin-bottom:.75rem;">
        <summary style="cursor:pointer;font-weight:600;">Combien de tirages sont analysés ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          Le simulateur couvre tous les tirages disponibles depuis 2019, mis à jour après chaque tirage.
        </p>
      </details>
      <details>
        <summary style="cursor:pointer;font-weight:600;">Peut-on vraiment gagner en jouant toujours les mêmes numéros ?</summary>
        <p style="margin-top:.5rem;font-size:.9rem;">
          Non — chaque tirage est indépendant. La probabilité de décrocher le jackpot est d'environ
          1/19&nbsp;068&nbsp;840. Ce simulateur illustre l'espérance mathématique de façon ludique.
        </p>
      </details>
    </div>

    <div class="card" style="text-align:center;">
      <p style="font-size:.9rem;">
        <a href="../">← Résultats du dernier tirage</a> &nbsp;|&nbsp;
        <a href="../stats/">Statistiques Loto</a> &nbsp;|&nbsp;
        <a href="../archive/">Archives</a>
      </p>
    </div>

  </article>
</main>

<footer>
  <p>Site non officiel · <a href="{SITE_URL}/">Accueil</a> · <a href="../">Loto</a> · <a href="../archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://www.fdj.fr/jeux-de-tirage/loto" rel="noopener" target="_blank">fdj.fr</a></p>
</footer>

<script>
(function() {{
  const LOTO_GAINS = {{1:null,2:100000,3:1500,4:250,5:50,6:10,7:5,8:2,9:2}};
  const LOTO_MISE = 2.20;
  const RANK_LABELS = {{
    1:'1er rang (Jackpot)',2:'2e rang',3:'3e rang',4:'4e rang',
    5:'5e rang',6:'6e rang',7:'7e rang',8:'8e rang',9:'9e rang'
  }};
  const RANK_DESC = {{
    1:'5 numéros + chance',2:'5 numéros',3:'4 numéros + chance',4:'4 numéros',
    5:'3 numéros + chance',6:'3 numéros',7:'2 numéros + chance',
    8:'1 numéro + chance',9:'2 numéros'
  }};

  function lotoRank(ub, ul, db, dl) {{
    const mb = ub.filter(n => db.includes(n)).length, ml = ul === dl;
    if (mb===5&&ml) return 1; if (mb===5) return 2;
    if (mb===4&&ml) return 3; if (mb===4) return 4;
    if (mb===3&&ml) return 5; if (mb===3) return 6;
    if (mb===2&&ml) return 7; if (mb===1&&ml) return 8;
    if (mb===2)     return 9; return 0;
  }}

  let userBalls = [], userLucky = null;

  function updateBtn() {{
    document.getElementById('sim-btn').disabled = !(userBalls.length === 5 && userLucky !== null);
  }}

  // Build ball picker
  const bg = document.getElementById('picker-balls');
  for (let n = 1; n <= 49; n++) {{
    const btn = document.createElement('button');
    btn.className = 'sim-ball'; btn.type = 'button'; btn.textContent = n;
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

  // Build lucky picker
  const lg = document.getElementById('picker-lucky');
  for (let n = 1; n <= 10; n++) {{
    const btn = document.createElement('button');
    btn.className = 'sim-ball'; btn.type = 'button'; btn.textContent = n;
    btn.style.background = '#fef3c7'; btn.style.borderColor = '#fbbf24';
    btn.addEventListener('click', () => {{
      if (userLucky === n) {{ userLucky = null; btn.classList.remove('selected'); }}
      else {{
        lg.querySelectorAll('.sim-ball').forEach(b => b.classList.remove('selected'));
        userLucky = n; btn.classList.add('selected');
      }}
      document.getElementById('cnt-lucky').textContent = userLucky !== null ? 1 : 0;
      updateBtn();
    }});
    lg.appendChild(btn);
  }}

  document.getElementById('sim-btn').addEventListener('click', () => {{
    fetch('data.json')
      .then(r => r.json())
      .then(draws => {{
        let totalGain = 0, rankCounts = {{}}, bestRank = 0, bestDate = null;
        for (const [dateStr, balls, lucky] of draws) {{
          const rank = lotoRank(userBalls, userLucky, balls, lucky);
          if (rank > 0) {{
            rankCounts[rank] = (rankCounts[rank] || 0) + 1;
            const gain = LOTO_GAINS[rank] || 0;
            totalGain += gain;
            if (bestRank === 0 || rank < bestRank) {{ bestRank = rank; bestDate = dateStr; }}
          }}
        }}
        const mise = draws.length * LOTO_MISE;
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
        for (let r = 1; r <= 9; r++) {{
          const cnt = rankCounts[r] || 0;
          if (cnt === 0) continue;
          const gain = LOTO_GAINS[r];
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
    print("[Loto] Simulateur index.html généré")
