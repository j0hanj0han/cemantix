"""
games/sutom.py — Logique complète du jeu Sutom (Wordle FR).

Génère :
  docs/sutom/solution.json
  docs/sutom/index.html
  docs/sutom/archive/YYYY-MM-DD.json
  docs/sutom/archive/YYYY-MM-DD.html
  docs/sutom/archive/index.html
"""

import base64
import json
from datetime import date, datetime, timezone
from pathlib import Path

from core import SITE_URL, DOCS_DIR, _session, date_fr, atomic_write, load_all_archives as _load_archives

# ── Configuration Sutom ───────────────────────────────────────────────────────

SUTOM_DIR = DOCS_DIR / "sutom"
SUTOM_ARCHIVE = SUTOM_DIR / "archive"
SUTOM_SITE_URL = f"{SITE_URL}/sutom"

# Identifiant de partie par défaut (source : js/instanceConfiguration.js)
_SUTOM_PARTIE_ID = "34ccc522-c264-4e51-b293-fd5bd60ef7aa"
# Date d'origine (source : instanceConfiguration.dateOrigine = new Date(2022, 0, 8))
_SUTOM_LAUNCH = date(2022, 1, 8)


# ── API Sutom ─────────────────────────────────────────────────────────────────

def get_sutom_solution(today: date) -> tuple[str | None, int | None]:
    """
    Récupère la solution Sutom du jour via l'endpoint fichier direct.
    URL : https://sutom.nocle.fr/mots/{btoa(uuid-YYYY-MM-DD)}.txt
    Retourne (word, puzzle_num) ou (None, None) si indisponible.
    """
    raw = f"{_SUTOM_PARTIE_ID}-{today.isoformat()}".encode()
    filename = base64.b64encode(raw).decode()  # padding = conservé (comme btoa JS)
    url = f"https://sutom.nocle.fr/mots/{filename}.txt"
    try:
        resp = _session.get(url, timeout=10)
        if resp.status_code == 200 and resp.text.strip():
            word = resp.text.strip().upper()
            puzzle_num = (today - _SUTOM_LAUNCH).days + 1
            return word, puzzle_num
    except Exception as e:
        print(f"   ⚠ Sutom : {e}")

    return None, None


# ── Génération des fichiers ───────────────────────────────────────────────────

def generate_solution_json(today: date, puzzle_num: int, word: str) -> dict:
    data = {
        "date": today.isoformat(),
        "puzzle_num": puzzle_num,
        "word": word,
        "letter_count": len(word),
        "first_letter": word[0] if word else "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    SUTOM_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(SUTOM_DIR / "solution.json", json.dumps(data, ensure_ascii=False, indent=2))
    return data


def generate_archive_json(today: date, data: dict) -> None:
    SUTOM_ARCHIVE.mkdir(parents=True, exist_ok=True)
    atomic_write(SUTOM_ARCHIVE / f"{today.isoformat()}.json",
                 json.dumps(data, ensure_ascii=False, indent=2))


def load_all_archives() -> list[dict]:
    return _load_archives(SUTOM_ARCHIVE)


def generate_archive_html(
    d: date,
    puzzle_num: int,
    word: str,
    prev_date,
    next_date,
) -> None:
    """Génère docs/sutom/archive/YYYY-MM-DD.html."""
    SUTOM_ARCHIVE.mkdir(parents=True, exist_ok=True)
    date_str = d.isoformat()
    date_display = date_fr(d)
    letter_count = len(word)
    first_letter = word[0] if word else "?"

    if prev_date is not None:
        nav_prev = f'<a class="nav-link" href="{prev_date.isoformat()}.html">&#8592; {date_fr(prev_date)}</a>'
    else:
        nav_prev = '<span class="nav-disabled">&#8592; Plus ancien</span>'

    if next_date is not None:
        nav_next = f'<a class="nav-link" href="{next_date.isoformat()}.html">{date_fr(next_date)} &#8594;</a>'
    else:
        nav_next = '<a class="nav-link" href="../index.html">Solution du jour &#8594;</a>'

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>Sutom {date_display} — Solution #{puzzle_num} · Archive</title>
  <meta name="description" content="Solution du Sutom #{puzzle_num} du {date_display}. Mot en {letter_count} lettres commençant par {first_letter}.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SUTOM_SITE_URL}/archive/{date_str}.html">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Sutom {date_display} — Solution #{puzzle_num}">
  <meta property="og:description" content="Réponse du Sutom du {date_display} : mot en {letter_count} lettres commençant par {first_letter}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{SUTOM_SITE_URL}/archive/{date_str}.html">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Solution Sutom #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution du Sutom #{puzzle_num} pour le {date_display} : {word}.",
    "url": "{SUTOM_SITE_URL}/archive/{date_str}.html",
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {{
        "@type": "Question",
        "name": "Quelle est la solution du Sutom du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La réponse du Sutom #{puzzle_num} du {date_display} est : {word}."
        }}
      }}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="//gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Sutom — Archive</h1>
  <p class="subtitle">Solution du {date_display} — #{puzzle_num}</p>
