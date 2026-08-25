"""
Option B (Windows) — OCR + Traduction FR + Analyse grammaticale coréenne

✅ Fonctionnement (clavier seul, aucun clic) :
- Maintiens Ctrl + Alt : le coin du rectangle est ancré là où se trouve la souris
- Déplace la souris, toujours en maintenant : le rectangle se dessine
- Relâche Ctrl + Alt : OCR de la zone + traduction FR + overlay traduction
  + overlay grammatical

Overlays :
  1. Overlay TRADUCTION  : texte traduit en français (coin haut droit)
  2. Overlay GRAMMAIRE   : la phrase coréenne entière, remise dans l'ordre et
                           colorée selon la nature de chaque mot, puis le détail
                           mot par mot en dessous

Couleurs par nature grammaticale :
  Verbe        → #E07B54 (orange)
  Nom          → #5B9BD5 (bleu)
  Adjectif     → #6DBF82 (vert)
  Adverbe      → #C98FD4 (violet)
  Pronom       → #F0C040 (jaune)
  Particule    → #8FBCBB (cyan)
  Autre        → #AAAAAA (gris)

Touches :
- ESC : ferme les overlays
- F8  : quitte le programme

Dépendances:
    pip install mss pillow pytesseract requests pynput customtkinter konlpy
+ Installer Tesseract (Windows) et vérifier TESSERACT_PATH.
+ Installer Java (requis par KoNLPy) : https://www.java.com/fr/download/
"""

import json
import os
import re
import time
import threading
import ctypes
from ctypes import wintypes

import requests
import pytesseract
from PIL import Image, ImageOps
import mss

from pynput import keyboard, mouse

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

# KoNLPy import avec message d'erreur clair
try:
    from konlpy.tag import Okt
    KONLPY_AVAILABLE = True
except ImportError:
    KONLPY_AVAILABLE = False


# ======================
# TESSERACT CONFIG
# ======================
# Chemin resolu automatiquement (PATH, emplacements habituels, registre) et
# langues verifiees au demarrage : voir tesseract_setup.py. C'est ce qui evite
# le cas ou kor.traineddata manque et ou Tesseract rend du charabia anglais
# sans signaler d'erreur. Pour imposer un chemin, definir TESSERACT_PATH.
from tesseract_setup import configure_tesseract

TESSERACT_PATH = configure_tesseract(("kor", "eng"))


# ======================
# COULEURS GRAMMATICALES
# ======================
# Tags Okt → couleur + label français
# Encres colorées sur papier crème, façon annotation à la main : des teintes
# sombres et rabattues, jamais des couleurs d'écran. Elles doivent rester
# lisibles sur #efe6d8, ce qui exclut tout ce qui est clair ou fluo.
POS_STYLES = {
    "Verb":           {"color": "#9c4221", "label": "Verbe"},
    "Adjective":      {"color": "#3f6b45", "label": "Adjectif"},
    "Noun":           {"color": "#2f5d8a", "label": "Nom"},
    "ProperNoun":     {"color": "#274c72", "label": "Nom propre"},
    "Pronoun":        {"color": "#8a6516", "label": "Pronom"},
    "Adverb":         {"color": "#6b4a7a", "label": "Adverbe"},
    "Josa":           {"color": "#2b6b66", "label": "Particule"},
    "Eomi":           {"color": "#8a5a2b", "label": "Terminaison"},
    "Conjunction":    {"color": "#7a4560", "label": "Conjonction"},
    "Determiner":     {"color": "#4f6b3f", "label": "Déterminant"},
    "Number":         {"color": "#7d6420", "label": "Nombre"},
    "Foreign":        {"color": "#5f5850", "label": "Étranger"},
    "Alpha":          {"color": "#5f5850", "label": "Alphabet"},
    "Punctuation":    {"color": "#a89d8c", "label": "Ponctuation"},
    "Unknown":        {"color": "#7a7167", "label": "Inconnu"},
}
DEFAULT_STYLE = {"color": "#5f5850", "label": "Autre"}


