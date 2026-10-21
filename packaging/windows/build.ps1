[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = $PSScriptRoot
$Tools = Join-Path $Root '_build'

function Download-File([string]$Url, [string]$Destination, [string]$Sha256 = '') {
    if (Test-Path -LiteralPath $Destination) {
        if (-not $Sha256 -or (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) { return }
        throw "Cached download checksum mismatch: $Destination. Rename it and retry."
    }
    Write-Host "Downloading: $Url"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile "$Destination.partial" -TimeoutSec 1200
    if ($Sha256 -and (Get-FileHash -LiteralPath "$Destination.partial" -Algorithm SHA256).Hash -ne $Sha256) {
        throw "Download checksum mismatch: $Url"
    }
    Move-Item -LiteralPath "$Destination.partial" -Destination $Destination -Force
}

function Run-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}

function Find-Python {
    $Candidates = @()
    $LocalPython = Join-Path $Tools 'python\python.exe'
    if (Test-Path -LiteralPath $LocalPython) { $Candidates += $LocalPython }
    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Launcher) {
        try {
            $Found = & $Launcher.Source -3.13-64 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $Candidates += $Found }
        } catch { }
    }
    $Python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($Python -and $Python.Source -notlike '*\WindowsApps\*') { $Candidates += $Python.Source }
    foreach ($Candidate in $Candidates) {
        try {
            $Valid = & $Candidate (Join-Path $Root 'build_portable.py') --check-python 2>$null
            if ($LASTEXITCODE -eq 0 -and $Valid -eq '1') { return $Candidate }
        } catch { }
    }
    Write-Host 'No suitable 64-bit Python found; installing private build tools (no system PATH changes).'
    $Installer = Join-Path $Tools 'python-3.13.16-amd64.exe'
    Download-File 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe' $Installer 'fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b'
    $Target = Join-Path $Tools 'python'
    $Process = Start-Process -FilePath $Installer -ArgumentList @('/quiet', 'InstallAllUsers=0', "TargetDir=`"$Target`"", 'PrependPath=0', 'AssociateFiles=0', 'Shortcuts=0', 'Include_launcher=0', 'Include_test=0', 'Include_doc=0') -Wait -PassThru
    if ($Process.ExitCode -notin @(0,3010) -or -not (Test-Path -LiteralPath $LocalPython)) {
        throw "Python installation failed ($($Process.ExitCode)). Install 64-bit Python 3.13 from python.org, then retry."
    }
    return $LocalPython
}

function Prepare-TinyTeX {
    $TexRoot = Join-Path $Tools 'tex\TinyTeX'
    $Bin = Join-Path $TexRoot 'bin\windows'
    $Ready = Join-Path $TexRoot '.problem-manager-ready-v1'
    if (Test-Path -LiteralPath $Ready) { return $TexRoot }
    if (-not (Test-Path -LiteralPath (Join-Path $Bin 'tlmgr.bat'))) {
        Write-Host 'Downloading a private portable LaTeX runtime from the official TinyTeX release.'
        $Release = Invoke-RestMethod 'https://api.github.com/repos/rstudio/tinytex-releases/releases/tags/daily' -Headers @{ 'User-Agent' = 'MathQuestionManager-Builder' }
        $Asset = $Release.assets | Where-Object { $_.name -eq 'TinyTeX-1-windows.exe' } | Select-Object -First 1
        if (-not $Asset) { throw 'Official TinyTeX Windows asset not found.' }
        $Hash = ''
        if ($Asset.digest -like 'sha256:*') { $Hash = $Asset.digest.Substring(7) }
        $Installer = Join-Path $Tools 'TinyTeX-1-windows.exe'
        Download-File $Asset.browser_download_url $Installer $Hash
        $Extract = Join-Path $Tools 'tex'
        New-Item -ItemType Directory -Force -Path $Extract | Out-Null
        $Process = Start-Process -FilePath $Installer -ArgumentList @('-y', "-o`"$Extract`"") -Wait -PassThru
        if ($Process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $Bin 'tlmgr.bat'))) {
            throw 'TinyTeX extraction failed; see build.log.'
        }
    }
    $SavedPath = $env:PATH
    try {
        $env:PATH = "$Bin;$SavedPath"
        $Tlmgr = Join-Path $Bin 'tlmgr.bat'
        Run-Checked $Tlmgr @('option', 'docfiles', '0')
        Run-Checked $Tlmgr @('option', 'srcfiles', '0')
        Run-Checked $Tlmgr @('option', 'repository', 'https://mirror.ctan.org/systems/texlive/tlnet')
        Run-Checked $Tlmgr @('install', 'latex-bin', 'amsmath', 'amsfonts', 'mathtools', 'newtx', 'ragged2e', 'xcolor', 'tools', 'graphics', 'needspace', 'geometry', 'fontspec', 'xecjk', 'ctex', 'hyperref', 'bookmark', 'xetex', 'dvisvgm', 'tex-gyre', 'fandol')
        Run-Checked (Join-Path $Bin 'fmtutil-sys.exe') @('--byfmt', 'latex')
        Run-Checked (Join-Path $Bin 'fmtutil-sys.exe') @('--byfmt', 'xelatex')
        foreach ($Name in @('latex.exe','xelatex.exe','dvisvgm.exe')) {
            if (-not (Test-Path -LiteralPath (Join-Path $Bin $Name))) { throw "Missing runtime: $Name" }
        }
        'ready' | Set-Content -LiteralPath $Ready -Encoding ascii
    } finally { $env:PATH = $SavedPath }
    return $TexRoot
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows 10/11 64-bit is required.' }
    foreach ($Required in @('source\app.py', 'source\problem_manager', 'data\questions.json', 'build_portable.py', 'build-requirements.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $Required))) { throw "Missing build input: $Required" }
    }
    if ($CheckOnly) { Write-Host 'Build inputs and Windows environment are ready.'; exit 0 }
    Start-Transcript -Path (Join-Path $Root 'build.log') -Append | Out-Null
    New-Item -ItemType Directory -Force -Path $Tools | Out-Null
    $Python = Find-Python
    $Venv = Join-Path $Tools 'venv'
    if (-not (Test-Path -LiteralPath (Join-Path $Venv 'Scripts\python.exe'))) {
        Run-Checked $Python @('-m', 'venv', $Venv)
    }
    $Python = Join-Path $Venv 'Scripts\python.exe'
    Run-Checked $Python @('-m', 'pip', 'install', '--upgrade', 'pip')
    Run-Checked $Python @('-m', 'pip', 'install', '-r', (Join-Path $Root 'build-requirements.txt'))
    $Tex = Prepare-TinyTeX
    Run-Checked $Python @((Join-Path $Root 'build_portable.py'), '--tex-root', $Tex)
    Write-Host 'SUCCESS. The complete portable app is in the dist folder.' -ForegroundColor Green
    Stop-Transcript | Out-Null
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'Build incomplete. No existing question data was overwritten. See build.log.'
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
