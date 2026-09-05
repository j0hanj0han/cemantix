"""
gsc_agent.py — Agent d'analyse Google Search Console.

Interroge l'API Search Console pour identifier les opportunités SEO :
- Quick wins (position 5-20, impressions > 5)
- Pages à fort potentiel mais faible CTR
- Rapport complet en Markdown

Usage :
  python gsc_agent.py [--days 30] [--output gsc_report.md]

Setup (une fois) :
  1. Google Cloud Console → activer "Google Search Console API"
  2. Créer identifiants OAuth2 Desktop → télécharger credentials.json à la racine du projet
  3. pip install google-auth-oauthlib google-api-python-client
  4. python gsc_agent.py  ← premier lancement : fenêtre OAuth dans le navigateur
     Le token est sauvegardé dans gsc_token.json pour les appels suivants.
"""

import argparse
import json
import random
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path


SITE_URL = "sc-domain:solution-du-jour.fr"  # format "sc-domain:" pour domaine vérifié
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "gsc_token.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


# ── Auth ──────────────────────────────────────────────────────────────────────

def _authenticate():
    """OAuth2 flow. Retourne un service Google Search Console."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit(
            "Dépendances manquantes. Lance :\n"
            "  pip install google-auth-oauthlib google-api-python-client"
        )

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise SystemExit(
                    f"Fichier {CREDENTIALS_FILE} introuvable.\n"
                    "Télécharge les identifiants OAuth2 depuis Google Cloud Console\n"
                    "(APIs & Services → Identifiants → OAuth 2.0 → Application de bureau)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("searchconsole", "v1", credentials=creds)


# ── Requêtes API ──────────────────────────────────────────────────────────────

def _query(
    service, start_date: str, end_date: str, dimensions: list,
    row_limit: int = 25000, start_row: int = 0, filters: list | None = None,
) -> list:
    """Lance une requête Search Console (une page de résultats) et retourne les rows.

    row_limit : 25000 = maximum accepté par l'API GSC.
    start_row : offset de pagination (voir _query_all pour la boucle complète).
    filters : dimensionFilterGroups optionnel (ex. filtrer sur une page/query précise).
    """
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "startRow": start_row,
        "dataState": "final",
    }
    if filters:
        body["dimensionFilterGroups"] = filters
    response = service.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
    return response.get("rows", [])


def _query_all(
    service, start_date: str, end_date: str, dimensions: list,
    row_limit: int = 25000, filters: list | None = None,
) -> list:
    """Pagine sur _query jusqu'à épuisement (fix du plafond à 500 lignes qui faussait
    les totaux : 684 clics affichés vs 2 144 réels sur la période de référence 2026-09-01)."""
    rows: list = []
    start_row = 0
    while True:
        batch = _query(service, start_date, end_date, dimensions,
                        row_limit=row_limit, start_row=start_row, filters=filters)
        rows.extend(batch)
        if len(batch) < row_limit:
            break
        start_row += row_limit
    return rows


def get_totals(service, start_date: str, end_date: str) -> dict:
    """Totaux agrégés directement par GSC (dimensions=[]) — plus fiable qu'une somme
    manuelle sur des lignes potentiellement tronquées."""
    rows = _query_all(service, start_date, end_date, [])
    if not rows:
        return {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
    r = rows[0]
    return {
        "clicks": r.get("clicks", 0),
        "impressions": r.get("impressions", 0),
        "ctr": round(r.get("ctr", 0) * 100, 2),
        "position": round(r.get("position", 0), 1),
    }


def get_breakdown(service, start_date: str, end_date: str, dimension: str) -> list[dict]:
    """Répartition clics/impressions par 'device' ou 'country', triée par clics DESC."""
    if dimension not in ("device", "country"):
        raise ValueError("dimension doit être 'device' ou 'country'")
    rows = _query_all(service, start_date, end_date, [dimension])
    result = []
    for r in rows:
        result.append({
            dimension: r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        })
    return sorted(result, key=lambda x: x["clicks"], reverse=True)


def get_weekly_trend(service, weeks: int = 8) -> list[dict]:
    """Tendance sur les `weeks` dernières semaines glissantes de 7 jours (ancrées sur
    aujourd'hui - 3 jours, latence connue de GSC). Chaque entrée porte un delta_pct
    (clics vs semaine précédente), None pour la première semaine."""
    anchor = date.today() - timedelta(days=3)
    results = []
    for i in range(weeks):
        w_end = anchor - timedelta(days=7 * i)
        w_start = w_end - timedelta(days=6)
        totals = get_totals(service, w_start.isoformat(), w_end.isoformat())
        results.append({"start": w_start.isoformat(), "end": w_end.isoformat(), **totals})
    results.reverse()  # ordre chronologique croissant
    for i, week in enumerate(results):
        if i == 0:
            week["delta_pct"] = None
            continue
        prev_clicks = results[i - 1]["clicks"]
        week["delta_pct"] = round((week["clicks"] - prev_clicks) / prev_clicks * 100, 1) if prev_clicks else None
    return results


GAME_SLUGS = ("cemantix", "sutom", "pedantix", "loto", "euromillions")
EVERGREEN_SEGMENTS = ("comment-jouer", "astuces", "meilleurs-mots", "statistiques", "indice")


def _classify_page(url: str) -> tuple[str, str] | None:
    """Classe une URL de page en (jeu, type). Retourne None si hors périmètre des 5 jeux."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = path.split("/") if path else []
    if not parts or parts[0] not in GAME_SLUGS:
        return None
    game = parts[0]
    rest = parts[1:]
    if not rest:
        return game, "index"
    if rest[0] == "archive":
        if len(rest) == 1:
            return game, "archive_index"
        d = rest[1]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            return game, "archive_jour"
        if re.fullmatch(r"\d{4}-\d{2}", d):
            return game, "archive_mois"
        if re.fullmatch(r"\d{4}", d):
            return game, "archive_annee"
        return game, "archive_autre"
    if rest[0] in ("simulateur", "stats"):
        return game, "outil"
    if rest[0] in EVERGREEN_SEGMENTS:
        return game, "evergreen"
    return game, "autre"


def get_game_aggregates(pages: list[dict]) -> dict:
    """Agrège clics/impressions/position (pondérée par impressions) par jeu et par type
    de page, à partir d'une liste de pages issue de _query_all(..., ["page"])."""
    agg: dict[str, dict[str, dict]] = {}
    for p in pages:
        classified = _classify_page(p["page"])
        if not classified:
            continue
        game, ptype = classified
        bucket = agg.setdefault(game, {}).setdefault(
            ptype, {"clicks": 0, "impressions": 0, "_pos_weighted": 0.0}
        )
        bucket["clicks"] += p["clicks"]
        bucket["impressions"] += p["impressions"]
        bucket["_pos_weighted"] += p["position"] * p["impressions"]
    for types in agg.values():
        for b in types.values():
            impr = b["impressions"]
            b["ctr"] = round(b["clicks"] / impr * 100, 2) if impr else 0.0
            b["position"] = round(b["_pos_weighted"] / impr, 1) if impr else 0.0
            del b["_pos_weighted"]
    return agg


def get_top_queries(service, days: int = 30) -> list[dict]:
    """Toutes les requêtes avec au moins 1 impression, triées par impressions DESC."""
    end = date.today() - timedelta(days=3)  # GSC a ~3 jours de latence
    start = end - timedelta(days=days)
    rows = _query_all(service, start.isoformat(), end.isoformat(), ["query"])
    result = []
    for r in rows:
        result.append({
            "query": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 1),
            "position": round(r.get("position", 0), 1),
        })
    return sorted(result, key=lambda x: x["impressions"], reverse=True)