</header>

<main>
  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="index.html">Toutes les archives</a>
    {nav_next}
  </nav>

  <article>

    <div class="card">
      <h2>Sutom #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Retrouvez la <strong>solution du Sutom du {date_display}</strong> (puzzle #{puzzle_num}).
        Le mot du jour contient <strong>{letter_count} lettres</strong> et commence par
        <strong>{first_letter}</strong>.
      </p>
    </div>

    <div class="card">
      <h2>Indice : première lettre</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Comme dans le jeu Sutom, la première lettre est toujours révélée.
      </p>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="sutom-grid">
          <span class="sutom-cell sutom-correct">{first_letter}</span>
          {"".join(f'<span class="sutom-cell sutom-empty">_</span>' for _ in range(letter_count - 1))}
        </div>
        <p class="puzzle-meta">{letter_count} lettres · commence par {first_letter}</p>
      </div>
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="solution-blur" id="solution-wrap">
          <span class="solution-word">{word}</span>
        </div>
        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">
          Cliquer pour révéler la réponse
        </button>
      </div>
      <p class="puzzle-meta">Puzzle #{puzzle_num} · {date_display}</p>
    </div>

  </article>

  <nav class="nav-archive" aria-label="Navigation entre les archives">
    {nav_prev}
    <a class="nav-center" href="index.html">Toutes les archives</a>
    {nav_next}
  </nav>
</main>

<footer>
  <p>
    <a href="../index.html">Solution du jour</a> ·
    <a href="index.html">Archives</a> ·
    <a href="https://sutom.nocle.fr" rel="noopener" target="_blank">Jouer à Sutom</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

<script>
  function revealSolution() {{
    document.getElementById('solution-wrap').classList.add('revealed');
    document.getElementById('reveal-btn').style.display = 'none';
  }}
</script>

