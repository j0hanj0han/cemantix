"""
og_images.py — Images sociales datées (1200×630) pour og:image, twitter:image et le
champ `image` du JSON-LD NewsArticle (éligibilité résultats enrichis + Google Discover).

Génère :
  docs/<jeu>/img/YYYY-MM-DD.png   ← une image par puzzle / tirage, sans spoiler
  docs/logo.png                   ← logo éditeur (publisher.logo du JSON-LD)

Pillow est optionnel : s'il manque (ex. GitHub Actions), rien n'est généré et
core.og_image_url() retombe sur /og-image.png. Une image existante n'est jamais
réécrite, pour que la régénération reste idempotente quelle que soit la machine.
"""

from datetime import date
from pathlib import Path

from core import DOCS_DIR, DAYS_FR, date_fr_short, load_all_archives

W, H = 1200, 630
FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "DejaVuSans-Bold.ttf"

BG = (15, 23, 42)          # slate-900
TEXT = (255, 255, 255)
SUBTEXT = (226, 232, 240)  # slate-200
MUTED = (148, 163, 184)    # slate-400
TILE = (51, 65, 85)        # slate-700

# jeu → (nom affiché, couleur d'accent)
GAMES = {
    "cemantix": ("CÉMANTIX", (245, 158, 11)),
    "sutom": ("SUTOM", (231, 0, 42)),
    "tusmo": ("TUSMO", (37, 99, 235)),
    "pedantix": ("PÉDANTIX", (139, 92, 246)),
    "loto": ("LOTO", (16, 185, 129)),
    "euromillions": ("EUROMILLIONS", (124, 58, 237)),
}

_HINT_CHIPS = {
    "cemantix": ["1re lettre", "Longueur", "Définition", "Mots proches", "Solution"],
    "pedantix": ["Catégories", "Indices", "Extrait", "Solution"],
}


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        return None


def image_path(game: str, d: date) -> Path:
    return DOCS_DIR / game / "img" / f"{d.isoformat()}.png"


def _date_label(d: date) -> str:
    return f"{DAYS_FR[d.weekday()].capitalize()} {date_fr_short(d)}"


def _font(size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT_PATH), size)


def _fit(draw, text: str, size: int, max_w: int = W - 120):
    """Police la plus grande (≤ size) pour que `text` tienne dans max_w."""
    while size > 20 and draw.textlength(text, font=_font(size)) > max_w:
        size -= 2
    return _font(size)


def _canvas(game: str, title: str, d: date):
    """Fond + bandeau + marque + titre + date, centrés (Google recadre les miniatures
    mobiles en carré au centre : rien d'important ne doit être collé à gauche)."""
    Image, ImageDraw, _ = _pil()
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    accent = GAMES[game][1]
    draw.rectangle([0, 0, W, 14], fill=accent)
    draw.text((W / 2, 70), "SOLUTION-DU-JOUR.FR", font=_font(26), fill=MUTED, anchor="mm")
    draw.text((W / 2, 150), title, font=_fit(draw, title, 84), fill=TEXT, anchor="mm")
    draw.text((W / 2, 238), _date_label(d), font=_font(42), fill=SUBTEXT, anchor="mm")
    return img, draw, accent


