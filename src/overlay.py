"""OCR Screen Translator — traduire du texte non sélectionnable à l'écran.

Au lancement, une fenêtre demande quelle langue lire et vers laquelle traduire.
Tout le reste en découle : le modèle passé à Tesseract, le couple de codes
envoyé à MyMemory, et la présence ou non du panneau grammatical.

Sélection, sans aucun clic :
  - maintenir Ctrl + Alt ancre un coin à la position de la souris
  - déplacer la souris trace le rectangle
  - relâcher la combinaison déclenche la capture

Les clics ne sont jamais interceptés : ils continuent d'aller à l'application
du dessous, on peut donc tourner les pages en lisant.

Panneaux :
  1. TRANSLATION : le texte traduit
  2. GRAMMAR     : pour une langue qui a un analyseur — le coréen aujourd'hui —
                   la phrase entière remise dans l'ordre et colorée selon la
                   nature de chaque mot, puis le détail mot par mot

Touches : ESC ferme les panneaux, F8 quitte.

Dépendances : pip install -r requirements.txt
              (ou requirements-korean.txt pour l'analyse grammaticale)
Installation complète, Tesseract compris : scripts/install_windows.bat
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
from tkinter import messagebox, ttk

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
# Chemin resolu automatiquement (PATH, emplacements habituels, registre) :
# voir tesseract_setup.py. Aucune langue n'est exigee ici — c'est le choix fait
# au demarrage qui la determine, et le selecteur ne propose que les modeles
# reellement installes.
import tesseract_setup
from tesseract_setup import configure_tesseract

TESSERACT_PATH = configure_tesseract(())


# ======================
# LANGUES
# ======================
# Code Tesseract -> (nom affiché, code MyMemory). Les deux nomenclatures ne se
# recouvrent pas : Tesseract suit l'ISO 639-2 sur trois lettres, MyMemory
# l'ISO 639-1 sur deux.
LANGUAGES = {
    "eng":     ("English", "en"),
    "fra":     ("Français — French", "fr"),
    "kor":     ("한국어 — Korean", "ko"),
    "jpn":     ("日本語 — Japanese", "ja"),
    "chi_sim": ("中文 — Chinese, simplified", "zh-CN"),
    "chi_tra": ("中文 — Chinese, traditional", "zh-TW"),
    "spa":     ("Español — Spanish", "es"),
    "deu":     ("Deutsch — German", "de"),
    "ita":     ("Italiano — Italian", "it"),
    "por":     ("Português — Portuguese", "pt"),
    "rus":     ("Русский — Russian", "ru"),
    "nld":     ("Nederlands — Dutch", "nl"),
    "ara":     ("العربية — Arabic", "ar"),
    "hin":     ("हिन्दी — Hindi", "hi"),
    "tur":     ("Türkçe — Turkish", "tr"),
    "vie":     ("Tiếng Việt — Vietnamese", "vi"),
    "tha":     ("ไทย — Thai", "th"),
    "pol":     ("Polski — Polish", "pl"),
    "swe":     ("Svenska — Swedish", "sv"),
    "ell":     ("Ελληνικά — Greek", "el"),
}

# Langues dont on sait analyser la grammaire. Une seule pour l'instant, mais le
# test porte sur cette table plutôt que sur "kor" écrit en dur.
GRAMMAR_LANGUAGES = {"kor"}

_ocr_langs_cache = []


def available_ocr_languages():
    """Modèles réellement installés dans Tesseract.

    C'est ce qui rend le sélecteur honnête : il ne propose que ce que la
    machine sait lire, plutôt que d'offrir une langue qui échouerait à l'OCR.
    """
    if not _ocr_langs_cache:
        try:
            found = set(pytesseract.get_languages(config=""))
        except Exception:
            found = {"eng"}
        found.discard("osd")
        ordered = [code for code in LANGUAGES if code in found]
        ordered += sorted(code for code in found if code not in LANGUAGES)
        _ocr_langs_cache.extend(ordered or ["eng"])
    return _ocr_langs_cache


class Session:
    """Couple de langues choisi au démarrage."""

    def __init__(self, source="eng", target="en", grammar=False, email=""):
        self.source = source
        self.target = target
        # Adresse facultative envoyée à MyMemory, qui double alors le quota
        # journalier. Vide tant que l'utilisateur n'en saisit pas une.
        self.email = email
        # Décochée par défaut : l'analyse traduit chaque mot séparément, donc
        # autant d'appels à l'API que de mots. C'est lent, ça épuise le quota
        # journalier, et sur un OCR approximatif ça ne renvoie que du bruit.
        self.grammar = grammar

    @property
    def ocr_lang(self):
        """Langue passée à Tesseract.

        L'anglais est ajouté en second quand la source ne l'est pas : un texte
        réel mêle presque toujours quelques mots latins, noms propres ou
        nombres, et Tesseract s'en tire mieux avec les deux modèles.
        """
        if self.source != "eng" and "eng" in available_ocr_languages():
            return self.source + "+eng"
        return self.source

    @property
    def langpair(self):
        return "%s|%s" % (LANGUAGES.get(self.source, ("", "en"))[1], self.target)

    @property
    def has_grammar(self):
        return (self.grammar
                and self.source in GRAMMAR_LANGUAGES
                and KONLPY_AVAILABLE)

    def label(self):
        source = LANGUAGES.get(self.source, (self.source, ""))[0]
        target = next((name for name, code in LANGUAGES.values()
                       if code == self.target), self.target)
        return "%s → %s" % (source, target)


SESSION = Session()


# ======================
# INSTANCE UNIQUE
# ======================
# Chaque instance écoute Ctrl+Alt globalement. Deux instances lancées, et une
# seule sélection déclenche deux captures, deux séries d'appels à l'API et deux
# jeux de panneaux — avec les réglages de leur propre démarrage, donc une
# ancienne instance peut rouvrir un panneau qu'on vient de désactiver. Comme
# l'outil se lance par pythonw.exe, sans fenêtre de console, rien ne signale
# qu'il tourne déjà : elles s'accumulent sans qu'on s'en rende compte.
_instance_handle = None


def acquire_single_instance(name="OcrTranslateOverlay.Mutex"):
    """Renvoie False si l'outil tourne déjà."""
    global _instance_handle
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, name)
        ERROR_ALREADY_EXISTS = 183
        if not handle or kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        # Gardé en référence : refermé, le mutex libérerait le verrou.
        _instance_handle = handle
        return True
    except Exception:
        return True   # hors Windows : on ne bloque personne