</body>
</html>"""

    atomic_write(SUTOM_ARCHIVE / f"{date_str}.html", html)


def generate_archive_index(entries: list[dict]) -> None:
    """Génère docs/sutom/archive/index.html."""
    SUTOM_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def item_html(e: dict) -> str:
        d = date.fromisoformat(e["date"])
        return (
            f'      <li class="arch-item">'
            f'<span class="arch-date">{date_fr(d)}</span>'
            f'<span class="arch-num">#{e["puzzle_num"]}</span>'
            f'<a class="arch-link" href="{e["date"]}.html">{e["word"].upper()}</a>'
            f'</li>'
        )

    items_html = "\n".join(item_html(e) for e in entries)
    count = len(entries)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>Archives Sutom — Toutes les solutions du jour</title>
  <meta name="description" content="Retrouvez toutes les solutions passées de Sutom : réponses de chaque puzzle depuis le début.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SUTOM_SITE_URL}/archive/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Archives Sutom — Toutes les solutions">
  <meta property="og:description" content="Toutes les solutions passées du jeu Sutom.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{SUTOM_SITE_URL}/archive/">

  <link rel="stylesheet" href="../../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="//gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Archives Sutom</h1>
  <p class="subtitle">{count} solution{"s" if count > 1 else ""} enregistrée{"s" if count > 1 else ""}</p>
</header>

<main>
  <div class="card">
    <h2>Toutes les solutions Sutom</h2>
    <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
      Cliquez sur un mot pour voir la solution complète de ce jour.
    </p>
    <ul class="arch-list">
{items_html}
    </ul>
  </div>

  <div style="text-align:center;margin-top:.5rem;">
    <a class="reveal-btn" href="../index.html">Solution du jour &#8594;</a>
  </div>
</main>

<footer>
  <p>
    <a href="../index.html">Solution du jour</a> ·
    <a href="https://sutom.nocle.fr" rel="noopener" target="_blank">Jouer à Sutom</a>
  </p>
  <p style="margin-top:.4rem;">Site non officiel — Solution générée automatiquement</p>
</footer>

</body>
</html>"""

    atomic_write(SUTOM_ARCHIVE / "index.html", html)


