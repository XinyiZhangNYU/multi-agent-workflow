[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Apply,
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
if ($Check -and $Apply) { throw 'Choose exactly one of -Check or -Apply.' }
if (-not $Check -and -not $Apply) { $Check = $true }

. (Join-Path $PSScriptRoot 'startup-contract-common.ps1')
if (-not $ConfigPath) {
    $ConfigPath = Get-DefaultStartupTargetConfig -WorkflowRoot (Split-Path $PSScriptRoot -Parent)
}
$deployment = Read-StartupContractConfig -ConfigPath $ConfigPath

# -Apply is for an explicitly authorized canonical/deployment update only.
# Validate the entire target configuration before writing the first file,
# and re-check each target's path for links right before it is written.
if ($Apply) {
    foreach ($target in $deployment.Targets) {
        Assert-StartupTargetPath -ProjectRoot $deployment.ProjectRoot -Path $target.Path -Label $target.Label
        Copy-Item -LiteralPath $deployment.Canonical -Destination $target.Path -Force
    }
}

$result = Test-StartupContractDeployment -Deployment $deployment
if (-not $result.Passed) {
    throw "Startup contract deployment differs from canonical $($result.CanonicalSha256): $($result.Problems -join ', ')"
}
if ($Apply) {
    Write-Output "STARTUP_CONTRACT_SYNC_OK $($result.TargetCount)/$($result.TargetCount) $($result.CanonicalSha256)"
} else {
    Write-Output "STARTUP_CONTRACT_CHECK_OK $($result.TargetCount)/$($result.TargetCount) $($result.CanonicalSha256)"
}
