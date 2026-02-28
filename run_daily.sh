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

  # Résolution + génération du site
  "$PYTHON" generate.py

  # Commit et push si des fichiers docs/ ont changé
  git add docs/
  if git diff --staged --quiet; then
    echo "Rien à commiter (solution déjà à jour)."
  else
    git commit -m "chore: solution $(date +'%Y-%m-%d') [skip ci]"
    git push
    echo "Pushé vers GitHub."
  fi

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Terminé ==="
} >> "$LOG_FILE" 2>&1
