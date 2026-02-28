"""
Solveur Cémantix — triangulation d'embeddings (sans /nearby)
https://cemantix.certitudes.org/

Algorithme :
  1. Seeds       : score ~80 mots diversifiés pour cartographier l'espace
  2. Reconstruct : estime le vecteur cible T par moindres carrés (X^+ · s)
  3. Local loop  : top 100 candidats locaux proches de T → score via API
                   → re-reconstruit T avec les nouveaux scores, répète

Suppose que le modèle local est identique au modèle du serveur.

Prérequis :
  pip install requests gensim numpy beautifulsoup4
  Modèle : frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin (120 Mo)
    → https://embeddings.net/embeddings/frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin

Lancement :
  python solver.py
  python solver.py --puzzle 1458
  python solver.py --model /chemin/vers/modele.bin
"""

import argparse
import sys
import time

import numpy as np
import cloudscraper
from bs4 import BeautifulSoup

BASE_URL = "https://cemantix.certitudes.org"
SIMILARITY_THRESHOLD = 0.1  # seuil cosinus minimum pour soumettre un candidat local
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}

# Session cloudscraper : gère automatiquement les défis Cloudflare JS
_session = cloudscraper.create_scraper()

SEEDS = [
    "vie", "mort", "amour", "temps", "monde", "homme", "femme", "enfant",
    "travail", "argent", "guerre", "paix", "liberté", "nature", "corps",
    "science", "art", "politique", "société", "histoire", "joie", "peur",
    "rouge", "grand", "vieux", "chien", "arbre", "montagne", "mer", "ville",
    "roi", "dieu", "soleil", "rêve", "silence",
]


# ── API ────────────────────────────────────────────────────────────────────────