# ======================
# COULEURS GRAMMATICALES
# ======================
# Tag Okt → couleur + libellé affiché.
#
# Encres colorées sur papier crème, façon annotation à la main : des teintes
# sombres et rabattues, jamais des couleurs d'écran. Elles doivent rester
# lisibles sur #efe6d8, ce qui exclut tout ce qui est clair ou fluo.
#
# Libellés en anglais comme le reste de l'interface. Pour repasser en français,
# c'est ici et nulle part ailleurs : la légende du panneau est construite à
# partir de cette table.
POS_STYLES = {
    "Verb":           {"color": "#9c4221", "label": "Verb"},
    "Adjective":      {"color": "#3f6b45", "label": "Adjective"},
    "Noun":           {"color": "#2f5d8a", "label": "Noun"},
    "ProperNoun":     {"color": "#274c72", "label": "Proper noun"},
    "Pronoun":        {"color": "#8a6516", "label": "Pronoun"},
    "Adverb":         {"color": "#6b4a7a", "label": "Adverb"},
    "Josa":           {"color": "#2b6b66", "label": "Particle"},
    "Eomi":           {"color": "#8a5a2b", "label": "Ending"},
    "Conjunction":    {"color": "#7a4560", "label": "Conjunction"},
    "Determiner":     {"color": "#4f6b3f", "label": "Determiner"},
    "Number":         {"color": "#7d6420", "label": "Number"},
    "Foreign":        {"color": "#5f5850", "label": "Foreign"},
    "Alpha":          {"color": "#5f5850", "label": "Latin"},
    "Punctuation":    {"color": "#a89d8c", "label": "Punctuation"},
    "Unknown":        {"color": "#7a7167", "label": "Unknown"},
}
DEFAULT_STYLE = {"color": "#5f5850", "label": "Other"}


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


# MyMemory refuse les requetes au-dela de 500 caracteres. Le piege est qu'elle
# ne renvoie pas une erreur HTTP : elle repond 200 en placant son message
# d'erreur DANS le champ de traduction. Sans controle, ce message s'affiche a la
# place du texte traduit — un paragraphe un peu long ressortait donc en
# charabia anglais majuscule. Meme mecanisme quand le quota du jour est epuise.
MYMEMORY_MAX_CHARS = 450

# Fins de phrase, latines et est-asiatiques.
_SENTENCE_END = "。．.！!？?…\n"


