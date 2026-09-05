"""
core.py — Utilitaires partagés pour tous les jeux.
"""

import json
import re
import time
import unicodedata
import urllib.request
from datetime import date, datetime
from html import escape as _html_escape
from pathlib import Path
from zoneinfo import ZoneInfo

import cloudscraper

# ── Configuration globale ─────────────────────────────────────────────────────

SITE_URL = "https://solution-du-jour.fr"
DOCS_DIR = Path("docs")

PARIS_TZ = ZoneInfo("Europe/Paris")

# Clé IndexNow — non secrète, doit juste correspondre à docs/<INDEXNOW_KEY>.txt
INDEXNOW_KEY = "a13f0c2b6e4d4f9a8c1b7e2d5a90c3f1"

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


def date_fr_short(d: date) -> str:
    """Retourne une date en français sans le jour de semaine : '1er septembre 2026', '3 septembre 2026'."""
    day = "1er" if d.day == 1 else str(d.day)
    return f"{day} {MONTHS_FR[d.month]} {d.year}"


def updated_block(dt_iso: str) -> str:
    """Bloc 'Mis à jour le ... à HHhMM' sous un H1 de page index (jamais sur une archive)."""
    dt = datetime.fromisoformat(dt_iso)
    label = f"{date_fr(dt.date())} à {dt.hour:02d}h{dt.minute:02d}"
    return f'  <p class="updated-block"><time datetime="{dt_iso}">Mis à jour le {label}</time></p>'


def iso_paris(d: date, hh: int, mm: int) -> str:
    """ISO 8601 avec offset Paris correct (+01:00 CET / +02:00 CEST selon la saison)."""
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=PARIS_TZ).isoformat()


def utc_iso_to_paris(s: str) -> str:
    """Convertit un ISO 8601 (UTC, avec 'Z' ou offset) vers l'heure de Paris."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(PARIS_TZ).isoformat()


FEED_LINK_TAG = f'  <link rel="alternate" type="application/atom+xml" title="Solutions du Jour" href="{SITE_URL}/feed.xml">'


def ping_indexnow(urls: list[str]) -> bool:
    """Notifie IndexNow (Bing, Yandex, Seznam...) qu'une liste d'URLs a changé.
    Best-effort : ne lève jamais d'exception, retourne False en cas d'échec."""
    if not urls:
        return False
    payload = json.dumps({
        "host": SITE_URL.split("//", 1)[1],
        "key": INDEXNOW_KEY,
        "keyLocation": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://api.indexnow.org/indexnow",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"   IndexNow : {resp.status}")
            return 200 <= resp.status < 300
    except Exception as e:
        print(f"   ⚠ ping_indexnow : {e}")
        return False


def faq_jsonld(items: list[tuple[str, str]]) -> str:
    """Bloc <script type="application/ld+json"> FAQPage à partir de paires (question, réponse)."""
    payload = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in items
        ],
    }
    return (
        '  <script type="application/ld+json">\n'
        f'  {json.dumps(payload, ensure_ascii=False)}\n'
        '  </script>'
    )


def faq_html(items: list[tuple[str, str]], *, open_first: bool = False) -> str:
    """FAQ visible en <details> — open_first=True ouvre la 1re question par défaut (archives)."""
    if not items:
        return ""
    rows = []
    for i, (q, a) in enumerate(items):
        open_attr = " open" if (open_first and i == 0) else ""
        rows.append(
            f'      <details{open_attr}>\n'
            f'        <summary>{_html_escape(q)}</summary>\n'
            f'        <p>{_html_escape(a)}</p>\n'
            f'      </details>'
        )
    rows_html = "\n".join(rows)
    return (
        '\n    <div class="card faq-card">'
        '\n      <h2>Questions fréquentes</h2>'
        f'\n{rows_html}'
        '\n    </div>'
    )


def hint_levels_html(levels: list[tuple[str, str, str]], mode: str) -> str:
    """Rend les niveaux d'indices progressifs.
    levels : liste de (titre, description, mots_html), un triplet par niveau.
    mode "details" : <details> natifs indépendants (page du jour, cliquables sans spoiler global).
    mode "plain"   : texte visible directement, sans interaction (archives passées).
    """
    parts = []
    for title, desc, words in levels:
        words_html = words or "<em>Aucun indice disponible</em>"
        if mode == "details":
            parts.append(
                '\n      <div class="hint-level hint-level-native">'
                '\n        <details>'
                f'\n          <summary class="hint-btn">{title}</summary>'
                '\n          <div class="hint-content-native">'
                f'\n            <p>{desc}</p>'
                f'\n            <div class="hint-words">{words_html}</div>'
                '\n          </div>'
                '\n        </details>'
                '\n      </div>'
            )
        else:
            parts.append(
                '\n      <div class="hint-level">'
                f'\n        <h3 style="font-size:.95rem;margin-bottom:.4rem;">{title}</h3>'
                '\n        <div class="hint-plain">'
                f'\n          <p>{desc}</p>'
                f'\n          <div class="hint-words">{words_html}</div>'
                '\n        </div>'
                '\n      </div>'
            )
    return "".join(parts)


def solution_box_html(word: str, reveal: bool) -> str:
    """Bloc solution : en clair (archives) ou flouté avec bouton JS de révélation (page du jour)."""
    if reveal:
        return (
            '\n      <div style="text-align:center;margin:.5rem 0 1rem;">'
            f'\n        <span class="solution-word">{word}</span>'
            '\n      </div>'
        )
    return (
        '\n      <div style="text-align:center;margin:.5rem 0 1rem;">'
        '\n        <div class="solution-blur" id="solution-wrap">'
        f'\n          <span class="solution-word">{word}</span>'
        '\n        </div>'
        '\n        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">'
        '\n          Cliquer pour révéler la réponse'
        '\n        </button>'
        '\n      </div>'
    )


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


# ── Wiktionnaire / Wikipédia (définitions) ────────────────────────────────────

# User-Agent conforme à la policy Wikimedia (identifie l'app + contact)
_WIKI_UA = "SolutionDuJour/1.0 (https://solution-du-jour.fr; contact@solution-du-jour.fr)"


def _strip_accents(s: str) -> str:
    """Retire les diacritiques (ex. 'ÉLÉPHANT' -> 'ELEPHANT')."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _clean_wikitext(wt: str) -> str:
    """Nettoie le wikitext : retire templates, liens, gras, HTML."""
    wt = re.sub(r"\{\{[^{}]*\}\}", "", wt)                       # {{templates}}
    wt = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", wt)    # [[liens|texte]]
    wt = re.sub(r"'''?", "", wt)                                  # '''gras'''/''italique''
    wt = re.sub(r"<[^>]+>", "", wt)                               # balises HTML
    return re.sub(r"\s+", " ", wt).strip()


