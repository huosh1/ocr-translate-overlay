# -*- coding: utf-8 -*-
"""Panneaux flottants : fond beige généré, coins arrondis, texte posé dessus.

Deux partis pris expliquent la forme du code.

Le fond est fabriqué à la volée par PIL plutôt que livré en PNG : il épouse la
taille exacte de la fenêtre au lieu d'être étiré, et le dépôt reste sans
binaire.

Le contenu est dessiné dans un Canvas, pas composé avec des widgets. Un widget
tkinter a un fond opaque : posé sur le dégradé, chaque libellé s'y découperait
en rectangle beige uni. Les items de Canvas, eux, n'ont pas de fond — le texte
flotte réellement sur l'image.
"""

import ctypes
import json
import os
import random
import re
import threading
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk


# ======================
# Coins arrondis par le système
# ======================
# Umbra est en WPF, qui sait faire de la transparence par pixel : ses coins
# sont découpés par le compositeur, avec un vrai dégradé d'opacité. Tkinter
# n'a pas cet équivalent.
#
# Windows 11 en offre un autre, accessible à n'importe quelle fenêtre : on
# demande à DWM d'arrondir la fenêtre elle-même. Le découpage est fait par le
# compositeur, donc parfaitement lissé, et le mode "small" donne l'arrondi
# discret plutôt que le grand rayon par défaut.
#
# Quand ça marche, on laisse le panneau carré et DWM s'occupe des coins. Sinon
# (Windows 10), on retombe sur l'arrondi dessiné dans l'image.
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_DWMWCP_ROUNDSMALL = 3


def enable_dwm_rounding(hwnd, small=True):
    """Renvoie True si le système a pris en charge l'arrondi."""
    if not hwnd:
        return False
    try:
        pref = ctypes.c_int(_DWMWCP_ROUNDSMALL if small else _DWMWCP_ROUND)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(_DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(pref),
            ctypes.sizeof(pref))
        return result == 0
    except Exception:
        return False


def toplevel_hwnd(window):
    """HWND de la fenêtre elle-même.

    winfo_id() renvoie le handle du widget ; pour un Toplevel, la fenêtre au
    sens Windows est son parent. GetParent renvoie 0 quand il n'y en a pas.
    """
    try:
        wid = window.winfo_id()
        parent = ctypes.windll.user32.GetParent(wid)
        return parent or wid
    except Exception:
        return 0


# ======================
# Palette
# ======================
BASE_RGB = (239, 230, 216)          # le papier
PAPER_HEX = "#efe6d8"               # le même, pour tkinter
BORDER_RGB = (206, 189, 161)

TEXT = "#141210"                    # l'encre
MUTED = "#7a7167"
FAINT = "#a89d8c"

# Taches de couleur, floutées ensuite jusqu'à ne plus être identifiables :
# position et rayon en fraction de la fenêtre, pour tenir à toute taille.
BLOBS = (
    ((246, 240, 229), 0.16, 0.10, 0.58),
    ((227, 211, 186), 0.84, 0.28, 0.52),
    ((237, 224, 203), 0.34, 0.88, 0.64),
    ((249, 244, 236), 0.70, 0.72, 0.42),
    ((224, 206, 179), 0.03, 0.60, 0.38),
)

# Pas de couleur clé de transparence. Le procédé n'efface que les pixels
# strictement égaux à la clé, ce qui interdit tout demi-pixel : les coins
# étaient soit frangés de rose, soit en escalier. On compose donc les coins
# contre une capture de ce qui se trouve derrière la fenêtre, et la fenêtre
# reste un rectangle parfaitement opaque — le dégradé du bord peut alors être
# aussi doux qu'on veut.
_backdrop = None


def set_backdrop(image):
    """Capture plein écran servant de fond aux coins arrondis.

    Prise une fois à l'ouverture des panneaux, avant qu'ils ne s'affichent,
    puis découpée selon la position de chaque fenêtre. Le bureau derrière ne
    bouge pas pendant qu'on lit, et seuls quelques pixels de coin en dépendent.
    """
    global _backdrop
    _backdrop = image

UI_FACE = "Segoe UI"
BODY_FACE = "Segoe UI"

# Segoe UI ne couvre pas le hangeul. En tête les faces humanistes que servent
# les sites coréens, Malgun Gothic ensuite, et seulement en dernier recours les
# vieilles fontes anguleuses, pour éviter les carrés vides.
KO_FACES = (
    "Pretendard", "Pretendard Variable",
    "Noto Sans KR", "Noto Sans CJK KR",
    "NanumBarunGothic", "NanumGothic", "NanumSquare",
    "Apple SD Gothic Neo", "Spoqa Han Sans Neo",
    "Malgun Gothic",
    "Dotum", "Gulim", "Batang", "MS Gothic",
)

