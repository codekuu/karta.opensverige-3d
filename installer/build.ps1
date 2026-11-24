# Build the installer of Tech Embassy Pixel for Windows. (Pixelbygget is the project's own
# name for it; a person who installs it never sees that word.)
#
#   installer\build.ps1 -Version 0.1.0
#
# Lays out the folder the installer will put on a person's machine (installer\stage) and
# runs Inno Setup on it. Output: installer\out\TechEmbassyPixel-Setup-<version>.exe.
#
# The installer is small on purpose: a Python of its own, the rutor code and one wheel.
# Everything large is fetched by the app page, after it has said that the machine can draw.
#
# Needs: Windows, PowerShell 7, tar, and Inno Setup 6 (ISCC.exe). Run by
# .github\workflows\installer.yml; it can be run by hand the same way.

param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{1,3}\.\d{1,3}\.\d{1,3}$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
# The staged Python is run below. It must not leave compiled files in what ships.
$env:PYTHONDONTWRITEBYTECODE = '1'

# ---- what is fetched, each by an exact address and a SHA-256
#
# To move a pin: read the new file's release page, change the address and the hash here
# together, in a pull request.

# CPython 3.11 from python-build-standalone, release 20261123. The hash is the one in that
# release's SHA256SUMS. 3.11 because the stack's wheels are cp311.
$PythonUrl = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261123/cpython-3.11.18%2B20261123-x86_64-pc-windows-msvc-install_only.tar.gz'
$PythonSha256 = '7b2cc1c6e0a21c8a577cc3af5f34595df706e0e4128835e8ddfc709863384455'

# diffusers at the commit requirements.txt pins. No index has a wheel of it, so one is
# built here and shipped in the installer: nothing is ever built on a person's machine.
$DiffusersCommit = '8c54b38fea3d624d23d0e43c0c96037f080a79f9'
$DiffusersUrl = "https://github.com/huggingface/diffusers/archive/$DiffusersCommit.zip"
$DiffusersSha256 = '7853fddc0cf42336ac029ea394bff2972644546c3c93853a7ee64cd5ad6c9ed2'

$Root = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $PSScriptRoot 'stage'
$Out = Join-Path $PSScriptRoot 'out'
$Fetched = Join-Path $PSScriptRoot 'fetched'
$NoMark = New-Object System.Text.UTF8Encoding($false)

function Get-Pinned {
    # Fetch a file and refuse it, before anything reads it, unless its SHA-256 is the pinned one.
    param([string]$Url, [string]$Sha256, [string]$Path)
    Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($got -ne $Sha256) {
        Remove-Item -LiteralPath $Path -Force
        throw "The file at $Url has SHA-256 $got, and $Sha256 is pinned. Nothing of it was used."
    }
}

function Invoke-Checked {
    # Run a program and stop the build when it fails.
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program ended with $LASTEXITCODE"
    }
}

foreach ($folder in @($Stage, $Out, $Fetched)) {
    if (Test-Path -LiteralPath $folder) { Remove-Item -LiteralPath $folder -Recurse -Force }
    New-Item -ItemType Directory -Path $folder | Out-Null
}

# ---- the Python the app runs on

$pythonArchive = Join-Path $Fetched 'python.tar.gz'
Get-Pinned -Url $PythonUrl -Sha256 $PythonSha256 -Path $pythonArchive
# The archive holds one folder, python\. Windows' own tar, by its full path: a tar from
# Git, if one comes first on PATH, reads the drive letter as a host name.
Invoke-Checked (Join-Path $env:SystemRoot 'System32\tar.exe') @('-xzf', $pythonArchive, '-C', $Stage)
$Python = Join-Path $Stage 'python\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'The Python archive held no python\python.exe' }

# ---- the app's own code

$cli = Join-Path $Stage 'cli'
New-Item -ItemType Directory -Path $cli | Out-Null
Copy-Item -LiteralPath (Join-Path $Root 'cli\rutor') -Destination $cli -Recurse
Copy-Item -LiteralPath (Join-Path $Root 'cli\google-render') -Destination $cli -Recurse
# Nothing a build or a test run on this machine left behind goes into the installer.
Get-ChildItem -LiteralPath $cli -Recurse -Force -Directory |
    Where-Object { $_.Name -in @('__pycache__', 'node_modules') } |
    Remove-Item -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root 'TERMS.md') -Destination $Stage
