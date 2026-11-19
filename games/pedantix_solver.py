"""
games/pedantix_solver.py — Solveur Pédantix (https://pedantix.certitudes.org/).

Pédantix masque un article Wikipédia : la page donne la longueur de chaque mot
(emplacements `.w`, ceux du titre portent aussi la classe `h`). `POST /score`
renvoie, pour un mot proposé :
  - les positions où il apparaît exactement (variantes comprises : le → la/les/l'),
  - pour les autres positions, la similarité cosinus ×100 (clé "#58.87") quand elle
    dépasse un seuil (~0.30).

Le serveur utilise le même modèle word2vec que Cémantix
(frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin) : chaque emplacement
masqué se triangule comme un mini-Cémantix dont on connaît la longueur.

Algorithme :
  1. Mots outils (de, le, est…) puis mots-thèmes variés → squelette + premiers scores.
  2. Boucle : pour chaque emplacement du titre ayant des scores, proposer le
     candidat (même longueur) le plus cohérent avec ces scores ; idem pour
     l'emplacement du corps le plus « chaud » (le thème se dévoile).
  3. Emplacement du titre sans score : candidat le plus proche du thème
     (centroïde des mots révélés et des sondes qui ont touché).
  4. Régulièrement : recherche Wikipédia avec les mots révélés, filtrée par
     le gabarit du titre (longueurs, mots déjà trouvés, ponctuation).

Les réponses sont mises en cache par puzzle (`.cache/pedantix/<n>.json`) :
un nouveau lancement reprend là où le précédent s'est arrêté sans rappeler l'API.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import requests

from core import _session

BASE_URL = "https://pedantix.certitudes.org"
WIKI_API = "https://fr.wikipedia.org/w/api.php"
MODEL_PATH_DEFAULT = "frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "pedantix"

_HEADERS_JSON = {
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}

SCORE_THRESHOLD = 0.30     # en dessous, le serveur ne renvoie pas de score
VOCAB_SIZE = 80_000        # mots les plus fréquents du modèle (les titres passés y sont)
RUN_BUDGET = 700           # appels /score max pour un lancement
PUZZLE_BUDGET = 1500       # appels /score max cumulés pour un puzzle (cache compris)
WIKI_EVERY = 40            # recherche Wikipédia tous les N appels
DELAY = 0.3                # pause entre deux appels /score

FUNCTION_WORDS = (
    "de le et à un en est du par pour qui que dans sur il au avec son se ne pas plus a été sont ou "
    "comme entre ce sa leur aussi mais dont elle ils on y après depuis sous lors être fait deux "
    "premier très cette tout nom autres siècle"
).split()
THEME_SEEDS = (
    "pays ville sport musique film histoire guerre roi science chimie physique biologie animal plante "
    "maladie médecine religion langue art peinture littérature philosophie économie politique empire "
    "mathématiques astronomie géographie fleuve montagne technologie informatique écrivain peuple "
    "architecture oiseau poisson mammifère insecte métal molécule roman dieu mythologie jeu football "
    "cuisine aliment voiture navire avion bataille révolution dynastie île océan climat énergie nombre "
    "théorie psychologie société droit corps eau feu terre arme"
).split()
_STOP = set(FUNCTION_WORDS) | {"la", "les", "l", "d", "des", "une", "s", "n", "qu", "c", "j", "aux"}
_WORD_RE = re.compile(r"[a-zàâäçéèêëîïôöùûüÿœæ]+")
TOKEN_RE = re.compile(r"[^\W\d_]+|\d+")   # découpage d'un titre en emplacements


# ── Page ───────────────────────────────────────────────────────────────────────

def parse_page(html: str) -> tuple[int, list[int], list[tuple[str, object]]]:
    """
    Retourne (puzzle_num, longueurs des emplacements, gabarit du titre).
    Le gabarit est une suite de ("slot", index) / ("txt", ponctuation).
    """
    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(html, "html.parser")
    puzzle_num = int(soup.find("script", id="script")["data-puzzle-number"])
    wiki = soup.find(id="wiki")
    slots = [len(s.get_text()) - 2 for s in wiki.select(".w")]
    template: list[tuple[str, object]] = []
    i = 0
    for node in wiki.find("h2").children:
        if isinstance(node, NavigableString):
            template.append(("txt", str(node)))
        elif "w" in (node.get("class") or []):
            template.append(("slot", i))
            i += 1
    return puzzle_num, slots, template


def render_title(template, words: dict[int, str], slots: list[int] | None = None) -> str:
    """Assemble le titre ; les emplacements inconnus deviennent des '_'."""
    out = []
    for kind, v in template:
        if kind == "txt":
            out.append(v)
        else:
            out.append(words.get(v) or "_" * (slots[v] if slots else 1))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def match_template(title: str, template, slots: list[int], revealed: dict[int, str]) -> dict[int, str] | None:
    """
    Vérifie qu'un titre Wikipédia candidat colle au gabarit (longueurs, mots
    déjà trouvés, ponctuation). Retourne {emplacement: mot} ou None.
    """
    ids = [v for k, v in template if k == "slot"]
    tokens = TOKEN_RE.findall(title)
    if len(tokens) != len(ids):
        return None
    words = {}
    for i, tok in zip(ids, tokens):
        if len(tok) != slots[i]:
            return None
        if i in revealed and revealed[i].lower() != tok.lower():
            return None
        words[i] = tok
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    if norm(render_title(template, words)) != norm(title):
        return None
    return words


# ── Solveur ────────────────────────────────────────────────────────────────────

class PedantixSolver:
    """État d'une résolution. `score_fn(word) -> dict` est injectable (tests, simulation)."""

    def __init__(self, model, puzzle_num: int, slots: list[int], template,
                 score_fn=None, cache_path: Path | None = None, wiki_search=None,
                 run_budget: int = RUN_BUDGET, puzzle_budget: int = PUZZLE_BUDGET,
                 vocab_size: int = VOCAB_SIZE, log=print):
        self.m = model
        self.num = puzzle_num
        self.slots = slots
        self.template = template
        self.title_ids = [v for k, v in template if k == "slot"]
        self.score_fn = score_fn or self._api_score
        self.wiki_search = wiki_search if wiki_search is not None else wikipedia_titles
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if cache_path and cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except ValueError:
                self.cache = {}
        self.run_budget = run_budget
        self.puzzle_budget = puzzle_budget
        self.log = log

        self.revealed: dict[int, str] = {}
        self.obs: dict[int, dict[int, float]] = defaultdict(dict)   # emplacement → {n° sonde: sim}
        self.probes: list[str] = []      # sondes connues du modèle (ordre = n° de sonde)
        self.P = np.zeros((0, model.vector_size), dtype=np.float32)
        self.tried: set[str] = set()
        self.wiki_queries: set[str] = set()
        self.calls = 0                   # appels réseau de ce lancement

        keys = model.index_to_key[:vocab_size]
        self.freq_rank = {w: i for i, w in enumerate(keys)}
        by_len: dict[int, list[str]] = defaultdict(list)
        for w in keys:
            if _WORD_RE.fullmatch(w):
                by_len[len(w)].append(w)
        self.by_len = dict(by_len)
        self.C = {L: self._norm(np.array([model[w] for w in ws], dtype=np.float32)) for L, ws in by_len.items()}
        self.prior = {L: 0.002 * np.log1p(np.array([self.freq_rank[w] for w in ws], dtype=np.float32))
                      for L, ws in by_len.items()}

    # ── utilitaires ──
    @staticmethod
    def _norm(X):
        n = np.linalg.norm(X, axis=-1, keepdims=True)
        return X / np.where(n == 0, 1, n)

    def _api_score(self, word: str) -> dict:
        time.sleep(DELAY)
        resp = _session.post(
            f"{BASE_URL}/score?n={self.num}",
            data=json.dumps({"num": self.num, "word": word, "answer": []}),
            headers=_HEADERS_JSON,
            timeout=15,
        )
        return resp.json()

    def solved(self) -> bool:
        return all(i in self.revealed for i in self.title_ids)

    def title(self) -> str:
        return render_title(self.template, self.revealed, self.slots)

    def _budget_left(self) -> bool:
        return self.calls < self.run_budget and len(self.cache) < self.puzzle_budget

    # ── API ──
    def submit(self, word: str) -> None:
        word = word.lower()
        if word in self.tried:
            return
        self.tried.add(word)
        if word in self.cache:
            data = self.cache[word]
        else:
            if not self._budget_left():
                return
            try:
                data = self.score_fn(word)
            except Exception as e:  # réseau / JSON invalide : on saute ce mot
                self.log(f"   ⚠ /score ({word!r}) : {e}")
                return
            self.calls += 1
            self.cache[word] = data
            if self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        col = None
        if word in self.m:
            col = len(self.probes)
            self.probes.append(word)
            self.P = np.vstack([self.P, self._norm(self.m[word].astype(np.float32))[None, :]])
        for key, positions in (data.get("x") or {}).items():
            for p in positions:
                if key.startswith("#"):
                    if col is not None:
                        self.obs[p][col] = float(key[1:]) / 100
                else:
                    self.revealed[p] = key

    # ── classement des candidats ──
    def _sims(self, L: int) -> np.ndarray:
        return self.C[L] @ self.P.T   # candidats × sondes

    def best_candidate(self, slot: int) -> str | None:
        """Candidat le plus cohérent avec les scores observés sur cet emplacement."""
        L = self.slots[slot]
        if L not in self.C or not self.probes:
            return None
        S = self._sims(L)
        err = np.maximum(0, S - SCORE_THRESHOLD) ** 2          # sonde sans score → sim < seuil
        obs = self.obs.get(slot)
        if obs:
            cols = np.fromiter(obs.keys(), dtype=int)
            vals = np.fromiter(obs.values(), dtype=np.float32)
            err[:, cols] = 4 * (S[:, cols] - vals) ** 2
        err = err.sum(axis=1) + self.prior[L]
        for i in np.argsort(err)[:300]:
            w = self.by_len[L][i]
            if w not in self.tried:
                return w
        return None

    def theme_candidate(self, slot: int) -> str | None:
        """Emplacement sans score : candidat le plus proche du thème de l'article."""
        L = self.slots[slot]
        if L not in self.C:
            return None
        vecs = [self._norm(self.m[w.lower()]) for w in self.revealed.values()
                if w.lower() not in _STOP and len(w) > 3 and w.lower() in self.m]
        hits: dict[int, float] = defaultdict(float)
        for o in self.obs.values():
            for col, v in o.items():
                hits[col] += v
        for col, h in sorted(hits.items(), key=lambda x: -x[1])[:15]:
            vecs.append(h * self.P[col])
        if not vecs:
            return None
        centroid = self._norm(np.sum(vecs, axis=0))
        score = self.C[L] @ centroid
        if self.probes:
            score = score - 0.5 * np.maximum(0, self._sims(L) - SCORE_THRESHOLD).sum(axis=1)
        for i in np.argsort(-score)[:300]:
            w = self.by_len[L][i]
            if w not in self.tried:
                return w
        return None

    # ── recherche Wikipédia ──
    def _content_words(self, n: int) -> list[str]:
        """Mots qui résument l'article : mots révélés du corps, complétés par les sondes qui ont le plus touché."""
        counts = Counter(w for p, w in self.revealed.items()
                         if p not in self.title_ids and w.lower() not in _STOP and len(w) > 3)
        # privilégier les mots fréquents dans l'article mais rares dans la langue
        ranked = sorted(counts, key=lambda w: (-counts[w], -self.freq_rank.get(w.lower(), 10**6)))
        if len(ranked) < n:
            hits: dict[int, float] = defaultdict(float)
            for o in self.obs.values():
                for col, v in o.items():
                    hits[col] += v
            lower = {w.lower() for w in ranked}
            for col, _ in sorted(hits.items(), key=lambda x: -x[1]):
                w = self.probes[col]
                if w not in _STOP and w not in lower:
                    ranked.append(w)
                    lower.add(w)
                if len(ranked) >= n:
                    break
        return ranked[:n]

    def wiki_assist(self) -> None:
        """Cherche des titres Wikipédia compatibles avec le gabarit et propose leurs mots manquants."""
        queries = []
        known = [self.revealed[i] for i in self.title_ids
                 if i in self.revealed and self.revealed[i].lower() not in _STOP]
        body = self._content_words(8)
        if known:
            queries.append(" ".join(known + body[:4]))
        if body:
            queries.append(" ".join(body))
        seen = set()
        for q in queries:
            if q in self.wiki_queries:      # mots révélés inchangés : inutile de redemander
                continue
            self.wiki_queries.add(q)
            for title in self.wiki_search(q):
                if title in seen:
                    continue
                seen.add(title)
                words = match_template(title, self.template, self.slots, self.revealed)
                if not words:
                    continue
                self.log(f"[Pédantix] 🔎 Wikipédia propose {title!r}")
                for i, w in words.items():
                    if i not in self.revealed:
                        self.submit(w)
                if self.solved():
                    return

    # ── boucle principale ──
    def run(self) -> str | None:
        t0 = time.time()
        for w in FUNCTION_WORDS + [w for w in THEME_SEEDS if w in self.m]:
            self.submit(w)
        self.log(f"[Pédantix] Après les mots de départ : {self.title()!r} ({self.calls} appels)")
        last_wiki = -WIKI_EVERY
        it = 0
        while not self.solved() and self._budget_left():
            it += 1
            before = len(self.tried)
            if len(self.tried) - last_wiki >= WIKI_EVERY:
                last_wiki = len(self.tried)
                self.wiki_assist()
                if self.solved():
                    break
            title_open = [s for s in self.title_ids if s not in self.revealed]
            picks = [s for s in title_open if self.obs.get(s)]
            body = sorted((s for s, o in self.obs.items()
                           if o and s not in self.revealed and s not in self.title_ids and self.slots[s] > 1),
                          key=lambda s: -max(self.obs[s].values()))
            picks += body[:1]
            for s in picks:
                w = self.best_candidate(s)
                if w:
                    self.submit(w)
            # un tour sur deux, ou dès que rien d'autre n'avance : tirer le titre vers le thème
            if it % 2 == 0 or len(self.tried) == before:
                for s in title_open:
                    if not self.obs.get(s):
                        w = self.theme_candidate(s)
                        if w:
                            self.submit(w)
            if len(self.tried) == before:   # plus aucun candidat à proposer
                break
            if it % 25 == 0:
                self.log(f"[Pédantix]    {len(self.tried)} mots essayés — {self.title()!r}")
        if not self.solved():
            self.wiki_assist()
        status = "✅" if self.solved() else "❌"
        self.log(f"[Pédantix] {status} {self.title()!r} — {self.calls} appels ce lancement, "
                 f"{len(self.cache)} au total pour #{self.num}, {time.time() - t0:.0f}s")
        return self.title() if self.solved() else None