def generate_index_html(
    today: date,
    puzzle_num: int,
    word: str,
    recent_archives: list | None = None,
) -> None:
    """Génère docs/sutom/index.html."""
    date_str = today.isoformat()
    date_display = date_fr(today)
    letter_count = len(word)
    first_letter = word[0] if word else "?"

    recent_archives_card = ""
    if recent_archives:
        def arch_item(e: dict) -> str:
            d = date.fromisoformat(e["date"])
            return (
                f'      <li class="arch-item">'
                f'<span class="arch-date">{date_fr(d)}</span>'
                f'<span class="arch-num">#{e["puzzle_num"]}</span>'
                f'<a class="arch-link" href="archive/{e["date"]}.html">{e["word"].upper()}</a>'
                f'</li>'
            )
        items = "\n".join(arch_item(e) for e in recent_archives[:7])
        recent_archives_card = f"""
    <div class="card">
      <h2>Solutions précédentes</h2>
      <ul class="arch-list">
{items}
      </ul>
      <p style="margin-top:.75rem;font-size:.9rem;">
        <a href="archive/">Voir toutes les archives &#8594;</a>
      </p>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>Sutom {date_display} — Solution #{puzzle_num} · Réponse du Jour</title>
  <meta name="description" content="Solution du Sutom #{puzzle_num} du {date_display}. Mot en {letter_count} lettres commençant par {first_letter}. Réponse du Wordle français du jour.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{SUTOM_SITE_URL}/">
  <meta name="google-site-verification" content="KLhfwprI4hatb7c2RyrwsiYjulATuj0vJueDdJt0yLs">

  <meta property="og:title" content="Sutom {date_display} — Solution #{puzzle_num}">
  <meta property="og:description" content="Réponse du Sutom du {date_display} : mot en {letter_count} lettres commençant par {first_letter}.">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{SUTOM_SITE_URL}/">
  <meta property="article:published_time" content="{date_str}T08:00:00+01:00">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Solution Sutom #{puzzle_num} du {date_display}",
    "datePublished": "{date_str}T08:00:00+01:00",
    "dateModified": "{date_str}T08:00:00+01:00",
    "description": "Solution et réponse du jeu Sutom #{puzzle_num} pour le {date_display}.",
    "url": "{SUTOM_SITE_URL}/",
    "author": {{"@type": "Organization", "name": "Solutions du Jour"}}
  }}
  </script>

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {{
        "@type": "Question",
        "name": "Quelle est la solution du Sutom du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "La réponse du Sutom #{puzzle_num} du {date_display} est : {word}."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Qu'est-ce que Sutom ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Sutom est la version française de Wordle. Chaque jour, un mot de 6 à 9 lettres est à deviner. La première lettre est toujours révélée. Vous avez 6 tentatives pour trouver le mot."
        }}
      }},
      {{
        "@type": "Question",
        "name": "Combien de lettres fait le mot Sutom du {date_display} ?",
        "acceptedAnswer": {{
          "@type": "Answer",
          "text": "Le mot Sutom du {date_display} (puzzle #{puzzle_num}) contient {letter_count} lettres et commence par {first_letter}."
        }}
      }}
    ]
  }}
  </script>

  <link rel="stylesheet" href="../css/style.css">
  <script data-goatcounter="https://j0hanj0han.goatcounter.com/count"
          async src="//gc.zgo.at/count.js"></script>
</head>
<body>

<header class="site-header">
  <h1>Sutom — Solution du jour</h1>
  <p class="subtitle">Réponse du Wordle français — #{puzzle_num}</p>
</header>

<main>
  <article>

    <div class="card">
      <h2>Sutom #{puzzle_num} — <time datetime="{date_str}">{date_display}</time></h2>
      <p>
        Vous cherchez la <strong>solution du Sutom du {date_display}</strong> (puzzle #{puzzle_num}) ?
        Le mot du jour contient <strong>{letter_count} lettres</strong> et commence par
        <strong>{first_letter}</strong>.
        Révélez la réponse complète ci-dessous si vous êtes bloqué.
      </p>
    </div>

    <div class="card">
      <h2>Indice : première lettre</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Comme dans le jeu Sutom, la première lettre est toujours révélée.
      </p>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="sutom-grid">
          <span class="sutom-cell sutom-correct">{first_letter}</span>
          {"".join(f'<span class="sutom-cell sutom-empty">_</span>' for _ in range(letter_count - 1))}
        </div>
        <p class="puzzle-meta">{letter_count} lettres · commence par {first_letter}</p>
      </div>
    </div>

    <div class="card">
      <h2>La solution du {date_display}</h2>
      <div style="text-align:center;margin:.5rem 0 1rem;">
        <div class="solution-blur" id="solution-wrap">
          <span class="solution-word">{word}</span>
        </div>
        <button class="reveal-btn" id="reveal-btn" onclick="revealSolution()">
          Cliquer pour révéler la réponse
        </button>
      </div>
      <p class="puzzle-meta">Puzzle #{puzzle_num} · Généré automatiquement le {date_display}</p>
    </div>

    <div class="card">
      <h2>Comment jouer à Sutom ?</h2>
      <p>
        <strong>Sutom</strong> est le Wordle français disponible sur
        <a href="https://sutom.nocle.fr" rel="noopener" target="_blank">sutom.nocle.fr</a>.
        Chaque jour, un nouveau mot (de 6 à 9 lettres) est à deviner en 6 tentatives.
        La première lettre est toujours révélée. Après chaque essai, les cases se colorent :
        rouge = bonne lettre bien placée, jaune = bonne lettre mal placée.
      </p>
      <p style="margin-top:.75rem;">
        Cette page est mise à jour automatiquement chaque matin avec la <strong>solution Sutom du jour</strong>.
        Revenez chaque jour pour la nouvelle <em>réponse Sutom</em> !
      </p>
    </div>
{recent_archives_card}
  </article>
</main>

<footer>
  <p>Site non officiel — Solution générée automatiquement · <a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives</a></p>
  <p style="margin-top:.4rem;">Jouer sur <a href="https://sutom.nocle.fr" rel="noopener" target="_blank">sutom.nocle.fr</a></p>
</footer>

<script>
  function revealSolution() {{
    document.getElementById('solution-wrap').classList.add('revealed');
    document.getElementById('reveal-btn').style.display = 'none';
  }}
</script>

</body>
</html>"""

    atomic_write(SUTOM_DIR / "index.html", html)


