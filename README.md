
# OCR Screen Translator (EN → FR)

This tool allows you to translate **non-selectable text directly from your screen** (web novels, locked websites, images, PDFs, applications).

It works by letting you draw a rectangular selection on the screen, performing **OCR in English**, then displaying the **French translation** in a floating overlay.


## Requirements (Windows)

### Python
- Python 3.10 or higher  
Download: https://www.python.org/downloads/windows/  
Make sure **“Add Python to PATH”** is checked during installation.


## Tesseract OCR (Required)

Install Tesseract from the official Windows build:  
https://github.com/UB-Mannheim/tesseract/wiki

Default expected path:
```

C:\Program Files\Tesseract-OCR\tesseract.exe

````

If your installation path is different, update it in the script:
```python
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
````


## Python Dependencies

Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\activate
```

Install dependencies:

```powershell
pip install pillow pytesseract requests mss pynput customtkinter
```

## Running the Tool

The tool is intended to be launched using **pythonw.exe** to avoid opening a terminal window.

Example PowerShell launcher (`run.ps1`):

```powershell
Set-Location "C:\Path\To\Project"
& ".\venv\Scripts\pythonw.exe" ".\ocr_translate_popup_windows.py"
```

You can create a Windows shortcut pointing to:

```
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Path\To\Project\run.ps1"
```


## Notes

* OCR is performed locally.
* Translation uses the public MyMemory API.
* Temporary screenshots are automatically deleted.
* Designed for text that cannot be copied or selected.



