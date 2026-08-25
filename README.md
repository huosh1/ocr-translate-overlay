# OCR Screen Translator

Translate **non-selectable text straight from your screen** — web novels, locked
websites, images, PDFs, games. Sweep a rectangle over any area: the tool runs OCR
locally, translates the result, and shows it in a floating overlay.

The Korean version goes further: alongside the translation it reprints the
original sentence, colour-coded by part of speech, and lists every word with its
grammatical nature and its French meaning.

## How to use it

Hold **`Ctrl` + `Alt`**. The first corner of the selection is anchored wherever
the mouse cursor happens to be. Keep holding, move the mouse — the rectangle
follows. Release `Ctrl` + `Alt` and the capture starts.

No clicking is involved at any point, so clicks keep going to whatever is
underneath: you can still turn pages or follow links while the tool is running.

`ESC` closes the overlays, `F8` quits the program.

Nothing happens if the mouse barely moved, or if the rectangle ends up smaller
than 18 pixels a side — that keeps stray `AltGr` presses on an AZERTY keyboard
from firing a capture, since Windows reports `AltGr` as `Ctrl` + `Alt`.

## Repository layout

```
.
├── src/
│   ├── overlay_windows.py         # Windows — English OCR, French translation
│   ├── overlay_windows_korean.py  # Windows — Korean OCR + translation + grammar
│   ├── overlay_linux.py           # Linux (Fedora) — English OCR, French translation
│   └── tesseract_setup.py         # locates Tesseract, verifies its language data
├── scripts/
│   ├── install_windows.bat        # one-click setup (double-click me first)
│   ├── install_windows.ps1        # what the above actually runs
│   ├── selftest.py                # end-to-end check of the OCR chain
│   ├── run_windows.bat            # launches overlay_windows.py
│   ├── run_windows_korean.bat     # launches overlay_windows_korean.py
│   ├── run_linux.sh               # launches overlay_linux.py
│   └── _run.bat                   # shared launcher logic, not run directly
├── requirements.txt               # core dependencies
└── requirements-korean.txt        # optional extra for the grammar analysis
```

The three overlays are standalone — pick the one matching your platform and
target language. They share only `tesseract_setup.py`.

## Prerequisites

**Python 3.10 or higher.** On Windows use the official installer and keep
*Add Python to PATH* checked. `tkinter` ships with it.

**Tesseract OCR**, plus the language data for what you intend to read. The
installer handles both.

**A JDK**, only for the Korean grammar colouring, which goes through KoNLPy and
therefore through Java. Check with `java -version`.

## Install

**The easy way (Windows).** Double-click `scripts\install_windows.bat`, or from a
terminal at the repository root:

```powershell
.\scripts\install_windows.ps1            # English version
.\scripts\install_windows.ps1 -Korean    # + Korean model and konlpy
.\scripts\install_windows.ps1 -Check     # diagnose only, installs nothing
```

It verifies your Python version, installs Tesseract through winget if it is
missing (expect a UAC prompt), downloads the language models, creates `venv/`,
installs the dependencies, and finishes by running `selftest.py` — which renders
a known sentence to an image, OCRs it, and compares. Re-run it any time: it only
redoes what is missing, and every step is re-checked on each run.

Language models go into `tessdata/` at the repository root, not into
`Program Files`. No administrator rights are needed for that part, and everyone
ends up with the same models regardless of how Tesseract was installed. The
models come from `tessdata_best`, the most accurate set — worth the extra size
and time on dense novel text.

**By hand.** Use `py -3` rather than `python` if another virtual environment is
active in your shell, otherwise you will install into that one:

```powershell
py -3 -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt      # or requirements-korean.txt
```

## Run

From anywhere — the launchers resolve the repository root themselves:

```powershell
.\scripts\run_windows.bat
.\scripts\run_windows_korean.bat
```

```bash
./scripts/run_linux.sh
```

They expect the virtual environment in `venv/` and say so if it is missing. On
Windows they start `pythonw.exe`, so no terminal window stays behind the overlay.
To pin one to the taskbar, make a shortcut to the `.bat` file.

## Overlays

The panels have no system title bar. **Drag them by their header**, resize from
the bottom-right corner. Position and size are remembered between sessions, per
panel, in `%APPDATA%\ocr-translate-overlay\windows.json`; a position that would
land off-screen is ignored in favour of the default placement.

### Korean fonts

The grammar panel renders Hangul with the first of these that is installed:
Pretendard, Noto Sans KR, the Nanum family, then Malgun Gothic, and only as a
last resort the older Gulim / Batang / MS Gothic.

None of the first ones ship with Windows, so out of the box you get Malgun
Gothic — correct but dry. Installing **Pretendard** or **Noto Sans KR**, both
free, gives the rounder, more open Hangul that Korean sites use. The code picks
it up on the next launch with no configuration.

## Troubleshooting

**"Tesseract est introuvable"** — run the installer, or point the
`TESSERACT_PATH` environment variable at your `tesseract.exe`. The script also
looks in the `PATH`, in the usual install directories, and in the registry.

**"Donnees de langue manquantes"** — Tesseract is installed but lacks the model
for that language. Run `.\scripts\install_windows.ps1 -Korean`.

This check matters more than it looks. When a model is missing, Tesseract prints
`Failed loading language` to stderr, then carries on with the remaining
languages and exits with status 0. Nothing surfaces the failure: Hangul gets
read as Latin letters and you receive fluent nonsense. So the languages are
verified once at startup instead.

**Nothing appears after a selection** — the OCR returned nothing, usually on text
that is too small or too low-contrast. Try a tighter rectangle around the text.
Note too that translation uses the public MyMemory API, which is rate-limited
for anonymous use.

## Notes

* OCR runs locally; only the extracted text leaves the machine.
* Translation uses MyMemory (`en|fr`, or `ko|fr` for the Korean script).
* Temporary screenshots are deleted automatically.
