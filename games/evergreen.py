"""
games/evergreen.py — Pages informationnelles statiques (« evergreen »).

Contrairement aux pages jeux (solution/index/archive), ces pages ne changent pas
tous les jours — sauf `cemantix/statistiques`, recalculée depuis les archives à
chaque régénération. Elles couvrent les requêtes informationnelles (« comment
jouer à... », « meilleurs mots... ») que les pages solution ne captent pas.

Génère :
  docs/cemantix/comment-jouer/index.html
  docs/cemantix/astuces/index.html
  docs/cemantix/statistiques/index.html
  docs/sutom/comment-jouer/index.html
  docs/sutom/meilleurs-mots/index.html
  docs/a-propos/index.html
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from html import escape as _html_escape
from pathlib import Path
from typing import Callable

from core import SITE_URL, DOCS_DIR, atomic_write, render_page, load_all_archives, faq_html, faq_jsonld


@dataclass
class EvergreenPage:
    path: str
    title: str
    description: str
    h1: str
    breadcrumb: list[tuple[str, str]]
    body_fn: Callable[[], str]
    faq: list[tuple[str, str]] = field(default_factory=list)
    og_type: str = "article"


# ── Cémantix — Comment jouer ──────────────────────────────────────────────────

def _cemantix_comment_jouer_body() -> str:
    return """    <div class="card">
      <h2>Les règles de Cémantix</h2>
      <p>
        <strong>Cémantix</strong> est un jeu de mots quotidien basé sur la proximité sémantique,
        disponible sur <a href="https://cemantix.certitudes.org" rel="noopener" target="_blank">cemantix.certitudes.org</a>.
        Chaque jour, un nouveau mot secret est à deviner.
      </p>
      <ol style="padding-left:1.2rem;line-height:1.8;">
        <li>Proposez un mot en français dans le champ de saisie.</li>
        <li>Le jeu affiche un <strong>score de température</strong> — la proximité sémantique entre
            votre proposition et la solution — de 0&#8201;% (aucun rapport) à 100&#8201;% (c'est le mot).</li>
        <li>Utilisez les mots déjà proposés comme indices : plus la température est élevée, plus vous
            vous rapprochez sémantiquement de la réponse.</li>
        <li>Il n'y a ni limite de tentatives, ni limite de temps.</li>
      </ol>
    </div>
    <div class="card">
      <h2>Qu'est-ce que la « proximité sémantique » ?</h2>
      <p>
        Cémantix utilise un modèle de <em>plongement lexical</em> (word embeddings) entraîné sur un
        large corpus de textes en français. Deux mots sont considérés proches s'ils apparaissent
        souvent dans des contextes similaires — par exemple « chien » et « chat » sont plus proches
        que « chien » et « voiture ». Le score affiché reflète cette proximité de sens, pas une
        ressemblance orthographique ou phonétique.
      </p>
      <p style="margin-top:.75rem;font-size:.9rem;">
        Bloqué aujourd'hui ? Retrouvez la <a href="../">solution et les indices du jour</a> ou
        juste des <a href="../indice/">indices sans la solution</a>.
      </p>
    </div>"""


def _cemantix_astuces_body() -> str:
    return """    <div class="card">
      <h2>Meilleurs mots de départ</h2>
      <p>
        Pour maximiser vos chances dès les premières propositions, privilégiez des <strong>mots
        concrets et courants</strong> plutôt que des mots abstraits ou rares. Les noms d'objets,
        d'animaux, de lieux ou de professions couvrent souvent une large zone sémantique et donnent
        un premier signal utile.
      </p>
      <ul style="padding-left:1.2rem;line-height:1.8;">
        <li>Commencez par des mots très généraux (« chose », « personne », « temps »…) pour situer
            grossièrement le champ lexical de la solution.</li>
        <li>Affinez ensuite avec des mots concrets liés au meilleur score déjà obtenu.</li>
        <li>Évitez les mots trop rares ou trop techniques en début de partie : le modèle les
            représente moins précisément.</li>
      </ul>
    </div>
    <div class="card">
      <h2>Comprendre la stratégie de température</h2>
      <p>
        La progression n'est pas linéaire : passer de 10&#8201;% à 20&#8201;% est souvent plus facile
        que de passer de 80&#8201;% à 90&#8201;%. Une fois au-dessus de 50&#8201;%, essayez des
        synonymes ou des variantes proches du meilleur mot trouvé (singulier/pluriel, mots de la
        même famille) plutôt que d'explorer un nouveau champ lexical.
      </p>
    </div>"""


def _cemantix_statistiques_body() -> str:
    from games.cemantix import CEMANTIX_ARCHIVE
    entries = load_all_archives(CEMANTIX_ARCHIVE)
    words = [e["word"] for e in entries if e.get("word")]
    if not words:
        return '    <div class="card"><p>Statistiques indisponibles pour le moment.</p></div>'

    lengths = [len(w) for w in words]
    avg_len = sum(lengths) / len(lengths)
    initials = Counter(w[0].upper() for w in words)
    dist = Counter(lengths)
    longest = max(words, key=len)
    shortest = min(words, key=len)

    initials_rows = "\n            ".join(
        f"<tr><td>{_html_escape(letter)}</td><td>{count}</td></tr>"
        for letter, count in initials.most_common(8)
    )
    dist_rows = "\n            ".join(
        f"<tr><td>{n} lettres</td><td>{dist[n]}</td></tr>"
        for n in sorted(dist)
    )

    return f"""    <div class="card">
      <h2>Statistiques sur {len(words)} solutions Cémantix</h2>
      <p style="font-size:.9rem;color:#6b7280;margin-bottom:1rem;">
        Calculées automatiquement à partir de toutes les archives disponibles.
      </p>
      <ul style="padding-left:1.2rem;line-height:1.8;">
        <li>Longueur moyenne des mots : <strong>{avg_len:.1f} lettres</strong></li>
        <li>Mot le plus long : <strong>{_html_escape(longest.upper())}</strong> ({len(longest)} lettres)</li>
        <li>Mot le plus court : <strong>{_html_escape(shortest.upper())}</strong> ({len(shortest)} lettres)</li>
      </ul>
    </div>
    <div class="card">
      <h2>Initiales les plus fréquentes</h2>
      <div style="overflow-x:auto;">
        <table class="nearby-table">
          <thead><tr><th>Lettre</th><th>Occurrences</th></tr></thead>
          <tbody>
            {initials_rows}
          </tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <h2>Distribution des longueurs de mots</h2>
      <div style="overflow-x:auto;">
        <table class="nearby-table">
          <thead><tr><th>Longueur</th><th>Nombre de mots</th></tr></thead>
          <tbody>
            {dist_rows}
          </tbody>
        </table>
      </div>
    </div>"""


def _sutom_comment_jouer_body() -> str:
    return """    <div class="card">
      <h2>Les règles de Sutom</h2>
      <p>
        <strong>Sutom</strong> est la version française du célèbre Wordle. Chaque jour, un mot
        secret est à deviner en <strong>6 tentatives maximum</strong>.
      </p>
      <ol style="padding-left:1.2rem;line-height:1.8;">
        <li>La première lettre du mot est révélée dès le départ.</li>
        <li>Proposez un mot complet ayant le bon nombre de lettres.</li>
        <li>Chaque lettre est coloriée : <strong>vert</strong> si elle est bien placée,
            <strong>orange</strong> si elle est dans le mot mais mal placée, <strong>gris</strong>
            si elle n'y est pas.</li>
        <li>Utilisez ces indices de couleur pour affiner vos propositions suivantes.</li>
      </ol>
      <p style="margin-top:.75rem;font-size:.9rem;">
        Bloqué aujourd'hui ? Retrouvez la <a href="../">solution du Sutom du jour</a>.
      </p>
    </div>"""


def _sutom_meilleurs_mots_body() -> str:
    return """    <div class="card">
      <h2>Meilleurs mots de départ pour Sutom</h2>
      <p>
        Comme pour tout jeu de type Wordle, le meilleur premier mot est celui qui couvre un
        maximum de <strong>lettres fréquentes en français</strong> — voyelles courantes (A, E, I, O)
        et consonnes fréquentes (R, S, T, N, L).
      </p>
      <ul style="padding-left:1.2rem;line-height:1.8;">
        <li>Privilégiez un mot sans lettre répétée pour tester un maximum de lettres différentes.</li>
        <li>Un mot contenant 2 à 3 voyelles courantes maximise les chances de « toucher » une lettre
            bien placée dès le premier essai.</li>
        <li>Gardez en tête que la première lettre du mot est déjà donnée par Sutom — inutile de la
            re-tester dans votre stratégie de départ.</li>
      </ul>
      <p style="margin-top:.75rem;font-size:.9rem;">
        Bloqué aujourd'hui ? Retrouvez la <a href="../">solution du Sutom du jour</a>.
      </p>
    </div>"""


PAGES: list[EvergreenPage] = [
    EvergreenPage(
        path="cemantix/comment-jouer",
        title="Comment jouer à Cémantix ? Règles du jeu",
        description="Les règles complètes de Cémantix : comment fonctionne le score de température et la proximité sémantique, stratégie de base pour deviner le mot du jour.",
        h1="Comment jouer à Cémantix ?",
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("Cémantix", f"{SITE_URL}/cemantix/"), ("Comment jouer", f"{SITE_URL}/cemantix/comment-jouer/")],
        body_fn=_cemantix_comment_jouer_body,
        faq=[
            ("Qu'est-ce que Cémantix ?", "Cémantix est un jeu de mots quotidien où l'on devine un mot secret grâce à un score de proximité sémantique, calculé par un modèle de langage entraîné sur des textes en français."),
            ("Y a-t-il une limite de tentatives sur Cémantix ?", "Non, Cémantix n'impose ni limite de tentatives ni limite de temps : vous pouvez essayer autant de mots que vous le souhaitez."),
        ],
    ),
    EvergreenPage(
        path="cemantix/astuces",
        title="Astuces Cémantix : meilleurs mots de départ",
        description="Nos astuces pour progresser plus vite à Cémantix : quels mots de départ choisir et comment interpréter la stratégie de température.",
        h1="Astuces pour progresser à Cémantix",
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("Cémantix", f"{SITE_URL}/cemantix/"), ("Astuces", f"{SITE_URL}/cemantix/astuces/")],
        body_fn=_cemantix_astuces_body,
        faq=[
            ("Quel est le meilleur mot pour commencer une partie de Cémantix ?", "Un mot concret et courant (objet, animal, lieu) donne généralement un premier signal sémantique plus utile qu'un mot abstrait ou rare."),
        ],
    ),
    EvergreenPage(
        path="cemantix/statistiques",
        title="Statistiques Cémantix : mots et lettres",
        description="Statistiques calculées sur toutes les solutions Cémantix passées : longueur moyenne des mots, initiales les plus fréquentes, mots les plus longs et les plus courts.",
        h1="Statistiques des solutions Cémantix",
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("Cémantix", f"{SITE_URL}/cemantix/"), ("Statistiques", f"{SITE_URL}/cemantix/statistiques/")],
        body_fn=_cemantix_statistiques_body,
    ),
    EvergreenPage(
        path="sutom/comment-jouer",
        title="Comment jouer à Sutom ? Règles du jeu",
        description="Les règles complètes de Sutom, le Wordle français : code couleur des lettres, nombre de tentatives, conseils pour deviner le mot du jour.",
        h1="Comment jouer à Sutom ?",
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("Sutom", f"{SITE_URL}/sutom/"), ("Comment jouer", f"{SITE_URL}/sutom/comment-jouer/")],
        body_fn=_sutom_comment_jouer_body,
        faq=[
            ("Combien de tentatives a-t-on à Sutom ?", "Sutom laisse 6 tentatives pour deviner le mot du jour, dont la première lettre est révélée dès le départ."),
        ],
    ),
    EvergreenPage(
        path="sutom/meilleurs-mots",
        title="Sutom : meilleurs mots de départ à essayer",
        description="Quels mots choisir pour bien démarrer une partie de Sutom : lettres fréquentes en français, voyelles à privilégier, erreurs à éviter.",
        h1="Meilleurs mots de départ pour Sutom",
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("Sutom", f"{SITE_URL}/sutom/"), ("Meilleurs mots", f"{SITE_URL}/sutom/meilleurs-mots/")],
        body_fn=_sutom_meilleurs_mots_body,
    ),
]


def generate_evergreen(today: date) -> list[str]:
    """Génère toutes les pages evergreen. Retourne la liste des chemins écrits (relatifs à docs/)."""
    written = []
    for page in PAGES:
        canonical = f"{SITE_URL}/{page.path}/"
        extra_head = faq_jsonld(page.faq) if page.faq else ""
        body = page.body_fn()
        if page.faq:
            body += faq_html(page.faq, open_first=True)
        html = render_page(
            title=page.title,
            description=page.description,
            canonical=canonical,
            h1=page.h1,
            body_html=body,
            breadcrumb=page.breadcrumb,
            css_rel="../../css/style.css",
            og_type=page.og_type,
            extra_head=extra_head,
            footer_links=f'<a href="{"/".join(page.path.split("/")[:-1])}/">{page.breadcrumb[-2][0]}</a>',
        )
        out_dir = DOCS_DIR / page.path
        out_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(out_dir / "index.html", html)
        written.append(f"docs/{page.path}/index.html")
    return written


# ── Page « À propos » ─────────────────────────────────────────────────────────

_ABOUT_BODY = """    <div class="card">
      <h2>Qui sommes-nous ?</h2>
      <p>
        <strong>Solutions du Jour</strong> est un site non officiel qui publie chaque jour les
        solutions et indices des jeux <strong>Cémantix</strong>, <strong>Sutom</strong> et
        <strong>Pédantix</strong>, ainsi que les <strong>résultats Loto et EuroMillions</strong>.
        Tout le contenu est généré automatiquement, sans intervention humaine, à partir des
        publications officielles de chaque jeu ou de la Française des Jeux.
      </p>
      <p style="margin-top:.75rem;">
        Les solutions Cémantix, Sutom et Pédantix sont publiées vers <strong>8h05</strong> chaque
        matin. Les résultats Loto sont mis à jour après chaque tirage (lundi, mercredi, samedi) et
        les résultats EuroMillions après chaque tirage (mardi, vendredi).
      </p>
    </div>
    <div class="card">
      <h2>Ce site n'est affilié à aucun des jeux présentés</h2>
      <p>
        Cémantix, Sutom, Pédantix, le Loto et l'EuroMillions sont des marques et jeux qui
        appartiennent à leurs éditeurs respectifs. Solutions du Jour se contente de documenter
        publiquement leurs résultats quotidiens à des fins d'information.
      </p>
    </div>"""


def generate_about_page() -> None:
    """Génère docs/a-propos/index.html — page « À propos » (E-E-A-T minimal)."""
    canonical = f"{SITE_URL}/a-propos/"
    organization_jsonld = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Solutions du Jour",
        "url": f"{SITE_URL}/",
        "logo": f"{SITE_URL}/og-image.png",
    }
    html = render_page(
        title="À propos de Solutions du Jour",
        description="Présentation du site Solutions du Jour : solutions Cémantix, Sutom, Pédantix et résultats Loto/EuroMillions générés automatiquement chaque jour.",
        canonical=canonical,
        h1="À propos de Solutions du Jour",
        body_html=_ABOUT_BODY,
        breadcrumb=[("Accueil", f"{SITE_URL}/"), ("À propos", canonical)],
        css_rel="../css/style.css",
        jsonld=[organization_jsonld],
    )
    out_dir = DOCS_DIR / "a-propos"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "index.html", html)
