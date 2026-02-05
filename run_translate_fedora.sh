#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/Documents/ext"
source venv/bin/activate
python3 ocr_translate_popup.py
