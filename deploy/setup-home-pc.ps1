# Installazione di Dinamik Advisor sul PC di casa (Windows 10/11), ruolo: scraper.
#
# Uso, da PowerShell (non serve amministratore, salvo per winget se chiede conferma):
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\deploy\setup-home-pc.ps1
# oppure, senza clone:
#   irm https://raw.githubusercontent.com/filstreetwear-cyber/DINAMIK-/claude/dinamik-advisor-scout-mvp-7no1zb/deploy/setup-home-pc.ps1 -OutFile setup-home-pc.ps1
#
# Cosa fa (rilanciabile): Python 3.12, Git e Tailscale via winget se mancano;
# clone del branch; virtualenv; dipendenze; Chromium per Playwright; .env.
# Non tocca Amazon: il primo run lo lanci tu (o la sessione Claude locale).
$ErrorActionPreference = "Stop"
$Branch = "claude/dinamik-advisor-scout-mvp-7no1zb"
$RepoUrl = if ($env:REPO_URL) { $env:REPO_URL } else { "https://github.com/filstreetwear-cyber/DINAMIK-.git" }
$AppDir = if ($env:APP_DIR) { $env:APP_DIR } else { Join-Path $HOME "DINAMIK-" }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Step "1/6 Programmi di base"
if (-not (Have "py") -and -not (Have "python")) { winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements }
if (-not (Have "git")) { winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements }
if (-not (Have "tailscale")) { winget install -e --id Tailscale.Tailscale --accept-source-agreements --accept-package-agreements }
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
$Py = if (Have "py") { "py -3.12" } else { "python" }
$ver = Invoke-Expression "$Py -c `"import sys; print(sys.version_info >= (3, 12))`""
if ($ver -ne "True") { throw "Serve Python 3.12 o superiore. Riapri PowerShell dopo l'installazione e rilancia." }

Step "2/6 Codice"
if (Test-Path (Join-Path $AppDir ".git")) {
  git -C $AppDir fetch -q origin $Branch
  git -C $AppDir checkout -q $Branch
  git -C $AppDir pull -q --ff-only origin $Branch
} else {
  git clone -q --branch $Branch $RepoUrl $AppDir
}
Set-Location $AppDir

Step "3/6 Ambiente Python e dipendenze"
if (-not (Test-Path ".venv")) { Invoke-Expression "$Py -m venv .venv" }
& .\.venv\Scripts\python.exe -m pip install -q --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -q -e ".[dev]"

Step "4/6 Browser per lo scraper (Chromium)"
& .\.venv\Scripts\python.exe -m playwright install chromium

Step "5/6 Configurazione (.env)"
if (-not (Test-Path ".env")) {
  Copy-Item .env.example .env
  (Get-Content .env) -replace '^MOCK_MODE=.*', 'MOCK_MODE=false' | Set-Content .env
  Write-Host "Creato .env. DEVI incollare in DATABASE_URL la riga stampata da setup-server.sh sul server OVH." -ForegroundColor Yellow
} else { Write-Host ".env già presente: non lo tocco" }

Step "6/6 Tailscale"
Write-Host "Apri Tailscale dall'area di notifica e accedi con lo STESSO account usato sul server."
Write-Host "Verifica poi con: tailscale status   (deve comparire il server OVH)"

Write-Host "`nFatto. Prossimi comandi, dalla cartella $AppDir :" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\dinamik health"
Write-Host "  .\.venv\Scripts\dinamik scout vies IT<11 cifre di una P.IVA che conosci>"
Write-Host "  .\.venv\Scripts\dinamik scout run --name casa-v1 --category ""Casa e cucina"" --urls-file urls.txt --max-products 25"
