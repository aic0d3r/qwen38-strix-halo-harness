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
  [switch]$Yes
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

function Winget-Install($id, $what) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Write-Host "  winget not found: install $what yourself, then re-run"; return $false }
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

if (-not (Get-Command pi -ErrorAction SilentlyContinue)) {
  Write-Host "pi is not installed. Install it first:  irm https://pi.dev/install.ps1 | iex   then re-run this script."
  exit 1
}

# pi's bash tool (and every guard in extensions\) needs Git Bash on native Windows
if (-not (Have-GitBash)) {
  Write-Host "Git for Windows (Git Bash) not found: pi's bash tool needs it."
  [void](Winget-Install 'Git.Git' 'Git for Windows')
}

$py = Find-Python
if (-not $py) {
  Write-Host "Python 3 not found (the Microsoft Store stub does not count)."
  if (Winget-Install 'Python.Python.3.12' 'Python 3.12') { $py = Find-Python }
}
if (-not $py) {
  Write-Host "Python 3 is still not available in this window. Install it (https://www.python.org/downloads/), open a new terminal and re-run."
  exit 1
}

$pre = $py.Pre
& $py.Exe @pre (Join-Path $PSScriptRoot 'setup\adopt.py') all --engine $Engine --url $Url
$code = $LASTEXITCODE

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
