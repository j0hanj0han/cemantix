# Architecture — solution-du-jour.fr

## Flux quotidien

```
08h05 (Mac launchd)
       │
       ▼
 run_daily.sh
       │
       ├─ python generate.py
       │         │
       │    ┌────┴──────────────────────────────────────────────┐
       │    │                                                   │
       │    ▼                                                   ▼
       │  Cémantix / Sutom                           Loto / EuroMillions
       │  (puzzles quotidiens)                       (tirages selon calendrier)
       │
       │  Pour chaque jeu → run(today)
       │  ┌─────────────────────────────────────────────────────────┐
       │  │                                                         │
       │  │  Fetch API externe                                      │
       │  │  ┌────────────────────────────────────────────────┐    │
       │  │  │ Cémantix   → cemantix.io (POST /score, /nearby)│    │
       │  │  │              + word2vec local                   │    │
       │  │  │ Sutom      → sutom.nocle.fr (GET mot chiffré)  │    │
       │  │  │ Loto       → OpenDataSoft API (dernier tirage) │    │
       │  │  │              + tirage-gagnant.com (jackpot)    │    │
       │  │  │              + reducmiz.com (nb gagnants)      │    │
       │  │  │ EuroMillions→ euro-millions.com (scraping HTML)│    │
       │  │  │              + pedro-mealha API (jackpot)      │    │
       │  │  │              + tirage-gagnant.com (prochain)   │    │
       │  │  └────────────────────────────────────────────────┘    │
       │  │                                                         │
       │  │  Déjà sauvegardé aujourd'hui ?                         │
       │  │                                                         │
       │  │  OUI ──────────────────────────────────────────────┐   │
       │  │  │  Mettre à jour jackpot/next_jackpot si manquant │   │
       │  │  │  Régénérer tout le HTML depuis les archives JSON │   │
       │  │  └─────────────────────────────────────────────────┘   │
       │  │                                                         │
       │  │  NON ──────────────────────────────────────────────┐   │
       │  │  │  Sauvegarder docs/{jeu}/solution.json            │   │
       │  │  │  Sauvegarder docs/{jeu}/archive/YYYY-MM-DD.json  │   │
       │  │  │  Générer tout le HTML (index + archives + stats) │   │
       │  │  └─────────────────────────────────────────────────┘   │
       │  │                                                         │
       │  └─────────────────────────────────────────────────────────┘
       │
       ├─ generate_hub_html()     → docs/index.html
       ├─ generate_global_sitemap() → docs/sitemap.xml
       │
       ├─ git add docs/
       ├─ git diff --staged → rien ? stop. sinon →
       ├─ git commit "chore: solution YYYY-MM-DD [skip ci]"
       └─ git push → GitHub → GitHub Pages → solution-du-jour.fr
```

## Calendrier des tirages

| Jeu          | Jours de tirage      | Heure    | Source principale      |
|--------------|----------------------|----------|------------------------|
| Cémantix     | tous les jours       | minuit   | cemantix.io            |
| Sutom        | tous les jours       | minuit   | sutom.nocle.fr         |
| Loto         | lun / mer / sam      | ~20h20   | OpenDataSoft           |
| EuroMillions | mar / ven            | ~21h05   | euro-millions.com      |

> Le cron tourne à **08h05** → récupère le tirage/puzzle de la veille ou du matin même.

## Fonctions backfill (one-shot, jamais appelées automatiquement)

```bash
# Télécharger tout l'historique Loto depuis 2019
python -c "from games.loto import backfill_archives; backfill_archives()"

# Enrichir les archives Loto avec données jackpot (reducmiz, ~2400 tirages)
python -c "from games.loto import backfill_loto_jackpot; backfill_loto_jackpot()"

# Télécharger tout l'historique EuroMillions depuis 2004
python -c "from games.euromillions import backfill_euromillions; backfill_euromillions()"

# Enrichir les archives EuroMillions avec données jackpot (pedro-mealha)
python -c "from games.euromillions import enrich_archives_with_jackpot; enrich_archives_with_jackpot()"
```

## Sources de données par jeu

### Loto
| Donnée              | Source                  | Méthode         |
|---------------------|-------------------------|-----------------|
| Tirage du jour      | OpenDataSoft API        | `_session.get`  |
| Jackpot exact       | tirage-gagnant.com      | `fetch_static_html` |
| Nb gagnants jackpot | reducmiz.com            | `_session.get` + BeautifulSoup |
| Historique complet  | OpenDataSoft (pagination) | backfill only |

### EuroMillions
| Donnée              | Source                  | Méthode         |
|---------------------|-------------------------|-----------------|
| Tirage du jour      | euro-millions.com       | `_session.get` + BeautifulSoup |
| Jackpot (montant, gagnants) | pedro-mealha API | `_session.get` |
| Prochain jackpot    | tirage-gagnant.com      | `fetch_static_html` |
| Historique complet  | FDJ CSV zips            | backfill only   |

## Structure des fichiers générés

```
docs/
├── index.html                        ← hub (tous les jeux)
├── sitemap.xml
├── css/style.css
├── cemantix/
│   ├── index.html                    ← solution du jour
│   ├── solution.json
│   └── archive/
│       ├── index.html
│       ├── YYYY-MM-DD.json
│       └── YYYY-MM-DD.html
├── sutom/                            ← même structure
├── loto/
│   ├── index.html
│   ├── solution.json                 ← {date, draw_num, balls, lucky_ball, jackpot_*}
│   ├── stats/index.html              ← statistiques (2600+ tirages)
│   └── archive/
└── euromillions/
    ├── index.html
    ├── solution.json                 ← {date, balls, stars, jackpot_*, next_jackpot}
    ├── stats/index.html
    └── archive/
```
