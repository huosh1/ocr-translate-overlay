"""
Option B (Windows) — OCR + Traduction FR + Analyse grammaticale coréenne

✅ Fonctionnement :
- Maintiens Ctrl + Alt
- Clic gauche maintenu + drag : dessine un rectangle
- Relâche : OCR de la zone + traduction FR + overlay traduction + overlay grammatical

Overlays :
  1. Overlay TRADUCTION  : texte traduit en français (coin haut droit)
  2. Overlay GRAMMAIRE   : chaque mot coréen avec sa nature grammaticale en couleur

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
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if not os.path.exists(TESSERACT_PATH):
    raise RuntimeError("Tesseract introuvable : " + TESSERACT_PATH)
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ======================
# COULEURS GRAMMATICALES
# ======================
# Tags Okt → couleur + label français
POS_STYLES = {
    "Verb":           {"color": "#E07B54", "label": "Verbe"},
    "Adjective":      {"color": "#6DBF82", "label": "Adjectif"},
    "Noun":           {"color": "#5B9BD5", "label": "Nom"},
    "ProperNoun":     {"color": "#5B9BD5", "label": "Nom propre"},
    "Pronoun":        {"color": "#F0C040", "label": "Pronom"},
    "Adverb":         {"color": "#C98FD4", "label": "Adverbe"},
    "Josa":           {"color": "#8FBCBB", "label": "Particule"},
    "Eomi":           {"color": "#E0A060", "label": "Terminaison"},
    "Conjunction":    {"color": "#D4A0C8", "label": "Conjonction"},
    "Determiner":     {"color": "#A8D8A8", "label": "Déterminant"},
    "Number":         {"color": "#FFD580", "label": "Nombre"},
    "Foreign":        {"color": "#BBBBBB", "label": "Étranger"},
    "Alpha":          {"color": "#BBBBBB", "label": "Alphabet"},
    "Punctuation":    {"color": "#666666", "label": "Ponctuation"},
    "Unknown":        {"color": "#888888", "label": "Inconnu"},
}
DEFAULT_STYLE = {"color": "#AAAAAA", "label": "Autre"}


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
    [{"word": "나는", "pos": "Pronoun", "color": "#F0C040", "label": "Pronom"}, ...]
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
# Overlay 1 : Traduction
# ======================
class TranslationOverlay(ctk.CTkToplevel):
    def __init__(self, master, text: str, on_close=None):
        super().__init__(master)
        self._on_close = on_close

        self.overrideredirect(True)
        self.update_idletasks()
        hwnd = self.winfo_id()
        enable_windows_blur(hwnd, acrylic=True)

        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.98)

        self._trans = "#ff00ff"
        self.configure(fg_color=self._trans)
        try:
            self.wm_attributes("-transparentcolor", self._trans)
        except Exception:
            pass

        ctk.set_appearance_mode("light")
        paper = "#efe6d8"
        textc = "#111111"

        w, h = 520, 200
        x = self.winfo_screenwidth() - w - 30
        y = 40
        self.geometry(f"{w}x{h}+{x}+{y}")

        card = ctk.CTkFrame(self, corner_radius=24, fg_color=paper, border_width=0)
        card.pack(fill="both", expand=True, padx=8, pady=8)

        # Header
        header = ctk.CTkFrame(card, corner_radius=0, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))

        label_tag = ctk.CTkLabel(
            header,
            text="🇫🇷  TRADUCTION",
            font=ctk.CTkFont("Segoe UI", 11, weight="bold"),
            text_color="#888888"
        )
        label_tag.pack(side="left")

        close_btn = ctk.CTkButton(
            header,
            text="✕",
            width=28, height=28,
            corner_radius=14,
            fg_color="#dbc9ad",
            hover_color="#cbb89c",
            text_color=textc,
            command=self._close
        )
        close_btn.pack(side="right")

        # Texte traduit
        holder = ctk.CTkFrame(card, corner_radius=16, fg_color=paper)
        holder.pack(fill="both", expand=True, padx=14, pady=(8, 14))

        textbox = ctk.CTkTextbox(
            holder,
            wrap="word",
            fg_color=paper,
            text_color=textc,
            border_width=0,
            font=ctk.CTkFont("Segoe UI Semibold", 17)
        )
        textbox.pack(fill="both", expand=True, padx=6, pady=6)
        textbox.insert("1.0", text.strip() if text else "OCR vide")
        textbox.configure(state="disabled")

        # Poignée de redimensionnement
        self._resize_start = None
        resize_grip = ctk.CTkLabel(card, text="⠿", text_color="#aaaaaa", cursor="size_nw_se")
        resize_grip.place(relx=1.0, rely=1.0, x=-10, y=-10, anchor="se")
        resize_grip.bind("<ButtonPress-1>", self._on_resize_start)
        resize_grip.bind("<B1-Motion>", self._on_resize_drag)

        self.bind("<Escape>", lambda e: self._close())

    def _on_resize_start(self, event):
        self._resize_start = (event.x_root, event.y_root, self.winfo_width(), self.winfo_height())

    def _on_resize_drag(self, event):
        if not self._resize_start:
            return
        x0, y0, w0, h0 = self._resize_start
        new_w = max(300, w0 + event.x_root - x0)
        new_h = max(100, h0 + event.y_root - y0)
        self.geometry(f"{new_w}x{new_h}")

    def _close(self):
        if self._on_close:
            self._on_close()
        self.destroy()


# ======================
# Overlay 2 : Analyse grammaticale
# ======================
class GrammarOverlay(ctk.CTkToplevel):
    def __init__(self, master, tokens: list, on_close=None):
        """
        tokens : liste de dicts {"word", "pos", "color", "label"}
        """
        super().__init__(master)
        self._on_close = on_close

        self.overrideredirect(True)
        self.update_idletasks()
        hwnd = self.winfo_id()
        enable_windows_blur(hwnd, acrylic=True)

        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.98)

        self._trans = "#ff00ff"
        self.configure(fg_color=self._trans)
        try:
            self.wm_attributes("-transparentcolor", self._trans)
        except Exception:
            pass

        ctk.set_appearance_mode("light")
        paper = "#1e1e2e"   # fond sombre pour le 2ème overlay (contraste couleurs)
        textc = "#cdd6f4"

        w = 560
        x = self.winfo_screenwidth() - w - 30
        y = 280   # sous le 1er overlay

        # Hauteur dynamique selon le nombre de tokens
        rows = max(4, len(tokens))
        h = min(60 + rows * 36, 520)
        self.geometry(f"{w}x{h}+{x}+{y}")

        card = ctk.CTkFrame(self, corner_radius=24, fg_color=paper, border_width=0)
        card.pack(fill="both", expand=True, padx=8, pady=8)

        # Header
        header = ctk.CTkFrame(card, corner_radius=0, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 4))

        label_tag = ctk.CTkLabel(
            header,
            text="🔬  ANALYSE GRAMMATICALE",
            font=ctk.CTkFont("Segoe UI", 11, weight="bold"),
            text_color="#6c7086"
        )
        label_tag.pack(side="left")

        close_btn = ctk.CTkButton(
            header,
            text="✕",
            width=28, height=28,
            corner_radius=14,
            fg_color="#313244",
            hover_color="#45475a",
            text_color=textc,
            command=self._close
        )
        close_btn.pack(side="right")

        # Légende compacte
        legend_frame = ctk.CTkFrame(card, corner_radius=10, fg_color="#181825")
        legend_frame.pack(fill="x", padx=14, pady=(0, 6))

        legend_items = [
            ("Verbe", "#E07B54"),
            ("Nom", "#5B9BD5"),
            ("Adjectif", "#6DBF82"),
            ("Adverbe", "#C98FD4"),
            ("Pronom", "#F0C040"),
            ("Particule", "#8FBCBB"),
        ]
        for lbl, col in legend_items:
            dot = ctk.CTkLabel(
                legend_frame,
                text="●",
                font=ctk.CTkFont("Segoe UI", 11),
                text_color=col,
                width=14
            )
            dot.pack(side="left", padx=(6, 0), pady=4)
            txt = ctk.CTkLabel(
                legend_frame,
                text=lbl,
                font=ctk.CTkFont("Segoe UI", 10),
                text_color="#9399b2",
                width=52
            )
            txt.pack(side="left", padx=(2, 4), pady=4)

        # Zone scrollable des tokens
        scroll = ctk.CTkScrollableFrame(
            card,
            corner_radius=16,
            fg_color="#181825",
            scrollbar_button_color="#313244",
            scrollbar_button_hover_color="#45475a"
        )
        scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        if not tokens:
            ctk.CTkLabel(
                scroll,
                text="KoNLPy non disponible ou texte vide.",
                text_color="#6c7086",
                font=ctk.CTkFont("Segoe UI", 13)
            ).pack(pady=20)
        else:
            self._build_tokens(scroll, tokens)

        # Poignée de redimensionnement
        self._resize_start = None
        resize_grip = ctk.CTkLabel(card, text="⠿", text_color="#45475a", cursor="size_nw_se")
        resize_grip.place(relx=1.0, rely=1.0, x=-10, y=-10, anchor="se")
        resize_grip.bind("<ButtonPress-1>", self._on_resize_start)
        resize_grip.bind("<B1-Motion>", self._on_resize_drag)

        self.bind("<Escape>", lambda e: self._close())

    def _on_resize_start(self, event):
        self._resize_start = (event.x_root, event.y_root, self.winfo_width(), self.winfo_height())

    def _on_resize_drag(self, event):
        if not self._resize_start:
            return
        x0, y0, w0, h0 = self._resize_start
        new_w = max(300, w0 + event.x_root - x0)
        new_h = max(150, h0 + event.y_root - y0)
        self.geometry(f"{new_w}x{new_h}")

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
            row = ctk.CTkFrame(parent, corner_radius=10, fg_color="#1e1e2e" if i % 2 == 0 else "#181825")
            row.pack(fill="x", padx=4, pady=2)

            # Numéro
            ctk.CTkLabel(
                row,
                text=f"{i+1:02d}",
                font=ctk.CTkFont("Segoe UI", 11),
                text_color="#45475a",
                width=28
            ).pack(side="left", padx=(8, 4), pady=6)

            # Mot coréen (coloré selon nature)
            ctk.CTkLabel(
                row,
                text=tok["word"],
                font=ctk.CTkFont("Malgun Gothic", 15, weight="bold"),
                text_color=tok["color"],
                width=120,
                anchor="w"
            ).pack(side="left", padx=(4, 6), pady=6)

            # Badge nature
            badge = ctk.CTkFrame(row, corner_radius=8, fg_color="#313244")
            badge.pack(side="left", padx=(0, 10), pady=6)
            ctk.CTkLabel(
                badge,
                text=tok["label"],
                font=ctk.CTkFont("Segoe UI", 11, weight="bold"),
                text_color=tok["color"],
                padx=8, pady=2
            ).pack()

            # Traduction française du mot
            trad = word_translations.get(tok["word"], "")
            if trad:
                ctk.CTkLabel(
                    row,
                    text=f"→  {trad}",
                    font=ctk.CTkFont("Segoe UI", 12),
                    text_color="#9399b2",
                    anchor="w"
                ).pack(side="left", padx=(0, 8), pady=6)

    def _close(self):
        if self._on_close:
            self._on_close()
        self.destroy()


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
        self.dragging = False
        self.drag_start = None
        self.drag_moved = False

        self.translation_overlay = None
        self.grammar_overlay = None

        # Pré-charger Okt en arrière-plan (évite le délai au premier usage)
        if KONLPY_AVAILABLE:
            threading.Thread(target=get_okt, daemon=True).start()

        self.k_listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
        self.m_listener = mouse.Listener(on_move=self.on_move, on_click=self.on_click)

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
        elif key == keyboard.Key.esc:
            self.root.after(0, self._close_overlays)

    def on_key_release(self, key):
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_down = False
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_down = False

    # ---- mouse ----
    def on_move(self, x, y):
        if self.dragging and self.drag_moved:
            self.rubber.move(int(x), int(y))
        elif self.dragging:
            dx = abs(int(x) - self.drag_start[0])
            dy = abs(int(y) - self.drag_start[1])
            if dx + dy > 6:
                self.drag_moved = True
                self.rubber.start(self.drag_start[0], self.drag_start[1])
                self.rubber.move(int(x), int(y))

    def on_click(self, x, y, button, pressed):
        if button != mouse.Button.left:
            return
        if not self.hover_enabled():
            return

        x, y = int(x), int(y)

        if pressed:
            self.dragging = True
            self.drag_start = (x, y)
            self.drag_moved = False
        else:
            if not self.dragging:
                return
            self.dragging = False
            if not self.drag_moved:
                return

            left, top, right, bottom = self.rubber.rect()
            self.rubber.stop()

            if (right - left) < 18 or (bottom - top) < 18:
                return

            threading.Thread(
                target=self._ocr_translate_analyze,
                args=(left, top, right, bottom),
                daemon=True,
            ).start()

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
