[CmdletBinding()]
param(
    [string]$HermesHome = $env:HERMES_HOME,
    [string]$PythonExe = 'python',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$skillInstallArgs = @((Join-Path $PSScriptRoot 'install_skill.py'))
if ($HermesHome) { $skillInstallArgs += @('--hermes-home', $HermesHome) }
if ($DryRun) { $skillInstallArgs += '--dry-run' }
& $PythonExe @skillInstallArgs
exit $LASTEXITCODE