# ======================
# Apparence des fenêtres
# ======================
# Ambiance page imprimée : fond crème, encre sombre. C'est la palette d'origine
# de la fenêtre de traduction, étendue à l'analyse grammaticale pour que les
# deux panneaux forment un même objet.
BG     = "#efe6d8"   # la page
BG_ALT = "#e6dbc8"   # bandeaux, zones en retrait
BG_ROW = "#e9dfce"   # alternance des lignes de la liste
BORDER = "#d3c3a9"
TEXT   = "#111111"   # l'encre
MUTED  = "#7a7167"
FAINT  = "#a89d8c"

UI_FACE   = "Segoe UI"
BODY_FACE = "Segoe UI Semibold"

# Segoe UI ne couvre pas le hangeul : sans face coréenne explicite, Windows
# substitue au coup par coup et l'alignement des colonnes part de travers.
#
# L'ordre compte. En tête, les faces humanistes que servent les sites coréens
# (Naver, Kakao) et qui donnent ce hangeul rond et ouvert. Malgun Gothic, la
# police système de Windows, vient ensuite : correcte mais plus sèche. En
# queue seulement Gulim, Batang et MS Gothic, qui datent et rendent un hangeul
# anguleux — elles ne servent que de dernier recours pour éviter les carrés
# vides. Aucune des premières n'est installée d'office sur Windows : poser
# Pretendard ou Noto Sans KR suffit à changer le rendu, le code la prendra.
KO_FACES = (
    "Pretendard", "Pretendard Variable",
    "Noto Sans KR", "Noto Sans CJK KR",
    "NanumBarunGothic", "NanumGothic", "NanumSquare",
    "Apple SD Gothic Neo", "Spoqa Han Sans Neo",
    "Malgun Gothic",
    "Dotum", "Gulim", "Batang", "MS Gothic",
)

_font_cache = {}


def _pick_font(candidates, fallback):
    """Première police installée parmi les candidates.

    tkfont.families() a besoin d'une racine Tk : l'appel est donc différé au
    moment de la construction des fenêtres, jamais à l'import.
    """
    try:
        from tkinter import font as tkfont
        available = set(name.lower() for name in tkfont.families())
    except Exception:
        return fallback
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback


def ko_font():
    if "ko" not in _font_cache:
        _font_cache["ko"] = _pick_font(KO_FACES, "Malgun Gothic")
    return _font_cache["ko"]


def ui_font():
    return UI_FACE


def body_font():
    return BODY_FACE


# ======================
# Mémorisation de la position des fenêtres
# ======================
_STATE_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "ocr-translate-overlay")
_STATE_FILE = os.path.join(_STATE_DIR, "windows.json")
_state_lock = threading.Lock()


def load_geometry(key):
    """Renvoie la géométrie mémorisée ("LxH+X+Y") ou None."""
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            value = json.load(f).get(key)
        return value if isinstance(value, str) else None
    except Exception:
        return None


def save_geometry(key, geometry):
    """Écrit la géométrie. Silencieux en cas d'échec : mémoriser la position
    d'une fenêtre ne doit jamais faire tomber l'outil."""
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


# ======================
# DPI AWARE
# ======================
def set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ======================
# BLUR (Windows 10/11)
# ======================
def enable_windows_blur(hwnd: int, acrylic: bool = True):
    user32 = ctypes.windll.user32

    class ACCENTPOLICY(ctypes.Structure):
        _fields_ = [
            ("AccentState", ctypes.c_int),
            ("AccentFlags", ctypes.c_int),
            ("GradientColor", ctypes.c_int),
            ("AnimationId", ctypes.c_int),
        ]

    class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
        _fields_ = [
            ("Attribute", ctypes.c_int),
            ("Data", ctypes.c_void_p),
            ("SizeOfData", ctypes.c_size_t),
        ]

    accent = ACCENTPOLICY()
    accent.AccentState = 4 if acrylic else 3
    accent.AccentFlags = 2
    accent.GradientColor = 0xBBEFE6D8

    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = 19
    data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(accent)

    set_attr = user32.SetWindowCompositionAttribute
    set_attr.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
    set_attr.restype = wintypes.BOOL
    set_attr(hwnd, ctypes.byref(data))


