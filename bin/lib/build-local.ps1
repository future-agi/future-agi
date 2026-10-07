# The Future AGI images built from this checkout and tagged `local`, for
# .\bin\install.ps1 -FromSource: the recipe of bin/lib/build-local.sh.
# Dot-sourced; needs the installer's output helpers and Append-Log.

function Build-Image {
  param([string]$Tag, [string[]]$BuildArgs)
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
