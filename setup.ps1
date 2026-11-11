<#
.SYNOPSIS
  Windows installer: sets pi up (extensions, provider, skills, MCP) for a Strata or gufo that is already running.
  The work is done by setup\adopt.py (shared with Linux); this script only finds Python and offers the prerequisites.
  It never installs or starts an engine: use Strata's own installer for that.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup.ps1
  powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Engine gufo -Url http://192.168.1.20:8080
#>
param(
  [ValidateSet('strata', 'gufo')][string]$Engine = 'strata',
  [string]$Url = 'http://127.0.0.1:8080',
  [switch]$Yes,
  [switch]$NoPi,
  [switch]$Pi
)
# Continue, not Stop: Windows PowerShell 5.1 turns a native command's stderr into a terminating error under Stop
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

function Ask($question) {
  if ($Yes) { return $true }
  return ((Read-Host "$question [y/N]") -match '^[yY]')
}

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Winget-Install($id, $what, $link) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Write-Host "  winget not found: install $what yourself ($link), then re-run"; return $false }
  if (-not (Ask "Install $what with winget ($id)?")) { return $false }
  winget install --id $id -e --accept-package-agreements --accept-source-agreements
  Refresh-Path
  return ($LASTEXITCODE -eq 0)
}

function Find-Python {
  foreach ($name in @('py', 'python', 'python3')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $pre = @(); if ($name -eq 'py') { $pre = @('-3') }
    # the Microsoft Store stub answers `python` with a hint and exit 9009; a real Python prints its version
    $out = & $cmd.Source @pre --version 2>&1
    if ($LASTEXITCODE -eq 0 -and "$out" -match '^Python 3') { return @{ Exe = $cmd.Source; Pre = $pre } }
  }
  return $null
}

function Have-GitBash {
  foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($root -and (Test-Path (Join-Path $root 'Git\bin\bash.exe'))) { return $true }
  }
  return [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

# the engine is installed and started with its own tools; here we only check it answers (never install or start it)
function Test-Engine { try { [void](Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$($Url.TrimEnd('/'))/v1/models"); return $true } catch { return $false } }
while (-not (Test-Engine)) {
  Write-Host "No engine answering at $Url. Install and start Strata with its own installer (https://github.com/Niko1221/Strata), or gufo, then check:  curl.exe -s $Url/v1/models"
  if ($Yes -or -not (Ask 'Started it? Check again')) { Write-Host "  continuing without an engine; start it before using your agent"; break }
}

# which agent gets wired: pi (default) or none (engine only). Saved in the file Linux's setup.sh reads too; -Pi / -NoPi override it.
$agentDir = if ($env:PI_CODING_AGENT_DIR) { $env:PI_CODING_AGENT_DIR } elseif ($env:PI_HOME) { Join-Path $env:PI_HOME 'agent' } else { Join-Path $env:USERPROFILE '.pi\agent' }
$agentPref = Join-Path $agentDir 'harness-agent'
if ($Pi -and $NoPi) { Write-Host '-Pi and -NoPi together make no sense'; exit 2 }
if ($NoPi -or $Pi) { $choice = if ($NoPi) { 'none' } else { 'pi' } }
elseif (Test-Path $agentPref) { $choice = (Get-Content $agentPref -TotalCount 1).Trim() }
elseif ($Yes) { $choice = 'pi' }
else { $choice = if ((Read-Host 'Wire pi as your coding agent? (No = engine only; point opencode or another OpenAI-compatible agent at it) [Y/n]') -match '^[nN]') { 'none' } else { 'pi' } }
New-Item -ItemType Directory -Force -Path $agentDir | Out-Null
Set-Content -Path $agentPref -Value $choice
$wirePi = $choice -ne 'none'

if ($wirePi) {
  if (-not (Get-Command pi -ErrorAction SilentlyContinue)) {
    Write-Host "pi is not installed. Install it first:  irm https://pi.dev/install.ps1 | iex   then re-run this script."
    exit 1
  }

  # pi's bash tool (and every guard in extensions\) needs Git Bash on native Windows
  if (-not (Have-GitBash)) {
    Write-Host "Git for Windows (Git Bash) not found: pi's bash tool needs it."
    [void](Winget-Install 'Git.Git' 'Git for Windows' 'https://git-scm.com/download/win')
  }

  $py = Find-Python
  if (-not $py) {
    Write-Host "Python 3 not found (the Microsoft Store stub does not count)."
    if (Winget-Install 'Python.Python.3.12' 'Python 3.12' 'https://www.python.org/downloads/') { $py = Find-Python }
  }
  if (-not $py) {
    Write-Host "Python 3 is still not available in this window. Install it (https://www.python.org/downloads/), open a new terminal and re-run."
    exit 1
  }
} else { Write-Host "pi not wired (-NoPi): skipping pi, Git Bash and Python checks" }

# the semble and ddg search tools run through uvx; Serena (symbol-level code navigation) is a uv tool. uv's installer refuses the
# default Restricted execution policy, hence the ByPass child process (uv's own documented way).
$localBin = Join-Path $env:USERPROFILE '.local\bin'
if (-not (Get-Command uvx -ErrorAction SilentlyContinue) -and (Ask 'Install uv (runs the semble and ddg search tools)?')) {
  powershell -NoProfile -ExecutionPolicy ByPass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
  Refresh-Path
  if (($env:Path -split ';') -notcontains $localBin) { $env:Path = $env:Path + ';' + $localBin }
}
if ((Get-Command uv -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $localBin 'serena.exe')) -and (Ask 'Install Serena (symbol-level code navigation for the agent)?')) {
  uv tool install -p 3.13 serena-agent
}

if ($wirePi) {
  $pre = $py.Pre
  & $py.Exe @pre (Join-Path $PSScriptRoot 'setup\adopt.py') all --engine $Engine --url $Url
  $code = $LASTEXITCODE
} else {
  $code = 0
  Write-Host "engine endpoint (OpenAI-compatible, key: any): $($Url.TrimEnd('/'))/v1   (re-enable pi: .\setup.ps1 -Pi)"
}

# rtk lands in ~\.local\bin; pi-rtk-optimizer warns until that is on PATH
$bin = Join-Path $env:USERPROFILE '.local\bin'
if ($code -eq 0 -and (Test-Path (Join-Path $bin 'rtk.exe'))) {
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  if (-not $user) { $user = '' }
  if (($user -split ';') -notcontains $bin -and (Ask "Add $bin to your user PATH (for rtk)?")) {
    [Environment]::SetEnvironmentVariable('Path', ($user.TrimEnd(';') + ';' + $bin), 'User')
    Write-Host "  added; open a new terminal for it to take effect"
  }
}
exit $code