Copy-Item -LiteralPath (Join-Path $Root 'LICENSE') -Destination $Stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start.pyw') -Destination $Stage

# The app's Python finds the app's code wherever it is started from. A path in a .pth file
# is read from the folder the file lies in.
[System.IO.File]::WriteAllText((Join-Path $Stage 'python\Lib\site-packages\pixelbygget.pth'), "..\..\..\cli`n", $NoMark)

# ---- the one wheel no index has

$diffusersSource = Join-Path $Fetched "diffusers-$DiffusersCommit.zip"
Get-Pinned -Url $DiffusersUrl -Sha256 $DiffusersSha256 -Path $diffusersSource
$wheels = Join-Path $Stage 'wheels'
New-Item -ItemType Directory -Path $wheels | Out-Null
Invoke-Checked $Python @('-m', 'pip', 'wheel', '--no-deps', '--no-cache-dir', '--disable-pip-version-check', '--wheel-dir', $wheels, $diffusersSource)
$built = @(Get-ChildItem -LiteralPath $wheels -Filter 'diffusers-*.whl')
if ($built.Count -ne 1) { throw "Expected one diffusers wheel, found $($built.Count)" }
$wheel = $built[0]
if ($wheel.Name -notmatch '^diffusers-(?<version>[A-Za-z0-9.]+)-py3-none-any\.whl$') {
    throw "The diffusers wheel has an unexpected name: $($wheel.Name)"
}
$diffusersVersion = $Matches['version']
$wheelSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel.FullName).Hash.ToLowerInvariant()

# ---- the lock the app installs from: the repository's, plus the wheel built above

$repoLock = Join-Path $Root 'requirements-app.lock'
if (-not (Test-Path -LiteralPath $repoLock)) { throw 'requirements-app.lock is missing. tools\make_app_lock.py writes it.' }
$lockText = [System.IO.File]::ReadAllText($repoLock).Replace("`r`n", "`n")
if (-not $lockText.EndsWith("`n")) { $lockText += "`n" }
if ($lockText -match '(?m)^diffusers[=@ ]') { throw 'requirements-app.lock names diffusers itself. This build adds that line.' }
$lockText += "diffusers==$diffusersVersion --hash=sha256:$wheelSha256`n"
$stageLock = Join-Path $Stage 'requirements-app.lock'
[System.IO.File]::WriteAllText($stageLock, $lockText, $NoMark)
$lockSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $stageLock).Hash.ToLowerInvariant()

# What tells an app install from a clone. The page checks the lock against this hash
# before it installs anything from it.
$install = [ordered]@{ version = $Version; lock_sha256 = $lockSha256 } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText((Join-Path $Stage 'install.json'), "$install`n", $NoMark)

# ---- what the uninstaller needs to know about the model, read from the code itself

$ask = 'import json; from rutor import app_install, pipeline; ' +
    'm = app_install.load_manifest(); ' +
    'print(json.dumps({"model": pipeline.MODEL_ID, "lora": pipeline.LORA_ID, "gb": -(-m.model // 10**9)}))'
$told = & $Python -c $ask
if ($LASTEXITCODE -ne 0) { throw 'The staged Python could not read the pipeline' }
$model = $told | ConvertFrom-Json
$modelFolder = 'models--' + $model.model.Replace('/', '--')
$loraFolder = 'models--' + $model.lora.Replace('/', '--')

# ---- the installer

# Where Inno Setup 6 puts its compiler: for the whole machine, or for one person.
$iscc = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    $onPath = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if (-not $onPath) { throw 'Inno Setup is not installed: ISCC.exe was not found' }
    $iscc = $onPath.Source
}
Invoke-Checked $iscc @(
    "/DAppVersion=$Version",
    "/DStage=$Stage",
    "/DModelFolder=$modelFolder",
    "/DLoraFolder=$loraFolder",
    "/DModelGb=$($model.gb)",
    "/O$Out",
    (Join-Path $PSScriptRoot 'pixelbygget.iss')
)

$exe = Join-Path $Out "TechEmbassyPixel-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Inno Setup wrote no $exe" }
$size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
# The installer is what a person fetches before the page has said whether their machine
# can draw. It stays small.
if ($size -gt 50) { throw "The installer is $size MB. Nothing over 50 MB is fetched before the machine check." }
Write-Host "built: $exe ($size MB)"
Write-Host "sha256: $((Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash.ToLowerInvariant())"