# ── Wikipédia ──────────────────────────────────────────────────────────────────

_wiki = requests.Session()
_wiki.headers["User-Agent"] = "PedantixSolver/1.0 (python-requests)"


def wikipedia_titles(query: str, limit: int = 20) -> list[str]:
    """Titres d'articles Wikipédia FR correspondant à une recherche plein texte."""
    params = {"action": "query", "list": "search", "srsearch": query, "srnamespace": 0,
              "srlimit": limit, "format": "json"}
    for attempt in range(2):
        try:
            resp = _wiki.get(WIKI_API, params=params, timeout=15)
            if resp.status_code == 429 and attempt == 0:   # limite de débit Wikimedia : attendre puis 1 réessai
                time.sleep(min(int(resp.headers.get("Retry-After", 10)), 40))
                continue
            return [r["title"] for r in resp.json().get("query", {}).get("search", [])]
        except Exception as e:
            print(f"   ⚠ recherche Wikipédia ({query!r}) : {e}")
            return []
    return []


def wikipedia_slug(title: str) -> str:
    """Slug canonique de l'article (suit les redirections), repli sur titre_avec_underscores."""
    fallback = title.replace(" ", "_")
    try:
        resp = _session.get(f"https://fr.wikipedia.org/api/rest_v1/page/summary/{fallback}", timeout=15)
        if resp.status_code == 200:
            return resp.json().get("titles", {}).get("canonical") or fallback
    except Exception:
        pass
    return fallback


