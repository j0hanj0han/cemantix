"""
games/keno.py — Keno FDJ, formule en vigueur depuis le 3 novembre 2025.

Règles (règlement FDJ, décision ANJ 2025-PR-174) : un tirage par jour, 16 numéros
tirés parmi 56, le joueur coche de 4 à 10 numéros, mise de 1, 2, 3, 5 ou 10 € par
grille, option Multiplicateur (même prix que la mise) qui multiplie le gain par
2, 3 ou 5. Les gains ne se cumulent pas : une grille ne touche que son rang.

Pages générées :
  docs/keno/index.html                ← dernier tirage + 20 derniers tirages
  docs/keno/calcul-gains/index.html   ← calculateur, grille des gains, test de numéros
  docs/keno/stats/index.html          ← statistiques depuis la nouvelle formule
  docs/keno/tirages.json              ← [[date, [16 numéros], multiplicateur, forclusion], …]
                                        du plus ancien au plus récent (lu par le JS)

Pas de page d'archive par tirage : le site ne se positionne pas sur l'intention
« résultat », et ces pages restent « explorées, non indexées » chez Loto/EM.
"""

import csv
import io
import json
import zipfile
from collections import Counter
from datetime import date, datetime
from itertools import combinations
from math import comb

from core import (
    DOCS_DIR,
    SITE_URL,
    _session,
    atomic_write,
    date_fr,
    date_fr_short,
    faq_html,
    faq_jsonld,
    render_page,
)

KENO_DIR = DOCS_DIR / "keno"
KENO_SITE_URL = f"{SITE_URL}/keno"
DATA_PATH = KENO_DIR / "tirages.json"

# Zip officiel FDJ « Keno depuis novembre 2025 » (page historique des résultats fdj.fr).
FDJ_KENO_ZIP = (
    "https://www.sto.api.fdj.fr/anonymous/service-draw-info/v3/documentations/"
    "1a2b3c4d-9876-4562-b3fc-2c963f66bft6"
)
FDJ_KENO_URL = "https://www.fdj.fr/jeux-de-tirage/keno"

REFORM_DATE = date(2025, 11, 3)
DRAWN, POOL = 16, 56
STAKES = (1, 2, 3, 5, 10)
LIFE_ANNUITY = 10_000          # 10/10 pour 1 € : 200 000 € cash ou 10 000 €/an à vie
RECENT_N = 30

# Gains pour une mise de 1 € — {numéros cochés: {numéros trouvés: gain}}
PAYTABLE: dict[int, dict[int, int]] = {
    10: {10: 200_000, 9: 2_000, 8: 150, 7: 15, 6: 5, 5: 2, 0: 2},
    9: {9: 30_000, 8: 100, 7: 25, 6: 8, 5: 2, 4: 1, 0: 2},
    8: {8: 8_000, 7: 100, 6: 30, 5: 5, 0: 2},
    7: {7: 3_000, 6: 90, 5: 5, 4: 2},
    6: {6: 900, 5: 30, 4: 3},
    5: {5: 80, 4: 10, 3: 2},
    4: {4: 70, 3: 3},
}
# Probabilités officielles du coefficient (« 1 chance sur 1,49 / 3,70 / 16,67 »).
MULT_PROBS = {2: 0.67, 3: 0.27, 5: 0.06}
MULT_EXPECTED = sum(m * p for m, p in MULT_PROBS.items())   # 2,45

NBSP = " "
NNBSP = " "

RESPONSIBLE_HTML = (
    '    <p class="keno-responsible">Les jeux d\'argent sont interdits aux mineurs. '
    "Jouer comporte des risques : endettement, isolement, dépendance. Pour être aidé, "
    "appelez le 09&nbsp;74&nbsp;75&nbsp;13&nbsp;13 (appel non surtaxé). "
    "Site non officiel, sans lien avec la FDJ : seuls les résultats publiés par la FDJ font foi.</p>"
)


# ── Calculs ───────────────────────────────────────────────────────────────────

def hit_probability(n: int, k: int) -> float:
    """Probabilité de trouver k numéros quand on en coche n (loi hypergéométrique)."""
    if k < 0 or k > n or k > DRAWN or n - k > POOL - DRAWN:
        return 0.0
    return comb(DRAWN, k) * comb(POOL - DRAWN, n - k) / comb(POOL, n)


def win_probability(n: int) -> float:
    """Probabilité de toucher un gain quelconque avec n numéros cochés."""
    return sum(hit_probability(n, k) for k in PAYTABLE[n])


def expected_return(n: int) -> float:
    """Gain moyen pour 1 € misé sans multiplicateur (10/10 compté en cash)."""
    return sum(hit_probability(n, k) * g for k, g in PAYTABLE[n].items())


def expected_return_mult(n: int) -> float:
    """Gain moyen par euro dépensé avec le multiplicateur (la grille coûte 2× la mise)."""
    return expected_return(n) * MULT_EXPECTED / 2


# ── Formatage ─────────────────────────────────────────────────────────────────

def _int_fr(x: int) -> str:
    return f"{x:,}".replace(",", NNBSP)


def _eur(x: float) -> str:
    if float(x).is_integer():
        return f"{_int_fr(int(x))}{NBSP}€"
    return f"{x:,.2f}".replace(",", NNBSP).replace(".", ",") + f"{NBSP}€"


def _pct(p: float, digits: int = 1) -> str:
    return f"{p * 100:.{digits}f}".replace(".", ",") + f"{NBSP}%"


def _one_in(p: float) -> str:
    """'1 chance sur 4 446 435' / '1 chance sur 7,78'."""
    if p <= 0:
        return "—"
    v = 1 / p
    if v >= 100:
        s = _int_fr(int(v + 0.5))      # arrondi « au plus proche » comme la FDJ (110 372,5 → 110 373)
    else:
        s = f"{v:.2f}".replace(".", ",")
    return f"1{NBSP}chance sur {s}"


