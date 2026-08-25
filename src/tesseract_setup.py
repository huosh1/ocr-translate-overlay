# -*- coding: utf-8 -*-
"""Resolution de Tesseract et verification de ses donnees de langue.

Importe par les trois overlays. Deux problemes sont traites ici, parce que
tous deux se manifestent loin de leur cause :

1. Le chemin de l'executable etait code en dur. Toute installation ailleurs
   que dans C:\\Program Files\\Tesseract-OCR faisait echouer le demarrage.

2. Plus vicieux : si un fichier de langue manque, Tesseract ecrit
   "Failed loading language 'kor'" sur la sortie d'erreur, PUIS continue
   avec les langues restantes et rend le code de sortie 0. pytesseract ne
   voit donc aucune erreur et renvoie le texte obtenu en anglais : les
   hangeul sont lus comme des lettres latines et l'utilisateur recoit du
   charabia sans le moindre message. On verifie donc les langues au
   demarrage, une bonne fois, plutot que de laisser passer.
"""

import os

import pytesseract

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Donnees de langue livrees avec le depot (modeles "best"), installees par
# scripts/install_windows.ps1. Prioritaires sur celles du systeme.
LOCAL_TESSDATA = os.path.join(REPO_ROOT, "tessdata")

# tessdata_best : le jeu le plus precis. Plus lourd et plus lent que
# tessdata_fast, mais c'est le bon compromis sur du texte dense.
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main/%s.traineddata"


def language_installed(code):
    path = os.path.join(LOCAL_TESSDATA, code + ".traineddata")
    if os.path.isfile(path) and os.path.getsize(path) > 500000:
        return True
    try:
        return code in set(pytesseract.get_languages(config=""))
    except Exception:
        return False


def download_language(code, on_progress=None):
    """Installe un modele de langue dans le tessdata du depot.

    On ecrit d'abord dans un fichier .part : une coupure de reseau laisserait
    sinon un modele tronque, que Tesseract chargerait sans rien dire de clair.
    Ecrit dans le depot et non dans Program Files, donc sans elevation.
    """
    import urllib.request

    os.makedirs(LOCAL_TESSDATA, exist_ok=True)
    dest = os.path.join(LOCAL_TESSDATA, code + ".traineddata")
    partial = dest + ".part"

    try:
        with urllib.request.urlopen(TESSDATA_URL % code, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(partial, "wb") as out:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
        os.replace(partial, dest)
    except Exception:
        if os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass
        raise
    return dest

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Tesseract-OCR\tesseract.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Tesseract-OCR\tesseract.exe"),
)

_UNIX_CANDIDATES = (
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
    "/snap/bin/tesseract",
)

_INSTALL_HINT = (
    "Installez-le puis relancez :\n"
    "    Windows : .\\scripts\\install_windows.ps1\n"
    "    Fedora  : sudo dnf install tesseract\n"
    "    Debian  : sudo apt install tesseract-ocr\n"
    "Si Tesseract est deja installe ailleurs, indiquez son chemin dans la\n"
    "variable d'environnement TESSERACT_PATH."
)


def find_tesseract():
    """Renvoie le chemin de l'executable Tesseract, ou None."""
    # 1. Chemin impose explicitement par l'utilisateur.
    forced = os.environ.get("TESSERACT_PATH")
    if forced and os.path.isfile(forced):
        return forced

    # 2. PATH.
    from shutil import which
    found = which("tesseract")
    if found:
        return found

    # 3. Emplacements habituels.
    candidates = _WINDOWS_CANDIDATES if os.name == "nt" else _UNIX_CANDIDATES
    for path in candidates:
        if path and os.path.isfile(path):
            return path

    # 4. Registre Windows, renseigne par l'installeur UB-Mannheim.
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Tesseract-OCR") as key:
                        base, _ = winreg.QueryValueEx(key, "Path")
                        exe = os.path.join(base, "tesseract.exe")
                        if os.path.isfile(exe):
                            return exe
                except OSError:
                    continue
        except ImportError:
            pass

    return None


def configure_tesseract(required_langs=("eng",)):
    """Configure pytesseract et verifie que les langues demandees existent.

    Leve RuntimeError avec un message actionnable si l'installation est
    incomplete. Renvoie le chemin de l'executable retenu.
    """
    exe = find_tesseract()
    if exe is None:
        raise RuntimeError("Tesseract est introuvable sur cette machine.\n" + _INSTALL_HINT)

    pytesseract.pytesseract.tesseract_cmd = exe

    # Les modeles du depot priment sur ceux du systeme quand ils sont la.
    if os.path.isdir(LOCAL_TESSDATA):
        os.environ["TESSDATA_PREFIX"] = LOCAL_TESSDATA

    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception as exc:  # tesseract present mais inutilisable
        raise RuntimeError("Tesseract (%s) ne repond pas : %s" % (exe, exc))

    missing = [lang for lang in required_langs if lang not in available]
    if missing:
        raise RuntimeError(
            "Donnees de langue manquantes pour Tesseract : %s\n"
            "Tesseract est bien installe (%s) mais ne sait pas lire cette langue.\n"
            "Sans elle, il bascule silencieusement sur les langues restantes et\n"
            "produit du texte incoherent au lieu de signaler l'erreur.\n"
            "Corrigez avec :  .\\scripts\\install_windows.ps1 -Korean\n"
            "Langues actuellement disponibles : %s"
            % (", ".join(missing), exe, ", ".join(sorted(available)) or "aucune")
        )

    return exe
