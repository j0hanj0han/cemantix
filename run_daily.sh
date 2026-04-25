#!/usr/bin/env bash
# run_daily.sh — Résout Cémantix et pousse vers GitHub Pages
# Lancé automatiquement par launchd chaque matin (voir io.cemantix.daily.plist)
#
# Usage manuel : bash run_daily.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/run_daily.log"
PYTHON="$SCRIPT_DIR/venv/bin/python"

cd "$SCRIPT_DIR"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Démarrage ==="

  # Récupère les éventuels commits distants avant de générer
  git pull --rebase origin main

  # Résolution + génération du site
  "$PYTHON" generate.py

  # Si Cémantix n'est pas à jour, réessayer une fois après 5 minutes
  TODAY_ISO=$(date '+%Y-%m-%d')
  CEMANTIX_JSON="$SCRIPT_DIR/docs/cemantix/solution.json"
  CEMANTIX_DATE=$("$PYTHON" -c "import json; print(json.load(open('$CEMANTIX_JSON')).get('date',''))" 2>/dev/null || echo "")
  if [ "$CEMANTIX_DATE" != "$TODAY_ISO" ]; then
    echo "⚠️  Cémantix non à jour ($CEMANTIX_DATE ≠ $TODAY_ISO) — nouvelle tentative dans 5 min…"
    sleep 300
    "$PYTHON" generate.py
  fi

  # Commit et push si des fichiers docs/ ont changé
  git add docs/
  if git diff --staged --quiet; then
    echo "Rien à commiter (solution déjà à jour)."
  else
    git commit -m "chore: solution $(date +'%Y-%m-%d') [skip ci]"
    git push
    echo "Pushé vers GitHub."
  fi

  # Post Reddit (Cémantix uniquement — Sutom désactivé par défaut)
  "$PYTHON" reddit_post.py || echo "⚠️  Reddit post ignoré (voir erreur ci-dessus)"

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Terminé ==="
} >> "$LOG_FILE" 2>&1
