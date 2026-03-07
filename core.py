"""
core.py — Utilitaires partagés pour tous les jeux.
"""

import urllib.request
from datetime import date
from pathlib import Path

import cloudscraper

# ── Configuration globale ─────────────────────────────────────────────────────

SITE_URL = "https://solution-du-jour.fr"
DOCS_DIR = Path("docs")

MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

DAYS_FR = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]

# Session cloudscraper partagée (gère les défis Cloudflare JS)
_session = cloudscraper.create_scraper()


# ── Helpers ───────────────────────────────────────────────────────────────────

def date_fr(d: date) -> str:
    """Retourne une date en français : 'samedi 28 février 2026'."""
    return f"{DAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month]} {d.year}"


def atomic_write(path: Path, content: str) -> None:
    """Écriture atomique : écrit dans .tmp puis renomme."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def fetch_static_html(url: str, timeout: int = 15) -> str | None:
    """Télécharge une page HTML statique (sans JS rendering). Retourne le contenu ou None."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; solution-du-jour/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"   ⚠ fetch_static_html({url}) : {e}")
        return None


def jackpot_html(jackpot_won: bool | None, jackpot_winners: int, jackpot_amount: float | None) -> str:
    """Retourne le HTML du bloc jackpot pour Loto ou EuroMillions.
    Si jackpot_won is None, retourne ''. Si jackpot_amount est inconnu, affiche juste le statut.
    """
    if jackpot_won is None:
        return ""
    if jackpot_won:
        label = "gagnant" if jackpot_winners == 1 else "gagnants"
        status = (
            f'<span style="color:#16a34a;font-weight:600;">'
            f'Jackpot remport\u00e9 \u2014 {jackpot_winners}\u202f{label}</span>'
        )
    else:
        status = '<span style="color:#6b7280;">Jackpot non remport\u00e9 \u2014 report\u00e9</span>'
    if jackpot_amount is not None:
        amount_str = f"{jackpot_amount:,.0f}".replace(",", "\u202f") + "\u202f\u20ac"
        return (
            f'      <p class="puzzle-meta" style="margin-top:.5rem;">'
            f'Jackpot\u202f: <strong>{amount_str}</strong> \u00b7 {status}</p>'
        )
    return f'      <p class="puzzle-meta" style="margin-top:.5rem;">{status}</p>'


def load_all_archives(archive_dir: Path, required_keys: list[str] | None = None) -> list[dict]:
    """
    Charge tous les fichiers JSON d'un dossier archive (pattern YYYY-MM-DD.json).
    Retourne une liste triée par date DESC.
    required_keys : clés JSON obligatoires (défaut : ["date", "word"]).
    """
    import json
    if required_keys is None:
        required_keys = ["date", "word"]
    entries = []
    if archive_dir.exists():
        for f in archive_dir.glob("????-??-??.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if all(k in data for k in required_keys):
                    entries.append(data)
            except Exception:
                pass
    entries.sort(key=lambda x: x["date"], reverse=True)
    return entries
