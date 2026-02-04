import os
import time
import tempfile
import subprocess
import requests
import pytesseract
from PIL import Image, ImageGrab, ImageOps
import tkinter as tk
from tkinter import messagebox
import ctypes
from ctypes import wintypes

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
    accent.GradientColor = 0xBBEFE6D8  # AABBGGRR (alpha first)

    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = 19  # WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(accent)

    set_attr = user32.SetWindowCompositionAttribute
    set_attr.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
    set_attr.restype = wintypes.BOOL
    set_attr(hwnd, ctypes.byref(data))


# ======================
# TESSERACT CONFIG
# ======================
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if not os.path.exists(TESSERACT_PATH):
    raise RuntimeError("Tesseract introuvable : " + TESSERACT_PATH)
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ======================
# CLIPBOARD HELPERS
# ======================
def _clipboard_image():
    img = ImageGrab.grabclipboard()
    return img if isinstance(img, Image.Image) else None

def _clipboard_clear():
    # Clear clipboard to avoid grabbing previous screenshot
    user32 = ctypes.windll.user32
    if user32.OpenClipboard(None):
        user32.EmptyClipboard()
        user32.CloseClipboard()


# ======================
# SCREEN CAPTURE (NO CMD FLASH)
# ======================
def capture_region(timeout_sec: float = 25.0) -> str:
    """
    Opens Windows Snip overlay without spawning cmd.exe,
    clears clipboard first, then waits for a NEW image.
    """
    # clear clipboard so we don't reuse an old image
    _clipboard_clear()

    # Launch snipping UI WITHOUT os.system() (avoids cmd flash)
    # explorer.exe is GUI; no console pop.
    subprocess.Popen(["explorer.exe", "ms-screenclip:"], shell=False)

    start = time.time()
    img = None

    while (time.time() - start) < timeout_sec:
        img = _clipboard_image()
        if img is not None:
            break
        time.sleep(0.12)

    if img is None:
        raise RuntimeError("Capture annulée ou aucune image dans le presse-papiers (timeout).")

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


# ======================
# OCR + TRANSLATION
# ======================
def cleanup(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return " ".join(lines)

def ocr_english(path: str) -> str:
    img = Image.open(path)

    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    img = img.resize((img.width * 2, img.height * 2))

    config = "--oem 3 --psm 6"
    text = pytesseract.image_to_string(img, lang="eng", config=config)
    return cleanup(text)

def translate_mymemory(text: str) -> str:
    r = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text, "langpair": "en|fr"},
        timeout=20,
    )
    r.raise_for_status()
    return cleanup(r.json()["responseData"]["translatedText"])


# ======================
# GUI
# ======================
import customtkinter as ctk

class RoundedOverlay(ctk.CTk):
    def __init__(self, text: str):
        super().__init__()

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

        self.paper = "#efe6d8"
        self.text = "#111111"

        w, h = 760, 360
        x = self.winfo_screenwidth() - w - 30
        y = 40
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.card = ctk.CTkFrame(
            self,
            corner_radius=28,
            fg_color=self.paper,
            border_width=0
        )
        self.card.pack(fill="both", expand=True, padx=10, pady=10)

        close = ctk.CTkButton(
            self.card,
            text="✕",
            width=34,
            height=34,
            corner_radius=17,
            fg_color="#dbc9ad",
            hover_color="#cbb89c",
            text_color=self.text,
            command=self.destroy
        )
        close.place(relx=1.0, x=-14, y=14, anchor="ne")

        holder = ctk.CTkFrame(self.card, corner_radius=22, fg_color=self.paper)
        holder.pack(fill="both", expand=True, padx=18, pady=(60, 18))

        self.textbox = ctk.CTkTextbox(
            holder,
            wrap="word",
            fg_color=self.paper,
            text_color=self.text,
            border_width=0,
            font=ctk.CTkFont("Segoe UI Semibold", 18)
        )
        self.textbox.pack(fill="both", expand=True, padx=6, pady=6)

        self.textbox.insert("1.0", text.strip() if text else "OCR vide")
        self.textbox.configure(state="disabled")

        self.bind("<Escape>", lambda e: self.destroy())


# ======================
# MAIN
# ======================
def main():
    png = None
    try:
        png = capture_region(timeout_sec=25.0)
        en = ocr_english(png)
        if not en:
            raise RuntimeError("OCR vide (prends un texte plus gros/contrasté).")
        fr = translate_mymemory(en)
        RoundedOverlay(fr).mainloop()

    except Exception as e:
        messagebox.showerror("Erreur OCR / Traduction", str(e))

    finally:
        # oui: on supprime le screenshot temporaire
        if png and os.path.exists(png):
            os.remove(png)

if __name__ == "__main__":
    main()
