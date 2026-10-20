"""Painted appearance themes for the launcher: palette, art loading and background composition.

The paintings are original generated illustrations (launcher/art/art-manifest.json); they
carry no text or status. Pillow, when installed, fits the art to the window and draws the
readability scrim. Without it the launcher still works: Tk decodes the PNG itself and the
art is shown at native scale beside an opaque panel.
"""
import hashlib
import json
import os
from pathlib import Path
import threading

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    HAVE_PIL = True
except ImportError:  # optional: the launcher degrades, it does not require Pillow
    HAVE_PIL = False

ART_DIR = Path(__file__).resolve().parent / 'art'
DEFAULT_THEME = 'doolin'
THEME_IDS = ('doolin', 'faery', 'undersea', 'ember')
# Used when the art manifest is missing, so the controls still read as words.
THEME_NAMES = {'doolin': 'Doolin Threshold', 'faery': 'The Lantern Court', 'undersea': 'Tidal Memory',
               'ember': 'Ember Procession'}

# theme-tokens.css
INK = '#080f16'
PANEL = '#101a21'
FIELD = '#0b141b'
IVORY = '#f1e9d9'
MUTED = '#c0b8ad'
GOLD = '#c9ac73'
GOLD_LIGHT = '#eddbaf'
JADE = '#99cec1'
MAUVE = '#a88da6'
RULE = '#4a4335'          # --rule (gold at 28%) flattened on the panel colour
DISABLED = '#6f767c'
ERROR = '#f2a094'
WARN = '#e8c27a'
INK_RGB = (8, 15, 22)
TITLE_FONT = ('Georgia', 'Times New Roman')
UI_FONT = ('Segoe UI', 'Arial')
FADE_MS = 240              # within the 220-350 ms guidance; 0 under reduced motion
SCRIM = ((0.0, 0.88), (0.30, 0.72), (0.51, 0.24), (0.69, 0.0))  # --scrim stops


class ArtLibrary:
    """Lazily decodes one painting at a time; every failure is recorded, never raised."""

    def __init__(self, root=ART_DIR):
        self.root = Path(root)
        self.errors = []
        self.themes, self.companions = {}, None
        try:
            manifest = json.loads((self.root / 'art-manifest.json').read_text(encoding='utf-8'))
            for entry in manifest['assets']:
                if entry['id'] in THEME_IDS:
                    self.themes[entry['id']] = entry
                elif entry['layer_type'] == 'single_transparent_group':
                    self.companions = entry
            self.notice = manifest.get('notice', '')
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.notice = ''
            self.errors.append(f'Launcher art manifest unreadable ({e}); using plain colours.')
        self._cache = {}
        self._lock = threading.Lock()

    def name(self, theme):
        return self.themes.get(theme, {}).get('name', THEME_NAMES.get(theme, theme))

    def description(self, theme):
        return self.themes.get(theme, {}).get('description', '')

    def path(self, entry_id, thumbnail=False):
        entry = self.themes.get(entry_id) or (self.companions if entry_id == 'spirits' else None)
        if not entry:
            return None
        return self.root / (entry['thumbnail'] if thumbnail else entry['file'])

    def verify(self, entry_id):
        """Hash check against the manifest (used by tests and diagnostics)."""
        entry = self.themes.get(entry_id) or self.companions
        p = self.path(entry_id)
        return bool(p and p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == entry['sha256'])

    def load(self, entry_id):
        """Decoded Pillow image, or None (error recorded). Keeps at most two decoded sources."""
        with self._lock:
            return self._load(entry_id)

    def _load(self, entry_id):
        if entry_id in self._cache:
            return self._cache[entry_id]
        p = self.path(entry_id)
        if p is None or not HAVE_PIL:
            return None
        entry = self.themes.get(entry_id) or self.companions
        try:
            with Image.open(p) as im:
                im.load()
                if im.size != (entry['width'], entry['height']):
                    raise ValueError(f'unexpected size {im.size[0]} x {im.size[1]}')
                image = im.convert('RGBA' if entry['mode'] == 'RGBA' else 'RGB')
        except (OSError, ValueError, SyntaxError) as e:
            self.errors.append(f'Artwork {p.name} could not be loaded ({e}); showing a plain background.')
            return None
        while len(self._cache) >= 2:
            self._cache.pop(next(iter(self._cache)))
        self._cache[entry_id] = image
        return image

    def release(self):
        """Drop decoded sources (called when the game starts)."""
        with self._lock:
            self._cache.clear()

    @property
    def cached(self):
        return tuple(self._cache)