def _date_short(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def _found_order(keys) -> list[int]:
    """Rangs du plus haut au plus bas, le « 0 trouvé » en dernier."""
    return sorted(keys, key=lambda k: (k == 0, -k))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


# ── Données ───────────────────────────────────────────────────────────────────

def _parse_fr_date(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_csv(text: str) -> list[dict]:
    """Parse le CSV FDJ (séparateur ';', dates JJ/MM/AAAA). Ignore les lignes invalides
    et tout tirage antérieur à la nouvelle formule."""
    draws = []
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        d = _parse_fr_date(row.get("date_de_tirage"))
        if not d or d < REFORM_DATE:
            continue
        try:
            balls = sorted(int(row[f"boule{i}"]) for i in range(1, DRAWN + 1))
            mult = int(row["multiplicateur"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(set(balls)) != DRAWN or not all(1 <= b <= POOL for b in balls) or mult not in MULT_PROBS:
            continue
        forclusion = _parse_fr_date(row.get("date_de_forclusion"))
        draws.append({
            "date": d.isoformat(),
            "balls": balls,
            "mult": mult,
            "forclusion": forclusion.isoformat() if forclusion else "",
        })
    return draws


def fetch_draws() -> list[dict] | None:
    """Télécharge le zip FDJ et retourne les tirages depuis le 03/11/2025, ou None."""
    try:
        resp = _session.get(FDJ_KENO_ZIP, timeout=30)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                print("[Keno] ⚠ Aucun CSV dans le zip FDJ")
                return None
            raw = z.read(names[0])
    except Exception as e:
        print(f"[Keno] ⚠ Téléchargement impossible : {e}")
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    draws = parse_csv(text)
    if not draws:
        print("[Keno] ⚠ CSV FDJ illisible ou vide")
        return None
    return draws


def load_draws() -> list[dict]:
    if not DATA_PATH.exists():
        return []
    rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return [
        {"date": r[0], "balls": r[1], "mult": r[2], "forclusion": r[3] if len(r) > 3 else ""}
        for r in rows
    ]


def save_draws(draws: list[dict]) -> None:
    KENO_DIR.mkdir(parents=True, exist_ok=True)
    rows = [[d["date"], d["balls"], d["mult"], d["forclusion"]] for d in draws]
    atomic_write(DATA_PATH, json.dumps(rows, separators=(",", ":")))


def update_draws() -> list[dict]:
    """Fusionne les tirages téléchargés avec ceux déjà stockés (garde l'existant si la FDJ ne répond pas)."""
    stored = load_draws()
    merged = {d["date"]: d for d in stored}
    fetched = fetch_draws()
    if fetched:
        for d in fetched:
            merged[d["date"]] = d
    draws = sorted(merged.values(), key=lambda d: d["date"])
    if draws != stored:
        save_draws(draws)
        print(f"[Keno] {len(draws)} tirages ({len(draws) - len(stored):+d})")
    return draws


def latest_draw() -> dict | None:
    """Dernier tirage stocké (pour le hub), sans appel réseau."""
    draws = load_draws()
    return draws[-1] if draws else None


def compute_stats(draws: list[dict], recent_n: int = RECENT_N) -> dict:
    n = len(draws)
    numbers = range(1, POOL + 1)
    counts = Counter()
    last_seen = {b: -1 for b in numbers}
    max_gap = {b: 0 for b in numbers}
    pairs = Counter()
    for i, d in enumerate(draws):
        for b in d["balls"]:
            max_gap[b] = max(max_gap[b], i - last_seen[b] - 1)
            last_seen[b] = i
            counts[b] += 1
        pairs.update(combinations(d["balls"], 2))
    current_gap = {b: n - 1 - last_seen[b] for b in numbers}
    for b in numbers:
        max_gap[b] = max(max_gap[b], current_gap[b])
    recent = draws[-recent_n:]
    recent_counts = Counter(b for d in recent for b in d["balls"])
    by_count = sorted(numbers, key=lambda b: (-counts[b], b))
    return {
        "n": n,
        "date_from": draws[0]["date"] if draws else "",
        "date_to": draws[-1]["date"] if draws else "",
        "expected": n * DRAWN / POOL,
        "counts": {b: counts[b] for b in numbers},
        "current_gap": current_gap,
        "max_gap": max_gap,
        "last_seen": {b: draws[last_seen[b]]["date"] if last_seen[b] >= 0 else "" for b in numbers},
        "top": by_count[:10],
        "bottom": by_count[::-1][:10],
        "late": sorted(numbers, key=lambda b: (-current_gap[b], b))[:10],
        "recent_n": len(recent),
        "recent_counts": {b: recent_counts[b] for b in numbers},
        "hot": sorted(numbers, key=lambda b: (-recent_counts[b], b))[:8],
        "cold": sorted(numbers, key=lambda b: (recent_counts[b], b))[:8],
        "pairs": pairs.most_common(10),
        "pair_expected": n * comb(DRAWN, 2) / comb(POOL, 2),
        "mult_counts": Counter(d["mult"] for d in draws),
    }


# ── Fragments HTML ────────────────────────────────────────────────────────────

def _balls_html(balls: list[int], small: bool = False, highlight: set[int] | None = None) -> str:
    wrap = "keno-balls keno-balls-sm" if small else "keno-balls"
    cls = "keno-ball keno-ball-sm" if small else "keno-ball"
    hl = highlight or set()
    inner = "".join(
        f'<span class="{cls}{" keno-ball-hl" if b in hl else ""}">{b}</span>' for b in balls
    )
    return f'<div class="{wrap}">{inner}</div>'


def _mult_html(m: int) -> str:
    return f'<span class="keno-mult" title="Multiplicateur tiré">×{m}</span>'


def _section_nav(current: str) -> str:
    links = [("", "Derniers tirages"), ("calcul-gains/", "Calcul des gains"), ("stats/", "Statistiques")]
    prefix = "" if current == "" else "../"
    items = []
    for path, label in links:
        if path == current:
            items.append(f'<span class="keno-nav-current">{label}</span>')
        else:
            href = f"{prefix}{path}" if path else (prefix or "./")
            items.append(f'<a href="{href}">{label}</a>')
    return '    <nav class="keno-nav" aria-label="Rubriques Keno">' + "".join(items) + "</nav>"


def _footer_links(prefix: str) -> str:
    return (
        f'<a href="{prefix or "./"}">Keno</a> · '
        f'<a href="{prefix}calcul-gains/">Calcul des gains Keno</a> · '
        f'<a href="{prefix}stats/">Statistiques Keno</a> · '
        f'<a href="{prefix}../loto/simulateur/">Simulateur Loto</a> · '
        f'<a href="{prefix}../euromillions/simulateur/">Simulateur EuroMillions</a>'
    )


def _breadcrumb(*extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [("Accueil", f"{SITE_URL}/"), ("Keno", f"{KENO_SITE_URL}/"), *extra]


def _write(rel: str, html: str) -> None:
    out_dir = KENO_DIR / rel if rel else KENO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "index.html", html)
    print(f"[Keno] docs/keno/{rel}index.html")


# ── Page /keno/ ───────────────────────────────────────────────────────────────

def generate_index_html(draws: list[dict]) -> None:
    last = draws[-1]
    d_last = date.fromisoformat(last["date"])
    n = len(draws)
    recent = draws[::-1][:20]

    rows = "\n".join(
        f'          <tr><td class="keno-td-date">{_date_short(d["date"])}<br>{_mult_html(d["mult"])}</td>'
        f'<td>{_balls_html(d["balls"], small=True)}</td></tr>'
        for d in recent
    )
    forclusion = ""
    if last["forclusion"]:
        forclusion = (
            f' Les gagnants ont jusqu\'au <strong>{date_fr_short(date.fromisoformat(last["forclusion"]))}</strong> '
            "pour réclamer leur gain (date de forclusion indiquée par la FDJ)."
        )
    top = max(PAYTABLE, key=lambda k: win_probability(k))

    faq_items = [
        ("À quelle heure a lieu le tirage du Keno ?",
         "Depuis le 3 novembre 2025, le Keno FDJ n'a plus qu'un seul tirage par jour, le soir vers 20 h, "
         "contre deux (midi et soir) avec l'ancienne formule. Les numéros sont ajoutés sur cette page dès que la FDJ "
         "publie le fichier officiel des résultats."),
        ("Combien de numéros sont tirés au Keno ?",
         "16 numéros sont tirés parmi 56 à chaque tirage, ainsi qu'un coefficient multiplicateur (×2, ×3 ou ×5). "
         "Le joueur coche de 4 à 10 numéros sur sa grille."),
        ("Combien peut-on gagner au Keno ?",
         "Pour 1 € misé, 10 numéros trouvés sur 10 rapportent 200 000 € en une fois ou 10 000 € par an à vie. "
         "Avec une mise de 10 €, le gain passe à 2 millions d'euros, et jusqu'à 10 millions avec le multiplicateur ×5."),
        ("Où vérifier les résultats officiels du Keno ?",
         "Sur le site de la FDJ (fdj.fr), rubrique Keno, ou dans un point de vente. Cette page reprend le fichier "
         "officiel des résultats publié par la FDJ, mais seuls les résultats FDJ font foi."),
    ]

    body = f"""{_section_nav("")}

    <div class="card">
      <h2>Résultat du Keno du {date_fr(d_last)}</h2>
      <p>Les 16 numéros gagnants du tirage Keno du <strong>{date_fr(d_last)}</strong> :</p>
      {_balls_html(last["balls"])}
      <p class="keno-mult-line">Multiplicateur tiré : {_mult_html(last["mult"])}</p>
      <p style="margin-top:.75rem;">Combien rapporte votre grille ? Le
        <a href="calcul-gains/">calculateur de gains Keno</a> applique la grille officielle à vos numéros,
        à votre mise et au multiplicateur.{forclusion}</p>
    </div>

    <div class="card">
      <h2>Les 20 derniers tirages du Keno</h2>
      <p style="font-size:.9rem;color:var(--color-muted);margin-bottom:.75rem;">
        Du plus récent au plus ancien. L'historique complet ({n} tirages depuis le 3 novembre 2025) sert au
        <a href="calcul-gains/#test-numeros">test de vos numéros</a> et aux <a href="stats/">statistiques Keno</a>.
      </p>
      <div class="table-scroll">
      <table class="keno-table keno-draws">
        <thead><tr><th>Date et multi.</th><th>Numéros tirés</th></tr></thead>
        <tbody>
{rows}
        </tbody>
      </table>
      </div>
    </div>

    <div class="card">
      <h2>Comment fonctionne le Keno depuis le 3 novembre 2025</h2>
      <p>La FDJ a refondu le Keno le <strong>3 novembre 2025</strong>. L'ancienne formule tirait 20 numéros sur 70,
        deux fois par jour. La nouvelle tire <strong>16 numéros parmi 56</strong>, une seule fois par jour.</p>
      <ul class="keno-list">
        <li>Vous cochez de <strong>4 à 10 numéros</strong> par grille, pour une mise de 1, 2, 3, 5 ou 10&nbsp;€.</li>
        <li>Le gain dépend du nombre de numéros cochés et trouvés, et il est multiplié par la mise.
          À 8, 9 ou 10 numéros, ne trouver <strong>aucun numéro</strong> rapporte aussi 2&nbsp;€ pour 1&nbsp;€ misé.</li>
        <li>L'option <strong>Multiplicateur</strong> coûte autant que la mise et multiplie le gain par 2, 3 ou 5.
          Le coefficient est tiré au sort à chaque tirage.</li>
        <li>Le gros lot, 10 numéros sur 10, vaut <strong>200&nbsp;000&nbsp;€ ou 10&nbsp;000&nbsp;€ par an à vie</strong>
          pour 1&nbsp;€ misé.</li>
        <li>C'est à {top} numéros qu'on gagne le plus souvent : {_one_in(win_probability(top))}.
          Le détail figure dans la <a href="calcul-gains/#grille">grille des gains Keno</a>.</li>
      </ul>
    </div>
{faq_html(faq_items)}
{RESPONSIBLE_HTML}
"""
    html = render_page(
        title="Keno FDJ : dernier tirage, calcul des gains et statistiques",
        description=(
            f"Keno du {date_fr_short(d_last)} : les 16 numéros tirés et le multiplicateur ×{last['mult']}, "
            "les 20 derniers tirages, la grille des gains et un calculateur gratuit."
        ),
        canonical=f"{KENO_SITE_URL}/",
        h1="Keno FDJ",
        subtitle=f"Dernier tirage : {date_fr(d_last)} · calcul des gains · statistiques",
        body_html=body,
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("Keno", f"{KENO_SITE_URL}/")],
        css_rel="../css/style.css",
        jsonld=[{
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "Keno FDJ : derniers tirages",
            "url": f"{KENO_SITE_URL}/",
            "dateModified": last["date"],
            "inLanguage": "fr-FR",
        }],
        extra_head=faq_jsonld(faq_items),
        footer_links=_footer_links(""),
    )
    _write("", html)


# ── Page /keno/calcul-gains/ ──────────────────────────────────────────────────

_CALC_JS = """<script>
(function () {
  const PAY = __PAY__;
  const ODDS = __ODDS__;
  const LIFE = __LIFE__;
  const $ = (id) => document.getElementById(id);
  const eur = (x) => x.toLocaleString('fr-FR', {maximumFractionDigits: 2}) + '\\u00a0€';
  const odds = (v) => v >= 100 ? Math.round(v).toLocaleString('fr-FR')
    : v.toLocaleString('fr-FR', {maximumFractionDigits: v >= 10 ? 1 : 2});
  const plural = (n, w) => n + ' ' + w + (n > 1 ? 's' : '');
  const track = (path) => { if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({path: path, event: true}); };

  /* Calculateur : cochés / trouvés / mise / coefficient */
  const cn = $('kc-n'), ck = $('kc-k'), cs = $('kc-stake'), cm = $('kc-mult'), co = $('kc-out');
  function fillFound() {
    const n = +cn.value, prev = ck.value === '' ? 7 : +ck.value;
    ck.innerHTML = '';
    for (let k = n; k >= 0; k--) { const o = document.createElement('option'); o.value = k; o.textContent = k; ck.appendChild(o); }
    ck.value = Math.min(prev, n);
  }
  function calc() {
    const n = +cn.value, k = +ck.value, s = +cs.value, m = +cm.value;
    const base = PAY[n][k] || 0, gain = base * s * m, cost = s * (m > 1 ? 2 : 1);
    let h = '<p class="sim-result-rank">' + plural(k, 'numéro') + ' trouvé' + (k > 1 ? 's' : '') + ' sur ' + n + ' cochés</p>';
    if (gain > 0) {
      h += '<p class="sim-result-gain">' + eur(gain) + '</p>';
      if (n === 10 && k === 10) h += '<p class="sim-result-detail">ou ' + eur(LIFE * s * m) + ' par an à vie</p>';
      co.classList.add('win');
    } else {
      h += '<p class="sim-result-gain" style="color:var(--color-muted)">Pas de gain</p>';
      co.classList.remove('win');
    }
    h += '<p class="sim-result-detail">Prix de la grille : ' + eur(cost) + (m > 1 ? ' (multiplicateur compris)' : '')
      + ' · probabilité : 1 chance sur ' + odds(ODDS[n][k]) + '</p>';
    co.innerHTML = h;
  }
  cn.addEventListener('change', () => { fillFound(); calc(); track('keno-calcul'); });
  [ck, cs, cm].forEach((e) => e.addEventListener('change', () => { calc(); track('keno-calcul'); }));
  fillFound(); calc();

  /* Test de numéros sur tous les tirages */
  const grid = $('kn-grid'), count = $('kn-count'), counter = $('kn-counter'), run = $('kn-run'), out = $('kn-result');
  let picks = new Set(), DATA = null;
  function refresh() {
    const max = +count.value;
    grid.querySelectorAll('.sim-ball').forEach((b) => {
      const v = +b.dataset.n, on = picks.has(v);
      b.classList.toggle('selected', on);
      b.classList.toggle('dimmed', !on && picks.size >= max);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    counter.innerHTML = '<strong>' + picks.size + '</strong> / ' + max + ' numéros';
    run.disabled = picks.size !== max;
  }
  grid.addEventListener('click', (e) => {
    const b = e.target.closest('.sim-ball'); if (!b) return;
    const v = +b.dataset.n;
    if (picks.has(v)) picks.delete(v); else if (picks.size < +count.value) picks.add(v);
    refresh();
  });
  count.addEventListener('change', () => { picks = new Set([...picks].slice(0, +count.value)); refresh(); });
  $('kn-random').addEventListener('click', () => {
    const pool = Array.from({length: 56}, (_, i) => i + 1);
    for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
    picks = new Set(pool.slice(0, +count.value)); refresh();
  });
  $('kn-clear').addEventListener('click', () => { picks = new Set(); out.innerHTML = ''; refresh(); });

  const fmtDate = (iso) => iso.split('-').reverse().join('/');
  run.addEventListener('click', async () => {
    run.disabled = true;
    out.innerHTML = '<p class="sim-result-detail">Chargement des tirages…</p>';
    try {
      if (!DATA) DATA = await (await fetch('../tirages.json')).json();
    } catch (e) {
      out.innerHTML = '<p class="sim-result-detail">Impossible de charger les tirages. Réessayez plus tard.</p>';
      refresh(); return;
    }
    const n = picks.size, s = +$('kn-stake').value, useM = $('kn-mult').checked, pay = PAY[n];
    const dist = {}; let spent = 0, won = 0, wins = 0, best = null;
    for (const [d, balls, m] of DATA) {
      let hits = 0; for (const b of balls) if (picks.has(b)) hits++;
      const g = (pay[hits] || 0) * s * (useM ? m : 1);
      spent += s * (useM ? 2 : 1); won += g;
      (dist[hits] = dist[hits] || {c: 0, g: 0}).c++; dist[hits].g += g;
      if (g > 0) { wins++; if (!best || g > best.g) best = {d: d, hits: hits, m: m, g: g}; }
    }
    const bilan = won - spent;
    let h = '<div class="sim-summary">'
      + '<div class="sim-stat"><div class="sim-stat-value">' + DATA.length + '</div><div class="sim-stat-label">tirages joués</div></div>'
      + '<div class="sim-stat"><div class="sim-stat-value">' + eur(spent) + '</div><div class="sim-stat-label">dépensés</div></div>'
      + '<div class="sim-stat"><div class="sim-stat-value">' + eur(won) + '</div><div class="sim-stat-label">gagnés</div></div>'
      + '<div class="sim-stat ' + (bilan >= 0 ? 'bilan-pos' : 'bilan-neg') + '"><div class="sim-stat-value">' + (bilan >= 0 ? '+' : '') + eur(bilan) + '</div><div class="sim-stat-label">bilan</div></div>'
      + '</div>';
    h += '<p>Grille gagnante ' + wins + ' fois sur ' + DATA.length + ' tirages, soit ' + Math.round(won / spent * 100) + '\\u00a0% de la mise récupérée.';
    if (best) h += ' Meilleur gain : <strong>' + eur(best.g) + '</strong> le ' + fmtDate(best.d) + ' (' + best.hits + ' sur ' + n + (useM ? ', multiplicateur ×' + best.m : '') + ').';
    h += '</p><div class="table-scroll"><table class="sim-rank-table"><thead><tr><th>Trouvés</th><th>Tirages</th><th>Gain unitaire</th><th>Total gagné</th></tr></thead><tbody>';
    for (let k = n; k >= 0; k--) {
      if (!dist[k] && !pay[k]) continue;
      const c = dist[k] ? dist[k].c : 0, unit = (pay[k] || 0) * s;
      h += '<tr><td>' + k + ' / ' + n + '</td><td>' + c + '</td><td>' + (unit ? eur(unit) + (useM ? ' × multi.' : '') : '—') + '</td><td>' + (dist[k] && dist[k].g ? eur(dist[k].g) : '—') + '</td></tr>';
    }
    h += '</tbody></table></div>';
    out.innerHTML = h;
    track('keno-test-numeros');
    refresh();
  });
  refresh();
})();
</script>"""


def generate_calc_html(draws: list[dict]) -> None:
    n_draws = len(draws)
    last = date.fromisoformat(draws[-1]["date"])
    year = last.year
    mult_obs = Counter(d["mult"] for d in draws)

    # Grille des gains, une table par nombre de numéros cochés
    grids = []
    for n in sorted(PAYTABLE, reverse=True):
        rows = []
        for k in _found_order(PAYTABLE[n]):
            g = PAYTABLE[n][k]
            g1, g10 = _eur(g), _eur(g * 10)
            if n == 10 and k == 10:
                g1 += f" ou {_eur(LIFE_ANNUITY)}/an à vie"
                g10 += f" ou {_eur(LIFE_ANNUITY * 10)}/an à vie"
            rows.append(
                f"            <tr><td>{k} / {n}</td><td><strong>{g1}</strong></td><td>{g10}</td>"
                f"<td>{_one_in(hit_probability(n, k))}</td></tr>"
            )
        grids.append(f"""      <h3 class="keno-h3">Keno à {n} numéros — {_one_in(win_probability(n))} de gagner</h3>
      <div class="table-scroll">
      <table class="keno-table">
        <thead><tr><th>Trouvés</th><th>Gain pour 1&nbsp;€</th><th>Gain pour 10&nbsp;€</th><th>Probabilité</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
      </div>""")

    # Taux de retour par nombre de numéros
    trj_rows = "\n".join(
        f"          <tr><td>{n} numéros</td><td>{_one_in(win_probability(n))}</td>"
        f"<td>{_pct(expected_return(n))}</td><td>{_pct(expected_return_mult(n))}</td></tr>"
        for n in sorted(PAYTABLE, reverse=True)
    )
    best_trj = max(PAYTABLE, key=expected_return)
    worst_trj = min(PAYTABLE, key=expected_return)
    most_often = max(PAYTABLE, key=win_probability)

    mult_rows = "\n".join(
        f"          <tr><td>{_mult_html(m)}</td><td>{_pct(p, 0)} ({_one_in(p)})</td>"
        f"<td>{mult_obs.get(m, 0)} tirages ({_pct(mult_obs.get(m, 0) / n_draws)})</td></tr>"
        for m, p in MULT_PROBS.items()
    )
    mult_gain = MULT_EXPECTED / 2 - 1
    mult_avg = f"{MULT_EXPECTED:.2f}".replace(".", ",")

    # Sélecteurs
    def opts(values, selected, fmt=str):
        return "".join(
            f'<option value="{v}"{" selected" if v == selected else ""}>{fmt(v)}</option>' for v in values
        )
    n_opts = opts(range(10, 3, -1), 10)
    k_opts = opts(range(10, -1, -1), 7)
    stake_opts = opts(STAKES, 1, lambda v: f"{v} €")
    mult_opts = '<option value="1">Sans</option>' + "".join(f'<option value="{m}">×{m}</option>' for m in MULT_PROBS)
    count_opts = opts(range(4, 11), 10)
    grid_buttons = "".join(
        f'<button type="button" class="sim-ball keno-pick" data-n="{b}" aria-pressed="false">{b}</button>'
        for b in range(1, POOL + 1)
    )

    ex = PAYTABLE[10][7]
    faq_items = [
        ("Comment calculer ses gains au Keno ?",
         "Prenez le gain de la grille officielle pour 1 € correspondant au nombre de numéros cochés et trouvés, "
         f"multipliez-le par votre mise (1, 2, 3, 5 ou 10 €), puis par le coefficient tiré si vous avez pris le "
         f"multiplicateur. Exemple : 7 numéros trouvés sur 10 cochés rapportent {ex} € pour 1 €, {ex * 5} € pour 5 €, "
         f"et {ex * 5 * 3} € avec un multiplicateur ×3."),
        ("Combien rapporte 5 numéros sur 10 au Keno ?",
         f"{PAYTABLE[10][5]} € pour 1 € misé, soit {PAYTABLE[10][5] * 10} € pour une mise de 10 €. "
         f"Cela arrive environ {_one_in(hit_probability(10, 5))}."),
        ("Gagne-t-on quelque chose avec 0 numéro au Keno ?",
         "Oui, si vous jouez 8, 9 ou 10 numéros : n'en trouver aucun rapporte 2 € pour 1 € misé "
         f"({_one_in(hit_probability(10, 0))} à 10 numéros). À 4, 5, 6 ou 7 numéros, 0 trouvé ne rapporte rien."),
        ("Le multiplicateur Keno vaut-il le coup ?",
         f"Il double le prix de la grille et multiplie le gain par 2 ({_pct(MULT_PROBS[2], 0)} des tirages), "
         f"3 ({_pct(MULT_PROBS[3], 0)}) ou 5 ({_pct(MULT_PROBS[5], 0)}). Le coefficient moyen est de {mult_avg}, "
         "donc sur le papier le multiplicateur améliore le retour moyen d'environ "
         f"{round(mult_gain * 100)} %. Il reste inférieur à la mise : il fait surtout gagner plus quand on gagne."),
        ("À combien de numéros a-t-on le plus de chances de gagner au Keno ?",
         f"À {most_often} numéros : {_one_in(win_probability(most_often))} de toucher un gain, surtout de petits gains. "
         f"Le meilleur retour moyen est à {best_trj} numéros ({_pct(expected_return(best_trj))} de la mise) "
         f"et le moins bon à {worst_trj} numéros ({_pct(expected_return(worst_trj))})."),
        ("Les gains du Keno sont-ils imposables ?",
         "Non. En France, les gains des jeux de la FDJ ne sont pas imposés pour le joueur et ne se déclarent pas. "
         "Les intérêts produits ensuite par l'argent placé, eux, sont imposables."),
    ]

    body = f"""{_section_nav("calcul-gains/")}

    <div class="card" id="calculateur">
      <h2>Calculateur de gain Keno</h2>
      <p>Choisissez combien de numéros vous avez cochés et trouvés, votre mise et le multiplicateur tiré :
        le gain est calculé avec la grille officielle FDJ en vigueur depuis le 3 novembre 2025.</p>
      <div class="keno-controls">
        <label>Numéros cochés <select id="kc-n">{n_opts}</select></label>
        <label>Numéros trouvés <select id="kc-k">{k_opts}</select></label>
        <label>Mise <select id="kc-stake">{stake_opts}</select></label>
        <label>Multiplicateur <select id="kc-mult">{mult_opts}</select></label>
      </div>
      <div class="sim-result win" id="kc-out" aria-live="polite">
        <p class="sim-result-rank">7 numéros trouvés sur 10 cochés</p>
        <p class="sim-result-gain">{_eur(PAYTABLE[10][7])}</p>
        <p class="sim-result-detail">Prix de la grille : {_eur(1)} · probabilité : {_one_in(hit_probability(10, 7))}</p>
      </div>
    </div>

    <div class="card" id="test-numeros">
      <h2>Testez vos numéros sur les {n_draws} tirages depuis le 3 novembre 2025</h2>
      <p>Cochez vos numéros : on rejoue votre grille sur <strong>tous les tirages de la nouvelle formule</strong>,
        du 3 novembre 2025 au {date_fr_short(last)}, avec votre mise et, si vous le souhaitez, le multiplicateur
        réellement tiré chaque soir. Vous voyez ce que vous auriez dépensé, gagné, et combien de fois chaque rang serait sorti.</p>
      <div class="keno-controls">
        <label>Numéros à cocher <select id="kn-count">{count_opts}</select></label>
        <label>Mise par tirage <select id="kn-stake">{stake_opts}</select></label>
        <label class="keno-check"><input type="checkbox" id="kn-mult"> Multiplicateur</label>
      </div>
      <p class="sim-label">Vos numéros (1 à 56)</p>
      <div class="sim-grid keno-grid" id="kn-grid">{grid_buttons}</div>
      <p class="sim-counter" id="kn-counter"><strong>0</strong> / 10 numéros</p>
      <button class="sim-btn" id="kn-run" type="button" disabled>Tester sur {n_draws} tirages</button>
      <button class="sim-btn sim-btn-alt" id="kn-random" type="button">🎲 Grille au hasard</button>
      <button class="sim-btn sim-btn-alt" id="kn-clear" type="button">Effacer</button>
      <div id="kn-result" aria-live="polite"></div>
    </div>

    <div class="card" id="grille">
      <h2>Grille des gains Keno {year}</h2>
      <p>Voici les gains officiels du Keno depuis le 3 novembre 2025, pour une mise de 1&nbsp;€ et de 10&nbsp;€. Pour 2, 3 ou 5&nbsp;€,
        multipliez le gain pour 1&nbsp;€ par 2, 3 ou 5. Avec le multiplicateur, multipliez encore par le coefficient tiré.
        Les gains ne se cumulent pas : une grille ne touche que le rang de son nombre de numéros trouvés.
        Les probabilités sont calculées pour 16 numéros tirés sur 56.</p>
{chr(10).join(grids)}
    </div>

    <div class="card">
      <h2>Combien rapporte le Keno en moyenne ?</h2>
      <p>Le <strong>taux de retour</strong> est la part de la mise que la grille rend en moyenne, sur un très grand nombre de tirages.
        Il se calcule en multipliant chaque gain par sa probabilité. Le gros lot du 10 sur 10 est compté en cash.</p>
      <div class="table-scroll">
      <table class="keno-table">
        <thead><tr><th>Numéros cochés</th><th>Chance de gagner</th><th>Retour moyen</th><th>Avec multiplicateur</th></tr></thead>
        <tbody>
{trj_rows}
        </tbody>
      </table>
      </div>
      <p style="margin-top:.75rem;">Dans tous les cas, le Keno rend moins que ce qu'il coûte. Pour 100&nbsp;€ joués à
        {best_trj} numéros, la FDJ en reverse en moyenne {round(expected_return(best_trj) * 100)}&nbsp;€.
        C'est à {most_often} numéros qu'on gagne le plus souvent, mais il s'agit surtout de petits gains qui remboursent à peine la mise.</p>
    </div>

    <div class="card">
      <h2>Le multiplicateur Keno : x2, x3 ou x5</h2>
      <p>L'option Multiplicateur coûte <strong>le même prix que la mise</strong> : une grille à 2&nbsp;€ passe à 4&nbsp;€.
        À chaque tirage, la FDJ tire un coefficient qui multiplie tous les gains des grilles qui ont pris l'option,
        y compris le gain « 0 numéro trouvé ».</p>
      <div class="table-scroll">
      <table class="keno-table">
        <thead><tr><th>Coefficient</th><th>Probabilité officielle</th><th>Observé depuis le 03/11/2025</th></tr></thead>
        <tbody>
{mult_rows}
        </tbody>
      </table>
      </div>
      <p style="margin-top:.75rem;">Le coefficient moyen vaut {mult_avg} pour un prix doublé : le multiplicateur rend donc en
        moyenne environ {round(mult_gain * 100)}&nbsp;% de plus que la grille seule, sans dépasser la mise. En contrepartie, il double la dépense.</p>
    </div>

    <div class="card">
      <h2>10 numéros sur 10 : 200&nbsp;000&nbsp;€ ou un gain à vie</h2>
      <p>Pour 1&nbsp;€ misé, trouver les 10 numéros rapporte <strong>200&nbsp;000&nbsp;€ en une fois ou 10&nbsp;000&nbsp;€ par an à vie</strong>,
        au choix du gagnant. Le gain suit la mise : 2&nbsp;millions d'euros ou 100&nbsp;000&nbsp;€ par an pour 10&nbsp;€, et jusqu'à
        10&nbsp;millions d'euros avec 10&nbsp;€ et un multiplicateur ×5. La probabilité est de {_one_in(hit_probability(10, 10))}.</p>
      <p style="margin-top:.6rem;">Le règlement prévoit deux limites. Au-delà de 20&nbsp;millions d'euros de gains sur un même tirage,
        le gain à vie n'est plus proposé et le 10 sur 10 est versé en une fois. Le total des gains d'un tirage est plafonné
        à 75&nbsp;millions d'euros ; au-delà, les gains sont réduits à due proportion.</p>
    </div>
{faq_html(faq_items)}
{RESPONSIBLE_HTML}
"""
    js = (
        _CALC_JS
        .replace("__PAY__", json.dumps({n: PAYTABLE[n] for n in PAYTABLE}, separators=(",", ":")))
        .replace("__ODDS__", json.dumps(
            {n: {k: round(1 / hit_probability(n, k), 2) for k in range(n + 1)} for n in PAYTABLE},
            separators=(",", ":"),
        ))
        .replace("__LIFE__", str(LIFE_ANNUITY))
    )
    canonical = f"{KENO_SITE_URL}/calcul-gains/"
    html = render_page(
        title=f"Calcul gain Keno {year} : calculateur et grille des gains FDJ",
        description=(
            "Calculez votre gain Keno selon les numéros cochés, la mise et le multiplicateur, "
            f"et testez vos numéros sur les {n_draws} tirages de la nouvelle formule."
        ),
        canonical=canonical,
        h1="Calcul des gains Keno",
        subtitle=f"Grille officielle FDJ depuis le 3 novembre 2025 · test sur {n_draws} tirages réels",
        body_html=body,
        breadcrumb=_breadcrumb(("Calcul des gains", canonical)),
        css_rel="../../css/style.css",
        jsonld=[{
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": "Calculateur de gains Keno",
            "description": "Calcul du gain Keno selon les numéros cochés et trouvés, la mise et le multiplicateur, "
                           "et test d'une grille sur tous les tirages depuis le 3 novembre 2025.",
            "url": canonical,
            "applicationCategory": "UtilityApplication",
            "operatingSystem": "Web",
            "inLanguage": "fr-FR",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
        }],
        extra_head=faq_jsonld(faq_items),
        footer_links=_footer_links("../"),
        scripts=js,
    )
    _write("calcul-gains/", html)


# ── Page /keno/stats/ ─────────────────────────────────────────────────────────

def generate_stats_html(draws: list[dict], stats: dict | None = None) -> None:
    s = stats or compute_stats(draws)
    n = s["n"]
    d_from = date.fromisoformat(s["date_from"])
    d_to = date.fromisoformat(s["date_to"])
    exp = s["expected"]
    counts = s["counts"]

    def ball(b: int) -> str:
        return f'<span class="keno-ball keno-ball-sm">{b}</span>'

    def rank_rows(numbers: list[int]) -> str:
        return "\n".join(
            f"          <tr><td>{ball(b)}</td><td>{counts[b]}</td><td>{_pct(counts[b] / n)}</td>"
            f"<td>{'+' if counts[b] >= exp else ''}{round(counts[b] - exp):d}</td></tr>"
            for b in numbers
        )

    late_rows = "\n".join(
        f"          <tr><td>{ball(b)}</td><td>{_plural(s['current_gap'][b], 'tirage')}</td>"
        f"<td>{_date_short(s['last_seen'][b]) if s['last_seen'][b] else '—'}</td>"
        f"<td>{_plural(s['max_gap'][b], 'tirage')}</td></tr>"
        for b in s["late"]
    )
    pair_rows = "\n".join(
        f"          <tr><td>{ball(a)} {ball(b)}</td><td>{c}</td></tr>" for (a, b), c in s["pairs"]
    )
    max_count = max(counts.values()) or 1
    full_rows = "\n".join(
        f'          <tr><td>{ball(b)}</td><td>{counts[b]}</td>'
        f'<td><span class="keno-bar" style="width:{round(counts[b] / max_count * 100)}%"></span></td>'
        f"<td>{s['recent_counts'][b]}</td><td>{s['current_gap'][b]}</td><td>{s['max_gap'][b]}</td></tr>"
        for b in range(1, POOL + 1)
    )
    mc = s["mult_counts"]
    mult_rows = "\n".join(
        f"          <tr><td>{_mult_html(m)}</td><td>{mc.get(m, 0)}</td><td>{_pct(mc.get(m, 0) / n)}</td>"
        f"<td>{_pct(p, 0)}</td></tr>"
        for m, p in MULT_PROBS.items()
    )
    top, bottom, late = s["top"][0], s["bottom"][0], s["late"][0]
    hot = " ".join(ball(b) for b in s["hot"])
    cold = " ".join(ball(b) for b in s["cold"])
    period = f"du {date_fr_short(d_from)} au {date_fr_short(d_to)}"
    exp_txt = f"{exp:.1f}".replace(".", ",")
    pair_exp_txt = f"{s['pair_expected']:.1f}".replace(".", ",")

    faq_items = [
        ("Quel numéro sort le plus au Keno ?",
         f"Depuis la nouvelle formule ({period}), le {top} est le numéro le plus sorti : {counts[top]} fois sur {n} tirages, "
         f"pour {exp_txt} sorties attendues en moyenne. Suivent le {s['top'][1]} et le {s['top'][2]}."),
        ("Quel numéro sort le moins au Keno ?",
         f"Le {bottom}, sorti {counts[bottom]} fois sur {n} tirages depuis le 3 novembre 2025."),
        ("Quel numéro n'est pas sorti depuis longtemps au Keno ?",
         f"Au {date_fr_short(d_to)}, le {late} n'est pas sorti depuis {_plural(s['current_gap'][late], 'tirage')}. "
         "Un retard n'augmente pas ses chances au tirage suivant."),
        ("Les statistiques permettent-elles de gagner au Keno ?",
         "Non. Chaque tirage est indépendant : chacun des 56 numéros a toujours 16 chances sur 56 (28,6 %) de sortir, "
         "quel que soit son passé. Les écarts de fréquence observés sont normaux sur quelques centaines de tirages."),
        ("Quel multiplicateur sort le plus souvent au Keno ?",
         f"Le ×2, tiré {mc.get(2, 0)} fois sur {n} ({_pct(mc.get(2, 0) / n)}), pour une probabilité officielle de 67 %. "
         f"Le ×5 est sorti {mc.get(5, 0)} fois."),
    ]

    th = "<thead><tr><th>Numéro</th><th>Sorties</th><th>% des tirages</th><th>Écart à la moyenne</th></tr></thead>"
    body = f"""{_section_nav("stats/")}

    <div class="card">
      <h2>Statistiques Keno depuis la nouvelle formule</h2>
      <p>Ces statistiques portent sur les <strong>{n} tirages Keno</strong> {period}, tous tirés avec la formule actuelle
        (16 numéros sur 56). Les tirages de l'ancienne formule (20 numéros sur 70) ne sont pas comparables et sont exclus.
        La page est mise à jour chaque jour après le tirage.</p>
      <p style="margin-top:.6rem;">Chaque numéro sort en moyenne dans 28,6&nbsp;% des tirages, soit <strong>{exp_txt} sorties</strong>
        attendues sur la période. Le Keno est un jeu de hasard : ces chiffres décrivent le passé et ne prédisent pas le prochain tirage.</p>
    </div>

    <div class="card">
      <h2>Numéros les plus sortis au Keno</h2>
      <div class="table-scroll">
      <table class="keno-table">
        {th}
        <tbody>
{rank_rows(s["top"])}
        </tbody>
      </table>
      </div>
    </div>

    <div class="card">
      <h2>Numéros les moins sortis au Keno</h2>
      <div class="table-scroll">
      <table class="keno-table">
        {th}
        <tbody>
{rank_rows(s["bottom"])}
        </tbody>
      </table>
      </div>
    </div>

    <div class="card">
      <h2>Numéros chauds et froids sur les {s["recent_n"]} derniers tirages</h2>
      <p>🔥 Les plus sortis récemment :</p>
      <div class="keno-balls keno-balls-left">{hot}</div>
      <p style="margin-top:.6rem;">❄️ Les moins sortis récemment :</p>
      <div class="keno-balls keno-balls-left">{cold}</div>
    </div>

    <div class="card">
      <h2>Numéros en retard (écarts)</h2>
      <p>L'écart est le nombre de tirages écoulés depuis la dernière sortie d'un numéro. L'écart maximum est le plus long
        observé depuis le 3 novembre 2025.</p>
      <div class="table-scroll">
      <table class="keno-table">
        <thead><tr><th>Numéro</th><th>Écart actuel</th><th>Dernière sortie</th><th>Écart max</th></tr></thead>
        <tbody>
{late_rows}
        </tbody>
      </table>
      </div>
    </div>

    <div class="card">
      <h2>Paires de numéros les plus fréquentes</h2>
      <p>Deux numéros donnés sortent ensemble en moyenne {pair_exp_txt} fois sur {n} tirages.</p>
      <div class="table-scroll">
      <table class="keno-table">
        <thead><tr><th>Paire</th><th>Sorties ensemble</th></tr></thead>
        <tbody>
{pair_rows}
        </tbody>
      </table>
      </div>
    </div>

    <div class="card">
      <h2>Multiplicateur : x2, x3, x5 observés</h2>
      <div class="table-scroll">
      <table class="keno-table">
        <thead><tr><th>Coefficient</th><th>Tirages</th><th>Observé</th><th>Probabilité officielle</th></tr></thead>
        <tbody>
{mult_rows}
        </tbody>
      </table>
      </div>
    </div>

    <div class="card">
      <h2>Fréquence des 56 numéros du Keno</h2>
      <p>Pour chaque numéro : sorties depuis le 3 novembre 2025, sorties sur les {s["recent_n"]} derniers tirages,
        écart actuel et écart maximum (en tirages).</p>
      <div class="table-scroll">
      <table class="keno-table keno-freq">
        <thead><tr><th>Numéro</th><th>Sorties</th><th></th><th>{s["recent_n"]} derniers</th><th>Écart</th><th>Écart max</th></tr></thead>
        <tbody>
{full_rows}
        </tbody>
      </table>
      </div>
      <p style="margin-top:.75rem;">Envie de voir ce qu'aurait donné une grille avec ces numéros ?
        <a href="../calcul-gains/#test-numeros">Testez-la sur les {n} tirages</a>.</p>
    </div>
{faq_html(faq_items)}
{RESPONSIBLE_HTML}
"""
    canonical = f"{KENO_SITE_URL}/stats/"
    html = render_page(
        title="Statistiques Keno : numéros les plus et moins sortis depuis 2025",
        description=(
            f"Fréquence des 56 numéros du Keno sur {n} tirages depuis la nouvelle formule du 3 novembre 2025 : "
            "plus et moins sortis, écarts, paires et multiplicateur."
        ),
        canonical=canonical,
        h1="Statistiques Keno FDJ",
        subtitle=f"{n} tirages {period} · mis à jour chaque jour",
        body_html=body,
        breadcrumb=_breadcrumb(("Statistiques", canonical)),
        css_rel="../../css/style.css",
        jsonld=[{
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "Statistiques des tirages Keno FDJ depuis le 3 novembre 2025",
            "description": f"Fréquence des numéros, écarts, paires et multiplicateurs sur {n} tirages Keno FDJ.",
            "url": canonical,
            "temporalCoverage": f"{s['date_from']}/{s['date_to']}",
            "dateModified": s["date_to"],
            "inLanguage": "fr-FR",
            "isAccessibleForFree": True,
            "distribution": {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": f"{KENO_SITE_URL}/tirages.json",
            },
            "creator": {"@type": "Organization", "name": "Solutions du Jour", "url": f"{SITE_URL}/"},
        }],
        extra_head=faq_jsonld(faq_items),
        footer_links=_footer_links("../"),
    )
    _write("stats/", html)


# ── Points d'entrée ───────────────────────────────────────────────────────────

def generate_all(draws: list[dict]) -> None:
    generate_index_html(draws)
    generate_calc_html(draws)
    generate_stats_html(draws)


def regenerate() -> dict | None:
    """Régénère les pages depuis docs/keno/tirages.json, sans réseau (GH Actions)."""
    draws = load_draws()
    if not draws:
        return None
    generate_all(draws)
    return draws[-1]


def run(today: date) -> dict | None:
    """Run quotidien : met à jour les tirages puis régénère les 3 pages. Retourne le dernier tirage."""
    print(f"[Keno] Mise à jour des tirages ({today.isoformat()})…")
    draws = update_draws()
    if not draws:
        print("[Keno] ⚠ Aucun tirage disponible — pages non générées")
        return None
    generate_all(draws)
    last = draws[-1]
    print(f"[Keno] Dernier tirage : {last['date']} (×{last['mult']})")
    return last