def generate_unavailable_html(today: date) -> None:
    """Génère une page 'solution non disponible' pour Sutom."""
    SUTOM_DIR.mkdir(parents=True, exist_ok=True)
    date_display = date_fr(today)
    date_str = today.isoformat()

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sutom {date_display} — Solution non disponible</title>
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="../css/style.css">
</head>
<body>

<header class="site-header">
  <h1>Sutom — Solution du jour</h1>
  <p class="subtitle">{date_display}</p>
</header>

<main>
  <div class="card">
    <h2>Solution non disponible</h2>
    <p>
      La solution Sutom du {date_display} n'a pas pu être récupérée automatiquement.
      Réessayez plus tard ou rendez-vous directement sur
      <a href="https://sutom.nocle.fr" rel="noopener" target="_blank">sutom.nocle.fr</a>.
    </p>
  </div>
</main>

<footer>
  <p><a href="{SITE_URL}/">Accueil</a> · <a href="archive/">Archives Sutom</a></p>
</footer>

</body>
</html>"""

    atomic_write(SUTOM_DIR / "index.html", html)


# ── Orchestration HTML ────────────────────────────────────────────────────────

def _generate_all_html(today: date, puzzle_num: int, word: str) -> None:
    """Génère tous les fichiers HTML Sutom à partir des JSON déjà en place."""
    all_archives = load_all_archives()
    today_str = today.isoformat()
    past_archives = [e for e in all_archives if e["date"] != today_str]

    print(f"[Sutom] Génération des pages HTML d'archive ({len(past_archives)} pages)…")
    for i, entry in enumerate(past_archives):
        d = date.fromisoformat(entry["date"])
        prev_date = date.fromisoformat(past_archives[i + 1]["date"]) if i + 1 < len(past_archives) else None
        next_date = date.fromisoformat(past_archives[i - 1]["date"]) if i > 0 else None
        generate_archive_html(d, entry["puzzle_num"], entry["word"], prev_date, next_date)

    print("[Sutom] Génération de docs/sutom/archive/index.html…")
    generate_archive_index(past_archives)

    recent_archives = past_archives[:7]
    print("[Sutom] Génération de docs/sutom/index.html…")
    generate_index_html(today, puzzle_num, word, recent_archives)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run(today: date) -> dict | None:
    """
    Récupère la solution Sutom du jour et génère tous les fichiers.
    Retourne le dict data ou None si la solution est indisponible.
    """
    SUTOM_DIR.mkdir(parents=True, exist_ok=True)
    SUTOM_ARCHIVE.mkdir(parents=True, exist_ok=True)

    # Vérifier si la solution est déjà générée pour aujourd'hui
    solution_path = SUTOM_DIR / "solution.json"
    if solution_path.exists():
        existing = json.loads(solution_path.read_text(encoding="utf-8"))
        if existing.get("date") == today.isoformat() and existing.get("word"):
            word = existing["word"]
            puzzle_num = existing["puzzle_num"]
            print(f"[Sutom] ℹ Solution déjà présente : {word!r} — régénération HTML uniquement.")
            generate_archive_json(today, existing)
            _generate_all_html(today, puzzle_num, word)
            return existing

    # Récupérer la solution
    print("[Sutom] Récupération de la solution…")
    word, puzzle_num = get_sutom_solution(today)

    if not word:
        print("[Sutom] ⚠ Solution non disponible — génération page indisponible.")
        generate_unavailable_html(today)
        return None

    print(f"[Sutom] ✅ Solution : {word!r} (puzzle #{puzzle_num}, {len(word)} lettres)")

    # Fichiers JSON
    data = generate_solution_json(today, puzzle_num, word)
    generate_archive_json(today, data)

    # HTML
    _generate_all_html(today, puzzle_num, word)

    print(f"[Sutom] 🎉 Site généré ({today.isoformat()}, #{puzzle_num}, {word!r})")
    return data