def get_top_pages(service, days: int = 30) -> list[dict]:
    """Pages avec au moins 1 impression, triées par impressions DESC."""
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=days)
    rows = _query_all(service, start.isoformat(), end.isoformat(), ["page"])
    result = []
    for r in rows:
        result.append({
            "page": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 1),
            "position": round(r.get("position", 0), 1),
        })
    return sorted(result, key=lambda x: x["impressions"], reverse=True)


def get_quick_wins(queries: list[dict], min_impressions: int = 5) -> list[dict]:
    """Position 5-20 + impressions >= min_impressions → quick wins."""
    return [
        q for q in queries
        if 5 <= q["position"] <= 20 and q["impressions"] >= min_impressions
    ]


def get_low_ctr_pages(pages: list[dict], min_impressions: int = 10, max_ctr: float = 3.0) -> list[dict]:
    """Pages avec beaucoup d'impressions mais CTR faible → améliorer title/description."""
    return [
        p for p in pages
        if p["impressions"] >= min_impressions and p["ctr"] < max_ctr
    ]


# ── Indexation ────────────────────────────────────────────────────────────────

def get_sitemaps_coverage(service) -> list[dict]:
    """Couverture par sitemap soumis : pages soumises, indexées, erreurs, warnings."""
    response = service.sitemaps().list(siteUrl=SITE_URL).execute()
    result = []
    for s in response.get("sitemap", []):
        submitted = indexed = 0
        for content in s.get("contents", []):
            submitted += int(content.get("submitted", 0))
            indexed += int(content.get("indexed", 0))
        result.append({
            "path": s.get("path", ""),
            "lastDownloaded": s.get("lastDownloaded", "")[:10],
            "errors": int(s.get("errors", 0)),
            "warnings": int(s.get("warnings", 0)),
            "submitted": submitted,
            "indexed": indexed,
        })
    return result


