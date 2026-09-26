#!/usr/bin/env bash
# run_draws.sh — Publie les résultats Loto / EuroMillions le soir même du tirage
# Lancé par launchd les soirs de tirage (voir io.cemantix.draws.plist) :
#   Loto lun/mer/sam 20h40, EuroMillions mar/ven 21h20.
# Relance generate.py toutes les 5 min jusqu'à ce que le tirage du jour soit publié.
# Pas de post Reddit ici (déjà fait par run_daily.sh, reddit_post.py ne déduplique pas).
#
# Usage manuel : bash run_draws.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/run_daily.log"
PYTHON="$SCRIPT_DIR/venv/bin/python"

cd "$SCRIPT_DIR"

export GIT_SSH_COMMAND="ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"

# Jeu tiré ce soir (date +%u : 1=lundi … 7=dimanche)
case "$(date +%u)" in
  1|3|6) GAME="loto" ;;
  2|5)   GAME="euromillions" ;;
  *)     echo "=== $(date '+%Y-%m-%d %H:%M:%S') — run_draws : pas de tirage aujourd'hui ===" >> "$LOG_FILE"; exit 0 ;;
esac

# Watchdog : 3h max (le polling s'arrête de lui-même à DEADLINE)
MAX_RUNTIME=10800
MAIN_PID=$$
(
  sleep "$MAX_RUNTIME"
  trap '' TERM
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — ⛔ run_draws : timeout après ${MAX_RUNTIME}s, arrêt forcé ===" >> "$LOG_FILE"
  pkill -TERM -P "$MAIN_PID" || true
  kill -TERM "$MAIN_PID" || true
) >/dev/null 2>&1 &
WATCHDOG_PID=$!
trap 'pkill -P "$WATCHDOG_PID" 2>/dev/null; kill "$WATCHDOG_PID" 2>/dev/null; true' EXIT

DEADLINE="2330"   # au-delà, on laisse le run de 00h15 prendre le relais
POLL_INTERVAL=300
TODAY_ISO=$(date '+%Y-%m-%d')
SOLUTION_JSON="$SCRIPT_DIR/docs/$GAME/solution.json"

draw_date() {
  "$PYTHON" -c "import json; print(json.load(open('$SOLUTION_JSON')).get('date',''))" 2>/dev/null || echo ""
}

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — run_draws ($GAME) : démarrage ==="

  while :; do
    git pull --rebase origin main
    "$PYTHON" generate.py || echo "⚠️  generate.py en erreur, nouvelle tentative au prochain tour"

    if [ "$(draw_date)" = "$TODAY_ISO" ]; then
      echo "✅ Tirage $GAME du $TODAY_ISO disponible."
      break
    fi
    if [ "$(date +%H%M)" -ge "$DEADLINE" ]; then
      echo "⚠️  Tirage $GAME du $TODAY_ISO toujours absent à $(date +%H:%M) — abandon (le run de 00h15 prendra le relais)."
      git checkout -- docs/ && git clean -fdq docs/   # ne rien laisser de non commité pour le run de 00h15
      exit 0
    fi
    echo "… tirage $GAME pas encore publié ($(draw_date) ≠ $TODAY_ISO), nouvel essai dans $((POLL_INTERVAL / 60)) min"
    git checkout -- docs/ && git clean -fdq docs/     # repartir propre pour le prochain git pull --rebase
    sleep "$POLL_INTERVAL"
  done

  git add docs/
  if git diff --staged --quiet; then
    echo "Rien à commiter."
  else
    git commit -m "chore: résultats $GAME $TODAY_ISO [skip ci]"
    git push
    echo "Pushé vers GitHub."
    "$PYTHON" -c "import generate as g; g.ping_daily_indexnow()" || echo "⚠️  ping IndexNow ignoré (voir erreur ci-dessus)"
  fi

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — run_draws ($GAME) : terminé ==="
} >> "$LOG_FILE" 2>&1
