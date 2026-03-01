# Solutions du Jour — Guide d'installation et déploiement

Site statique multi-jeux publiant automatiquement les solutions quotidiennes de
[Cémantix](https://cemantix.certitudes.org) et [Sutom](https://sutom.nocle.fr) sur GitHub Pages.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  TON MAC  (IP résidentielle — non bloquée par Cémantix)             │
│                                                                     │
│  ⏰ launchd  →  run_daily.sh  →  generate.py                        │
│  (08h05)                         │                                  │
│                                  ├─ 1. games/cemantix.run()         │
│                                  │     → résout via word2vec        │
│                                  ├─ 2. games/sutom.run()            │
│                                  │     → récupère via API           │
│                                  ├─ 3. Hub + sitemap global         │
│                                  └─ 4. git commit + git push ──────►│
└─────────────────────────────────────────────────────────────────────┘
                                                                      │
                                                          push vers   │
                                                          GitHub      │
                                                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│  GITHUB                                                             │
│                                                                     │
│  push docs/cemantix/solution.json                                   │
│       docs/sutom/solution.json                                      │
│        │                                                            │
│        ▼                                                            │
│  daily.yml (GitHub Actions)                                         │
│        ├─ solution Cémantix à jour ? → _generate_all_html()        │
│        ├─ solution Sutom à jour ?    → _generate_all_html()        │
│        ├─ hub page + sitemap global                                 │
│        └─ git commit + git push (si HTML changé)                   │
│                                                                     │
│  branch main / docs/  ──►  GitHub Pages                            │
└─────────────────────────────────────────────────────────────────────┘
                                                                      │
                                                                      ▼
                              https://j0hanj0han.github.io/cemantix/

Fichiers générés chaque jour :
  docs/index.html                       ← hub multi-jeux
  docs/sitemap.xml                      ← sitemap global
  docs/cemantix/index.html              ← solution + indices Cémantix
  docs/cemantix/solution.json           ← {date, puzzle_num, word, hints, tried_count}
  docs/cemantix/archive/YYYY-MM-DD.json
  docs/cemantix/archive/YYYY-MM-DD.html
  docs/cemantix/archive/index.html
  docs/sutom/index.html                 ← solution Sutom
  docs/sutom/solution.json              ← {date, puzzle_num, word, letter_count, first_letter}
  docs/sutom/archive/YYYY-MM-DD.json
  docs/sutom/archive/YYYY-MM-DD.html
  docs/sutom/archive/index.html
```

**Pourquoi le solveur Cémantix tourne en local ?**
L'API Cémantix bloque les IPs des datacenters GitHub Actions (Cloudflare). Le Mac utilise une IP résidentielle non bloquée. GitHub Actions ne fait que régénérer le HTML à partir du JSON déjà produit.

Sutom n'a pas cette contrainte — son API est publique — mais on le génère aussi en local pour simplifier.

---

## Structure du code

```
cemantix/
├── core.py               ← utilitaires partagés (session, date_fr, atomic_write, load_all_archives)
├── games/
│   ├── cemantix.py       ← logique Cémantix complète (API, hints, HTML)
│   ├── solver.py         ← algorithme word2vec (phases seeds→reconstruction→itération)
│   └── sutom.py          ← récupération Sutom + HTML
├── generate.py           ← orchestrateur (hub + sitemap global)
├── run_daily.sh          ← script lancé par launchd
├── io.cemantix.daily.plist
└── docs/                 ← racine GitHub Pages
```

---

## Prérequis

- macOS (pour le cron launchd)
- Python 3.11+
- Git configuré avec accès SSH ou token au repo GitHub
- Modèle word2vec français : `frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin` (≈120 Mo, non commité)

---

## 1. Cloner le dépôt

```bash
git clone git@github.com:j0hanj0han/cemantix.git
cd cemantix
```

---

## 2. Créer le virtualenv et installer les dépendances

```bash
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

Vérifier l'installation :

```bash
venv/bin/python -c "import cloudscraper, gensim, bs4; print('OK')"
```

---

## 3. Télécharger le modèle word2vec (Cémantix uniquement)

Le fichier `.bin` n'est pas dans le repo (`.gitignore`). Le placer à la racine du projet :

```bash
# Télécharger depuis embeddings.org (≈120 Mo)
# https://embeddings.net/embeddings/frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin
curl -L -o frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin "URL_DU_MODELE"
```

---

## 4. Tester en local

```bash
# Lancement complet (Cémantix + Sutom + hub + sitemap)
venv/bin/python generate.py

# Ou via le script cron (identique à ce que fait launchd)
bash run_daily.sh
```

Vérifier le résultat :

```bash
cat docs/cemantix/solution.json   # solution Cémantix
cat docs/sutom/solution.json      # solution Sutom
open docs/index.html              # hub
open docs/cemantix/index.html     # page Cémantix
open docs/sutom/index.html        # page Sutom
```

**Mode régénération uniquement** — si les `solution.json` contiennent déjà la solution du jour,
`generate.py` ne relance pas les solveurs et régénère juste le HTML :

```bash
venv/bin/python generate.py  # détecte automatiquement
```

Voir les logs du cron local :

```bash
tail -f run_daily.log
```

---

## 5. Configurer le cron launchd (macOS)

Le fichier `io.cemantix.daily.plist` configure launchd pour lancer `run_daily.sh` chaque jour à **08h05**.

### Installer le plist

```bash
cp io.cemantix.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/io.cemantix.daily.plist
```

### Vérifier que l'agent est chargé

```bash
launchctl list | grep cemantix
# Doit afficher : -  0  io.cemantix.daily
```

### Lancer manuellement (pour tester)

```bash
launchctl start io.cemantix.daily
tail run_daily.log
```

### Décharger/supprimer l'agent

```bash
launchctl unload ~/Library/LaunchAgents/io.cemantix.daily.plist
```

### Notes

- Si le Mac est **éteint** à 08h05, le cron ne tourne pas ce jour-là (`RunAtLoad: false`).
- Les logs sont dans `run_daily.log` (exclu du git).
- Pour changer l'heure, modifier `Hour` et `Minute` dans le plist, puis `unload` + `load`.

---

## 6. Configurer GitHub Pages

Dans les **Settings** du repo GitHub :

1. `Pages` → Source : **Deploy from a branch**
2. Branch : `main`, Folder : `/docs`
3. Sauvegarder

Le site sera disponible sur : `https://j0hanj0han.github.io/cemantix/`

---

## 7. Vérifier le workflow GitHub Actions

Le workflow `.github/workflows/daily.yml` se déclenche sur push vers
`docs/cemantix/solution.json` ou `docs/sutom/solution.json`.

Pour déclencher manuellement :

```bash
gh workflow run daily.yml
# Ou : Actions → "Regenerate HTML from solution" → Run workflow
```

Pour vérifier les dernières exécutions :

```bash
gh run list --workflow=daily.yml
```

---

## Résolution de problèmes

| Symptôme | Cause probable | Solution |
|---|---|---|
| `ModuleNotFoundError: cloudscraper` | cloudscraper pas installé dans le venv | `venv/bin/pip install cloudscraper` |
| `0 appels API` dans les logs | IP bloquée par Cloudflare | Normal depuis un datacenter. Le Mac (IP résidentielle) contourne ça |
| Puzzle number incorrect | Site Cémantix inaccessible | Fallback automatique par calcul de date. Mettre à jour `_REF_DATE`/`_REF_PUZZLE` dans `games/cemantix.py` si décalé |
| Solution Sutom `None` | UUID ou URL Sutom changé | Vérifier `js/instanceConfiguration.js` sur sutom.nocle.fr pour le nouvel UUID (`idPartieParDefaut`) |
| Rien dans les archives | Premier jour d'utilisation | Normal — les archives s'accumulent à partir du lendemain |
| GitHub Actions échoue | `solution.json` pas pour aujourd'hui | Normal — le Mac n'a pas encore tourné. Le workflow attend le push local |