def _get_sitemap_urls() -> list[tuple[str, str]]:
    """Lit toutes les URLs de pages depuis docs/sitemap.xml (fichier local), en (url, slug).

    Depuis PR1, sitemap.xml est un <sitemapindex> : on suit chaque sous-sitemap
    listé (sitemap-cemantix.xml, etc.) et on agrège leurs URLs. `slug` = nom du
    sous-sitemap sans préfixe/suffixe (ex. "cemantix", "pages") — pratique pour
    échantillonner par jeu (voir sample_urls_per_game).
    """
    docs_dir = Path(__file__).parent / "docs"
    sitemap_path = docs_dir / "sitemap.xml"
    if not sitemap_path.exists():
        return []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.parse(sitemap_path).getroot()
    tag = root.tag.split("}")[-1]
    if tag != "sitemapindex":
        return [(loc.text.strip(), "site") for loc in root.findall(".//sm:loc", ns)]
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        sub_path = docs_dir / Path(loc.text.strip()).name
        if not sub_path.exists():
            continue
        slug = sub_path.stem.removeprefix("sitemap-") or sub_path.stem
        sub_root = ET.parse(sub_path).getroot()
        urls.extend((l.text.strip(), slug) for l in sub_root.findall(".//sm:loc", ns))
    return urls


