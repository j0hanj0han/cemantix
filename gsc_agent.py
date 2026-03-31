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


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent d'analyse Google Search Console")
    parser.add_argument("--days", type=int, default=30, help="Fenêtre d'analyse en jours (défaut: 30)")
    parser.add_argument("--output", default="gsc_report.md", help="Fichier de sortie Markdown")
    args = parser.parse_args()

    print("Authentification Google Search Console…")
    service = _authenticate()
    print("✅ Authentifié\n")

    generate_report(service, days=args.days, output=args.output)


if __name__ == "__main__":
    main()