_font_cache = {}


def ko_face():
    if "ko" not in _font_cache:
        try:
            available = set(name.lower() for name in tkfont.families())
        except Exception:
            available = set()
        _font_cache["ko"] = next(
            (name for name in KO_FACES if name.lower() in available),
            "Malgun Gothic")
    return _font_cache["ko"]


# ======================
# Fond généré
# ======================
_bg_cache = {}
_BG_CACHE_MAX = 8

# Facteur de suréchantillonnage du masque. PIL ne lisse pas rounded_rectangle :
# on trace donc l'arrondi quatre fois trop grand, puis on réduit avec un filtre
# BOX — moyenne exacte de chaque bloc de 4x4, donc un dégradé d'opacité juste
# sur le pourtour, sans le halo que laisserait un LANCZOS sur un masque binaire.
_SS = 4


def _rounded_alpha(width, height, radius, inset=0):
    big = Image.new("L", (width * _SS, height * _SS), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        [inset * _SS, inset * _SS,
         (width - inset) * _SS - 1, (height - inset) * _SS - 1],
        radius=max(1, radius - inset) * _SS, fill=255)
    return big.resize((width, height), Image.BOX)


def make_card(width, height, radius=16):
    """Le panneau : beige, formes floutées, bord et coins adoucis (RGBA)."""
    width, height = max(1, int(width)), max(1, int(height))
    key = (width, height, radius)
    if key in _bg_cache:
        return _bg_cache[key]

    layer = Image.new("RGB", (width, height), BASE_RGB)
    draw = ImageDraw.Draw(layer)
    span = max(width, height) * 0.5
    for color, cx, cy, rel in BLOBS:
        r = rel * span
        x, y = cx * width, cy * height
        draw.ellipse([x - r, y - r * 0.72, x + r, y + r * 0.72], fill=color)

    # Flou large : on ne veut pas reconnaître les ellipses, juste sentir que le
    # fond n'est pas uniforme.
    layer = layer.filter(ImageFilter.GaussianBlur(max(20, min(width, height) * 0.24)))
    img = Image.blend(Image.new("RGB", (width, height), BASE_RGB), layer, 0.9)

    # Grain léger : sans lui, un dégradé aussi doux se découpe en bandes.
    rnd = random.Random(7)
    pixels = img.load()
    for _ in range((width * height) // 30):
        x, y = rnd.randrange(width), rnd.randrange(height)
        n = rnd.randint(-3, 3)
        r, g, b = pixels[x, y]
        pixels[x, y] = (min(255, max(0, r + n)),
                        min(255, max(0, g + n)),
                        min(255, max(0, b + n)))

    # Le filet de bordure est lui aussi adouci : c'est l'anneau entre le
    # contour extérieur et le même contour rentré d'un pixel.
    outer = _rounded_alpha(width, height, radius)
    inner = _rounded_alpha(width, height, radius, inset=1)
    img.paste(Image.new("RGB", (width, height), BORDER_RGB), (0, 0),
              ImageChops.subtract(outer, inner))

    card = img.convert("RGBA")
    card.putalpha(outer)

    if len(_bg_cache) >= _BG_CACHE_MAX:
        _bg_cache.clear()
    _bg_cache[key] = card
    return card


def compose_panel(width, height, radius, x, y):
    """Panneau aplati sur ce qui se trouve derrière lui à l'écran.

    C'est ce qui permet des coins réellement lissés : les pixels à demi
    opaques du pourtour sont mélangés au vrai fond, et la fenêtre livrée à
    Windows reste un rectangle plein, sans transparence ni couleur clé.
    """
    width, height = max(1, int(width)), max(1, int(height))
    back = None
    if _backdrop is not None:
        bw, bh = _backdrop.size
        cx = min(max(0, int(x)), max(0, bw - width))
        cy = min(max(0, int(y)), max(0, bh - height))
        try:
            back = _backdrop.crop((cx, cy, cx + width, cy + height)).convert("RGB")
        except Exception:
            back = None
    if back is None or back.size != (width, height):
        back = Image.new("RGB", (width, height), BASE_RGB)

    card = make_card(width, height, radius)
    back.paste(card, (0, 0), card)
    return back


# ======================
# Position mémorisée
# ======================
_STATE_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "ocr-translate-overlay")
_STATE_FILE = os.path.join(_STATE_DIR, "windows.json")
_state_lock = threading.Lock()