# ======================
# UTIL: traduction (MyMemory) + cache
# ======================
def cleanup(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return " ".join(lines).strip()


class LRUCache:
    def __init__(self, max_items=2000):
        self.max_items = max_items
        self._d = {}
        self._order = []

    def get(self, k):
        if k in self._d:
            try:
                self._order.remove(k)
            except ValueError:
                pass
            self._order.append(k)
            return self._d[k]
        return None

    def set(self, k, v):
        if k in self._d:
            self._d[k] = v
            try:
                self._order.remove(k)
            except ValueError:
                pass
            self._order.append(k)
            return
        self._d[k] = v
        self._order.append(k)
        if len(self._order) > self.max_items:
            old = self._order.pop(0)
            self._d.pop(old, None)


TRANSLATE_CACHE = LRUCache(max_items=4000)


def translate_mymemory(text: str) -> str:
    text = cleanup(text)
    if not text:
        return ""
    cached = TRANSLATE_CACHE.get(text)
    if cached is not None:
        return cached
    r = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text, "langpair": "ko|fr"},
        timeout=20,
    )
    r.raise_for_status()
    out = cleanup(r.json()["responseData"]["translatedText"])
    TRANSLATE_CACHE.set(text, out)
    return out


# ======================
# OCR helpers
# ======================
def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    img = img.resize((img.width * 2, img.height * 2), Image.BICUBIC)
    return img


def ocr_text_block(img: Image.Image) -> str:
    config = "--oem 3 --psm 6"
    text = pytesseract.image_to_string(img, lang="kor+eng", config=config)
    return cleanup(text)


# ======================
# Analyse grammaticale KoNLPy
# ======================
_okt_instance = None
_okt_lock = threading.Lock()


def get_okt():
    global _okt_instance
    with _okt_lock:
        if _okt_instance is None and KONLPY_AVAILABLE:
            _okt_instance = Okt()
    return _okt_instance


def analyze_korean(text: str) -> list:
    """
    Retourne une liste de dicts :
    [{"word": "나는", "pos": "Pronoun", "color": "#8a6516", "label": "Pronom"}, ...]
    """
    if not KONLPY_AVAILABLE:
        return []

    okt = get_okt()
    if okt is None:
        return []

    # Okt.pos() retourne [(mot, tag), ...]
    tagged = okt.pos(text, norm=True, stem=False)

    result = []
    for word, pos in tagged:
        word = word.strip()
        if not word:
            continue
        style = POS_STYLES.get(pos, DEFAULT_STYLE)
        result.append({
            "word": word,
            "pos": pos,
            "color": style["color"],
            "label": style["label"],
        })
    return result


# ======================
# Fast capture (mss)
# ======================
class ScreenGrabber:
    def __init__(self):
        self._tls = threading.local()

    def _get_sct(self):
        if not hasattr(self._tls, "sct"):
            self._tls.sct = mss.mss()
        return self._tls.sct

    def grab_rect(self, left: int, top: int, width: int, height: int) -> Image.Image:
        sct = self._get_sct()
        mon = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


