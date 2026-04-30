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
import time
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

def _query(service, start_date: str, end_date: str, dimensions: list, row_limit: int = 500) -> list:
    """Lance une requête Search Console et retourne les rows."""
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "dataState": "final",
    }
    response = service.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
    return response.get("rows", [])


def get_top_queries(service, days: int = 30) -> list[dict]:
    """Toutes les requêtes avec au moins 1 impression, triées par impressions DESC."""
    end = date.today() - timedelta(days=3)  # GSC a ~3 jours de latence
    start = end - timedelta(days=days)
    rows = _query(service, start.isoformat(), end.isoformat(), ["query"])
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
    rows = _query(service, start.isoformat(), end.isoformat(), ["page"])
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


def _get_sitemap_urls() -> list[str]:
    """Lit toutes les URLs depuis docs/sitemap.xml (fichier local)."""
    sitemap_path = Path(__file__).parent / "docs" / "sitemap.xml"
    if not sitemap_path.exists():
        return []
    root = ET.parse(sitemap_path).getroot()
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [loc.text.strip() for loc in root.findall(".//sm:loc", ns)]


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


def get_indexation_issues(service, known_pages: list[dict], max_urls: int = 50) -> list[dict]:
    """
    Croise le sitemap local avec les pages GSC pour trouver les URLs non vues,
    puis les inspecte via l'API URL Inspection (rate-limited à 1 req/s).
    """
    sitemap_urls = _get_sitemap_urls()
    seen_pages = {p["page"] for p in known_pages}
    # Priorité : URLs dans le sitemap mais jamais vues dans Search Analytics
    unindexed_candidates = [u for u in sitemap_urls if u not in seen_pages]
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

    total_clicks = sum(q["clicks"] for q in queries)
    total_impressions = sum(q["impressions"] for q in queries)
    avg_ctr = round(total_clicks / total_impressions * 100, 1) if total_impressions else 0
    avg_pos = round(sum(q["position"] for q in queries) / len(queries), 1) if queries else 0

    lines = [
        f"# Rapport GSC — {today.isoformat()}",
        f"_Période analysée : {start} → {end} ({days} jours)_",
        f"_Site : solution-du-jour.fr_",
        "",
        "---",
        "",
        "## Vue d'ensemble",
        "",
        f"| Métrique | Valeur |",
        f"|---|---|",
        f"| Clics totaux | {total_clicks:,} |",
        f"| Impressions totales | {total_impressions:,} |",
        f"| CTR moyen | {avg_ctr}% |",
        f"| Position moyenne | {avg_pos} |",
        f"| Requêtes uniques | {len(queries)} |",
        f"| Pages indexées actives | {len(pages)} |",
        "",
    ]

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

def generate_indexation_report(service, output: str = "gsc_indexation.md", max_urls: int = 50) -> None:
    """Rapport dédié aux erreurs d'indexation : couverture sitemaps + inspection URL."""
    today = date.today()

    print("Récupération de la couverture des sitemaps…")
    sitemaps = get_sitemaps_coverage(service)

    print("Récupération des pages connues de GSC (90 jours)…")
    known_pages = get_top_pages(service, days=90)

    print(f"Inspection des URLs non indexées (max {max_urls})…")
    issues = get_indexation_issues(service, known_pages, max_urls=max_urls)

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
    parser.add_argument("--max-urls", type=int, default=50, help="Nb max d'URLs à inspecter en mode --indexation (défaut: 50)")
    args = parser.parse_args()

    print("Authentification Google Search Console…")
    service = _authenticate()
    print("✅ Authentifié\n")

    if args.indexation:
        out = args.output if args.output != "gsc_report.md" else "gsc_indexation.md"
        generate_indexation_report(service, output=out, max_urls=args.max_urls)
    else:
        generate_report(service, days=args.days, output=args.output)


if __name__ == "__main__":
    main()
