<#
.SYNOPSIS
    Installe tout ce qu'il faut pour OCR Screen Translator sous Windows.

.DESCRIPTION
    Verifie Python, installe Tesseract OCR s'il manque (via winget), telecharge
    les modeles de langue haute qualite dans tessdata/ a la racine du depot,
    cree l'environnement virtuel, installe les dependances, puis verifie que la
    chaine OCR fonctionne reellement sur une image de test.

    Le script est idempotent : relancez-le autant de fois que vous voulez, il ne
    refait que ce qui manque. Chaque etape est verifiee a chaque execution.

    Les modeles de langue vont dans tessdata/ a la racine du depot et non dans
    Program Files : aucun droit administrateur n'est requis pour cette partie.

.PARAMETER Korean
    Ajoute le modele coreen et konlpy, requis par src/overlay.py.

.PARAMETER Check
    Diagnostic seul : affiche ce qui manque sans rien installer ni modifier.

.EXAMPLE
    .\scripts\install_windows.ps1 -Check
    .\scripts\install_windows.ps1 -Korean
#>

[CmdletBinding()]
param(
    [switch]$Korean,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

$RepoRoot    = Split-Path -Parent $PSScriptRoot
$VenvDir     = Join-Path $RepoRoot 'venv'
$VenvPython  = Join-Path $VenvDir 'Scripts\python.exe'
$TessdataDir = Join-Path $RepoRoot 'tessdata'
$SelfTest    = Join-Path $PSScriptRoot 'selftest.py'

# tessdata_best : modeles les plus precis. Plus lourds et plus lents que
# tessdata_fast, mais c'est le bon compromis pour du texte de roman, souvent
# dense et en petits caracteres.
$TessdataBaseUrl = 'https://github.com/tesseract-ocr/tessdata_best/raw/main/'

# eng est toujours necessaire : les overlays l'utilisent seul ou en secours du
# coreen, et TESSDATA_PREFIX pointe sur tessdata/, qui doit donc tout contenir.
$Languages = @('eng')
if ($Korean) { $Languages += 'kor' }

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK   $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    !    $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "    X    $msg" -ForegroundColor Red }

# --------------------------------------------------------------------------
# Python : on privilegie le lanceur "py", qui ignore un venv deja actif
# (sinon on installe dans l'environnement d'un autre projet sans s'en rendre
# compte).
# --------------------------------------------------------------------------
function Find-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $v = & $py.Source -3 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            return [pscustomobject]@{ Exe = $py.Source; Args = @('-3'); Version = $v }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $v = & $python.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            return [pscustomobject]@{ Exe = $python.Source; Args = @(); Version = $v }
        }
    }
    return $null
}

function Find-Tesseract {
    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        'C:\Program Files\Tesseract-OCR\tesseract.exe',
        'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR\tesseract.exe'),
        (Join-Path $env:LOCALAPPDATA 'Tesseract-OCR\tesseract.exe')
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }

    foreach ($key in @('HKLM:\SOFTWARE\Tesseract-OCR', 'HKCU:\SOFTWARE\Tesseract-OCR')) {
        $p = (Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).Path
        if ($p) {
            $exe = Join-Path $p 'tesseract.exe'
            if (Test-Path $exe) { return $exe }
        }
    }
    return $null
}

function Get-MissingLanguages {
    $missing = @()
    foreach ($lang in $Languages) {
        $file = Join-Path $TessdataDir "$lang.traineddata"
        # Un fichier tronque par un telechargement interrompu est aussi un
        # fichier manquant : les modeles best pesent plusieurs Mo.
        if (-not (Test-Path $file) -or (Get-Item $file).Length -lt 500KB) {
            $missing += $lang
        }
    }
    return $missing
}

# ==========================================================================
# 1. Diagnostic — effectue a chaque execution
# ==========================================================================
Write-Host "OCR Screen Translator - installation Windows" -ForegroundColor White
Write-Host "Depot   : $RepoRoot"
Write-Host "Langues : $($Languages -join ', ')"

Write-Step "Python"
$python = Find-Python
if (-not $python) {
    Write-Fail "Python introuvable. Installez-le depuis https://www.python.org/downloads/windows/"
    Write-Fail "en cochant « Add Python to PATH », puis relancez ce script."
    exit 1
}
Write-Ok "$($python.Exe) (version $($python.Version))"
$major, $minor = $python.Version.Split('.')
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
    Write-Fail "Python 3.10 minimum est requis."
    exit 1
}