# ======================
# Fenêtre de base
# ======================
class PanelWindow(ctk.CTkToplevel):
    """Habillage commun aux deux overlays.

    Trois choses réglées ici, qui manquaient ou étaient cassées :

    - Fond opaque. L'ancienne version peignait la fenêtre en magenta et
      demandait à Windows de rendre cette couleur transparente. Le procédé
      compare les pixels à l'identique : ceux que l'anti-aliasing des coins
      arrondis situe entre le magenta et la carte ne sont pas éliminés, et
      restent à l'écran sous forme d'un liseré rose.
    - En-tête saisissable. Sans décoration système, une fenêtre n'a aucune
      zone de préhension : il fallait viser la poignée d'angle.
    - Position et taille mémorisées d'une session à l'autre.
    """

    state_key = "panel"
    default_size = (560, 220)
    default_top = 40
    min_size = (320, 120)

    def __init__(self, master, title, on_close=None):
        super().__init__(master)
        self._on_close = on_close
        self._drag_origin = None
        self._resize_start = None
        self._save_job = None

        # Palette claire : les widgets CustomTkinter dont on ne fixe pas
        # explicitement la couleur doivent piocher dans les tons clairs.
        ctk.set_appearance_mode("light")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG)
        self._apply_saved_geometry()

        card = ctk.CTkFrame(self, corner_radius=0, fg_color=BG,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True)

        header = ctk.CTkFrame(card, corner_radius=0, fg_color=BG_ALT, height=32)
        header.pack(fill="x")
        header.pack_propagate(False)

        title_label = ctk.CTkLabel(
            header, text=title,
            font=ctk.CTkFont(ui_font(), 11),
            text_color=MUTED, anchor="w",
        )
        title_label.pack(side="left", padx=12)

        # Un label plutôt qu'un CTkButton : pas de pastille arrondie qui jure
        # avec les angles droits du panneau.
        close = ctk.CTkLabel(
            header, text="✕", width=32,
            font=ctk.CTkFont(ui_font(), 12),
            text_color=MUTED, cursor="hand2",
        )
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self._close())
        close.bind("<Enter>", lambda e: close.configure(text_color=TEXT))
        close.bind("<Leave>", lambda e: close.configure(text_color=MUTED))

        for widget in (header, title_label):
            widget.bind("<ButtonPress-1>", self._on_drag_start)
            widget.bind("<B1-Motion>", self._on_drag)

        self.body = ctk.CTkFrame(card, corner_radius=0, fg_color=BG)
        self.body.pack(fill="both", expand=True)

        grip = ctk.CTkLabel(card, text="◢", text_color=BORDER,
                            font=ctk.CTkFont(ui_font(), 10), cursor="size_nw_se")
        grip.place(relx=1.0, rely=1.0, x=-4, y=-2, anchor="se")
        grip.bind("<ButtonPress-1>", self._on_resize_start)
        grip.bind("<B1-Motion>", self._on_resize_drag)

        self.bind("<Escape>", lambda e: self._close())
        self.bind("<Configure>", self._schedule_save)

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
                # Un écran débranché depuis peut avoir laissé une position
                # hors champ : on ne restaure que si elle reste atteignable.
                if (-40 <= x <= self.winfo_screenwidth() - 120
                        and -10 <= y <= self.winfo_screenheight() - 80):
                    pos = (x, y)

        if pos is None:
            pos = (self.winfo_screenwidth() - w - 30, self.default_top)

        self.geometry("%dx%d+%d+%d" % (w, h, pos[0], pos[1]))

    def _schedule_save(self, _event=None):
        # Un déplacement émet des dizaines d'événements : on n'écrit qu'une
        # fois le geste terminé.
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

    # ---- déplacement ----
    def _on_drag_start(self, event):
        self._drag_origin = (event.x_root - self.winfo_x(),
                             event.y_root - self.winfo_y())

    def _on_drag(self, event):
        if not self._drag_origin:
            return
        self.geometry("+%d+%d" % (event.x_root - self._drag_origin[0],
                                  event.y_root - self._drag_origin[1]))

    # ---- redimensionnement ----
    def _on_resize_start(self, event):
        self._resize_start = (event.x_root, event.y_root,
                              self.winfo_width(), self.winfo_height())

    def _on_resize_drag(self, event):
        if not self._resize_start:
            return
        x0, y0, w0, h0 = self._resize_start
        self.geometry("%dx%d" % (
            max(self.min_size[0], w0 + event.x_root - x0),
            max(self.min_size[1], h0 + event.y_root - y0)))

    def _close(self):
        self._save_now()
        if self._on_close:
            self._on_close()
        self.destroy()


# ======================
# Overlay 1 : Traduction
# ======================
class TranslationOverlay(PanelWindow):
    state_key = "translation"
    default_size = (560, 190)
    default_top = 40
    min_size = (320, 120)

    def __init__(self, master, text: str, on_close=None):
        super().__init__(master, "TRADUCTION", on_close=on_close)

        box = tk.Text(
            self.body, wrap="word",
            bg=BG, fg=TEXT,
            relief="flat", borderwidth=0, highlightthickness=0,
            padx=22, pady=16, spacing1=2, spacing2=2, spacing3=6,
            cursor="arrow", font=(body_font(), 14),
        )
        box.pack(fill="both", expand=True)
        box.insert("1.0", text.strip() if text else "OCR vide")
        box.configure(state="disabled")


