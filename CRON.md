# Cron quotidien (launchd) — Mise en place et débogage

Ce document couvre uniquement le **cron macOS** qui fait tourner `run_daily.sh` chaque nuit
sur le Mac de production (IP résidentielle). Pour l'installation générale du projet
(venv, modèle word2vec, dépendances), voir [INSTALL.md](INSTALL.md).

---

## Pourquoi un Mac et pas GitHub Actions ?

Cémantix (et dans une moindre mesure Pédantix) bloquent les IPs de datacenter
(Cloudflare détecte les IPs GitHub Actions). Il faut donc une IP résidentielle,
d'où l'exécution sur un Mac perso via `launchd`, qui pousse ensuite le résultat
sur GitHub. `daily.yml` (GitHub Actions) ne fait que régénérer du HTML à partir
des `solution.json` déjà commités — il ne relance jamais les solveurs.

```
launchd (StartCalendarInterval)
   │
   ▼
run_daily.sh
   │
   ├─ git pull --rebase origin main
   ├─ venv/bin/python generate.py     ← résout les 5 jeux, génère docs/
   ├─ git add docs/ && git commit && git push   (si docs/ a changé)
   ├─ ping IndexNow (via generate.ping_daily_indexnow())
   └─ venv/bin/python reddit_post.py  (best-effort)
```

Tout dépend de ce run unique par jour. S'il ne tourne pas, **rien** ne se met à jour
(pas seulement le jeu dont le tirage a eu lieu la veille au soir).

---

## Fichiers impliqués

| Fichier | Rôle |
|---|---|
| `run_daily.sh` | Script exécuté par launchd (pull, generate.py, commit, push, IndexNow, Reddit) |
| `io.cemantix.daily.plist` | Définition du job launchd, **versionnée dans le repo** — sert de référence |
| `~/Library/LaunchAgents/io.cemantix.daily.plist` | Copie installée localement sur le Mac de prod (à tenir synchronisée avec celle du repo) |
| `run_daily.log` | Logs du script (stdout+stderr), **local, non versionné** (`.gitignore`) |
| `venv/` | Environnement Python du projet, non versionné |

⚠️ **Point de vigilance connu** : `io.cemantix.daily.plist` dans le repo indique
`Hour=0 / Minute=15` (00h15), mais `INSTALL.md` et `ARCHITECTURE.md` mentionnent encore
08h05 (ancien horaire). Se fier au **plist réellement chargé sur la machine de prod**
(`launchctl print`, voir plus bas), pas à la doc générale. Avant toute réinstallation,
vérifier l'horaire souhaité et le mettre à jour partout (plist du repo + docs) si besoin.

---

## Mise en place sur une nouvelle machine

1. Suivre **INSTALL.md** jusqu'à l'étape 4 (clone, venv, modèle `.bin`, test manuel de
   `venv/bin/python generate.py`).

2. **Vérifier l'authentification git en SSH sans terminal interactif** — c'est la cause
   d'échec la plus fréquente pour un job `launchd` (qui n'hérite pas forcément de l'agent
   SSH de la session Terminal) :

   ```bash
   ssh -T git@github.com
   # doit répondre "Hi <user>! You've successfully authenticated..."
   ```

   Si ça échoue en dehors d'un terminal interactif, s'assurer que la clé est bien
   ajoutée à l'agent SSH **persistant** du Mac (pas juste `ssh-add` dans le shell courant) :

   ```bash
   ssh-add --apple-use-keychain ~/.ssh/id_ed25519   # ou la clé utilisée pour GitHub
   ```

3. Adapter `io.cemantix.daily.plist` avec les **chemins absolus de cette machine**
   (utilisateur, dossier du repo) :

   ```xml
   <key>ProgramArguments</key>
   <array>
     <string>/bin/bash</string>
     <string>/Users/<user>/chemin/vers/cemantix/run_daily.sh</string>
   </array>
   ```

   Ajouter aussi (recommandé, absent du plist du repo mais présent sur cette machine-ci) :

   ```xml
   <key>StandardOutPath</key>
   <string>/Users/<user>/chemin/vers/cemantix/run_daily.log</string>
   <key>StandardErrorPath</key>
   <string>/Users/<user>/chemin/vers/cemantix/run_daily.log</string>
   ```

   Sans ça, un échec au démarrage du job (avant même la première ligne de `run_daily.sh`)
   ne laisse aucune trace nulle part.

4. Installer et charger l'agent :

   ```bash
   cp io.cemantix.daily.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.cemantix.daily.plist
   # (macOS récent : "bootstrap" remplace "load", plus fiable)
   ```

5. **Empêcher le Mac de dormir à l'heure du cron.** `launchd` ne réveille pas un Mac
   éteint, et un Mac endormi peut aussi rater l'horaire selon la configuration
   d'alimentation. Programmer un réveil planifié juste avant l'horaire du cron :

   ```bash
   sudo pmset repeat wakeorpoweron MTWRFSU 00:10:00   # pour un cron à 00h15
   ```

   Vérifier ensuite :

   ```bash
   pmset -g sched
   ```

