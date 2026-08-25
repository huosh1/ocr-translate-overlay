#!/usr/bin/env bash
set -euo pipefail

# Lance l'overlay depuis la racine du depot, quel que soit le dossier courant.
cd "$(dirname "$0")/.."

if [ ! -x venv/bin/python3 ]; then
    echo "Environnement virtuel introuvable. Creez-le d'abord :" >&2
    echo "    python3 -m venv venv" >&2
    echo "    source venv/bin/activate" >&2
    echo "    pip install -r requirements.txt" >&2
    exit 1
fi

source venv/bin/activate
python3 src/overlay.py