# ======================
# Overlay 2 : Analyse grammaticale
# ======================
class GrammarOverlay(PanelWindow):
    state_key = "grammar"
    default_size = (620, 420)
    default_top = 260
    min_size = (380, 200)

    def __init__(self, master, tokens: list, source_text: str = "", on_close=None):
        """
        tokens      : liste de dicts {"word", "pos", "color", "label"}
        source_text : phrase coréenne d'origine, réaffichée en tête colorée
        """
        super().__init__(master, "ANALYSE GRAMMATICALE", on_close=on_close)

        # ~34 caractères coréens par ligne à la largeur par défaut.
        sentence_lines = 0
        if source_text:
            sentence_lines = max(1, min(6, len(source_text) // 34 + 1))

        # Phrase complète, remise dans l'ordre et colorée
        if source_text and tokens:
            self._build_sentence(self.body, tokens, source_text, sentence_lines)

        # Légende, tirée des mêmes styles que la coloration pour qu'elles ne
        # puissent pas diverger.
        legend = ctk.CTkFrame(self.body, corner_radius=0, fg_color=BG)
        legend.pack(fill="x", padx=14, pady=(2, 8))

        for tag in ("Verb", "Noun", "Adjective", "Adverb", "Pronoun", "Josa"):
            style = POS_STYLES[tag]
            ctk.CTkLabel(
                legend, text="■", width=12,
                font=ctk.CTkFont(ui_font(), 10),
                text_color=style["color"],
            ).pack(side="left", padx=(8, 2))
            ctk.CTkLabel(
                legend, text=style["label"],
                font=ctk.CTkFont(ui_font(), 10),
                text_color=MUTED,
            ).pack(side="left")

        # Zone scrollable des tokens
        scroll = ctk.CTkScrollableFrame(
            self.body,
            corner_radius=0,
            fg_color=BG_ALT,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=MUTED,
        )
        scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        if not tokens:
            ctk.CTkLabel(
                scroll,
                text="KoNLPy non disponible ou texte vide.",
                text_color=MUTED,
                font=ctk.CTkFont(ui_font(), 12),
            ).pack(pady=20)
        else:
            self._build_tokens(scroll, tokens)

    def _build_sentence(self, parent, tokens, source_text, lines):
        """Réaffiche la phrase entière, dans l'ordre, chaque mot dans la
        couleur de sa nature.

        La liste détaillée en dessous découpe la phrase mot par mot, ce qui
        fait perdre le fil. Ce bandeau garde la phrase telle qu'elle a été
        lue et n'y ajoute que la couleur, pour se repérer d'un coup d'œil.

        La reconstruction se fait sur le texte d'origine plutôt qu'en
        recollant les tokens : Okt sépare les particules du mot qu'elles
        suivent (나는 devient 나 + 는), donc les recoller avec des espaces
        donnerait une phrase fausse. On avance dans le texte source, on
        colore chaque token là où il apparaît, et on laisse tel quel ce qui
        se trouve entre deux tokens (espaces, ponctuation).
        """
        box = tk.Text(
            parent,
            wrap="word",
            height=lines,
            bg=BG_ALT,
            fg=FAINT,          # ce qui n'est pas un mot analysé : ponctuation, espaces
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=20,
            pady=16,
            spacing1=4,
            spacing2=4,
            spacing3=4,
            cursor="arrow",
            font=(ko_font(), 18),
        )
        box.pack(fill="x", padx=16, pady=(14, 10))

        cursor = 0
        for tok in tokens:
            word = tok["word"]
            if not word:
                continue

            tag = "pos_" + tok["color"].lstrip("#")
            box.tag_configure(tag, foreground=tok["color"])

            idx = source_text.find(word, cursor)
            if idx >= 0:
                if idx > cursor:
                    # Espaces et ponctuation d'origine, laissés en gris.
                    box.insert("end", source_text[cursor:idx])
                box.insert("end", word, tag)
                cursor = idx + len(word)
            else:
                # Okt a normalisé le mot : il ne se retrouve pas tel quel
                # dans la source. On l'ajoute quand même, séparé d'un espace.
                if box.index("end-1c") != "1.0":
                    box.insert("end", " ")
                box.insert("end", word, tag)

        if cursor < len(source_text):
            box.insert("end", source_text[cursor:])

        box.configure(state="disabled")

    def _build_tokens(self, parent, tokens):
        """Affiche les tokens en grille : num | mot | badge nature | traduction FR"""
        # Filtrer la ponctuation pure pour ne pas polluer l'affichage
        filtered = [t for t in tokens if t["pos"] not in ("Punctuation",) and t["word"].strip()]

        # Traduire tous les mots en parallèle
        word_translations = {}
        skip_pos = {"Josa", "Eomi", "Punctuation", "Unknown"}

        def fetch_translation(word):
            try:
                trad = translate_mymemory(word)
                word_translations[word] = trad if trad and trad.lower() != word.lower() else ""
            except Exception:
                word_translations[word] = ""

        threads = []
        for tok in filtered:
            if tok["pos"] not in skip_pos:
                t = threading.Thread(target=fetch_translation, args=(tok["word"],), daemon=True)
                t.start()
                threads.append(t)
        for t in threads:
            t.join(timeout=8)

        for i, tok in enumerate(filtered):
            row = ctk.CTkFrame(parent, corner_radius=0,
                               fg_color=BG_ROW if i % 2 == 0 else BG_ALT)
            row.pack(fill="x", pady=1)

            # Numéro
            ctk.CTkLabel(
                row,
                text="%02d" % (i + 1),
                font=ctk.CTkFont(ui_font(), 11),
                text_color=FAINT,
                width=30,
            ).pack(side="left", padx=(10, 6), pady=7)

            # Mot coréen, dans la couleur de sa nature
            ctk.CTkLabel(
                row,
                text=tok["word"],
                font=ctk.CTkFont(ko_font(), 17),
                text_color=tok["color"],
                width=130,
                anchor="w",
            ).pack(side="left", padx=(0, 10), pady=7)

            # Nature, sans pastille : le mot porte déjà la couleur
            ctk.CTkLabel(
                row,
                text=tok["label"].lower(),
                font=ctk.CTkFont(ui_font(), 11),
                text_color=MUTED,
                width=92,
                anchor="w",
            ).pack(side="left", padx=(0, 10), pady=7)

            # Traduction française du mot
            trad = word_translations.get(tok["word"], "")
            if trad:
                ctk.CTkLabel(
                    row,
                    text=trad,
                    font=ctk.CTkFont(ui_font(), 12),
                    text_color=TEXT,
                    anchor="w",
                ).pack(side="left", padx=(0, 10), pady=7)


# ======================
# Rubberband selection
# ======================
class RubberBand:
    def __init__(self, root: tk.Tk):
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.22)
        self.win.config(bg="#4a90e2")

        self.active = False
        self.x1 = self.y1 = self.x2 = self.y2 = 0

    def start(self, x, y):
        self.active = True
        self.x1 = self.x2 = x
        self.y1 = self.y2 = y
        self._update()

    def move(self, x, y):
        if not self.active:
            return
        self.x2, self.y2 = x, y
        self._update()

    def stop(self):
        self.active = False
        self.win.withdraw()

    def rect(self):
        left = min(self.x1, self.x2)
        top = min(self.y1, self.y2)
        right = max(self.x1, self.x2)
        bottom = max(self.y1, self.y2)
        return left, top, right, bottom

    def _update(self):
        left, top, right, bottom = self.rect()
        w = max(1, right - left)
        h = max(1, bottom - top)
        self.win.geometry(f"{w}x{h}+{left}+{top}")
        self.win.deiconify()


# ======================
# APP
# ======================
class App:
    def __init__(self):
        set_dpi_aware()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.attributes("-topmost", True)

        self.rubber = RubberBand(self.root)
        self.grabber = ScreenGrabber()

        self.ctrl_down = False
        self.alt_down = False

        # Selection au clavier seul : Ctrl+Alt ancre le premier coin a la
        # position courante de la souris, le deplacement trace le rectangle,
        # le relachement de la combinaison valide. Aucun clic n'intervient,
        # les clics restent donc disponibles pour l'application du dessous.
        self.armed = False
        self.anchor = (0, 0)
        self.band_shown = False
        self.mouse_ctl = mouse.Controller()

        self.translation_overlay = None
        self.grammar_overlay = None

        # Pré-charger Okt en arrière-plan (évite le délai au premier usage)
        if KONLPY_AVAILABLE:
            threading.Thread(target=get_okt, daemon=True).start()

        self.k_listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
        self.m_listener = mouse.Listener(on_move=self.on_move)

    def run(self):
        self.k_listener.start()
        self.m_listener.start()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.mainloop()

    def quit(self):
        try:
            self.k_listener.stop()
            self.m_listener.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def hover_enabled(self):
        return self.ctrl_down and self.alt_down

    def _close_overlays(self):
        for attr in ("translation_overlay", "grammar_overlay"):
            ov = getattr(self, attr, None)
            if ov is not None:
                try:
                    if ov.winfo_exists():
                        ov.destroy()
                except Exception:
                    pass
            setattr(self, attr, None)

    # ---- keyboard ----
    def on_key_press(self, key):
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_down = True
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_down = True
        elif key == keyboard.Key.f8:
            self.quit()
            return
        elif key == keyboard.Key.esc:
            self.root.after(0, self._close_overlays)
            return
        else:
            return
        self._arm()

    def on_key_release(self, key):
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_down = False
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_down = False
        else:
            return
        # La combinaison vient d'etre rompue : c'est la validation.
        if self.armed and not self.hover_enabled():
            self._finish_selection()

    def _arm(self):
        """Ancre le premier coin quand Ctrl+Alt devient complet.

        on_key_press se repete tant qu'une touche est maintenue : le garde
        self.armed fait qu'on n'ancre qu'a la transition, sinon le coin
        suivrait la souris au lieu de rester fixe.
        """
        if self.armed or not self.hover_enabled():
            return
        self.armed = True
        self.band_shown = False
        pos = self.mouse_ctl.position
        self.anchor = (int(pos[0]), int(pos[1]))

    def _finish_selection(self):
        self.armed = False
        if not self.band_shown:
            return
        self.band_shown = False

        left, top, right, bottom = self.rubber.rect()
        self.rubber.stop()

        if (right - left) < 18 or (bottom - top) < 18:
            return

        threading.Thread(
            target=self._ocr_translate_analyze,
            args=(left, top, right, bottom),
            daemon=True,
        ).start()

    # ---- mouse ----
    def on_move(self, x, y):
        if not self.armed:
            return
        x, y = int(x), int(y)

        if self.band_shown:
            self.rubber.move(x, y)
            return

        # Rien ne s'affiche tant que la souris n'a pas bouge nettement : sur
        # clavier AZERTY, AltGr est envoye comme Ctrl+Alt, et on ne veut pas
        # faire clignoter un rectangle a chaque @, # ou € tape.
        if abs(x - self.anchor[0]) + abs(y - self.anchor[1]) > 6:
            self.band_shown = True
            self.rubber.start(self.anchor[0], self.anchor[1])
            self.rubber.move(x, y)

    # ---- capture + ocr + analyse ----
    def _safe_grab(self, left, top, right, bottom) -> Image.Image:
        w = max(1, int(right - left))
        h = max(1, int(bottom - top))
        return self.grabber.grab_rect(int(left), int(top), w, h)

    def _ocr_translate_analyze(self, left, top, right, bottom):
        try:
            img = self._safe_grab(left, top, right, bottom)
            img = preprocess_for_ocr(img)

            # 1. OCR
            raw_text = ocr_text_block(img)
            if not raw_text:
                raise RuntimeError("OCR vide (zone trop petite ou peu contrastée).")

            # 2. Traduction
            text_fr = translate_mymemory(raw_text)
            if not text_fr:
                raise RuntimeError("Traduction vide (API MyMemory).")

            # 3. Analyse grammaticale (locale, KoNLPy)
            tokens = analyze_korean(raw_text)

            # 4. Affichage dans le thread principal
            def _show():
                self._close_overlays()
                self.translation_overlay = TranslationOverlay(
                    self.root,
                    text_fr,
                    on_close=self._close_overlays
                )
                if tokens:
                    self.grammar_overlay = GrammarOverlay(
                        self.root,
                        tokens,
                        source_text=raw_text,
                        on_close=self._close_overlays
                    )

            self.root.after(0, _show)

        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            def _err(msg=err_msg):
                messagebox.showerror("Erreur OCR / Traduction", msg)
            self.root.after(0, _err)


# ======================
# MAIN
# ======================
if __name__ == "__main__":
    if not KONLPY_AVAILABLE:
        print("⚠️  KoNLPy non installé. L'analyse grammaticale sera désactivée.")
        print("   Installez-le avec : pip install konlpy")
        print("   Et Java : https://www.java.com/fr/download/\n")

    try:
        App().run()
    except Exception as e:
        messagebox.showerror("Erreur", str(e))