Write-Step "Tesseract OCR"
$tesseract = Find-Tesseract
if ($tesseract) { Write-Ok $tesseract } else { Write-Warn2 "absent" }

Write-Step "Modeles de langue (tessdata/)"
$missingLangs = Get-MissingLanguages
foreach ($lang in $Languages) {
    if ($missingLangs -contains $lang) { Write-Warn2 "$lang absent" } else { Write-Ok "$lang present" }
}

Write-Step "Environnement virtuel"
if (Test-Path $VenvPython) { Write-Ok $VenvDir } else { Write-Warn2 "absent" }

if ($Check) {
    Write-Host "`nMode -Check : rien n'a ete installe ni modifie." -ForegroundColor White
    exit 0
}

# ==========================================================================
# 2. Tesseract
# ==========================================================================
if (-not $tesseract) {
    Write-Step "Installation de Tesseract OCR"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Fail "winget est introuvable sur cette machine."
        Write-Fail "Installez Tesseract a la main : https://github.com/UB-Mannheim/tesseract/wiki"
        Write-Fail "puis relancez ce script."
        exit 1
    }
    Write-Host "    winget install UB-Mannheim.TesseractOCR  (une fenetre UAC va s'ouvrir)"
    & $winget.Source install --id UB-Mannheim.TesseractOCR --exact `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "L'installation a echoue (code $LASTEXITCODE)."
        exit 1
    }
    $tesseract = Find-Tesseract
    if (-not $tesseract) {
        Write-Fail "Tesseract reste introuvable apres installation."
        Write-Fail "Ouvrez un NOUVEAU terminal et relancez ce script :"
        Write-Fail "    .\scripts\install_windows.ps1 -Check"
        exit 1
    }
    Write-Ok $tesseract
}

# ==========================================================================
# 3. Modeles de langue
# ==========================================================================
if ($missingLangs.Count -gt 0) {
    Write-Step "Telechargement des modeles de langue (tessdata_best)"
    if (-not (Test-Path $TessdataDir)) {
        New-Item -ItemType Directory -Path $TessdataDir | Out-Null
    }
    foreach ($lang in $missingLangs) {
        $dest = Join-Path $TessdataDir "$lang.traineddata"
        $tmp  = "$dest.part"
        Write-Host "    $lang.traineddata ..."
        try {
            Invoke-WebRequest -Uri "$TessdataBaseUrl$lang.traineddata" -OutFile $tmp -UseBasicParsing
        } catch {
            Write-Fail "Telechargement de $lang impossible : $($_.Exception.Message)"
            Write-Fail "Verifiez votre connexion et relancez le script."
            if (Test-Path $tmp) { Remove-Item $tmp -Force }
            exit 1
        }
        Move-Item $tmp $dest -Force
        $mo = [math]::Round((Get-Item $dest).Length / 1MB, 1)
        Write-Ok "$lang.traineddata ($mo Mo)"
    }
}

# ==========================================================================
# 4. Environnement virtuel et dependances
# ==========================================================================
if (-not (Test-Path $VenvPython)) {
    Write-Step "Creation de l'environnement virtuel"
    $pyArgs = @($python.Args)
    & $python.Exe @pyArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Fail "echec de la creation du venv."; exit 1 }
    Write-Ok $VenvDir
}

Write-Step "Dependances Python"
if ($Korean) {
    $req = Join-Path $RepoRoot 'requirements-korean.txt'
} else {
    $req = Join-Path $RepoRoot 'requirements.txt'
}
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r $req --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "pip a echoue."; exit 1 }
Write-Ok (Split-Path -Leaf $req)

# ==========================================================================
# 5. Autotest de la chaine OCR
# ==========================================================================
Write-Step "Autotest de la chaine OCR"
if ($Korean) {
    & $VenvPython $SelfTest --korean
} else {
    & $VenvPython $SelfTest
}
if ($LASTEXITCODE -ne 0) {
    Write-Fail "L'autotest a echoue : voir le detail ci-dessus."
    exit 1
}

if ($Korean) {
    & $VenvPython -c "from konlpy.tag import Okt; Okt(); print('konlpy OK')" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "konlpy ne demarre pas : un JDK est-il installe ? (java -version)"
        Write-Warn2 "L'overlay fonctionnera, sans la coloration grammaticale."
    } else {
        Write-Ok "konlpy operationnel"
    }
}

Write-Host "`nTout est pret. Lancez l'outil avec :" -ForegroundColor Green
Write-Host "    .\scripts\run.bat"
