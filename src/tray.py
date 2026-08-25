# -*- coding: utf-8 -*-
"""Icone de barre d'etat.

L'outil se lance par pythonw.exe, sans fenetre ni console : une fois demarre,
rien a l'ecran ne rappelle qu'il tourne, ni comment l'arreter. F8 quitte, mais
encore faut-il le savoir. L'icone donne une prise visible, et rappelle les
raccourcis dans son menu.

L'icone est dessinee par PIL plutot que livree en .ico : le depot reste sans
binaire et l'image suit la palette des panneaux.

pystray est optionnel. Sans lui, l'outil fonctionne comme avant et seuls les
raccourcis restent.
"""

import threading

from PIL import Image, ImageDraw

try:
    import pystray
    PYSTRAY_AVAILABLE = True
except Exception:
    PYSTRAY_AVAILABLE = False


PAPER = (239, 230, 216)
INK = (32, 28, 24)
ACCENT = (156, 66, 33)


def make_icon_image(size=64):
    """Un rectangle de selection sur fond papier : ce que fait l'outil."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = size // 10
    draw.rounded_rectangle([pad, pad, size - pad, size - pad],
                           radius=size // 6, fill=PAPER)

    # Deux lignes de "texte" et le cadre de selection par-dessus.
    line = size // 12
    top = size // 3
    for i, width in enumerate((0.52, 0.38)):
        y = top + i * (line * 2)
        draw.rounded_rectangle(
            [size * 0.26, y, size * (0.26 + width), y + line],
            radius=line // 2, fill=INK)

    box = [size * 0.20, size * 0.28, size * 0.80, size * 0.66]
    draw.rounded_rectangle(box, radius=size // 14, outline=ACCENT,
                           width=max(2, size // 24))
    return img


def start_tray(on_quit, on_close_overlays=None, subtitle=""):
    """Lance l'icone dans un thread demon. Renvoie l'icone, ou None.

    Les fonctions passees sont appelees depuis le thread de l'icone : c'est a
    l'appelant de repasser par la boucle Tk, comme partout ailleurs ici.
    """
    if not PYSTRAY_AVAILABLE:
        return None

    title = "OCR Screen Translator"
    if subtitle:
        title += "  —  " + subtitle

    items = []
    if on_close_overlays is not None:
        items.append(pystray.MenuItem("Close overlays  (Esc)",
                                      lambda icon, item: on_close_overlays()))
    items.append(pystray.MenuItem("Quit  (F8)",
                                 lambda icon, item: on_quit(),
                                 default=True))

    icon = pystray.Icon("ocr-translate-overlay",
                        icon=make_icon_image(),
                        title=title,
                        menu=pystray.Menu(*items))

    thread = threading.Thread(target=icon.run, daemon=True)
    thread.start()
    return icon


if __name__ == "__main__":
    # Apercu : ecrit l'image pour la regarder sans lancer l'outil.
    make_icon_image(256).save("tray_preview.png")
    print("apercu ecrit dans tray_preview.png")
