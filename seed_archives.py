"""
seed_archives.py — Amorçage de l'historique Loto et EuroMillions.

Usage :
  python seed_archives.py            # 200 derniers tirages de chaque jeu
  python seed_archives.py --loto-max 500 --em-max 100
  python seed_archives.py --loto-only
  python seed_archives.py --em-only

Ce script est à lancer UNE SEULE FOIS pour pré-remplir les archives.
Les runs quotidiens de generate.py gèrent ensuite les nouveaux tirages.
"""

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from core import _session, date_fr, atomic_write, DOCS_DIR


# ── Loto (OpenDataSoft) ───────────────────────────────────────────────────────

LOTO_ARCHIVE = DOCS_DIR / "loto" / "archive"
_LOTO_API_BASE = (
    "https://data.opendatasoft.com/api/records/1.0/search/"
    "?dataset=resultats-loto-2019-a-aujourd-hui%40agrall"
    "&sort=date_de_tirage"
)


def seed_loto(max_rows: int = 200) -> int:
    """Récupère les max_rows derniers tirages Loto et crée les JSON d'archive manquants."""
    LOTO_ARCHIVE.mkdir(parents=True, exist_ok=True)
    saved = 0
    batch = 100  # max par appel API

    for start in range(0, max_rows, batch):
        rows = min(batch, max_rows - start)
        url = f"{_LOTO_API_BASE}&rows={rows}&start={start}"
        try:
            resp = _session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  ⚠ Loto API erreur (start={start}) : {e}")
            break

        records = resp.json().get("records", [])
        if not records:
            break

        for rec in records:
            f = rec["fields"]
            draw_date_str = f["date_de_tirage"]
            archive_path = LOTO_ARCHIVE / f"{draw_date_str}.json"
            if archive_path.exists():
                continue  # déjà présent
            balls = sorted(f[f"boule_{i}"] for i in range(1, 6))
            data = {
                "date": draw_date_str,
                "draw_num": f.get("annee_numero_de_tirage", ""),
                "balls": balls,
                "lucky_ball": int(f["numero_chance"]),
            }
            atomic_write(archive_path, json.dumps(data, ensure_ascii=False, indent=2))
            saved += 1

        print(f"  Loto : {start + len(records)} tirages traités, {saved} nouveaux…")
        if len(records) < rows:
            break  # fin des données
        time.sleep(0.3)  # politesse envers l'API

    return saved


# ── EuroMillions (euro-millions.com) ─────────────────────────────────────────