def _split_for_translation(text: str, limit: int = MYMEMORY_MAX_CHARS) -> list:
    """Decoupe en morceaux traduisibles, sur une fin de phrase quand possible."""
    chunks = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(ch) for ch in _SENTENCE_END)
        if cut < limit // 3:
            cut = window.rfind(" ")      # pas de ponctuation : on coupe aux mots
        if cut < limit // 3:
            cut = limit - 1              # ni l'un ni l'autre : coupe franche
        chunks.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].lstrip()
    if rest.strip():
        chunks.append(rest.strip())
    return chunks


def _translate_chunk(chunk: str) -> str:
    cached = TRANSLATE_CACHE.get(chunk)
    if cached is not None:
        return cached

    params = {"q": chunk, "langpair": SESSION.langpair}
    # MyMemory double le quota journalier pour une requete signee d'une adresse
    # e-mail. Saisie dans le selecteur de langues, ou a defaut variable
    # d'environnement. Rien n'est envoye tant que l'une des deux n'est remplie.
    email = SESSION.email or os.environ.get("MYMEMORY_EMAIL")
    if email:
        params["de"] = email

    r = requests.get("https://api.mymemory.translated.net/get",
                     params=params, timeout=20)
    r.raise_for_status()
    payload = r.json()
    out = cleanup((payload.get("responseData") or {}).get("translatedText") or "")

    status = payload.get("responseStatus")
    if status not in (200, "200"):
        detail = payload.get("responseDetails") or out or "réponse illisible"
        raise RuntimeError("MyMemory a refusé la requête : %s" % detail)

    # Le quota epuise arrive avec un statut 200 et l'avertissement en guise de
    # traduction : il faut le reconnaitre au contenu.
    upper = out.upper()
    if upper.startswith("MYMEMORY WARNING") or "ALL AVAILABLE FREE TRANSLATIONS" in upper:
        raise RuntimeError(
            "Quota MyMemory épuisé pour aujourd'hui. Il se réinitialise sous 24 h ; "
            "définir la variable d'environnement MYMEMORY_EMAIL le double.")

    TRANSLATE_CACHE.set(chunk, out)
    return out


