# Cémantix Solution — Guide d'installation et déploiement

Site statique publiant automatiquement la solution quotidienne de [Cémantix](https://cemantix.certitudes.org) sur GitHub Pages.

---

## Architecture

```
Mac (cron launchd, 08h05)
  └─ run_daily.sh
       └─ generate.py  ──► résout Cémantix via API (IP résidentielle)
                       ──► génère docs/ (HTML, JSON, archives)
                       └─ git push

GitHub (déclenché sur push de docs/solution.json)
  └─ .github/workflows/daily.yml
       └─ régénère le HTML si solution.json est pour aujourd'hui

GitHub Pages (automatique)
  └─ sert docs/ depuis branch main
```

**Pourquoi le solveur tourne en local ?**
L'API Cémantix bloque les IPs des datacenters GitHub Actions. Le Mac utilise une IP résidentielle non bloquée.

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
venv/bin/pip install cloudscraper  # si pas déjà dans requirements.txt
```

Vérifier l'installation :

```bash
venv/bin/python -c "import cloudscraper, gensim, bs4; print('OK')"
```

---

## 3. Télécharger le modèle word2vec

Le fichier `.bin` n'est pas dans le repo (`.gitignore`). Le placer à la racine du projet :

```bash
# Télécharger depuis embeddings.org (≈120 Mo)
# https://embeddings.net/embeddings/frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin
# Ou via curl :
curl -L -o frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin "URL_DU_MODELE"
```

---

## 4. Tester en local

```bash
bash run_daily.sh
```

Ou directement :

```bash
venv/bin/python generate.py
```

Vérifier le résultat :

```bash
# La solution du jour
cat docs/solution.json

# Le site généré
open docs/index.html
```

**Mode régénération uniquement** (si `docs/solution.json` contient déjà la solution du jour,
`generate.py` ne relance pas le solveur — il régénère juste le HTML) :

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
# Copier dans le dossier LaunchAgents de l'utilisateur
cp io.cemantix.daily.plist ~/Library/LaunchAgents/

# Charger l'agent (active le cron)
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
# Puis vérifier les logs :
tail run_daily.log
```

### Décharger/supprimer l'agent

```bash
launchctl unload ~/Library/LaunchAgents/io.cemantix.daily.plist
```

### Notes

- Si le Mac est **éteint** à 08h05, le cron ne tourne pas ce jour-là (`RunAtLoad: false`).
- Les logs sont dans `run_daily.log` (exclu du git).
- Pour changer l'heure, modifier `Hour` et `Minute` dans le plist, puis recharger avec `unload` + `load`.

---

## 6. Configurer GitHub Pages

Dans les **Settings** du repo GitHub :

1. `Pages` → Source : **Deploy from a branch**
2. Branch : `main`, Folder : `/docs`
3. Sauvegarder

Le site sera disponible sur : `https://j0hanj0han.github.io/cemantix/`

---

## 7. Vérifier le workflow GitHub Actions

Le workflow `.github/workflows/daily.yml` se déclenche automatiquement sur chaque push vers `docs/solution.json`.

Pour déclencher manuellement :

```bash
# Via GitHub CLI
gh workflow run daily.yml

# Ou dans l'interface GitHub : Actions → "Regenerate HTML from solution" → Run workflow
```

Pour vérifier les dernières exécutions :

```bash
gh run list --workflow=daily.yml
```

---

## Structure des fichiers générés

```
docs/
├── index.html              ← solution du jour (mise à jour quotidiennement)
├── solution.json           ← {date, puzzle_num, word, hints, tried_count}
├── sitemap.xml             ← mis à jour quotidiennement
├── robots.txt              ← statique
├── css/style.css           ← statique, mobile-first
└── archive/
    ├── index.html          ← liste de toutes les solutions passées
    ├── 2026-02-28.json     ← données JSON de chaque jour
    ├── 2026-02-28.html     ← page HTML avec nav prev/next
    └── ...
```

---

## Mettre à jour les dépendances

```bash
venv/bin/pip install --upgrade -r requirements.txt
```

---

## Résolution de problèmes

| Symptôme | Cause probable | Solution |
|---|---|---|
| `ModuleNotFoundError: cloudscraper` | cloudscraper pas installé dans le venv | `venv/bin/pip install cloudscraper` |
| `0 appels API` dans les logs | IP bloquée par Cloudflare | Normal si lancé depuis un datacenter. Le Mac (IP résidentielle) contourne ça |
| Puzzle number incorrect | Site Cémantix inaccessible | Fallback automatique par calcul de date. Mettre à jour `_REF_DATE`/`_REF_PUZZLE` dans `generate.py` si décalé |
| Rien dans les archives | Premier jour d'utilisation | Normal — les archives s'accumulent à partir du lendemain |
| GitHub Actions échoue | `solution.json` pas pour aujourd'hui | Normal — le Mac n'a pas encore tourné. Le workflow attend le push local |
