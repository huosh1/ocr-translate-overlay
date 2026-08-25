@echo off
setlocal
rem Lance l'overlay depuis la racine du depot, quel que soit le dossier courant.
cd /d "%~dp0.."

if not exist "venv\Scripts\pythonw.exe" (
    echo Environnement virtuel introuvable.
    echo Creez-le d'abord :
    echo     scripts\install_windows.bat
    pause
    exit /b 1
)

rem pythonw.exe : pas de fenetre de terminal derriere l'overlay.
start "" "venv\Scripts\pythonw.exe" "src\overlay.py"