def get_puzzle_number() -> int:
    resp = _session.get(BASE_URL, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", id="script")
    if not script:
        raise RuntimeError("Impossible de trouver le numéro du puzzle.")
    return int(script["data-puzzle-number"])


api_calls = 0


def score_word(word: str, puzzle_num: int, delay: float = 0.2) -> dict | None:
    """Retourne {"s": cosine_sim, "p": percentile} ou None si mot inconnu/rate-limit."""
    global api_calls
    time.sleep(delay)
    try:
        resp = _session.post(
            f"{BASE_URL}/score?n={puzzle_num}",
            data=f"word={word}",
            headers=HEADERS,
            timeout=10,
        )
        data = resp.json()
        if "s" in data:
            api_calls += 1
            return data
        return None
    except Exception:
        return None


# ── Affichage ──────────────────────────────────────────────────────────────────

def emoji_for(s: float, p) -> str:
    if s >= 1.0:             return "🥳 TROUVÉ !"
    if p is not None:
        if p >= 999:         return "😱"
        if p >= 990:         return "🔥"
        if p >= 900:         return "🥵"
    if s > 0:                return "😎"
    if s == 0:               return "🥶"
    return "🧊"


def display(word: str, s: float, p, attempt: int):
    p_str = f"{p:>4}‰" if p is not None else "    "
    print(f"  #{attempt:>3}  {word:<28} {s*100:>7.2f}°C  {p_str}  {emoji_for(s, p)}")


# ── Reconstruction ─────────────────────────────────────────────────────────────

def reconstruct_target(tried: dict[str, float], model) -> np.ndarray | None:
    """
    Estime le vecteur cible T par moindres carrés.
    cosine(embed(w_i), T) ≈ tried[w_i]  →  X · T ≈ s  →  T = X^+ · s
    Plus on a de probes (surtout avec des scores élevés), plus c'est précis.
    """
    words = [w for w in tried if w in model and tried[w] > -0.5]
    if len(words) < 5:
        return None
    X = np.array([model[w] for w in words], dtype=np.float32)
    s = np.array([tried[w] for w in words], dtype=np.float32)
    T, _, _, _ = np.linalg.lstsq(X, s, rcond=None)
    norm = np.linalg.norm(T)
    return T / norm if norm > 1e-9 else None


# ── Solveur principal ──────────────────────────────────────────────────────────

def solve(puzzle_num: int, model_path: str):
    global api_calls
    api_calls = 0

    try:
        from gensim.models import KeyedVectors
    except ImportError:
        print("❌  pip install gensim")
        sys.exit(1)

    print(f"Chargement du modèle : {model_path} …")
    model = KeyedVectors.load_word2vec_format(model_path, binary=True, unicode_errors="ignore")
    print(f"Vocabulaire : {len(model.key_to_index):,} mots\n")

    tried: dict[str, float] = {}
    attempt = 0
    t_start = time.time()

    def log_stats():
        elapsed = time.time() - t_start
        print(f"   [stats] {api_calls} appels API — {elapsed:.0f}s écoulées\n")

    # ── Phase 1 : Seeds ───────────────────────────────────────────────────────
    print(f"🌱 Puzzle #{puzzle_num} — Phase 1 : seeds\n")
    for word in [w for w in SEEDS if w in model]:
        result = score_word(word, puzzle_num)
        if result is None:
            continue
        attempt += 1
        s, p = result["s"], result.get("p")
        tried[word] = s
        display(word, s, p, attempt)
        if s >= 1.0:
            log_stats()
            return word, tried

    log_stats()

    # ── Phase 2 : Reconstruction + candidats locaux ───────────────────────────
    print(f"🧮 Phase 2 : reconstruction du vecteur cible ({len(tried)} probes) …")
    T = reconstruct_target(tried, model)

    if T is not None:
        candidates = [(w, sim) for w, sim in model.similar_by_vector(T, topn=300) if sim >= SIMILARITY_THRESHOLD]
        print(f"   {len(candidates)} candidats au-dessus du seuil {SIMILARITY_THRESHOLD} (sur 300)")
        print(f"   Top 5 : {', '.join(w for w, _ in candidates[:5])}\n")
        print(f"🎯 Phase 2b : vérification des candidats locaux …\n")
        for word, _ in candidates:
            if word in tried:
                continue
            result = score_word(word, puzzle_num)
            if result is None:
                continue
            attempt += 1
            s, p = result["s"], result.get("p")
            tried[word] = s
            display(word, s, p, attempt)
            if s >= 1.0:
                log_stats()
                return word, tried
    else:
        print("   Reconstruction insuffisante (pas assez de probes).\n")

    log_stats()

    # ── Phase 3 : Boucle de reconstruction itérative ─────────────────────────
    print(f"🔁 Phase 3 : boucle de reconstruction itérative …\n")

    for iteration in range(20):
        T = reconstruct_target(tried, model)
        if T is None:
            print("   Reconstruction insuffisante, arrêt.")
            break

        candidates = [
            w for w, sim in model.similar_by_vector(T, topn=100)
            if w not in tried and sim >= SIMILARITY_THRESHOLD
        ]

        if not candidates:
            print("   Plus de nouveaux candidats locaux, arrêt.")
            break

        print(f"  [{iteration+1}] {len(candidates)} candidats locaux\n")
        for word in candidates:
            result = score_word(word, puzzle_num)
            if result is None:
                continue
            attempt += 1
            s, p = result["s"], result.get("p")
            tried[word] = s
            display(word, s, p, attempt)
            if s >= 1.0:
                log_stats()
                return word, tried

        best = max(tried, key=tried.get)
        print(f"\n   Meilleur jusqu'ici : '{best}' ({tried[best]*100:.2f}°C) — {attempt} essais")
        log_stats()

    if tried:
        best = max(tried, key=tried.get)
        print(f"❌ Non trouvé après {attempt} essais. Meilleur : '{best}' ({tried[best]*100:.2f}°C)")
    else:
        print("❌ Aucune réponse API obtenue (site inaccessible ou bloqué).")
    log_stats()
    return None, tried


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Solveur Cémantix")
    parser.add_argument("--puzzle", type=int, default=None)
    parser.add_argument(
        "--model",
        default="frWac_non_lem_no_postag_no_phrase_200_cbow_cut100.bin",
        help="Chemin vers le modèle word2vec .bin de Fauconnier",
    )
    args = parser.parse_args()

    puzzle_num = args.puzzle or get_puzzle_number()
    print(f"Puzzle du jour : #{puzzle_num}\n")

    word, tried = solve(puzzle_num, args.model)
    if word:
        print(f"\n🎉 Réponse : {word}")


if __name__ == "__main__":
    main()
