<#
.SYNOPSIS
Explicit, bounded historical backfill for the unified property catalog.

.DESCRIPTION
Runs the management command from the exact property-catalog-supervisor image
already selected by the current Compose deployment. It performs no source-
control checkout and no image pull.
#>

[CmdletBinding()]
param(
  [switch]$Execute,
  [int]$WallMs = 0,
  [switch]$Help
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
  @'
Usage: .\bin\property-catalog-backfill.ps1 -Execute [-WallMs MILLISECONDS]

Backfill inactive workspace catalogs from the existing deployed data. Already
active catalogs are skipped; their normal supervisor continues incremental
reconciliation. The operation is bounded and resumable by the catalog ledger.

This command never checks out a branch, pulls source code, or pulls an image.
'@ | Write-Host
}

if ($Help) {
  Show-Usage
  exit 0
}
if (-not $Execute) {
  Write-Error 'Refusing historical writes without -Execute.'
  Show-Usage
  exit 64
}

if ($WallMs -eq 0) {
  if ($env:PROPERTY_CATALOG_INITIAL_BACKFILL_WALL_MS) {
    $parsedWallMs = 0
    if (-not [int]::TryParse(
        $env:PROPERTY_CATALOG_INITIAL_BACKFILL_WALL_MS,
        [ref]$parsedWallMs
      )) {
      Write-Error 'PROPERTY_CATALOG_INITIAL_BACKFILL_WALL_MS must be an integer.'
      exit 64
    }
    $WallMs = $parsedWallMs
  } else {
    $WallMs = 1740000
  }
}
if ($WallMs -lt 100 -or $WallMs -gt 1740000) {
  Write-Error '-WallMs must be in [100, 1740000].'
  exit 64
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ComposeKind = $null
& docker compose version *> $null
if ($LASTEXITCODE -eq 0) {
  $ComposeKind = 'plugin'
} elseif (Get-Command docker-compose -ErrorAction SilentlyContinue) {
  $ComposeKind = 'standalone'
} else {
  Write-Error 'Docker Compose is required.'
  exit 69
}

function Invoke-Compose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  if ($ComposeKind -eq 'plugin') {
    & docker compose @Arguments
  } else {
    & docker-compose @Arguments
  }
}

$RequiredServices = @(
  'postgres',
  'clickhouse',
  'property-catalog-kafka',
  'fi-collector',
  'fi-property-catalog-sequencer',
  'fi-property-catalog-consumer'
)
foreach ($Service in $RequiredServices) {
  $ContainerId = (Invoke-Compose ps -q $Service 2>$null | Select-Object -First 1)
  if (-not $ContainerId) {
    Write-Error "Required service is not created: $Service. Start the deployed stack first."
    exit 69
  }
  $Status = (& docker inspect --format '{{.State.Status}}' $ContainerId 2>$null)
  if ($Status -ne 'running') {
    Write-Error "Required service is not running: $Service (status=$Status)."
    exit 69
  }
}

Write-Host 'Starting explicit unified property-catalog backfill.'
Write-Host "Per-workspace wall allowance: $WallMs ms"
Write-Host 'Already-active workspaces will be skipped.'

Invoke-Compose run --rm --no-deps -T `
  --entrypoint python `
  property-catalog-supervisor `
  manage.py ch25_property_catalog_oss_supervisor `
  --once `
  --initial-backfill `
  --initial-backfill-wall-ms $WallMs
exit $LASTEXITCODE
