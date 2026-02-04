"""
Option B (Windows) — Version simplifiée (SELECTION RECTANGLE ONLY)

✅ Fonctionnement :
- Maintiens Ctrl + Alt
- Clic gauche maintenu + drag : dessine un rectangle
- Relâche : OCR de la zone + traduction FR + overlay

❌ Retiré :
- hover (mot sous souris)
- clic gauche simple (sans drag)

Touches :
- ESC : ferme l’overlay
- F8  : quitte le programme

Dépendances:
    pip install mss pillow pytesseract requests pynput customtkinter
+ Installer Tesseract (Windows) et vérifier TESSERACT_PATH.
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

import customtkinter as ctk


# ======================
# TESSERACT CONFIG
# ======================
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if not os.path.exists(TESSERACT_PATH):
    raise RuntimeError("Tesseract introuvable : " + TESSERACT_PATH)
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ======================
# DPI AWARE (important for coords on Win10/11)
# ======================
def set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
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

    ACCENT_ENABLE_BLURBEHIND = 3
    ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

    accent = ACCENTPOLICY()
    accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND if acrylic else ACCENT_ENABLE_BLURBEHIND
    accent.AccentFlags = 2
    accent.GradientColor = 0xBBEFE6D8  # AABBGGRR

    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = 19  # WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(accent)

    set_attr = user32.SetWindowCompositionAttribute
    set_attr.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
    set_attr.restype = wintypes.BOOL
    set_attr(hwnd, ctypes.byref(data))


# ======================
# UTIL: translation (MyMemory) + cache
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
        params={"q": text, "langpair": "en|fr"},
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
    text = pytesseract.image_to_string(img, lang="eng", config=config)
    return cleanup(text)


# ======================
# Fast capture (mss) - thread-local
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
# UI: big overlay
# ======================
class RoundedOverlay(ctk.CTkToplevel):
    def __init__(self, master, text: str):
        super().__init__(master)

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

        w, h = 540, 240
        x = self.winfo_screenwidth() - w - 30
        y = 40
        self.geometry(f"{w}x{h}+{x}+{y}")

        card = ctk.CTkFrame(self, corner_radius=28, fg_color=paper, border_width=0)
        card.pack(fill="both", expand=True, padx=10, pady=10)

        close = ctk.CTkButton(
            card,
            text="✕",
            width=34,
            height=34,
            corner_radius=17,
            fg_color="#dbc9ad",
            hover_color="#cbb89c",
            text_color=textc,
            command=self.destroy
        )
        close.place(relx=1.0, x=-14, y=14, anchor="ne")

        holder = ctk.CTkFrame(card, corner_radius=22, fg_color=paper)
        holder.pack(fill="both", expand=True, padx=18, pady=(60, 18))

        textbox = ctk.CTkTextbox(
            holder,
            wrap="word",
            fg_color=paper,
            text_color=textc,
            border_width=0,
            font=ctk.CTkFont("Segoe UI Semibold", 18)
        )
        textbox.pack(fill="both", expand=True, padx=6, pady=6)

        textbox.insert("1.0", text.strip() if text else "OCR vide")
        textbox.configure(state="disabled")

        self.bind("<Escape>", lambda e: self.destroy())


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
        self.root.withdraw()  # invisible root
        self.root.attributes("-topmost", True)

        self.rubber = RubberBand(self.root)
        self.grabber = ScreenGrabber()

        self.ctrl_down = False
        self.alt_down = False

        self.dragging = False
        self.drag_start = None
        self.drag_moved = False

        self.big_overlay = None

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

    # ---- keyboard ----
    def on_key_press(self, key):
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_down = True
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_down = True
        elif key == keyboard.Key.f8:
            self.quit()

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
            # start showing rectangle after small move threshold
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
                # no drag => ignore (we removed simple click)
                return

            left, top, right, bottom = self.rubber.rect()
            self.rubber.stop()

            if (right - left) < 18 or (bottom - top) < 18:
                return

            threading.Thread(
                target=self._ocr_translate_show,
                args=(left, top, right, bottom),
                daemon=True,
            ).start()

    # ---- capture + ocr ----
    def _safe_grab(self, left, top, right, bottom) -> Image.Image:
        w = max(1, int(right - left))
        h = max(1, int(bottom - top))
        return self.grabber.grab_rect(int(left), int(top), w, h)

    def _ocr_translate_show(self, left, top, right, bottom):
        try:
            img = self._safe_grab(left, top, right, bottom)
            img = preprocess_for_ocr(img)

            text_en = ocr_text_block(img)
            if not text_en:
                raise RuntimeError("OCR vide (zone trop petite / pas assez contrastée).")

            text_fr = translate_mymemory(text_en)
            if not text_fr:
                raise RuntimeError("Traduction vide (API).")

            def _show():
                try:
                    if self.big_overlay is not None and self.big_overlay.winfo_exists():
                        self.big_overlay.destroy()
                except Exception:
                    pass
                self.big_overlay = RoundedOverlay(self.root, text_fr)

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
    try:
        App().run()
    except Exception as e:
        messagebox.showerror("Erreur", str(e))
