# Shared startup-contract validation. No strict task, LOCK or checker dependency.

function Get-DefaultStartupTargetConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$WorkflowRoot)

    # The default follows `project_configuration` in STATE.md, never a fixed project name.
    $statePath = Join-Path $WorkflowRoot 'STATE.md'
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        throw "STATE.md is missing; pass -ConfigPath explicitly: $statePath"
    }
    $match = Select-String -LiteralPath $statePath -Pattern '^project_configuration:\s*(\S+)\s*$' |
        Select-Object -First 1
    if (-not $match) { throw 'STATE.md has no project_configuration; pass -ConfigPath explicitly.' }
    $configuration = $match.Matches[0].Groups[1].Value
    if ([IO.Path]::IsPathRooted($configuration)) {
        throw 'STATE.md project_configuration must be relative to .ai-workflow/.'
    }
    $configDir = Split-Path (Join-Path $WorkflowRoot $configuration) -Parent
    return Join-Path $configDir 'startup-targets.json'
}

function Assert-StartupTargetPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    # A string prefix check alone would let a junction or symbolic link inside the project
    # redirect the write elsewhere, so every existing component below project_root must be real.
    $root = $ProjectRoot.TrimEnd('\', '/')
    $current = $Path
    while ($current -and -not $current.TrimEnd('\', '/').Equals($root, [StringComparison]::OrdinalIgnoreCase)) {
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Target $Label passes through a link (junction or symbolic link): $current"
        }
        $current = Split-Path $current -Parent
    }
    if (-not $current) { throw "Target $Label must be a strict descendant of project_root." }
}

function Read-StartupContractConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ConfigPath)

    $resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path
    $configDir = Split-Path $resolvedConfig -Parent
    $config = Get-Content -LiteralPath $resolvedConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($config.schema_version -ne 1) { throw 'Startup target config requires schema_version: 1.' }
    foreach ($key in @('project_root', 'canonical')) {
        $value = $config.$key
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value) -or
            $value -match '[\x00-\x1f]' -or [IO.Path]::IsPathRooted($value)) {
            throw "Config $key must be a nonempty relative path without control characters."
        }
    }
    $projectRoot = [IO.Path]::GetFullPath((Join-Path $configDir $config.project_root))
    if (-not (Test-Path -LiteralPath $projectRoot -PathType Container)) {
        throw "Project root directory is missing: $projectRoot"
    }
    $rootPrefix = $projectRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $canonical = [IO.Path]::GetFullPath((Join-Path $configDir $config.canonical))
    if (-not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
        throw "Canonical startup contract is missing: $canonical"
    }
    if ($config.targets -isnot [array] -or $config.targets.Count -eq 0) {
        throw 'Startup target config requires a nonempty targets array.'
    }

    $labels = @{}
    $paths = @{}
    $targets = @(foreach ($entry in $config.targets) {
        if ($entry.label -isnot [string] -or [string]::IsNullOrWhiteSpace($entry.label) -or
            $entry.label -match '[\x00-\x1f]') {
            throw 'Each target requires a nonempty label without control characters.'
        }
        if ($labels.ContainsKey($entry.label)) { throw "Duplicate target label: $($entry.label)" }
        $labels[$entry.label] = $true
        if ($entry.path -isnot [string] -or [string]::IsNullOrWhiteSpace($entry.path) -or
            $entry.path -match '[\x00-\x1f]' -or [IO.Path]::IsPathRooted($entry.path)) {
            throw "Target $($entry.label) requires a nonempty relative path without control characters."
        }
        $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $entry.path))
        if (-not $path.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Target $($entry.label) must be a strict descendant of project_root."
        }
        if ($path.Equals($canonical, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Target $($entry.label) cannot refer to the canonical file itself."
        }
        if ($paths.ContainsKey($path)) { throw "Duplicate target path: $($entry.label): $path" }
        $paths[$path] = $true
        if (-not (Test-Path -LiteralPath (Split-Path $path -Parent) -PathType Container)) {
            throw "Target parent directory is missing: $($entry.label): $path"
        }
        if (Test-Path -LiteralPath $path -PathType Container) {
            throw "Target is a directory, not a file: $($entry.label): $path"
        }
        Assert-StartupTargetPath -ProjectRoot $projectRoot -Path $path -Label $entry.label
        [pscustomobject]@{ Label = $entry.label; Path = $path }
    })
    return [pscustomobject]@{
        ConfigPath = $resolvedConfig
        ProjectRoot = $projectRoot
        Canonical = $canonical
        Targets = $targets
    }
}

function Test-StartupContractDeployment {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Deployment)

    $expected = (Get-FileHash -LiteralPath $Deployment.Canonical -Algorithm SHA256).Hash.ToLowerInvariant()
    $problems = @(foreach ($target in $Deployment.Targets) {
        if (-not (Test-Path -LiteralPath $target.Path -PathType Leaf)) {
            "$($target.Label):missing"
        } else {
            $actual = (Get-FileHash -LiteralPath $target.Path -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne $expected) { "$($target.Label):drift:$actual" }
        }
    })
    return [pscustomobject]@{
        Passed = ($problems.Count -eq 0)
        CanonicalSha256 = $expected
        TargetCount = $Deployment.Targets.Count
        Problems = $problems
    }
}