# ── Point d'entrée ─────────────────────────────────────────────────────────────

def load_model(model_path: str = MODEL_PATH_DEFAULT):
    from gensim.models import KeyedVectors
    print(f"[Pédantix] Chargement du modèle : {model_path} …")
    return KeyedVectors.load_word2vec_format(model_path, binary=True, unicode_errors="ignore")


def solve(html: str | None = None, model=None, model_path: str = MODEL_PATH_DEFAULT,
          expected_puzzle: int | None = None) -> tuple[int, str, str] | None:
    """
    Résout le puzzle Pédantix en cours.
    Retourne (puzzle_num, titre affiché, slug Wikipédia) ou None.
    """
    if html is None:
        html = _session.get(BASE_URL, timeout=15).text
    puzzle_num, slots, template = parse_page(html)
    if expected_puzzle is not None and puzzle_num != expected_puzzle:
        print(f"[Pédantix] ⚠ La page affiche #{puzzle_num}, attendu #{expected_puzzle}.")
    if model is None:
        model = load_model(model_path)
    print(f"[Pédantix] Solveur #{puzzle_num} : {len(slots)} mots, titre {[slots[i] for k, i in template if k == 'slot']}")
    solver = PedantixSolver(model, puzzle_num, slots, template, cache_path=CACHE_DIR / f"{puzzle_num}.json")
    title = solver.run()
    if not title:
        return None
    return puzzle_num, title, wikipedia_slug(title)