EM_ARCHIVE = DOCS_DIR / "euromillions" / "archive"
_EM_HISTORY_URL = "https://www.euro-millions.com/fr/archive-resultats-{year}"
_EM_CURRENT_URL = "https://www.euro-millions.com/fr/resultats"  # année en cours (pas encore archivée)
_EM_HEADERS = {
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


def _parse_em_history_page(html: str) -> list[dict]:
    """Parse une page d'historique EuroMillions et retourne une liste de tirages."""
    soup = BeautifulSoup(html, "html.parser")
    draws = []

    # Sur euro-millions.com/fr/historique-resultats-YYYY, chaque tirage est
    # dans un bloc contenant à la fois des .ball, des .lucky-star et une date FR.
    # On remonte depuis chaque groupe de boules vers le bloc parent daté.
    processed_dates = set()

    for ball_tag in soup.find_all(class_="ball"):
        node = ball_tag.parent
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
                draw_str = draw_date.isoformat()
                if draw_str in processed_dates:
                    break  # déjà traité
                balls_in = sorted(
                    int(b.text.strip()) for b in node.find_all(class_="ball")[:5]
                )
                stars_in = sorted(
                    int(s.text.strip()) for s in node.find_all(class_="lucky-star")[:2]
                )
                if len(balls_in) == 5 and len(stars_in) == 2:
                    draws.append({
                        "date": draw_str,
                        "balls": balls_in,
                        "stars": stars_in,
                    })
                    processed_dates.add(draw_str)
                break
            node = node.parent

    return draws


def seed_euromillions(max_rows: int = 200) -> int:
    """Scrape l'historique EuroMillions et crée les JSON d'archive manquants."""
    EM_ARCHIVE.mkdir(parents=True, exist_ok=True)
    saved = 0
    current_year = date.today().year

    # Scraper l'année en cours (/fr/resultats) puis les archives (/fr/archive-resultats-YYYY)
    urls_to_scrape = [
        (current_year, _EM_CURRENT_URL),
    ] + [
        (year, _EM_HISTORY_URL.format(year=year))
        for year in range(current_year - 1, current_year - 25, -1)
    ]

    for year, url in urls_to_scrape:
        if saved >= max_rows:
            break

        print(f"  EuroMillions : scraping {url}…")
        try:
            resp = _session.get(url, headers=_EM_HEADERS, timeout=20)
            if resp.status_code == 404:
                print(f"  EuroMillions : {year} → 404, fin de l'historique")
                break
            resp.raise_for_status()
        except Exception as e:
            print(f"  ⚠ EuroMillions erreur {year} : {e}")
            break

        draws = _parse_em_history_page(resp.text)
        print(f"  EuroMillions {year} : {len(draws)} tirages trouvés")

        for draw in draws:
            if saved >= max_rows:
                break
            archive_path = EM_ARCHIVE / f"{draw['date']}.json"
            if archive_path.exists():
                continue
            atomic_write(archive_path, json.dumps(draw, ensure_ascii=False, indent=2))
            saved += 1

        time.sleep(0.8)  # politesse entre les pages

    return saved


# ── Régénération HTML ─────────────────────────────────────────────────────────

def regenerate_html() -> None:
    """Relit les JSON d'archive et régénère tout le HTML Loto + EuroMillions."""
    today = date.today()

    # Loto
    loto_solution = DOCS_DIR / "loto" / "solution.json"
    if loto_solution.exists():
        data = json.loads(loto_solution.read_text(encoding="utf-8"))
        from games.loto import _generate_all_html as loto_html
        loto_html(date.fromisoformat(data["date"]), data)
        print("✅ HTML Loto régénéré")

    # EuroMillions
    em_solution = DOCS_DIR / "euromillions" / "solution.json"
    if em_solution.exists():
        data = json.loads(em_solution.read_text(encoding="utf-8"))
        from games.euromillions import _generate_all_html as em_html
        em_html(date.fromisoformat(data["date"]), data)
        print("✅ HTML EuroMillions régénéré")

    # Hub + sitemap
    from generate import generate_hub_html, generate_global_sitemap
    cemantix_data = None
    sutom_data = None
    loto_data = None
    em_data = None

    for path, key in [
        (DOCS_DIR / "cemantix" / "solution.json", "cemantix"),
        (DOCS_DIR / "sutom" / "solution.json", "sutom"),
        (DOCS_DIR / "loto" / "solution.json", "loto"),
        (DOCS_DIR / "euromillions" / "solution.json", "euromillions"),
    ]:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            if key == "cemantix":
                cemantix_data = d
            elif key == "sutom":
                sutom_data = d
            elif key == "loto":
                loto_data = d
            elif key == "euromillions":
                em_data = d

    generate_hub_html(today, {
        "cemantix": cemantix_data, "sutom": sutom_data,
        "loto": loto_data, "euromillions": em_data,
    })
    generate_global_sitemap(today)
    print("✅ Hub + sitemap régénérés")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Amorçage de l'historique Loto et EuroMillions")
    parser.add_argument("--loto-max", type=int, default=200,
                        help="Nombre max de tirages Loto à récupérer (défaut: 200)")
    parser.add_argument("--em-max", type=int, default=200,
                        help="Nombre max de tirages EuroMillions à récupérer (défaut: 200)")
    parser.add_argument("--loto-only", action="store_true", help="Loto uniquement")
    parser.add_argument("--em-only", action="store_true", help="EuroMillions uniquement")
    parser.add_argument("--no-html", action="store_true",
                        help="Ne pas régénérer le HTML (JSON seulement)")
    args = parser.parse_args()

    print(f"\n=== Seed Archives ===\n")

    loto_saved = 0
    em_saved = 0

    if not args.em_only:
        print(f"─── Loto (max {args.loto_max} tirages) ─────────────────────")
        loto_saved = seed_loto(args.loto_max)
        print(f"  → {loto_saved} nouveaux tirages Loto sauvegardés\n")

    if not args.loto_only:
        print(f"─── EuroMillions (max {args.em_max} tirages) ──────────────")
        em_saved = seed_euromillions(args.em_max)
        print(f"  → {em_saved} nouveaux tirages EuroMillions sauvegardés\n")

    if not args.no_html and (loto_saved > 0 or em_saved > 0):
        print("─── Régénération HTML ──────────────────────────────────")
        regenerate_html()

    print(f"\n🎉 Terminé : {loto_saved} Loto + {em_saved} EuroMillions ajoutés")
    loto_count = len(list(LOTO_ARCHIVE.glob("????-??-??.json"))) if LOTO_ARCHIVE.exists() else 0
    em_count = len(list(EM_ARCHIVE.glob("????-??-??.json"))) if EM_ARCHIVE.exists() else 0
    print(f"   Total archives : {loto_count} Loto, {em_count} EuroMillions\n")


if __name__ == "__main__":
    main()
