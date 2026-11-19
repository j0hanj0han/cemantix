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
       │  │  │ Tusmo      → tusmo.xyz API (partie invitée)    │    │
       │  │  │ Wordle     → nytimes.com (JSON du jour)        │    │
       │  │  │              + Wiktionnaire (sens en français) │    │
       │  │  │ Loto       → OpenDataSoft API (dernier tirage) │    │
       │  │  │              + tirage-gagnant.com (jackpot)    │    │
       │  │  │              + reducmiz.com (nb gagnants)      │    │
       │  │  │ EuroMillions→ euro-millions.com (scraping HTML)│    │
       │  │  │              + pedro-mealha API (jackpot)      │    │
       │  │  │              + tirage-gagnant.com (prochain)   │    │
       │  │  │ Keno       → zip CSV FDJ (sto.api.fdj.fr)      │    │
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
| Tusmo        | tous les jours       | minuit   | tusmo.xyz (API)        |
| Wordle       | tous les jours       | minuit   | nytimes.com (JSON)     |
| Loto         | lun / mer / sam      | ~20h20   | OpenDataSoft           |
| EuroMillions | mar / ven            | ~21h05   | euro-millions.com      |
| Keno         | tous les jours       | ~20h00   | zip CSV FDJ            |

> Le cron tourne à **08h05** → récupère le tirage/puzzle de la veille ou du matin même.

## Fonctions backfill (one-shot, jamais appelées automatiquement)

```bash
# Importer les ~60 derniers jours Tusmo encore jouables en mode archive sur tusmo.xyz
python -c "from games.tusmo import backfill_archives; backfill_archives()"

# Archiver les Wordle passés depuis une date (1 requête NYT + Wiktionnaire par jour)
python -c "from datetime import date; from games.wordle import backfill_archives; backfill_archives(date(2026, 10, 1))"

# Télécharger tout l'historique Loto depuis 2019
python -c "from games.loto import backfill_archives; backfill_archives()"

# Enrichir les archives Loto avec données jackpot (reducmiz, ~2400 tirages)
python -c "from games.loto import backfill_loto_jackpot; backfill_loto_jackpot()"

# Télécharger tout l'historique EuroMillions depuis 2004
python -c "from games.euromillions import backfill_euromillions; backfill_euromillions()"

# Enrichir les archives EuroMillions avec données jackpot (pedro-mealha)
python -c "from games.euromillions import enrich_archives_with_jackpot; enrich_archives_with_jackpot()"

# Générer les images sociales manquantes (og_images.py, Pillow requis) depuis une date
python -c "from datetime import date; import og_images; print(og_images.backfill(date(2026, 9, 1)))"
```

**Images sociales (`og_images.py`)** : chaque `_generate_all_html` crée `docs/<jeu>/img/YYYY-MM-DD.png`
(1200×630, sans spoiler) utilisée par `og:image`, `twitter:image` et le champ `image` du NewsArticle
via `core.og_image_url()`. Sans Pillow (GitHub Actions) ou pour les vieilles archives, repli sur
`/og-image.png` ; une image existante n'est jamais réécrite (régénération idempotente).

## Sources de données par jeu

### Tusmo
| Donnée              | Source                  | Méthode         |
|---------------------|-------------------------|-----------------|
| N°, longueur, 1re lettre | `GET /api/daily/fr/meta` | session invitée partagée |
| Mot du jour         | `POST /api/game` (daily) + 6 essais valides → `answer` | `giveup` refusé en daily |
| Jours passés (≤ 60 j) | `POST /api/game` (archive) + `giveup` | rattrapage J-1 + backfill |

> Une seule session invitée par processus : l'API renvoie 429 si on crée trop d'invités.

### Wordle
| Donnée              | Source                  | Méthode         |
|---------------------|-------------------------|-----------------|
| Mot du jour, n°     | `GET nytimes.com/svc/wordle/v2/AAAA-MM-JJ.json` (`solution`, `days_since_launch`) | `_session.get` |
| Sens en français    | Wiktionnaire FR, section `{{langue|en}}` | `_extract_en_senses` |
| Définition de secours | Wiktionnaire EN (`/api/rest_v1/page/definition/`) | si le Wiktionnaire FR n'a pas le mot |

> Toute date passée est disponible : rattrapage J-1 automatique et backfill sans limite.
> L'accès automatisé est contraire aux CGU du New York Times (risque accepté, une requête par jour).

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

### Keno
| Donnée              | Source                  | Méthode         |
|---------------------|-------------------------|-----------------|
| Tous les tirages depuis le 03/11/2025 | zip CSV FDJ `sto.api.fdj.fr/.../documentations/1a2b3c4d-…-2c963f66bft6` | `_session.get` à chaque run |

Formule du 3 novembre 2025 : 16 numéros sur 56, 1 tirage par jour, multiplicateur x2/x3/x5.
Les tirages de l'ancienne formule (20 sur 70) sont exclus. `docs/keno/tirages.json` est la seule
source des pages : si la FDJ ne répond pas, on garde l'existant. Pas de page d'archive par tirage.
La grille des gains (`PAYTABLE`) et les probabilités du multiplicateur viennent du règlement FDJ
(décision ANJ 2025-PR-174) : à mettre à jour à la main si la FDJ change la formule.

## Structure des fichiers générés

```
docs/
├── index.html                        ← hub (tous les jeux)
├── logo.png                          ← publisher.logo du JSON-LD
├── sitemap.xml
├── css/style.css
├── cemantix/
│   ├── index.html                    ← solution du jour
│   ├── solution.json
│   ├── img/YYYY-MM-DD.png            ← image sociale datée (og_images.py)
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
├── euromillions/
│   ├── index.html
│   ├── solution.json                 ← {date, balls, stars, jackpot_*, next_jackpot}
│   ├── stats/index.html
│   └── archive/
└── keno/
    ├── index.html                    ← dernier tirage + 20 derniers
    ├── tirages.json                  ← [[date, [16 numéros], multiplicateur, forclusion], …]
    ├── calcul-gains/index.html       ← calculateur + grille + test sur tous les tirages
    └── stats/index.html
```
