"""
Wordle board image renderer.

Generates a PNG of the game board — colored tiles with centred bold letters,
matching the classic Wordle dark-theme aesthetic.  Tiles are cached in memory
after the first render so repeated composition is fast.

Font resolution order:
  1. DejaVu Sans Bold  (ships with most Linux distros / Railway)
  2. Liberation Sans Bold
  3. Noto Sans Bold
  4. Arial Bold (macOS / Windows)
  5. PIL built-in fallback (Pillow ≥ 10 required for readable size)
"""

import io
from pathlib import Path

import discord
from PIL import Image, ImageDraw, ImageFont

from utils.words import CORRECT, PRESENT, ABSENT
from game.wordle import WordleGame

# ── Layout constants ──────────────────────────────────────────────────────────

TILE  = 62       # tile width and height in pixels
GAP   = 5        # gap between tiles
PAD   = 12       # board outer padding
FSIZE = 34       # font size for the letter

# ── Wordle dark-theme palette ─────────────────────────────────────────────────

_BG        = (18,  18,  19)    # #121213
_GREEN     = (83,  141, 78)    # #538D4E  — correct position
_YELLOW    = (181, 159, 59)    # #B59F3B  — present, wrong position
_DARKGREY  = (58,  58,  60)    # #3A3A3C  — absent / empty border
_WHITE     = (255, 255, 255)

_STATE_COLOR = {CORRECT: _GREEN, PRESENT: _YELLOW, ABSENT: _DARKGREY}

# ── Font resolution ───────────────────────────────────────────────────────────

_FONT_PATHS = [
    # Linux (Debian/Ubuntu/Railway/Render)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    # Bundled in project (drop any TTF here as data/fonts/wordle.ttf)
    str(Path(__file__).parent.parent / "data" / "fonts" / "wordle.ttf"),
]

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        for path in _FONT_PATHS:
            try:
                _font_cache[size] = ImageFont.truetype(path, size)
                break
            except (OSError, IOError):
                continue
        else:
            try:
                _font_cache[size] = ImageFont.load_default(size=size)
            except TypeError:
                _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


# ── Tile cache ────────────────────────────────────────────────────────────────

_tile_cache: dict[tuple, Image.Image] = {}


def _make_tile(letter: str | None, state: int | None) -> Image.Image:
    """Render one tile.  state=None means an empty (unguessed) slot."""
    img  = Image.new("RGB", (TILE, TILE), _BG)
    draw = ImageDraw.Draw(img)

    if state is None:
        # Empty slot — dark background + grey outline
        draw.rectangle([1, 1, TILE - 2, TILE - 2], outline=_DARKGREY, width=2)
    else:
        fill = _STATE_COLOR.get(state, _DARKGREY)
        draw.rectangle([0, 0, TILE - 1, TILE - 1], fill=fill)
        if letter:
            f    = _font(FSIZE)
            bbox = draw.textbbox((0, 0), letter, font=f)
            tw   = bbox[2] - bbox[0]
            th   = bbox[3] - bbox[1]
            tx   = (TILE - tw) // 2 - bbox[0]
            ty   = (TILE - th) // 2 - bbox[1]
            draw.text((tx, ty), letter, fill=_WHITE, font=f)

    return img


def _tile(letter: str | None, state: int | None) -> Image.Image:
    key = (letter, state)
    if key not in _tile_cache:
        _tile_cache[key] = _make_tile(letter, state)
    return _tile_cache[key]


# ── Board composer ────────────────────────────────────────────────────────────

def render_board_bytes(game: WordleGame) -> bytes:
    """Compose the full board as PNG bytes."""
    rows = game.max_guesses
    w = PAD * 2 + TILE * 5 + GAP * 4
    h = PAD * 2 + TILE * rows + GAP * (rows - 1)

    board = Image.new("RGB", (w, h), _BG)

    for r in range(rows):
        for c in range(5):
            x = PAD + c * (TILE + GAP)
            y = PAD + r * (TILE + GAP)
            if r < len(game.guesses):
                img = _tile(game.guesses[r][c], game.patterns[r][c])
            else:
                img = _tile(None, None)
            board.paste(img, (x, y))

    buf = io.BytesIO()
    board.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()


def board_file(game: WordleGame) -> discord.File | None:
    """Return a discord.File for the board PNG, or None if Pillow fails."""
    try:
        return discord.File(io.BytesIO(render_board_bytes(game)), filename="board.png")
    except Exception:
        return None
