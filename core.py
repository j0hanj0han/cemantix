"""
core.py — Utilitaires partagés pour tous les jeux.
"""

from datetime import date
from pathlib import Path

import cloudscraper

# ── Configuration globale ─────────────────────────────────────────────────────

SITE_URL = "https://j0hanj0han.github.io/cemantix"
DOCS_DIR = Path("docs")

MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

# Session cloudscraper partagée (gère les défis Cloudflare JS)
_session = cloudscraper.create_scraper()


# ── Helpers ───────────────────────────────────────────────────────────────────

def date_fr(d: date) -> str:
    """Retourne une date en français : '28 février 2026'."""
    return f"{d.day} {MONTHS_FR[d.month]} {d.year}"


def atomic_write(path: Path, content: str) -> None:
    """Écriture atomique : écrit dans .tmp puis renomme."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


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