def _save(img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Palette adaptative : aplats + texte anticrénelé → ~25 Ko au lieu de ~60 Ko en RGB
    img.quantize(colors=64).save(path, optimize=True)


def _centered(draw, box, text: str, font, fill) -> None:
    x0, y0, x1, y1 = box
    draw.text(((x0 + x1) / 2, (y0 + y1) / 2), text, font=font, fill=fill, anchor="mm")


def _tagline(draw, text: str, accent) -> None:
    draw.text((W / 2, 545), text, font=_fit(draw, text, 38), fill=accent, anchor="mm")


def word_game(game: str, d: date, puzzle_num: int, *, word: str | None = None) -> Path | None:
    """Image d'un jeu de mots. `word` (Sutom/Tusmo) n'est utilisé que pour la longueur et
    la 1re lettre, déjà publiques dans le title ; Cémantix/Pédantix n'en révèlent rien."""
    path = image_path(game, d)
    if path.exists() or _pil() is None:
        return None
    name, _ = GAMES[game]
    img, draw, accent = _canvas(game, f"{name} #{puzzle_num}", d)

    if word:
        n = len(word)
        gap = 12
        size = min(96, (W - 120 - (n - 1) * gap) // n)
        x = (W - (n * size + (n - 1) * gap)) / 2
        y0 = 320
        for i in range(n):
            box = (x, y0, x + size, y0 + size)
            draw.rounded_rectangle(box, radius=10, fill=accent if i == 0 else TILE)
            if i == 0:
                _centered(draw, box, word[0].upper(), _font(int(size * 0.6)), TEXT)
            x += size + gap
        _tagline(draw, f"Mot de {n} lettres · indices & solution", accent)
    else:
        labels, gap, pad = _HINT_CHIPS[game], 16, 48
        size = 30
        while size > 18 and sum(draw.textlength(l, font=_font(size)) + pad for l in labels) + gap * (len(labels) - 1) > W - 120:
            size -= 2
        chip_font = _font(size)
        widths = [draw.textlength(l, font=chip_font) + pad for l in labels]
        x, y0 = (W - sum(widths) - gap * (len(labels) - 1)) / 2, 330
        for label, w in zip(labels, widths):
            box = (x, y0, x + w, y0 + 76)
            draw.rounded_rectangle(box, radius=38, fill=TILE)
            _centered(draw, box, label, chip_font, SUBTEXT)
            x += w + gap
        _tagline(draw, "Indices progressifs & solution du jour", accent)

    _save(img, path)
    return path


def _star(cx: float, cy: float, r_out: float, r_in: float) -> list[tuple[float, float]]:
    from math import cos, sin, pi
    pts = []
    for k in range(10):
        r = r_out if k % 2 == 0 else r_in
        a = -pi / 2 + k * pi / 5
        pts.append((cx + r * cos(a), cy + r * sin(a)))
    return pts


def draw_result(game: str, d: date, balls: list[int], extras: list[int]) -> Path | None:
    """Image d'un tirage (Loto : extras = [chance] ; EuroMillions : extras = étoiles)."""
    path = image_path(game, d)
    if path.exists() or _pil() is None:
        return None
    name, _ = GAMES[game]
    img, draw, accent = _canvas(game, f"RÉSULTATS {name}", d)

    size, gap, sep, y0 = 120, 22, 18, 320
    num_font = _font(52)
    total = (len(balls) + len(extras)) * size + (len(balls) + len(extras) - 1) * gap + sep
    x = (W - total) / 2
    for b in balls:
        box = (x, y0, x + size, y0 + size)
        draw.ellipse(box, fill=(243, 244, 246), outline=(209, 213, 219), width=4)
        _centered(draw, box, str(b), num_font, (31, 41, 55))
        x += size + gap
    x += sep
    for e in extras:
        box = (x, y0, x + size, y0 + size)
        if game == "loto":
            draw.ellipse(box, fill=(251, 191, 36), outline=(245, 158, 11), width=4)
            _centered(draw, box, str(e), num_font, (120, 53, 15))
        else:
            cx, cy = x + size / 2, y0 + size / 2
            draw.polygon(_star(cx, cy, size / 2 + 6, size / 4 + 4), fill=(124, 58, 237))
            _centered(draw, (x, y0 + 10, x + size, y0 + size), str(e), _font(40), TEXT)
        x += size + gap
    _tagline(draw, "Numéros gagnants du tirage", accent)
    _save(img, path)
    return path


def ensure_logo() -> Path | None:
    """Logo éditeur 512×512 (publisher.logo du JSON-LD NewsArticle)."""
    path = DOCS_DIR / "logo.png"
    pil = _pil()
    if path.exists() or pil is None:
        return None
    Image, ImageDraw, _ = pil
    img = Image.new("RGB", (512, 512), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 512, 24], fill=GAMES["cemantix"][1])
    _centered(draw, (0, 24, 512, 512), "SDJ", _font(190), TEXT)
    _save(img, path)
    return path


def backfill(since: date) -> int:
    """Génère les images manquantes de toutes les archives depuis `since`. Retourne le nombre créé."""
    created = 0
    for game in ("cemantix", "sutom", "tusmo", "pedantix"):
        for e in load_all_archives(DOCS_DIR / game / "archive", required_keys=["date", "puzzle_num"]):
            d = date.fromisoformat(e["date"])
            if d < since:
                break
            word = e.get("word") if game in ("sutom", "tusmo") else None
            created += word_game(game, d, e["puzzle_num"], word=word) is not None
    for game, extra_key in (("loto", "lucky_ball"), ("euromillions", "stars")):
        for e in load_all_archives(DOCS_DIR / game / "archive", required_keys=["date", "balls", extra_key]):
            d = date.fromisoformat(e["date"])
            if d < since:
                break
            extras = e[extra_key] if isinstance(e[extra_key], list) else [e[extra_key]]
            created += draw_result(game, d, e["balls"], extras) is not None
    return created
