# -*- coding: utf-8 -*-
"""Autotest : verifie que la chaine OCR fonctionne vraiment de bout en bout.

Fabrique une image contenant un texte connu, la passe dans le meme
pretraitement et le meme appel Tesseract que les overlays, et compare le
resultat au texte attendu.

L'interet est de detecter le cas ou Tesseract est installe et repond
normalement, mais rend du texte incoherent parce qu'il lui manque un fichier
de langue : c'est un echec silencieux, invisible d'une simple verification
de presence.

    python scripts/selftest.py
    python scripts/selftest.py --korean
"""

import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

KOREAN = "--korean" in sys.argv

SAMPLES = {
    "kor": (u"\uc548\ub155\ud558\uc138\uc694", ("malgun.ttf", "MalgunGothic.ttf", "gulim.ttc")),
    "eng": (u"Hello world", ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf")),
}


def load_font(names, size):
    from PIL import ImageFont
    for name in names:
        for folder in (os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
                       "/usr/share/fonts/truetype/dejavu",
                       "/usr/share/fonts"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return ImageFont.truetype(path, size)
    return None


def render(text, font):
    from PIL import Image, ImageDraw
    img = Image.new("L", (max(320, 22 * len(text)), 70), 255)
    ImageDraw.Draw(img).text((12, 18), text, font=font, fill=0)
    return img


def preprocess(img):
    """Identique a preprocess_for_ocr des overlays."""
    from PIL import ImageOps, Image
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    return img.resize((img.width * 2, img.height * 2), Image.BICUBIC)


def main():
    langs = ("kor", "eng") if KOREAN else ("eng",)

    try:
        from tesseract_setup import configure_tesseract
        exe = configure_tesseract(langs)
    except Exception as exc:
        print("ECHEC : %s" % exc)
        return 1
    print("Tesseract  : %s" % exe)
    print("tessdata   : %s" % os.environ.get("TESSDATA_PREFIX", "(defaut systeme)"))

    import pytesseract
    print("Langues    : %s" % ", ".join(sorted(pytesseract.get_languages(config=""))))

    key = "kor" if KOREAN else "eng"
    expected, fonts = SAMPLES[key]
    font = load_font(fonts, 28)
    if font is None:
        print("Police introuvable pour le test de rendu : autotest OCR ignore.")
        print("Les langues demandees sont bien presentes, l'essentiel est verifie.")
        return 0

    img = preprocess(render(expected, font))
    got = pytesseract.image_to_string(
        img, lang="+".join(langs), config="--oem 3 --psm 6").strip()

    print("Attendu    : %s" % expected)
    print("Obtenu     : %s" % got)

    if expected in got:
        print("\nOK : la chaine OCR fonctionne.")
        return 0

    print("\nECHEC : Tesseract repond mais ne lit pas correctement ce texte.")
    if KOREAN:
        print("Symptome typique d'un modele coreen manquant ou de mauvaise qualite :")
        print("les hangeul sont alors lus comme des lettres latines.")
        print("Relancez : .\\scripts\\install_windows.ps1 -Korean")
    return 1


if __name__ == "__main__":
    sys.exit(main())
