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
    pip install -r requirements-korean.txt
+ Installer Tesseract (Windows) et vérifier TESSERACT_PATH.
+ Installer Java (requis par KoNLPy) : https://www.java.com/fr/download/
"""

import os
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

import panels
from panels import TranslationPanel, GrammarPanel

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
# Apparence
# ======================
# Palette, polices et memorisation des positions : voir panels.py.

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
# Panneaux flottants
# ======================
# Le dessin des fenetres vit dans panels.py : fond beige genere, coins
# arrondis, contenu pose sur un Canvas pour flotter sur le degrade.

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
            legend = [(POS_STYLES[tag]["label"], POS_STYLES[tag]["color"])
                      for tag in ("Verb", "Noun", "Adjective", "Adverb",
                                  "Pronoun", "Josa")]

            def _show():
                self._close_overlays()
                # Sert de fond aux coins arrondis quand le système ne les
                # découpe pas lui-même (Windows 10). Pris une fois les anciens
                # panneaux fermés, pour ne pas les photographier.
                try:
                    panels.set_backdrop(self.grabber.grab_rect(
                        0, 0,
                        self.root.winfo_screenwidth(),
                        self.root.winfo_screenheight()))
                except Exception:
                    pass

                self.translation_overlay = TranslationPanel(
                    self.root, text_fr, on_close=self._close_overlays)
                if tokens:
                    self.grammar_overlay = GrammarPanel(
                        self.root, tokens,
                        source_text=raw_text,
                        legend=legend,
                        on_close=self._close_overlays)

            self.root.after(0, _show)

            # 5. Traduction mot à mot, ensuite : les panneaux s'affichent tout
            # de suite et se complètent quand MyMemory a répondu. L'ancienne
            # version attendait ces requêtes dans le thread Tk, ce qui figeait
            # l'interface jusqu'à huit secondes.
            if tokens:
                self._fetch_word_translations(tokens)

        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            def _err(msg=err_msg):
                messagebox.showerror("Erreur OCR / Traduction", msg)
            self.root.after(0, _err)

    def _fetch_word_translations(self, tokens):
        """Traduit chaque mot, puis complète le panneau grammatical.

        Les particules et terminaisons sont écartées : MyMemory n'en fait rien
        de sensé, et ce sont les plus nombreuses.
        """
        skip = {"Josa", "Eomi", "Punctuation", "Unknown"}
        words = []
        for tok in tokens:
            word = (tok.get("word") or "").strip()
            if word and tok.get("pos") not in skip and word not in words:
                words.append(word)
        if not words:
            return

        found = {}
        lock = threading.Lock()

        def fetch(word):
            try:
                trad = translate_mymemory(word)
            except Exception:
                return
            if trad and trad.lower() != word.lower():
                with lock:
                    found[word] = trad

        threads = [threading.Thread(target=fetch, args=(w,), daemon=True) for w in words]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=8)

        def _apply():
            panel = self.grammar_overlay
            if panel is not None:
                try:
                    if panel.winfo_exists():
                        panel.set_translations(found)
                except Exception:
                    pass

        self.root.after(0, _apply)


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