def load_geometry(key):
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            value = json.load(f).get(key)
        return value if isinstance(value, str) else None
    except Exception:
        return None


def save_geometry(key, geometry):
    """Silencieux en cas d'échec : mémoriser une position ne doit jamais faire
    tomber l'outil."""
    with _state_lock:
        data = {}
        try:
            with open(_STATE_FILE, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            pass
        data[key] = geometry
        try:
            os.makedirs(_STATE_DIR, exist_ok=True)
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


# Les préférences partagent ce magasin avec les géométries, mais dans un espace
# de noms distinct. Sans le préfixe, la préférence "grammar" et la géométrie du
# panneau GRAMMAR occupaient la même clé et s'écrasaient l'une l'autre.
_PREF_PREFIX = "pref:"


def load_setting(key):
    return load_geometry(_PREF_PREFIX + key)


def save_setting(key, value):
    save_geometry(_PREF_PREFIX + key, value)


# ======================
# Panneau de base
# ======================
class PanelWindow(tk.Toplevel):
    state_key = "panel"
    default_size = (560, 220)
    default_top = 40
    min_size = (340, 150)
    radius = 10          # repli quand le système ne découpe pas les coins
    header_h = 40
    pad = 22

    def __init__(self, master, title, on_close=None):
        super().__init__(master)
        self._on_close = on_close
        self._panel_title = title
        self._bg_photo = None
        self._drag_origin = None
        self._resize_start = None
        self._save_job = None
        self._redraw_job = None
        self._scroll = 0
        self._scroll_span = 0
        self._close_box = None
        self._grip_box = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=PAPER_HEX)

        self._apply_saved_geometry()
        self.update_idletasks()

        # Si le système découpe les coins, l'image reste carrée : deux arrondis
        # superposés se verraient. Sinon on dessine le nôtre.
        self._dwm_rounded = enable_dwm_rounding(toplevel_hwnd(self), small=True)
        self._draw_radius = 0 if self._dwm_rounded else self.radius

        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, bg=PAPER_HEX)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Escape>", lambda e: self._close())

    # ---- géométrie ----
    def _apply_saved_geometry(self):
        w, h = self.default_size
        pos = None
        saved = load_geometry(self.state_key)
        if saved:
            m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", saved)
            if m:
                w = max(self.min_size[0], int(m.group(1)))
                h = max(self.min_size[1], int(m.group(2)))
                x, y = int(m.group(3)), int(m.group(4))
                # Un écran débranché depuis peut avoir laissé une position hors
                # champ : on ne restaure que si elle reste atteignable.
                if (-40 <= x <= self.winfo_screenwidth() - 120
                        and -10 <= y <= self.winfo_screenheight() - 80):
                    pos = (x, y)
        if pos is None:
            pos = (self.winfo_screenwidth() - w - 30, self.default_top)
        self.geometry("%dx%d+%d+%d" % (w, h, pos[0], pos[1]))

    def _schedule_save(self):
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        try:
            self._save_job = self.after(400, self._save_now)
        except Exception:
            self._save_job = None

    def _save_now(self):
        self._save_job = None
        try:
            save_geometry(self.state_key, self.geometry())
        except Exception:
            pass

    # ---- dessin ----
    def _on_configure(self, event):
        # Le fond est régénéré à chaque taille : on attend la fin du geste.
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except Exception:
                pass
        self._redraw_job = self.after(90, self.redraw)
        self._schedule_save()

    def redraw(self):
        self._redraw_job = None
        try:
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
        except Exception:
            return
        if width < 10 or height < 10:
            return

        self.canvas.delete("all")

        image = compose_panel(width, height, self._draw_radius,
                              self.winfo_x(), self.winfo_y())
        self._bg_photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(0, 0, image=self._bg_photo, anchor="nw", tags="bg")

        content_h = self._draw_body(width, height)
        visible = max(1, height - self.header_h - 10)
        self._scroll_span = max(0, content_h - visible)
        self._scroll = min(self._scroll, self._scroll_span)
        if self._scroll:
            self.canvas.move("scrollable", 0, -self._scroll)

        # Un Canvas ne découpe rien : le contenu qui défile passerait par-dessus
        # l'en-tête et déborderait sur les coins arrondis du bas. On repose donc
        # par-dessus deux tranches du fond lui-même — le dégradé reste continu,
        # là où un rectangle uni se verrait — puis l'en-tête au-dessus.
        band = image.crop((0, 0, width, self.header_h))
        self._top_photo = ImageTk.PhotoImage(band)
        self.canvas.create_image(0, 0, image=self._top_photo, anchor="nw")

        foot_h = min(14, height)
        foot = image.crop((0, height - foot_h, width, height))
        self._foot_photo = ImageTk.PhotoImage(foot)
        self.canvas.create_image(0, height - foot_h, image=self._foot_photo, anchor="nw")

        self._draw_header(width)
        self._draw_grip(width, height)

    def _draw_header(self, width):
        self.canvas.create_text(
            self.pad, self.header_h / 2,
            text=self._panel_title, anchor="w",
            fill=MUTED, font=(UI_FACE, 10))

        x = width - self.pad
        self._close_id = self.canvas.create_text(
            x, self.header_h / 2, text="✕", anchor="e",
            fill=MUTED, font=(UI_FACE, 12))
        self._close_box = (x - 18, self.header_h / 2 - 12, x + 6, self.header_h / 2 + 12)

    def _draw_grip(self, width, height):
        self.canvas.create_text(
            width - 8, height - 6, text="◢", anchor="se",
            fill=FAINT, font=(UI_FACE, 8))
        self._grip_box = (width - 22, height - 22, width, height)

    def _draw_body(self, width, height):
        """À implémenter : dessine le contenu, renvoie sa hauteur totale.

        Tout item qui doit défiler porte le tag "scrollable".
        """
        return 0

    # ---- interactions ----
    @staticmethod
    def _inside(box, x, y):
        return box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def _on_press(self, event):
        if self._inside(self._close_box, event.x, event.y):
            self._close()
            return
        if self._inside(self._grip_box, event.x, event.y):
            self._resize_start = (event.x_root, event.y_root,
                                  self.winfo_width(), self.winfo_height())
            return
        if event.y <= self.header_h:
            self._drag_origin = (event.x_root - self.winfo_x(),
                                 event.y_root - self.winfo_y())

    def _on_motion(self, event):
        if self._resize_start:
            x0, y0, w0, h0 = self._resize_start
            self.geometry("%dx%d" % (
                max(self.min_size[0], w0 + event.x_root - x0),
                max(self.min_size[1], h0 + event.y_root - y0)))
        elif self._drag_origin:
            self.geometry("+%d+%d" % (event.x_root - self._drag_origin[0],
                                      event.y_root - self._drag_origin[1]))

    def _on_release(self, _event):
        moved = self._drag_origin is not None or self._resize_start is not None
        self._drag_origin = None
        self._resize_start = None
        self._schedule_save()
        # Les coins sont composés sur le fond d'écran découpé à la position de
        # la fenêtre : elle a bougé, la découpe n'est plus la bonne. Inutile si
        # c'est le système qui découpe.
        if moved and not self._dwm_rounded:
            self.redraw()

    def _on_hover(self, event):
        over_close = self._inside(self._close_box, event.x, event.y)
        try:
            self.canvas.itemconfigure(self._close_id, fill=TEXT if over_close else MUTED)
        except Exception:
            pass
        if over_close:
            cursor = "hand2"
        elif self._inside(self._grip_box, event.x, event.y):
            cursor = "size_nw_se"
        elif event.y <= self.header_h:
            cursor = "fleur"
        else:
            cursor = "arrow"
        try:
            self.canvas.configure(cursor=cursor)
        except Exception:
            pass

    def _on_wheel(self, event):
        if self._scroll_span <= 0:
            return
        step = -int(event.delta / 120) * 40
        new = min(self._scroll_span, max(0, self._scroll + step))
        if new != self._scroll:
            self.canvas.move("scrollable", 0, self._scroll - new)
            self._scroll = new

    def _close(self):
        self._save_now()
        if self._on_close:
            self._on_close()
        self.destroy()


