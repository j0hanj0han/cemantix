#!/usr/bin/env bash
# run_pedantix.sh — Résout le Pédantix du jour dès sa sortie et pousse vers GitHub Pages
# Pédantix change d'article à MIDI (heure de Paris), pas à minuit : lancé par launchd
# chaque jour à 12h05 (voir io.cemantix.pedantix.plist).
# Réessaie toutes les 5 min tant que la page affiche encore le puzzle d'hier.
# Pas de post Reddit ici (déjà fait par run_daily.sh).
#
# Usage manuel : bash run_pedantix.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/run_daily.log"
PYTHON="$SCRIPT_DIR/venv/bin/python"

cd "$SCRIPT_DIR"

export GIT_SSH_COMMAND="ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"

# Watchdog : 1h max
MAX_RUNTIME=3600
MAIN_PID=$$
(
  sleep "$MAX_RUNTIME"
  trap '' TERM
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — ⛔ run_pedantix : timeout après ${MAX_RUNTIME}s, arrêt forcé ===" >> "$LOG_FILE"
  pkill -TERM -P "$MAIN_PID" || true
  kill -TERM "$MAIN_PID" || true
) >/dev/null 2>&1 &
WATCHDOG_PID=$!
trap 'pkill -P "$WATCHDOG_PID" 2>/dev/null; kill "$WATCHDOG_PID" 2>/dev/null; true' EXIT

DEADLINE="1300"        # au-delà, on publie ce qu'on a (le run de 00h15 retentera)
POLL_INTERVAL=300
MAX_SOLVER_RETRIES=2   # le cache .cache/pedantix/ fait reprendre le solveur là où il s'est arrêté
TODAY_ISO=$(date '+%Y-%m-%d')

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — run_pedantix : démarrage ==="

  FREE_KB=$(df -k "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
  if [ "$FREE_KB" -lt 1048576 ]; then
    echo "⛔ Espace disque insuffisant ($((FREE_KB / 1024)) Mo libres) — run annulé."
    exit 1
  fi

  solver_retries=0
  while :; do
    git checkout -- docs/ && git clean -fdq docs/   # repartir propre même si un run précédent a été interrompu
    git pull --rebase origin main
    code=0
    "$PYTHON" -c "import sys, generate as g; sys.exit(g.run_pedantix_noon())" || code=$?

    if [ "$code" -eq 0 ]; then
      echo "✅ Pédantix du $TODAY_ISO résolu."
      break
    fi
    if [ "$(date +%H%M)" -ge "$DEADLINE" ]; then
      echo "⚠️  Pédantix du $TODAY_ISO non résolu à $(date +%H:%M) (code $code) — publication de l'existant, le run de 00h15 retentera."
      break
    fi
    if [ "$code" -eq 3 ]; then
      solver_retries=$((solver_retries + 1))
      if [ "$solver_retries" -gt "$MAX_SOLVER_RETRIES" ]; then
        echo "⚠️  Solveur Pédantix en échec après $MAX_SOLVER_RETRIES reprises — publication de l'existant."
        break
      fi
      echo "… solveur Pédantix sans résultat (code 3), reprise dans $((POLL_INTERVAL / 60)) min"
    else
      echo "… Pédantix pas encore passé au puzzle du jour (code $code), nouvel essai dans $((POLL_INTERVAL / 60)) min"
    fi
    git checkout -- docs/ && git clean -fdq docs/   # repartir propre pour le prochain git pull --rebase
    sleep "$POLL_INTERVAL"
  done

  git add docs/
  if git diff --staged --quiet; then
    echo "Rien à commiter."
  else
    git commit -m "chore: pédantix $TODAY_ISO [skip ci]"
    git push
    echo "Pushé vers GitHub."
    "$PYTHON" -c "import generate as g; g.ping_daily_indexnow()" || echo "⚠️  ping IndexNow ignoré (voir erreur ci-dessus)"
  fi

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — run_pedantix : terminé ==="
} >> "$LOG_FILE" 2>&1
