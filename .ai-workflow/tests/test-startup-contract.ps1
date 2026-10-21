[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$toolRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'bin'
. (Join-Path $toolRoot 'startup-contract-common.ps1')
$syncPath = Join-Path $toolRoot 'sync-startup-contract.ps1'
$testCount = 0

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Write-TestConfig {
    param($Targets = $script:validTargets, [string]$Canonical = '../source.md')
    $config = [ordered]@{
        schema_version = 1
        project_root = '..'
        canonical = $Canonical
        targets = @($Targets)
    }
    Write-Utf8NoBom -Path $script:configPath -Text ($config | ConvertTo-Json -Depth 5)
}

function Assert-Check {
    param([string[]]$ExpectedProblems = @())
    $deployment = Read-StartupContractConfig -ConfigPath $script:configPath
    $result = Test-StartupContractDeployment -Deployment $deployment
    if ($result.Passed -ne ($ExpectedProblems.Count -eq 0) -or
        $result.Problems.Count -ne $ExpectedProblems.Count) {
        throw "Unexpected check result: $($result | ConvertTo-Json -Compress)"
    }
    foreach ($expected in $ExpectedProblems) {
        if (-not @($result.Problems | Where-Object { $_ -like "$expected*" }).Count) {
            throw "Missing per-file diagnostic: $expected"
        }
    }
    $script:testCount++
}

function Assert-ConfigRejected {
    param([string]$Pattern)
    $message = $null
    try { Read-StartupContractConfig -ConfigPath $script:configPath | Out-Null }
    catch { $message = $_.Exception.Message }
    if ($null -eq $message -or $message -notmatch $Pattern) {
        throw "Expected rejected config /$Pattern/; actual: $message"
    }
    $script:testCount++
}

function Invoke-SyncProcess {
    param([string]$ScriptPath, [string[]]$ScriptArguments)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $ScriptPath @ScriptArguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    return [pscustomobject]@{ ExitCode = $exitCode; Text = ($output -join "`n") }
}

$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempRoot = [IO.Path]::GetFullPath((Join-Path $tempBase ('startup-contract-' + [guid]::NewGuid().ToString('N'))))
$tempPrefix = $tempBase.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if (-not $tempRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Temporary test directory escaped the OS temp root: $tempRoot"
}

try {
    # Deliberately a different layout from the shipped example project.
    [IO.Directory]::CreateDirectory((Join-Path $tempRoot 'configuration')) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $tempRoot 'application\agent-docs')) | Out-Null
    $script:configPath = Join-Path $tempRoot 'configuration\targets.json'
    $canonical = Join-Path $tempRoot 'source.md'
    $first = Join-Path $tempRoot 'AGENTS.md'
    $second = Join-Path $tempRoot 'application\agent-docs\CLAUDE.md'
    $script:validTargets = @(
        @{ label = 'top'; path = 'AGENTS.md' },
        @{ label = 'nested'; path = 'application/agent-docs/CLAUDE.md' }
    )
    foreach ($path in @($canonical, $first, $second)) {
        Write-Utf8NoBom -Path $path -Text "canonical startup contract`n"
    }
    Write-TestConfig
    Assert-Check

    foreach ($path in @($first, $second)) { Write-Utf8NoBom -Path $path -Text 'same stale contents' }
    Assert-Check -ExpectedProblems @('top:drift:', 'nested:drift:')
    [IO.File]::Delete($second)
    Assert-Check -ExpectedProblems @('top:drift:', 'nested:missing')

    $syncOutput = Invoke-SyncProcess -ScriptPath $syncPath -ScriptArguments @('-Apply', '-ConfigPath', $configPath)
    if ($syncOutput.ExitCode -ne 0 -or $syncOutput.Text -notmatch '^STARTUP_CONTRACT_SYNC_OK 2/2 ') {
        throw "Apply subprocess failed: $($syncOutput.Text)"
    }
    Assert-Check
    $checkOutput = Invoke-SyncProcess -ScriptPath $syncPath -ScriptArguments @('-Check', '-ConfigPath', $configPath)
    if ($checkOutput.ExitCode -ne 0 -or $checkOutput.Text -notmatch '^STARTUP_CONTRACT_CHECK_OK 2/2 ') {
        throw "Check subprocess failed: $($checkOutput.Text)"
    }
    $testCount++

    Write-TestConfig -Targets @()
    Assert-ConfigRejected -Pattern 'nonempty targets'
    $emptyOutput = Invoke-SyncProcess -ScriptPath $syncPath -ScriptArguments @('-Check', '-ConfigPath', $configPath)
    if ($emptyOutput.ExitCode -eq 0 -or $emptyOutput.Text -notmatch 'nonempty targets') {
        throw "Empty config subprocess did not fail correctly: $($emptyOutput.Text)"
    }
    $testCount++
    Write-TestConfig -Targets @(@{label='a';path='AGENTS.md'}, @{label='b';path='./AGENTS.md'})
    Assert-ConfigRejected -Pattern 'Duplicate target path'
    Write-TestConfig -Targets @(@{label='a';path='AGENTS.md'}, @{label='a';path='application/agent-docs/CLAUDE.md'})
    Assert-ConfigRejected -Pattern 'Duplicate target label'
    Write-TestConfig -Targets @(@{label='self';path='source.md'})
    Assert-ConfigRejected -Pattern 'canonical file itself'
    Write-TestConfig -Targets @(@{label='escape';path='../AGENTS.md'})
    Assert-ConfigRejected -Pattern 'strict descendant'
    Write-TestConfig -Targets @(@{label='root';path='.'})
    Assert-ConfigRejected -Pattern 'strict descendant'
    Write-TestConfig -Targets @(@{label='wrong-layout';path='missing-repo/AGENTS.md'})
    Assert-ConfigRejected -Pattern 'parent directory is missing'
    Write-TestConfig -Targets @(@{label='directory';path='application'})
    Assert-ConfigRejected -Pattern 'directory, not a file'
    Write-TestConfig -Canonical '../missing-canonical.md'
    Assert-ConfigRejected -Pattern 'Canonical startup contract is missing'

    # Invalid later target must be caught before Apply changes the first target.
    Write-Utf8NoBom -Path $first -Text 'must remain untouched'
    Write-TestConfig -Targets @(@{label='valid';path='AGENTS.md'}, @{label='invalid';path='../outside.md'})
    $rejected = $false
    try { & $syncPath -Apply -ConfigPath $configPath | Out-Null } catch { $rejected = $true }
    if (-not $rejected -or [IO.File]::ReadAllText($first) -ne 'must remain untouched') {
        throw 'Invalid topology was not rejected before the first Apply write.'
    }
    $testCount++

    # A junction below project_root must not redirect a target, even when the path string stays
    # inside project_root. The junction points inside the temp tree so cleanup cannot reach out.
    $linkTarget = Join-Path $tempRoot 'application\agent-docs'
    $junction = Join-Path $tempRoot 'linked'
    New-Item -ItemType Junction -Path $junction -Target $linkTarget | Out-Null
    try {
        Write-Utf8NoBom -Path $second -Text 'behind the junction'
        Write-TestConfig -Targets @(@{label='valid';path='AGENTS.md'}, @{label='linked';path='linked/CLAUDE.md'})
        Assert-ConfigRejected -Pattern 'passes through a link'
        $rejected = $false
        try { & $syncPath -Apply -ConfigPath $configPath | Out-Null } catch { $rejected = $true }
        if (-not $rejected -or [IO.File]::ReadAllText($first) -ne 'must remain untouched' -or
            [IO.File]::ReadAllText($second) -ne 'behind the junction') {
            throw 'A target behind a junction was not rejected before the first Apply write.'
        }
        $testCount++
    } finally {
        # Remove only the link itself, never its contents.
        [IO.Directory]::Delete($junction, $false)
    }

    # Exercise -File without -ConfigPath in Windows PowerShell 5.1: PSScriptRoot
    # must be used in the script body, not while binding parameter defaults. The default
    # comes from STATE.md project_configuration, here deliberately not projects/example.
    $fixtureBin = Join-Path $tempRoot 'tools\bin'
    $fixtureConfigDir = Join-Path $tempRoot 'tools\projects\sample'
    $fixtureState = Join-Path $tempRoot 'tools\STATE.md'
    [IO.Directory]::CreateDirectory($fixtureBin) | Out-Null
    [IO.Directory]::CreateDirectory($fixtureConfigDir) | Out-Null
    foreach ($name in @('sync-startup-contract.ps1', 'startup-contract-common.ps1')) {
        Copy-Item -LiteralPath (Join-Path $toolRoot $name) -Destination (Join-Path $fixtureBin $name)
    }
    $fixtureConfig = [ordered]@{
        schema_version = 1
        project_root = '../../..'
        canonical = '../../../source.md'
        targets = $validTargets
    }
    Write-Utf8NoBom -Path (Join-Path $fixtureConfigDir 'startup-targets.json') `
        -Text ($fixtureConfig | ConvertTo-Json -Depth 5)
    $fixtureSync = Join-Path $fixtureBin 'sync-startup-contract.ps1'
    $noState = Invoke-SyncProcess -ScriptPath $fixtureSync -ScriptArguments @('-Check')
    if ($noState.ExitCode -eq 0 -or $noState.Text -notmatch 'STATE.md is missing') {
        throw "Missing STATE.md did not fail correctly: $($noState.Text)"
    }
    $testCount++
    Write-Utf8NoBom -Path $fixtureState -Text "~~~yaml`nproject_configuration: projects/sample/README.md`n~~~`n"
    $defaultApply = Invoke-SyncProcess -ScriptPath $fixtureSync -ScriptArguments @('-Apply')
    if ($defaultApply.ExitCode -ne 0 -or $defaultApply.Text -notmatch '^STARTUP_CONTRACT_SYNC_OK 2/2 ') {
        throw "Default config Apply subprocess failed: $($defaultApply.Text)"
    }
    $testCount++
    $defaultCheck = Invoke-SyncProcess -ScriptPath $fixtureSync -ScriptArguments @('-Check')
    if ($defaultCheck.ExitCode -ne 0 -or $defaultCheck.Text -notmatch '^STARTUP_CONTRACT_CHECK_OK 2/2 ') {
        throw "Default config Check subprocess failed: $($defaultCheck.Text)"
    }
    $testCount++
    Write-Output "STARTUP_CONTRACT_TESTS_OK $testCount/$testCount"
}
finally {
    if ([IO.Directory]::Exists($tempRoot)) {
        if (-not $tempRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove non-temp path: $tempRoot"
        }
        [IO.Directory]::Delete($tempRoot, $true)
    }
}
