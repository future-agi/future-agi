# The Future AGI images built from this checkout and tagged `local`, for
# .\bin\install.ps1 -FromSource: the recipe of bin/lib/build-local.sh.
# Dot-sourced; needs the installer's output helpers, Append-Log and
# Invoke-Probe.

# FUTURE_AGI_REVISION overrides the checkout revision; use a full lowercase
# commit SHA. Archives without either keep the Dockerfile's default.
function Build-Image {
  param([string]$Tag, [string[]]$BuildArgs)
  $Revision = $env:FUTURE_AGI_REVISION
  if (-not [string]::IsNullOrEmpty($Revision)) {
    if ($Revision.Length -ne 40 -or $Revision -cnotmatch '^[0-9a-f]{40}$') {
      Die 'FUTURE_AGI_REVISION must be a full 40-character lowercase hexadecimal commit SHA.'
    }
  } else {
    $Revision = $null
    try {
      $head = & git rev-parse --verify -q HEAD 2>$null
      if ($LASTEXITCODE -eq 0) { $Revision = $head }
    } catch {
      # Git may be unavailable for an archive install.
      $Revision = $null
    }
  }
  if ($Revision) {
    $BuildArgs = @('--build-arg', "REVISION=$Revision", '--label', "org.opencontainers.image.revision=$Revision") + $BuildArgs
  }
  Say "  building $Tag"
  Append-Log @("running: docker build -t $Tag $($BuildArgs -join ' ')")
  & docker build -t $Tag @BuildArgs
  if ($LASTEXITCODE -ne 0) { Die "Building $Tag failed; the build output above has the reason." }
  Ok "Built $Tag"
}

# Standalone's app image is assembled from the other four on the slim backend
# variant, as the published futureagi/standalone is; Distributed runs the
# default variant.
function Build-LocalImages {
  param([bool]$Distributed)
  $backendVariant = @()
  if (-not $Distributed) { $backendVariant = @('--build-arg', 'IMAGE_VARIANT=slim') }
  Build-Image 'futureagi/future-agi:local' (@('-f', 'futureagi/Dockerfile.oss') + $backendVariant + @('futureagi'))
  Build-Image 'futureagi/frontend:local' @('frontend')
  Build-Image 'futureagi/fi-collector:local' @('fi-collector')
  Build-Image 'futureagi/agentcc-gateway:local' @('agentcc-gateway')
  if (-not $Distributed) {
    Build-Image 'futureagi/standalone:local' @(
      '-f', 'deploy/standalone/Dockerfile',
      '--build-arg', 'BACKEND_IMAGE=futureagi/future-agi:local',
      '--build-arg', 'FRONTEND_IMAGE=futureagi/frontend:local',
      '--build-arg', 'FI_COLLECTOR_IMAGE=futureagi/fi-collector:local',
      '--build-arg', 'AGENTCC_GATEWAY_IMAGE=futureagi/agentcc-gateway:local',
      'deploy/standalone'
    )
  }
}

# The code-evaluation sandbox, tagged `local`. The stack runs it as a non-root
# user on a read-only root filesystem, settings a published executor of another
# release may not support. Its Dockerfile builds FROM the base image it pins
# (ARG CODE_EXECUTOR_BASE); a pin that is neither on this host nor published yet
# is built first, under that tag, from Dockerfile.base.
function Build-LocalCodeExecutor {
  $base = $null
  foreach ($line in @(Get-Content -LiteralPath 'futureagi/code-executor/Dockerfile' -ErrorAction SilentlyContinue)) {
    if ($line -cmatch '^ARG CODE_EXECUTOR_BASE=(.*)$') { $base = $matches[1]; break }
  }
  if ([string]::IsNullOrEmpty($base)) {
    Die 'futureagi/code-executor/Dockerfile pins no CODE_EXECUTOR_BASE, so the code executor cannot be built from this checkout.'
  }
  Invoke-Probe { docker image inspect $base } | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Invoke-Probe { docker manifest inspect $base } | Out-Null
    if ($LASTEXITCODE -ne 0) {
      Build-Image $base @('-f', 'futureagi/code-executor/Dockerfile.base', 'futureagi/code-executor')
    }
  }
  Build-Image 'futureagi/code-executor:local' @('futureagi/code-executor')
}