# ======================
# Panneau 1 : traduction
# ======================
class TranslationPanel(PanelWindow):
    state_key = "translation"
    default_size = (560, 190)
    default_top = 40
    min_size = (340, 150)

    def __init__(self, master, text, on_close=None):
        self._text = (text or "").strip() or "OCR vide"
        super().__init__(master, "TRANSLATION", on_close=on_close)
        self.after(10, self.redraw)

    def _draw_body(self, width, height):
        # Une seule couleur : create_text sait replier tout seul.
        item = self.canvas.create_text(
            self.pad, self.header_h + 4,
            text=self._text, anchor="nw", justify="left",
            width=max(80, width - 2 * self.pad),
            fill=TEXT, font=(BODY_FACE, 14), tags="scrollable")
        box = self.canvas.bbox(item)
        return (box[3] - self.header_h) if box else 0


# ======================
# Panneau 2 : analyse grammaticale
# ======================
class GrammarPanel(PanelWindow):
    state_key = "grammar"
    default_size = (620, 430)
    default_top = 260
    min_size = (400, 220)

    def __init__(self, master, tokens, source_text="", legend=(), on_close=None):
        self._tokens = list(tokens or [])
        self._source = source_text or ""
        self._legend = list(legend or [])
        self._translations = {}
        super().__init__(master, "GRAMMAR", on_close=on_close)
        self.after(10, self.redraw)

    def set_translations(self, mapping):
        """Traductions mot à mot, arrivées après coup : on redessine."""
        self._translations = mapping or {}
        try:
            self.redraw()
        except Exception:
            pass

    def _draw_body(self, width, height):
        y = self.header_h + 2
        if self._source and self._tokens:
            y = self._draw_sentence(width, y)
        y = self._draw_legend(width, y)
        return self._draw_rows(width, y) - self.header_h

    # ---- la phrase, remise dans l'ordre ----
    def _draw_sentence(self, width, top):
        """Réaffiche la phrase entière, chaque mot dans la couleur de sa nature.

        La reconstruction se fait sur le texte d'origine et non en recollant les
        tokens : Okt sépare les particules du mot qu'elles suivent (나는 devient
        나 + 는), donc les recoller avec des espaces donnerait une phrase fausse.
        On avance dans la source, on colore chaque token là où il apparaît, et
        on laisse tel quel ce qui se trouve entre deux tokens.

        Le repli est calculé à la main : un item de Canvas ne porte qu'une
        couleur, il en faut donc un par mot, et create_text ne peut pas replier
        pour nous.
        """
        face = ko_font_spec()
        measure = tkfont.Font(family=face[0], size=face[1])
        line_h = measure.metrics("linespace") + 6
        left, right = self.pad, width - self.pad
        x, y = left, top + 10

        def emit(chunk, color):
            nonlocal x, y
            if not chunk:
                return
            w = measure.measure(chunk)
            if x > left and x + w > right:
                x, y = left, y + line_h
            self.canvas.create_text(x, y, text=chunk, anchor="nw",
                                    fill=color, font=face, tags="scrollable")
            x += w

        cursor = 0
        for tok in self._tokens:
            word = tok.get("word") or ""
            if not word:
                continue
            idx = self._source.find(word, cursor)
            if idx >= 0:
                if idx > cursor:
                    emit(self._source[cursor:idx], FAINT)
                emit(word, tok.get("color", TEXT))
                cursor = idx + len(word)
            else:
                # Okt a normalisé le mot : introuvable tel quel dans la source.
                emit(" ", FAINT)
                emit(word, tok.get("color", TEXT))
        if cursor < len(self._source):
            emit(self._source[cursor:], FAINT)

        return y + line_h + 6

    def _draw_legend(self, width, top):
        x, y = self.pad, top + 2
        for label, color in self._legend:
            self.canvas.create_text(x, y, text="■", anchor="nw",
                                    fill=color, font=(UI_FACE, 7), tags="scrollable")
            x += 12
            self.canvas.create_text(x, y, text=label, anchor="nw",
                                    fill=MUTED, font=(UI_FACE, 9), tags="scrollable")
            x += tkfont.Font(family=UI_FACE, size=9).measure(label) + 14
            if x > width - self.pad - 60:
                x, y = self.pad, y + 18
        return y + 24

    def _draw_rows(self, width, top):
        face = ko_font_spec()
        y = top
        for i, tok in enumerate(self._tokens):
            if tok.get("pos") == "Punctuation" or not (tok.get("word") or "").strip():
                continue
            self.canvas.create_text(self.pad, y, text="%02d" % (i + 1), anchor="nw",
                                    fill=FAINT, font=(UI_FACE, 9), tags="scrollable")
            self.canvas.create_text(self.pad + 34, y - 3, text=tok["word"], anchor="nw",
                                    fill=tok.get("color", TEXT), font=face,
                                    tags="scrollable")
            self.canvas.create_text(self.pad + 170, y, text=tok.get("label", "").lower(),
                                    anchor="nw", fill=MUTED, font=(UI_FACE, 9),
                                    tags="scrollable")
            trad = self._translations.get(tok["word"], "")
            if trad:
                self.canvas.create_text(self.pad + 268, y, text=trad, anchor="nw",
                                        fill=TEXT, font=(UI_FACE, 10),
                                        width=max(60, width - self.pad - 278),
                                        tags="scrollable")
            y += 28
        return y + 10


def ko_font_spec(size=17):
    return (ko_face(), size)