def sample_urls_per_game(per_game: int, seed: int | None = None) -> list[str]:
    """Échantillon stratifié par jeu (récent / milieu / ancien) pour l'inspection URL,
    en respectant le quota API (2000/jour, 1 req/s — voir inspect_url/get_indexation_issues).
    Les pages sans date (index, outils, evergreen) reçoivent une petite part réservée.
    """
    rng = random.Random(seed)
    by_game: dict[str, list[str]] = {}
    for url, slug in _get_sitemap_urls():
        by_game.setdefault(slug, []).append(url)

    date_re = re.compile(r"/(\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|\d{4})$")
    result: list[str] = []
    for slug, urls in by_game.items():
        dated = sorted((u for u in urls if date_re.search(u)), key=lambda u: date_re.search(u).group(1))
        undated = [u for u in urls if not date_re.search(u)]

        n_undated = min(len(undated), max(1, per_game // 5)) if undated else 0
        n_dated = per_game - n_undated

        picked: list[str] = []
        if dated and n_dated > 0:
            third = max(1, n_dated // 3)
            picked += dated[:third]                                   # anciennes
            mid = len(dated) // 2
            picked += dated[max(0, mid - third // 2): max(0, mid - third // 2) + third]  # milieu
            picked += dated[-third:]                                  # récentes
            picked = list(dict.fromkeys(picked))[:n_dated]

        if undated and n_undated:
            picked += rng.sample(undated, min(n_undated, len(undated)))

        result.extend(picked[:per_game])
    return result


def inspect_url(service, url: str) -> dict:
    """Inspecte une URL via l'API URL Inspection. 1 req/s, 2000 req/jour max."""
    try:
        result = service.urlInspection().index().inspect(body={
            "inspectionUrl": url,
            "siteUrl": SITE_URL,
        }).execute()
        isr = result.get("inspectionResult", {}).get("indexStatusResult", {})
        return {
            "url": url,
            "verdict": isr.get("verdict", "UNKNOWN"),
            "coverageState": isr.get("coverageState", ""),
            "robotsTxtState": isr.get("robotsTxtState", ""),
            "indexingState": isr.get("indexingState", ""),
            "lastCrawlTime": (isr.get("lastCrawlTime") or "")[:10],
        }
    except Exception as e:
        return {
            "url": url,
            "verdict": "ERROR",
            "coverageState": str(e)[:80],
            "robotsTxtState": "",
            "indexingState": "",
            "lastCrawlTime": "",
        }


def get_indexation_issues(
    service, known_pages: list[dict], max_urls: int = 50, per_game: int | None = None,
) -> list[dict]:
    """
    Croise le sitemap local avec les pages GSC pour trouver les URLs non vues,
    puis les inspecte via l'API URL Inspection (rate-limited à 1 req/s).

    Si `per_game` est fourni, l'échantillon à inspecter est stratifié par jeu
    (voir sample_urls_per_game) plutôt que les N premières URLs du sitemap.
    """
    sitemap_urls = [u for u, _slug in _get_sitemap_urls()]
    seen_pages = {p["page"] for p in known_pages}
    # Priorité : URLs dans le sitemap mais jamais vues dans Search Analytics
    unindexed_candidates = [u for u in sitemap_urls if u not in seen_pages]

    if per_game is not None:
        sample = sample_urls_per_game(per_game)
        # Priorise les candidats sans impressions, complète avec le reste de l'échantillon
        to_inspect = [u for u in sample if u in set(unindexed_candidates)]
        to_inspect += [u for u in sample if u not in to_inspect]
    else:
        to_inspect = unindexed_candidates[:max_urls]

    print(f"  {len(sitemap_urls)} URLs dans le sitemap · {len(seen_pages)} pages vues dans GSC")
    print(f"  {len(unindexed_candidates)} URLs candidates (0 impressions) → inspection de {len(to_inspect)}")

    results = []
    for i, url in enumerate(to_inspect, 1):
        print(f"  [{i}/{len(to_inspect)}] {url}", end="\r")
        results.append(inspect_url(service, url))
        time.sleep(1.1)  # API limit : 1 req/s
    print()
    return results


# ── Rapport Markdown ──────────────────────────────────────────────────────────

def _table(headers: list[str], rows: list[dict], keys: list[str], max_rows: int = 20) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "|" + "|".join(["---"] * len(headers)) + "|"
    lines = [header_line, sep_line]
    for row in rows[:max_rows]:
        lines.append("| " + " | ".join(str(row.get(k, "")) for k in keys) + " |")
    if len(rows) > max_rows:
        lines.append(f"_… {len(rows) - max_rows} autres résultats non affichés_")
    return "\n".join(lines)


def generate_report(service, days: int = 30, output: str = "gsc_report.md") -> None:
    """Génère un rapport Markdown complet des opportunités SEO."""
    today = date.today()
    end = today - timedelta(days=3)
    start = end - timedelta(days=days)

    print(f"Récupération des données GSC ({start} → {end})…")
    queries = get_top_queries(service, days)
    pages = get_top_pages(service, days)
    quick_wins = get_quick_wins(queries)
    low_ctr = get_low_ctr_pages(pages)

    print("Récupération des totaux réels (dimensions=[])…")
    totals = get_totals(service, start.isoformat(), end.isoformat())

    print("Récupération de la tendance sur 8 semaines…")
    trend = get_weekly_trend(service, weeks=8)

    print("Récupération de la répartition device/pays…")
    devices = get_breakdown(service, start.isoformat(), end.isoformat(), "device")
    countries = get_breakdown(service, start.isoformat(), end.isoformat(), "country")

    print("Agrégation par jeu…")
    game_aggregates = get_game_aggregates(pages)

    lines = [
        f"# Rapport GSC — {today.isoformat()}",
        f"_Période analysée : {start} → {end} ({days} jours)_",
        f"_Site : solution-du-jour.fr_",
        "",
        "---",
        "",
        "## Totaux réels",
        "",
        "_Agrégés directement par GSC (dimensions=[]), sans risque de troncature — "
        "fiable même si le nombre de requêtes/pages dépasse la pagination._",
        "",
        f"| Métrique | Valeur |",
        f"|---|---|",
        f"| Clics totaux | {totals['clicks']:,} |",
        f"| Impressions totales | {totals['impressions']:,} |",
        f"| CTR moyen | {totals['ctr']}% |",
        f"| Position moyenne | {totals['position']} |",
        f"| Requêtes uniques | {len(queries)} |",
        f"| Pages actives | {len(pages)} |",
        "",
    ]

    # Tendance 8 semaines
    lines += [
        "---",
        "",
        "## Tendance 8 semaines",
        "",
        _table(
            ["Semaine (lun-dim)", "Clics", "Impressions", "CTR", "Position", "Δ clics vs sem. préc."],
            [
                {**w, "week": f"{w['start']} → {w['end']}",
                 "delta_pct": "—" if w["delta_pct"] is None else f"{w['delta_pct']:+.1f}%"}
                for w in trend
            ],
            ["week", "clicks", "impressions", "ctr", "position", "delta_pct"],
            max_rows=8,
        ),
        "",
    ]

    # Device / Pays
    lines += [
        "---",
        "",
        "## Répartition Device / Pays",
        "",
        "### Par device",
        "",
        _table(["Device", "Clics", "Impressions", "CTR", "Position"], devices,
               ["device", "clicks", "impressions", "ctr", "position"], max_rows=10),
        "",
        "### Par pays (top 10)",
        "",
        _table(["Pays", "Clics", "Impressions", "CTR", "Position"], countries,
               ["country", "clicks", "impressions", "ctr", "position"], max_rows=10),
        "",
    ]

    # Par jeu
    lines += ["---", "", "## Par jeu", ""]
    if game_aggregates:
        for game in sorted(game_aggregates):
            lines.append(f"### {game.capitalize()}")
            lines.append("")
            rows = [{"type": ptype, **stats} for ptype, stats in game_aggregates[game].items()]
            rows.sort(key=lambda r: r["impressions"], reverse=True)
            lines.append(_table(
                ["Type de page", "Clics", "Impressions", "CTR", "Position"],
                rows, ["type", "clicks", "impressions", "ctr", "position"], max_rows=15,
            ))
            lines.append("")
    else:
        lines.append("_Aucune page reconnue parmi les 5 jeux dans la période analysée._")
        lines.append("")

    # Quick wins
    lines += [
        "---",
        "",
        f"## 🎯 Quick wins — position 5-20 ({len(quick_wins)} requêtes)",
        "",
        "_Ces requêtes sont proches du top 3. Améliorer le contenu ciblant ces mots-clés peut doubler les clics._",
        "",
    ]
    if quick_wins:
        lines.append(_table(
            ["Requête", "Position", "Impressions", "Clics", "CTR"],
            quick_wins,
            ["query", "position", "impressions", "clicks", "ctr"],
        ))
    else:
        lines.append("_Aucun quick win identifié (pas encore assez de données)._")
    lines.append("")

    # Top requêtes
    lines += [
        "---",
        "",
        "## 📊 Top 30 requêtes (par impressions)",
        "",
        _table(
            ["Requête", "Impressions", "Clics", "CTR", "Position"],
            queries[:30],
            ["query", "impressions", "clicks", "ctr", "position"],
        ),
        "",
    ]

    # Pages faible CTR
    lines += [
        "---",
        "",
        f"## ⚠️ Pages à fort potentiel, CTR faible (<3%) — {len(low_ctr)} pages",
        "",
        "_Ces pages apparaissent souvent mais peu de personnes cliquent. Améliorer title + meta description._",
        "",
    ]
    if low_ctr:
        lines.append(_table(
            ["Page", "Impressions", "CTR", "Position"],
            low_ctr,
            ["page", "impressions", "ctr", "position"],
        ))
    else:
        lines.append("_Aucune page problématique identifiée._")
    lines.append("")

    # Top pages
    lines += [
        "---",
        "",
        "## 📄 Top pages (par impressions)",
        "",
        _table(
            ["Page", "Impressions", "Clics", "CTR", "Position"],
            pages[:20],
            ["page", "impressions", "clicks", "ctr", "position"],
        ),
        "",
        "---",
        "",
        f"_Généré par gsc_agent.py le {today.isoformat()}_",
    ]

    report = "\n".join(lines) + "\n"
    Path(output).write_text(report, encoding="utf-8")
    print(f"\n✅ Rapport sauvegardé : {output}")
    print(f"   {total_clicks} clics · {total_impressions} impressions · {len(quick_wins)} quick wins · {len(low_ctr)} pages à améliorer")


# ── Rapport indexation ────────────────────────────────────────────────────────

def generate_indexation_report(
    service, output: str = "gsc_indexation.md", max_urls: int = 50, per_game: int | None = None,
) -> None:
    """Rapport dédié aux erreurs d'indexation : couverture sitemaps + inspection URL."""
    today = date.today()

    print("Récupération de la couverture des sitemaps…")
    sitemaps = get_sitemaps_coverage(service)

    print("Récupération des pages connues de GSC (90 jours)…")
    known_pages = get_top_pages(service, days=90)

    if per_game is not None:
        print(f"Inspection des URLs (échantillon stratifié, {per_game}/jeu)…")
    else:
        print(f"Inspection des URLs non indexées (max {max_urls})…")
    issues = get_indexation_issues(service, known_pages, max_urls=max_urls, per_game=per_game)

    # Classement des issues par verdict
    verdict_order = {"FAIL": 0, "NEUTRAL": 1, "UNKNOWN": 2, "ERROR": 3, "PASS": 4}
    issues_sorted = sorted(issues, key=lambda x: verdict_order.get(x["verdict"], 5))

    fail_count = sum(1 for i in issues_sorted if i["verdict"] == "FAIL")
    neutral_count = sum(1 for i in issues_sorted if i["verdict"] == "NEUTRAL")
    pass_count = sum(1 for i in issues_sorted if i["verdict"] == "PASS")

    lines = [
        f"# Rapport Indexation GSC — {today.isoformat()}",
        f"_Site : solution-du-jour.fr_",
        "",
        "---",
        "",
        "## Couverture des sitemaps soumis",
        "",
        _table(
            ["Sitemap", "Dernière lecture", "Soumises", "Indexées", "Erreurs", "Warnings"],
            sitemaps,
            ["path", "lastDownloaded", "submitted", "indexed", "errors", "warnings"],
            max_rows=20,
        ),
        "",
        "---",
        "",
        f"## Inspection des URLs sans impressions (90j)",
        "",
        f"**{fail_count} erreurs · {neutral_count} neutres · {pass_count} indexées** sur {len(issues_sorted)} URLs inspectées",
        "",
    ]

    if issues_sorted:
        lines.append(_table(
            ["URL", "Verdict", "État couverture", "Dernier crawl", "Indexing state"],
            issues_sorted,
            ["url", "verdict", "coverageState", "lastCrawlTime", "indexingState"],
            max_rows=100,
        ))
    else:
        lines.append("_Toutes les URLs du sitemap ont des impressions GSC — aucune inspection nécessaire._")

    lines += [
        "",
        "---",
        "",
        "### Légende verdicts",
        "- **PASS** : indexée et visible dans Google",
        "- **NEUTRAL** : crawlée mais non indexée (doublon, noindex, etc.)",
        "- **FAIL** : erreur d'indexation (page introuvable, bloquée, etc.)",
        "",
        f"_Généré par gsc_agent.py le {today.isoformat()} — {len(issues_sorted)} URLs inspectées_",
    ]

    report = "\n".join(lines) + "\n"
    Path(output).write_text(report, encoding="utf-8")
    print(f"\n✅ Rapport indexation sauvegardé : {output}")
    print(f"   {fail_count} erreurs · {neutral_count} neutres · {pass_count} OK sur {len(issues_sorted)} URLs")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent d'analyse Google Search Console")
    parser.add_argument("--days", type=int, default=30, help="Fenêtre d'analyse en jours (défaut: 30)")
    parser.add_argument("--output", default="gsc_report.md", help="Fichier de sortie Markdown")
    parser.add_argument("--indexation", action="store_true", help="Mode indexation : couverture sitemaps + inspection URLs")
    parser.add_argument("--per-game", type=int, default=None,
                        help="Mode indexation : échantillon stratifié (récent/milieu/ancien) de N URLs par jeu, "
                             "remplace --max-urls")
    parser.add_argument("--max-urls", type=int, default=50,
                        help="Mode indexation : nb max d'URLs candidates à inspecter si --per-game n'est pas fourni (défaut: 50)")
    args = parser.parse_args()

    print("Authentification Google Search Console…")
    service = _authenticate()
    print("✅ Authentifié\n")

    if args.indexation:
        out = args.output if args.output != "gsc_report.md" else "gsc_indexation.md"
        generate_indexation_report(service, output=out, max_urls=args.max_urls, per_game=args.per_game)
    else:
        generate_report(service, days=args.days, output=args.output)


if __name__ == "__main__":
    main()