def _cover(image, w, h, focal_x, focal_y=0.5):
    scale = max(w / image.width, h / image.height)
    sw, sh = max(w, round(image.width * scale)), max(h, round(image.height * scale))
    resized = image.resize((sw, sh), Image.LANCZOS)
    left = min(max(0, round(sw * focal_x - w / 2)), sw - w)
    top = min(max(0, round(sh * focal_y - h / 2)), sh - h)
    return resized.crop((left, top, left + w, top + h))


def _horizontal_scrim(w, h, stops=SCRIM, strength=1.0):
    """Ink overlay whose alpha follows --scrim from the left edge."""
    row = []
    for x in range(w):
        t = x / max(1, w - 1)
        alpha = 0.0
        for (a, va), (b, vb) in zip(stops, stops[1:]):
            if a <= t <= b:
                alpha = va + (vb - va) * (t - a) / (b - a)
                break
        row.append(round(255 * min(1.0, alpha * strength)))
    mask = Image.new('L', (w, 1))
    mask.putdata(row)
    return mask.resize((w, h))


def _vertical_vignette(w, h):
    """Bottom (0.96 -> 0 over 28%) and top (0.42 -> 0 over 20%) shading for footer and header text."""
    col = []
    for y in range(h):
        t = y / max(1, h - 1)
        bottom = 0.96 * max(0.0, (t - 0.72) / 0.28)
        top = 0.42 * max(0.0, (0.20 - t) / 0.20)
        col.append(round(255 * max(bottom, top)))
    mask = Image.new('L', (1, h))
    mask.putdata(col)
    return mask.resize((w, h))


def layout_mode(w, h):
    """'wide' (16:10..2:1 cover), 'ultrawide' (contained right with matte), 'narrow' (dimmed cover)."""
    ratio = w / max(1, h)
    if ratio > 2.0:
        return 'ultrawide'
    if ratio < 1.45:
        return 'narrow'
    return 'wide'


def compose(image, w, h, entry, page='home'):
    """Background for a window of w x h physical pixels; never distorts the painting."""
    w, h = max(1, int(w)), max(1, int(h))
    ink = Image.new('RGB', (w, h), INK_RGB)
    if image is None:
        return ink
    mode = layout_mode(w, h)
    fx, fy = entry.get('focal_x', 0.78), entry.get('focal_y', 0.5)
    if mode == 'ultrawide':
        # Keep the 16:9 aspect: full height, anchored right, ink gradient extension on the left.
        art = image.resize((round(image.width * h / image.height), h), Image.LANCZOS)
        canvas = ink.copy()
        x = w - art.width
        canvas.paste(art, (x, 0))
        fade = Image.new('L', (min(art.width, round(h * 0.6)), 1))
        fade.putdata([round(255 * (1 - i / max(1, fade.width - 1))) for i in range(fade.width)])
        canvas.paste(ink.crop((0, 0, fade.width, h)), (x, 0), fade.resize((fade.width, h)))
        base = canvas
    else:
        base = _cover(image, w, h, fx - (0.04 if mode == 'narrow' else 0.0), fy)
    if page == 'home':
        if mode == 'narrow':
            base = ImageEnhance.Brightness(base).enhance(0.55)
            base = Image.composite(ink, base, Image.new('L', (w, h), round(255 * 0.45)))
        else:
            base = Image.composite(ink, base, _horizontal_scrim(w, h))
        base = Image.composite(ink, base, _vertical_vignette(w, h))
    else:
        # Dense pages sit on an opaque panel; the art is only a dimmed backdrop.
        base = ImageEnhance.Brightness(base.filter(ImageFilter.GaussianBlur(max(2, w // 400)))).enhance(0.32)
    return base


def companion_image(image, max_w, max_h):
    """The transparent spirit group scaled as one image (aspect kept, nothing cropped)."""
    if image is None:
        return None
    scale = min(max_w / image.width, max_h / image.height, 1.0)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)


def blend(a, b, t):
    if a.size != b.size or a.mode != b.mode:
        return b
    return Image.blend(a, b, t)


def reduced_motion():
    """True when Windows 'Show animations' is off (SPI_GETCLIENTAREAANIMATION) or FOLKLORE_REDUCED_MOTION=1."""
    if os.environ.get('FOLKLORE_REDUCED_MOTION') == '1':
        return True
    if os.name != 'nt':
        return False
    try:
        import ctypes
        value = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
            return not value.value
    except (AttributeError, OSError):
        pass
    return False
