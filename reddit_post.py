"""
reddit_post.py — Publie la solution Cémantix du jour sur Reddit.

Usage :
  python reddit_post.py            ← poste Cémantix
  python reddit_post.py --sutom    ← poste aussi Sutom

Setup (une fois) :
  1. Aller sur https://www.reddit.com/prefs/apps
     → "create another app..." → type "script"
     → redirect URI : http://localhost:8080
     → noter client_id (sous le nom de l'app) et client_secret

  2. Créer le fichier .reddit_env à la racine du projet :
     REDDIT_CLIENT_ID=xxx
     REDDIT_CLIENT_SECRET=xxx
     REDDIT_USERNAME=votre_pseudo_reddit
     REDDIT_PASSWORD=votre_mot_de_passe
     REDDIT_SUBREDDIT_CEMANTIX=france   ← ou un subreddit dédié
     REDDIT_SUBREDDIT_SUTOM=france

  3. pip install praw
  4. Ajouter .reddit_env dans .gitignore !
"""

import argparse
import json
import os
from datetime import date
from pathlib import Path


def _load_env() -> None:
    """Charge .reddit_env si présent."""
    env_file = Path(__file__).parent / ".reddit_env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def _get_reddit():
    """Retourne un client PRAW authentifié."""
    try:
        import praw
    except ImportError:
        raise SystemExit("PRAW non installé. Lance : venv/bin/pip install praw")

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    username = os.environ.get("REDDIT_USERNAME")
    password = os.environ.get("REDDIT_PASSWORD")

    if not all([client_id, client_secret, username, password]):
        raise SystemExit(
            "Variables Reddit manquantes. Crée le fichier .reddit_env — voir commentaire en haut du script."
        )

    import praw
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        user_agent=f"solution-du-jour-bot/1.0 by u/{username}",
    )


def post_cemantix(reddit=None) -> bool:
    """Poste la solution Cémantix du jour sur Reddit."""
    solution_file = Path("docs/cemantix/solution.json")
    if not solution_file.exists():
        print("⚠️  docs/cemantix/solution.json introuvable — post ignoré")
        return False

    data = json.loads(solution_file.read_text(encoding="utf-8"))
    today = date.today().isoformat()

    if data.get("date") != today:
        print(f"⚠️  Solution Cémantix non à jour ({data.get('date')} ≠ {today}) — post ignoré")
        return False

    word = data["word"]
    puzzle_num = data["puzzle_num"]
    letter_count = len(word)
    first_letter = word[0].upper()

    # Importer date_fr depuis core
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from core import date_fr
    from datetime import date as _date
    date_display = date_fr(_date.fromisoformat(today))

    subreddit_name = os.environ.get("REDDIT_SUBREDDIT_CEMANTIX", "france")

    title = f"Cémantix #{puzzle_num} du {date_display} — Solution + indices"

    body = f"""Bloqué·e sur le Cémantix d'aujourd'hui ? Voici les indices.

**Indices :**
- {letter_count} lettres
- Commence par **{first_letter}**

**Solution :** >!{word.upper()}!<

---
Indices progressifs complets (3 niveaux) disponibles sur : https://solution-du-jour.fr/cemantix/

*Posté automatiquement par [solution-du-jour.fr](https://solution-du-jour.fr)*"""

    if reddit is None:
        reddit = _get_reddit()

    sub = reddit.subreddit(subreddit_name)
    post = sub.submit(title, selftext=body)
    print(f"✅ Cémantix posté sur r/{subreddit_name} : https://reddit.com{post.permalink}")
    return True


def post_sutom(reddit=None) -> bool:
    """Poste la solution Sutom du jour sur Reddit."""
    solution_file = Path("docs/sutom/solution.json")
    if not solution_file.exists():
        print("⚠️  docs/sutom/solution.json introuvable — post ignoré")
        return False

    data = json.loads(solution_file.read_text(encoding="utf-8"))
    today = date.today().isoformat()

    if data.get("date") != today:
        print(f"⚠️  Solution Sutom non à jour — post ignoré")
        return False

    word = data["word"]
    puzzle_num = data["puzzle_num"]
    letter_count = len(word)
    first_letter = word[0].upper()

    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from core import date_fr
    from datetime import date as _date
    date_display = date_fr(_date.fromisoformat(today))

    subreddit_name = os.environ.get("REDDIT_SUBREDDIT_SUTOM", "france")

    title = f"Sutom #{puzzle_num} du {date_display} — Solution"
    body = f"""Bloqué·e sur le Sutom d'aujourd'hui ?

**Indices :**
- {letter_count} lettres
- Commence par **{first_letter}**

**Solution :** >!{word.upper()}!<

---
Solution complète : https://solution-du-jour.fr/sutom/

*Posté automatiquement par [solution-du-jour.fr](https://solution-du-jour.fr)*"""

    if reddit is None:
        reddit = _get_reddit()

    sub = reddit.subreddit(subreddit_name)
    post = sub.submit(title, selftext=body)
    print(f"✅ Sutom posté sur r/{subreddit_name} : https://reddit.com{post.permalink}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Publie les solutions du jour sur Reddit")
    parser.add_argument("--sutom", action="store_true", help="Poster aussi la solution Sutom")
    parser.add_argument("--dry-run", action="store_true", help="Afficher le post sans l'envoyer")
    args = parser.parse_args()

    _load_env()

    if args.dry_run:
        print("=== DRY RUN — aucun post envoyé ===\n")
        # Charger et afficher ce qui serait posté
        for name, path in [("Cémantix", "docs/cemantix/solution.json"), ("Sutom", "docs/sutom/solution.json")]:
            f = Path(path)
            if f.exists():
                d = json.loads(f.read_text(encoding="utf-8"))
                print(f"[{name}] mot={d.get('word')} puzzle={d.get('puzzle_num')} date={d.get('date')}")
        return

    try:
        reddit = _get_reddit()
    except SystemExit as e:
        print(f"⚠️  Reddit non configuré : {e}")
        return

    post_cemantix(reddit)
    if args.sutom:
        post_sutom(reddit)


if __name__ == "__main__":
    main()