def translate_mymemory(text: str) -> str:
    text = cleanup(text)
    if not text:
        return ""
    return " ".join(_translate_chunk(chunk)
                    for chunk in _split_for_translation(text) if chunk)


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
    text = pytesseract.image_to_string(img, lang=SESSION.ocr_lang, config=config)
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

            # 3. Analyse grammaticale, seulement pour une langue qui en a une
            # et si l'analyseur est disponible. Sinon on s'en tient à la
            # traduction, et le second panneau ne s'ouvre pas.
            tokens = analyze_korean(raw_text) if SESSION.has_grammar else []

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
def ask_languages():
    """Startup window: which language to read, which one to translate into.

    Returns (source, target), or None if the window is closed without a choice.
    It owns its own Tk root, destroyed before the overlay builds its own: two
    live roots in a single process interfere with each other.

    Languages that are not installed are still listed, and picking one offers
    to fetch its model. Hiding them would leave the user guessing why their
    language is absent.
    """
    installed = set(available_ocr_languages())

    # Installed first, then the rest — the common case stays at the top of the
    # list, while everything remains reachable.
    codes = [c for c in LANGUAGES if c in installed]
    codes += [c for c in LANGUAGES if c not in installed]
    codes += sorted(c for c in installed if c not in LANGUAGES)

    remembered = (panels.load_setting("languages") or "").split(">")
    source = remembered[0] if len(remembered) == 2 and remembered[0] in codes else codes[0]
    target = remembered[1] if len(remembered) == 2 else "en"

    # Targets do not depend on Tesseract: translating needs no local model,
    # only reading does.
    targets, seen = [], set()
    for name, code in LANGUAGES.values():
        if code not in seen:
            seen.add(code)
            targets.append((name, code))
    targets.sort(key=lambda item: item[0].lower())

    def entry(code):
        name = LANGUAGES.get(code, (code, ""))[0]
        return name if code in installed else name + "   — not installed"

    root = tk.Tk()
    root.title("OCR Screen Translator")
    root.configure(bg=panels.PAPER_HEX)
    root.resizable(False, False)

    frame = tk.Frame(root, bg=panels.PAPER_HEX, padx=26, pady=22)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="Which languages?", bg=panels.PAPER_HEX,
             fg=panels.TEXT, font=(panels.UI_FACE, 15)).grid(
        row=0, column=0, columnspan=2, sticky="w")
    hint = tk.Label(frame, text="", bg=panels.PAPER_HEX, fg=panels.MUTED,
                    font=(panels.UI_FACE, 9), justify="left", anchor="w")
    hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 16))

    tk.Label(frame, text="Read", bg=panels.PAPER_HEX, fg=panels.MUTED,
             font=(panels.UI_FACE, 10)).grid(row=2, column=0, sticky="w")
    src_box = ttk.Combobox(frame, state="readonly", width=34,
                           values=[entry(c) for c in codes])
    src_box.grid(row=2, column=1, sticky="ew", padx=(14, 0), pady=4)
    src_box.current(codes.index(source))

    tk.Label(frame, text="Translate into", bg=panels.PAPER_HEX, fg=panels.MUTED,
             font=(panels.UI_FACE, 10)).grid(row=3, column=0, sticky="w")
    dst_box = ttk.Combobox(frame, state="readonly", width=34,
                           values=[name for name, _ in targets])
    dst_box.grid(row=3, column=1, sticky="ew", padx=(14, 0), pady=4)
    dst_box.current(next((i for i, (_, c) in enumerate(targets) if c == target), 0))

    # Option, décochée par défaut : l'analyse traduit chaque mot séparément,
    # donc autant d'appels à l'API que de mots dans la phrase. C'est ce qui
    # rendait l'outil lent, et sur un OCR approximatif ça ne produit que du
    # bruit. Elle ne s'active que pour une langue qui a un analyseur.
    grammar_var = tk.BooleanVar(value=(panels.load_setting("grammar") == "1"))
    grammar_box = tk.Checkbutton(
        frame, text="Grammar analysis  (slower: one lookup per word)",
        variable=grammar_var, bg=panels.PAPER_HEX, fg=panels.TEXT,
        activebackground=panels.PAPER_HEX, selectcolor=panels.PAPER_HEX,
        font=(panels.UI_FACE, 9), anchor="w", highlightthickness=0, bd=0)
    grammar_box.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

    # MyMemory double le quota journalier pour une requête signée d'une adresse
    # e-mail. Le champ est facultatif et vide par défaut : l'adresse part vers
    # MyMemory à chaque traduction, c'est donc à l'utilisateur de la donner, pas
    # au programme de la deviner.
    tk.Label(frame, text="Email", bg=panels.PAPER_HEX, fg=panels.MUTED,
             font=(panels.UI_FACE, 10)).grid(row=5, column=0, sticky="w", pady=(14, 0))
    email_entry = tk.Entry(frame, width=36, relief="flat", bd=6,
                           bg="#e9dfce", fg=panels.TEXT,
                           insertbackground=panels.TEXT,
                           font=(panels.UI_FACE, 10))
    email_entry.grid(row=5, column=1, sticky="ew", padx=(14, 0), pady=(14, 0))
    email_entry.insert(0, panels.load_setting("email") or "")
    tk.Label(frame, text="optional — doubles the free daily translation quota",
             bg=panels.PAPER_HEX, fg=panels.FAINT,
             font=(panels.UI_FACE, 8)).grid(row=6, column=1, sticky="w", padx=(14, 0))

    # Barre d'installation, montrée seulement pendant un téléchargement. Sur une
    # bonne connexion un modèle passe en quelques secondes : sans elle, l'écran
    # ne montrait qu'un texte qui clignote, et l'application semblait démarrer
    # sans rien avoir installé.
    bar = ttk.Progressbar(frame, mode="determinate", maximum=1000, length=260)
    bar.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(14, 0))
    bar.grid_remove()

    start_btn = tk.Button(frame, text="Start", relief="flat",
                          bg="#e0d3bc", fg=panels.TEXT, activebackground="#d5c6ae",
                          font=(panels.UI_FACE, 10), padx=18, pady=6)
    start_btn.grid(row=8, column=0, columnspan=2, sticky="e", pady=(18, 0))

    def refresh_hint(_event=None):
        code = codes[src_box.current()]

        # La case ne vaut que pour une langue qu'on sait analyser : on la grise
        # ailleurs plutôt que de la masquer, pour que son existence se voie.
        can_analyse = code in GRAMMAR_LANGUAGES and KONLPY_AVAILABLE
        grammar_box.configure(state="normal" if can_analyse else "disabled",
                              fg=panels.TEXT if can_analyse else panels.FAINT)
        if not can_analyse:
            grammar_var.set(False)

        if code not in installed:
            hint.configure(text="Model not installed — it will be downloaded (a few MB).")
        elif can_analyse:
            hint.configure(text="Grammar analysis available for this language.")
        elif code in GRAMMAR_LANGUAGES:
            hint.configure(text="Grammar analysis unavailable: konlpy or Java missing.")
        else:
            hint.configure(text="Translation only for this language.")

    src_box.bind("<<ComboboxSelected>>", refresh_hint)
    refresh_hint()

    result = {}

    def finish(code):
        result["source"] = code
        result["target"] = targets[dst_box.current()][1]
        result["grammar"] = bool(grammar_var.get())
        result["email"] = email_entry.get().strip()
        root.destroy()

    def install_then_finish(code):
        """Fetch the missing model, then start.

        English is fetched alongside it: once a local tessdata directory is in
        play it becomes the only one Tesseract reads, so it has to hold every
        language the tool uses — and English is always the OCR fallback.
        """
        # Le test porte sur le tessdata du dépôt et non sur le système : une
        # fois TESSDATA_PREFIX pointé dessus, c'est le seul dossier que
        # Tesseract lit, donc l'anglais du système n'y servirait plus.
        needed = [code]
        if not os.path.isfile(
                os.path.join(tesseract_setup.LOCAL_TESSDATA, "eng.traineddata")):
            needed.append("eng")

        def report(text, fraction=None):
            def apply():
                hint.configure(text=text)
                if fraction is not None:
                    bar["value"] = max(0, min(1000, int(fraction * 1000)))
            root.after(0, apply)

        # Le rappel de progression arrive tous les 64 Ko : sur 14 Mo cela ferait
        # deux cents replanifications Tk. On ne rafraîchit que par pas de 1 %.
        state = {"step": -1}

        def work():
            try:
                for index, lang in enumerate(needed):
                    name = LANGUAGES.get(lang, (lang, ""))[0]
                    prefix = ""
                    if len(needed) > 1:
                        prefix = "(%d/%d) " % (index + 1, len(needed))

                    def progress(done, total, name=name, prefix=prefix, index=index):
                        share = 1.0 / len(needed)
                        if total:
                            step = int(done * 100 / total)
                            if step == state["step"]:
                                return
                            state["step"] = step
                            report("%sDownloading %s — %.1f of %.1f MB"
                                   % (prefix, name, done / 1048576.0, total / 1048576.0),
                                   index * share + share * done / float(total))
                        else:
                            report("%sDownloading %s — %.1f MB"
                                   % (prefix, name, done / 1048576.0))

                    report("%sStarting download of %s…" % (prefix, name),
                           index / float(len(needed)))
                    state["step"] = -1
                    tesseract_setup.download_language(lang, progress)
            except Exception as exc:
                report("Download failed: %s" % exc, 0)
                root.after(0, restore)
                return

            os.environ["TESSDATA_PREFIX"] = tesseract_setup.LOCAL_TESSDATA
            del _ocr_langs_cache[:]
            report("Installed. Starting…", 1.0)
            # Une pause courte, sinon la barre atteint 100 % et disparaît dans
            # la même image : on ne voit jamais qu'elle a abouti.
            root.after(450, lambda: finish(code))

        def restore():
            start_btn.configure(state="normal")
            src_box.configure(state="readonly")
            dst_box.configure(state="readonly")
            bar.grid_remove()

        start_btn.configure(state="disabled")
        src_box.configure(state="disabled")
        dst_box.configure(state="disabled")
        bar["value"] = 0
        bar.grid()
        threading.Thread(target=work, daemon=True).start()

    def start(_event=None):
        code = codes[src_box.current()]
        if code in installed:
            finish(code)
        else:
            install_then_finish(code)

    start_btn.configure(command=start)
    root.bind("<Return>", start)
    root.bind("<Escape>", lambda e: root.destroy())

    root.update_idletasks()
    root.geometry("+%d+%d" % (
        (root.winfo_screenwidth() - root.winfo_width()) // 2,
        (root.winfo_screenheight() - root.winfo_height()) // 3))
    src_box.focus_set()
    root.mainloop()

    if not result:
        return None
    panels.save_setting("languages", "%s>%s" % (result["source"], result["target"]))
    panels.save_setting("grammar", "1" if result["grammar"] else "0")
    panels.save_setting("email", result["email"])
    return result["source"], result["target"], result["grammar"], result["email"]


if __name__ == "__main__":
    if not acquire_single_instance():
        warning = tk.Tk()
        warning.withdraw()
        messagebox.showinfo(
            "OCR Screen Translator",
            "The tool is already running.\n\n"
            "Press F8 in the running instance to quit it, then start again.")
        warning.destroy()
        raise SystemExit(0)

    choice = ask_languages()
    if choice is None:
        raise SystemExit(0)

    SESSION.source, SESSION.target, SESSION.grammar, SESSION.email = choice
    print("Session : %s   (OCR : %s)" % (SESSION.label(), SESSION.ocr_lang))

    try:
        App().run()
    except Exception as e:
        messagebox.showerror("Erreur", str(e))