def wiktionary_resolve_title(word: str) -> str | None:
    """Résout le titre exact d'une page Wiktionnaire pour un mot MAJUSCULE SANS ACCENTS
    (ex. mots Sutom). Essaie d'abord `word.lower()` tel quel, puis une recherche avec
    accent-folding (ex. 'ELEPHANT' -> 'éléphant'). Retourne le titre trouvé ou None."""
    candidate = word.lower()
    resp = None
    for attempt in range(3):
        resp = _session.get(
            "https://fr.wiktionary.org/w/api.php",
            params={"action": "query", "format": "json", "titles": candidate},
            headers={"User-Agent": _WIKI_UA},
            timeout=10,
        )
        if resp.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))
    if resp is not None and resp.status_code == 200:
        pages = resp.json().get("query", {}).get("pages", {})
        if any(pid != "-1" for pid in pages):
            return candidate

    # Recherche avec accent-folding
    target = _strip_accents(candidate)
    resp = None
    for attempt in range(3):
        resp = _session.get(
            "https://fr.wiktionary.org/w/api.php",
            params={"action": "query", "list": "search", "format": "json",
                    "srsearch": candidate, "srlimit": 10},
            headers={"User-Agent": _WIKI_UA},
            timeout=10,
        )
        if resp.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))
    if resp is None or resp.status_code != 200:
        return None
    for hit in resp.json().get("query", {}).get("search", []):
        title = hit.get("title", "")
        if _strip_accents(title.lower()) == target:
            return title
    return None


def _wiktionary_definition(word: str) -> str:
    """Première définition française via le Wiktionnaire (action=parse wikitext)."""
    resp = None
    for attempt in range(3):
        resp = _session.get(
            "https://fr.wiktionary.org/w/api.php",
            params={"action": "parse", "format": "json", "prop": "wikitext",
                    "page": word, "redirects": 1},
            headers={"User-Agent": _WIKI_UA},
            timeout=10,
        )
        if resp.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))          # backoff sur rate-limit Wikimedia
    if resp.status_code != 200:
        return ""
    data = resp.json()
    if "parse" not in data:
        return ""
    wt = data["parse"]["wikitext"]["*"]
    # Isoler la section « Français » (avant la langue suivante éventuelle)
    m = re.search(r"==\s*\{\{langue\|fr\}\}\s*==", wt) or re.search(r"==\s*Français\s*==", wt)
    if m:
        rest = wt[m.end():]
        nxt = re.search(r"\n==\s*\{\{langue\|", rest)
        wt = rest[:nxt.start()] if nxt else rest
    # Première section de nature grammaticale, puis 1re ligne de définition « # … »
    pm = re.search(
        r"\{\{S\|(?:nom|adjectif|verbe|adverbe|nom commun|adjectif numéral|"
        r"préposition|pronom|interjection|conjonction)[^}]*\|fr[^}]*\}\}(.*?)"
        r"(?=\n=+\s*\{\{S\||\Z)",
        wt, re.S,
    )
    if not pm:
        return ""
    for line in pm.group(1).splitlines():
        if re.match(r"#\s*[^*:]", line):          # ligne de définition (pas exemple #* ni #:)
            d = _clean_wikitext(line[1:])
            if d:
                return d if d.endswith(".") else d + "."
    return ""


def fetch_definition(word: str, resolve_accents: bool = False) -> str:
    """Définition FR : Wiktionnaire d'abord (dictionnaire), Wikipédia en secours (noms propres).
    resolve_accents=True : le mot est MAJUSCULE SANS ACCENTS (ex. mots Sutom) — résout d'abord
    le titre exact de la page Wiktionnaire avant de la parser."""
    lookup = word
    if resolve_accents:
        resolved = wiktionary_resolve_title(word)
        if not resolved:
            return ""
        lookup = resolved
    try:
        d = _wiktionary_definition(lookup)
        if d:
            return d[:300]
    except Exception as e:
        print(f"   ⚠ Définition Wiktionnaire : {e}")
    if resolve_accents:
        # Pas de secours Wikipédia pertinent pour un mot commun Sutom sans page Wiktionnaire trouvée.
        return ""
    try:
        resp = _session.get(
            f"https://fr.wikipedia.org/api/rest_v1/page/summary/{word}",
            headers={"User-Agent": _WIKI_UA},
            timeout=10,
        )
        if resp.status_code == 200:
            extract = resp.json().get("extract", "").strip()
            if extract:
                idx = extract.find(". ")
                return extract[:idx + 1] if idx != -1 else extract[:300]
    except Exception as e:
        print(f"   ⚠ Définition Wikipedia : {e}")
    return ""
