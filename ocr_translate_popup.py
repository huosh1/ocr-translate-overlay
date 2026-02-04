import os
import subprocess
import tempfile
import requests
import pytesseract
from PIL import Image
import tkinter as tk


# ======================
# OCR + TRANSLATION
# ======================
def capture_region() -> str:
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    subprocess.run(["gnome-screenshot", "-a", "-f", path], check=True)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise RuntimeError("Screenshot annulé ou vide")
    return path


def ocr_english(path: str) -> str:
    img = Image.open(path).convert("L")
    img = img.point(lambda x: 0 if x < 165 else 255, "1")
    text = pytesseract.image_to_string(img, lang="eng")
    return cleanup(text)


def cleanup(s: str) -> str:
    lines = [l.strip() for l in s.splitlines() if l.strip()]
    return " ".join(lines)


def translate_mymemory(text: str) -> str:
    r = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text, "langpair": "en|fr"},
        timeout=20,
    )
    r.raise_for_status()
    return cleanup(r.json()["responseData"]["translatedText"])


# ======================
# ROUNDED WINDOW
# ======================
class RoundedOverlay(tk.Tk):
    def __init__(self, en_text, fr_text):
        super().__init__()

        self.en_text = en_text
        self.fr_text = fr_text
        self.lang = "fr"

        # Window config
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.97)

        # Palette (paper style)
        self.paper = "#f4efe8"
        self.border = "#d6cbbf"
        self.text = "#1f1f1f"
        self.muted = "#6b6258"
        self.accent = "#c7b299"

        # Size & position
        w, h = 720, 320
        x = self.winfo_screenwidth() - w - 30
        y = 30
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.configure(bg=self.paper)

        # Canvas for rounded corners
        self.canvas = tk.Canvas(
            self,
            bg=self.paper,
            highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.radius = 22
        self.draw_card()

        # Content frame
        self.frame = tk.Frame(self.canvas, bg=self.paper)
        self.canvas.create_window(
            self.radius,
            self.radius,
            anchor="nw",
            window=self.frame,
            width=w - 2 * self.radius,
            height=h - 2 * self.radius,
        )

        self.build_ui()

        # Keybinds
        self.bind("<Escape>", lambda e: self.destroy())

    def draw_card(self):
        w = self.winfo_width() or 720
        h = self.winfo_height() or 320
        r = self.radius

        self.canvas.create_round_rect = lambda x1, y1, x2, y2, r, **kw: (
            self.canvas.create_polygon(
                x1+r, y1,
                x2-r, y1,
                x2, y1,
                x2, y1+r,
                x2, y2-r,
                x2, y2,
                x2-r, y2,
                x1+r, y2,
                x1, y2,
                x1, y2-r,
                x1, y1+r,
                x1, y1,
                smooth=True,
                **kw
            )
        )

        self.canvas.create_round_rect(
            0, 0, w, h, r,
            fill=self.paper,
            outline=self.border,
            width=1
        )

    def build_ui(self):
        # Header
        header = tk.Frame(self.frame, bg=self.paper)
        header.pack(fill="x", pady=(6, 10))

        self.title = tk.Label(
            header,
            text="Traduction (FR)",
            bg=self.paper,
            fg=self.text,
            font=("Libertinus Serif", 13, "bold"),
        )
        self.title.pack(side="left")

        close = tk.Label(
            header,
            text="✕",
            bg=self.paper,
            fg=self.muted,
            font=("Libertinus Serif", 14),
            cursor="hand2",
        )
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.destroy())

        # Text
        self.textbox = tk.Text(
            self.frame,
            bg=self.paper,
            fg=self.text,
            wrap="word",
            relief="flat",
            highlightthickness=0,
            font=("Libertinus Serif", 12),
        )
        self.textbox.pack(fill="both", expand=True)
        self.textbox.insert("1.0", self.fr_text)
        self.textbox.config(state="disabled")

        # Footer
        footer = tk.Frame(self.frame, bg=self.paper)
        footer.pack(fill="x", pady=(10, 4))

        def button(txt, cmd):
            b = tk.Label(
                footer,
                text=txt,
                bg=self.accent,
                fg=self.text,
                padx=12,
                pady=6,
                font=("Libertinus Serif", 10, "bold"),
                cursor="hand2",
            )
            b.bind("<Button-1>", lambda e: cmd())
            return b

        button("EN / FR", self.toggle_lang).pack(side="left")
        button("Copier", self.copy).pack(side="left", padx=10)

        hint = tk.Label(
            footer,
            text="Esc pour fermer",
            bg=self.paper,
            fg=self.muted,
            font=("Libertinus Serif", 9),
        )
        hint.pack(side="right")

    def toggle_lang(self):
        self.lang = "en" if self.lang == "fr" else "fr"
        self.textbox.config(state="normal")
        self.textbox.delete("1.0", "end")

        if self.lang == "fr":
            self.title.config(text="Traduction (FR)")
            self.textbox.insert("1.0", self.fr_text)
        else:
            self.title.config(text="Original (EN)")
            self.textbox.insert("1.0", self.en_text)

        self.textbox.config(state="disabled")

    def copy(self):
        content = self.fr_text if self.lang == "fr" else self.en_text
        self.clipboard_clear()
        self.clipboard_append(content)
        self.update()


# ======================
# MAIN
# ======================
def main():
    png = None
    try:
        png = capture_region()
        en = ocr_english(png)
        fr = translate_mymemory(en) if en else "OCR vide"
        RoundedOverlay(en, fr).mainloop()
    finally:
        if png and os.path.exists(png):
            os.remove(png)


if __name__ == "__main__":
    main()