6. Tester immédiatement sans attendre l'horaire :

   ```bash
   launchctl kickstart -k gui/$(id -u)/io.cemantix.daily
   tail -f run_daily.log
   ```

7. Confirmer côté GitHub que le commit `chore: solution YYYY-MM-DD [skip ci]` est bien
   arrivé sur `main`, et que les `solution.json` des jeux concernés portent la bonne date.

---

## Débogage — check-list en cas de site qui ne se met plus à jour

### 1. Le job a-t-il tourné récemment ?

```bash
launchctl print gui/$(id -u)/io.cemantix.daily
```

Regarder :
- `last exit code` — voir table des codes ci-dessous
- `state` — doit repasser à `not running` après chaque exécution (pas `running` en continu,
  signe d'un job bloqué)

### 2. Que disent les logs locaux ?

```bash
tail -100 run_daily.log
```

- **Aucune ligne récente du tout** (même pas `=== ... — Démarrage ===`) → le job n'a
  jamais été invoqué par launchd. Cause probable : plist mal chargé, Mac éteint/endormi
  à l'heure prévue, ou `StandardOutPath`/`StandardErrorPath` absents du plist (dans ce
  cas l'échec est invisible — voir étape 3 du "Mise en place").
- **Le script démarre mais s'arrête en cours** → `set -euo pipefail` en tête de
  `run_daily.sh` fait échouer tout le script à la première commande en erreur.
  Chercher la dernière ligne avant l'arrêt.

### 3. Le Mac était-il éveillé à l'heure du cron ?

```bash
pmset -g log | grep -E "Wake|Sleep" | tail -20
pmset -g sched
```

Si le Mac dormait à l'heure programmée et qu'aucun réveil planifié (`pmset repeat`)
n'est configuré, c'est la cause la plus probable d'un run manqué sans aucune trace.

### 4. Git : pull/push en échec ?

Causes classiques rencontrées avec `launchd` + `git` en SSH :
- Agent SSH non accessible depuis l'environnement launchd (voir étape 2 de mise en place)
- `git pull --rebase` en conflit si quelqu'un a modifié `docs/` manuellement entre deux runs
  (voir note dans la mémoire du projet : le cron peut aussi committer entre deux pushs manuels)
- Remote `origin` qui a changé d'URL (HTTPS vs SSH) sans que les credentials suivent

Tester manuellement dans les mêmes conditions que launchd (shell non interactif,
sans agrément de terminal) :

```bash
env -i /bin/bash run_daily.sh
```

### 5. Rattraper le retard manuellement en attendant la correction

```bash
git pull --rebase origin main
venv/bin/python generate.py
git add docs/
git commit -m "chore: solution $(date +'%Y-%m-%d') [skip ci]"
git push
venv/bin/python -c "import generate as g; g.ping_daily_indexnow()"
```

Vérifier ensuite que chaque `docs/<jeu>/solution.json` porte la date attendue :

```bash
for g in cemantix sutom loto euromillions; do
  echo -n "$g: "; python3 -c "import json;print(json.load(open('docs/$g/solution.json')).get('date'))"
done
```

(Pédantix n'a pas toujours de `solution.json` — la réponse du jour n'est publiée par
le site source que le lendemain ; c'est normal, voir `games/pedantix.py::run()`.)

---

## Table des codes de sortie launchd les plus fréquents

| Code | Signification | Piste |
|---|---|---|
| `0` | Succès | — |
| `78` (`EX_CONFIG`) | Erreur de configuration — souvent le job n'a même pas pu démarrer | Vérifier chemins absolus dans le plist, permissions d'exécution (`chmod +x run_daily.sh`), que le binaire `/bin/bash` existe bien à ce chemin |
| `126` | Commande trouvée mais non exécutable | `chmod +x run_daily.sh` |
| `127` | Commande introuvable | Chemin `venv/bin/python` incorrect, ou venv non créé sur cette machine |
| `1` | Erreur générique dans le script (`set -e`) | Voir la dernière ligne de `run_daily.log` avant l'arrêt |

---

## Après un incident (post-mortem rapide)

1. `git log --oneline --since="<date du dernier run connu>"` sur `docs/*/solution.json`
   pour dater précisément le début de la panne.
2. Comparer avec `pmset -g log` pour voir si le Mac dormait à ces horaires.
3. Une fois la cause corrigée, laisser tourner le cron nativement au moins un cycle avant
   de considérer l'incident clos (un rattrapage manuel ne prouve pas que launchd est réparé).
4. Si le plist a été modifié, penser à répercuter le nouvel horaire dans
   `io.cemantix.daily.plist` (repo), `INSTALL.md` et `ARCHITECTURE.md` pour éviter que
   la doc diverge de la prod (cas déjà constaté : doc à 08h05, prod à 00h15).
