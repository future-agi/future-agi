<#
.SYNOPSIS
  Future AGI - self-hosted installer for Windows / PowerShell.

.DESCRIPTION
  PowerShell counterpart of bin/install. Sets up .env (a fresh install gets
  generated secrets; an existing install's values are never changed), runs
  docker compose pull + up, waits for health, and prints the URLs to open.
  Re-running is idempotent.

  The standalone install is docker-compose.yml: one app container plus
  Postgres and ClickHouse. -Distributed records COMPOSE_FILE=docker-compose.distributed.yml
  in .env, so later runs and a plain `docker compose` stay on the distributed stack.
  An existing install keeps its stack: moving one between standalone and distributed
  is refused, because its data does not carry over. So is a COMPOSE_FILE set in
  your shell that does not start the install's stack: Compose reads it over .env.

.PARAMETER Distributed
  Run the Distributed setup, one container per service
  (docker-compose.distributed.yml), instead of the Standalone one.

.PARAMETER FromSource
  Build the Future AGI images from this checkout instead of pulling them.
  Needed for a dev branch: published images are built from main.

.PARAMETER Force
  Continue past failed preflight checks (Docker memory, path visibility).

.PARAMETER SkipUserCreation
  Don't prompt for the first admin user.

.PARAMETER NoUp
  Bootstrap .env only; don't pull or start the stack.

.PARAMETER WipeVolumes
  Explicitly stop this Compose project and remove only its inventoried named
  volumes, including Kafka and collector spool state. Existing data is deleted.
  With -Distributed this is also how an existing standalone install becomes a distributed one.

.PARAMETER NonInteractive
  CI / unattended. Reads FAGI_ADMIN_EMAIL, FAGI_ADMIN_NAME,
  FAGI_ADMIN_PASSWORD from env if you want a user auto-created.

.PARAMETER NoTelemetry
  Turn deployment telemetry off: writes FUTURE_AGI_TELEMETRY_DISABLED=true to
  .env before anything starts
  (https://docs.futureagi.com/docs/self-hosting/configuration/telemetry).

.EXAMPLE
  .\bin\install.ps1
  .\bin\install.ps1 -Distributed
  .\bin\install.ps1 -FromSource
  .\bin\install.ps1 -NoUp
  .\bin\install.ps1 -WipeVolumes
  .\bin\install.ps1 -NonInteractive
  .\bin\install.ps1 -NoTelemetry
#>

[CmdletBinding()]
param(
  [Alias('Full')][switch]$Distributed,
  [switch]$FromSource,
  [switch]$Force,
  [switch]$SkipUserCreation,
  [switch]$NoUp,
  [switch]$WipeVolumes,
  [switch]$NonInteractive,
  [switch]$NoTelemetry
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'lib/env.ps1')
. (Join-Path $PSScriptRoot 'lib/secrets.ps1')
. (Join-Path $PSScriptRoot 'lib/ports.ps1')
. (Join-Path $PSScriptRoot 'lib/summary.ps1')
. (Join-Path $PSScriptRoot 'lib/build-local.ps1')

$timestamp = (Get-Date -Format 'yyyyMMdd-HHmmss')
$LogFile = Join-Path $Root "install-$timestamp.log"

function Append-Log {
  param([string[]]$Lines)
  $utf8 = [System.Text.UTF8Encoding]::new($false)
  [System.IO.File]::AppendAllLines($LogFile, [string[]]$Lines, $utf8)
}

Append-Log @(
  "# Future AGI install log - $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))",
  "# host: $(hostname), ps: $($PSVersionTable.PSVersion)",
  ""
)

function Say  { param([string]$Msg) Write-Host $Msg; Append-Log @($Msg) }
function Step { param([string]$Msg) Write-Host ""; Write-Host "==> $Msg" -ForegroundColor Blue; Append-Log @("", "==> $Msg") }
function Ok   { param([string]$Msg) Write-Host "  $([char]0x2713) $Msg" -ForegroundColor Green; Append-Log @("  [ok]   $Msg") }
function Warn { param([string]$Msg) Write-Host "  ! $Msg" -ForegroundColor Yellow; Append-Log @("  [warn] $Msg") }
function Die  { param([string]$Msg) Write-Host ""; Write-Host "$([char]0x2717) $Msg" -ForegroundColor Red; Append-Log @("[fail] $Msg"); exit 1 }

# A failed preflight check stops the install unless -Force was given.
function Fail-Preflight {
  param([string]$Msg)
  if ($Force) { Warn "$Msg (continuing: -Force)" } else { Die "$Msg (re-run with -Force to continue anyway)" }
}

# Windows PowerShell 5.1 turns a native command's redirected stderr into
# errors, which 'Stop' makes fatal. Probes that may fail run through this;
# their exit code is still in $LASTEXITCODE.
function Invoke-Probe {
  param([scriptblock]$Command)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $Command 2>$null } finally { $ErrorActionPreference = $saved }
}

# ---- welcome ----
Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Blue
Write-Host "  |   Future AGI . self-hosted installer       |" -ForegroundColor Blue
Write-Host "  +-------------------------------------------+" -ForegroundColor Blue
$recordedDistributed = (Test-Path '.env') -and [bool](Select-String -Path '.env' -Pattern '^COMPOSE_FILE=.*docker-compose\.distributed\.yml' -Quiet)
if ($Distributed -or $recordedDistributed) {
  Write-Host "  mode: Distributed . one container per service" -ForegroundColor DarkGray
} else {
  Write-Host "  mode: Standalone . pass -Distributed for one container per service" -ForegroundColor DarkGray
}
Write-Host ""

# ---- preflight ----
Step "Preflight"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Die "docker not found in PATH. Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
  Die "Docker is installed but the daemon isn't running. Start Docker Desktop and re-run."
}
Ok "Docker daemon reachable"

$DcCmd = $null
$DcArgs = @()
& docker compose version *> $null
if ($LASTEXITCODE -eq 0) {
  $DcCmd = 'docker'
  $DcArgs = @('compose')
} elseif (Get-Command 'docker-compose' -ErrorAction SilentlyContinue) {
  $DcCmd = 'docker-compose'
  Warn "Using legacy docker-compose v1. Consider upgrading to Compose v2 (built into Docker Desktop)."
} else {
  Die "Neither 'docker compose' nor 'docker-compose' is available."
}
$DcText = (@($DcCmd) + $DcArgs) -join ' '
$composeVersion = & $DcCmd @DcArgs version --short 2>$null
Ok "Compose: $composeVersion"

function Invoke-Compose {
  & $DcCmd @DcArgs @args
}

# ---- .env ----
Step "Configuring .env"

if (-not (Test-Path '.env.example')) {
  Die ".env.example missing -- are you running this from the repo root?"
}

if (-not (Test-Path '.env')) {
  Copy-Item '.env.example' '.env'
  Write-EnvLines (Read-EnvLines)  # rewrite without any inherited BOM
  Ok "Created .env from .env.example"
} else {
  $existingLines = Read-EnvLines
  $existingKeys = @{}
  foreach ($line in $existingLines) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=') { $existingKeys[$matches[1]] = $true }
  }
  $added = 0
  $newLines = @() + $existingLines
  foreach ($line in [System.IO.File]::ReadAllLines('.env.example')) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=' -and -not $existingKeys.ContainsKey($matches[1])) {
      $newLines += $line
      $added++
    }
  }
  if ($added -gt 0) {
    Write-EnvLines $newLines
    Ok ".env exists -- added $added new key(s) from .env.example"
  } else {
    Ok ".env already exists (existing values preserved)"
  }
}

# ---- telemetry and unsafe settings ----
# Written before anything starts, so the first account's registration is
# already the minimal opt-out one.
if ($NoTelemetry) {
  Set-EnvValue 'FUTURE_AGI_TELEMETRY_DISABLED' 'true'
  Ok "FUTURE_AGI_TELEMETRY_DISABLED=true written to .env: telemetry is off"
}
# .env files made from an older .env.example turned this on.
if ((Get-EnvValue 'OSS_RETURN_PASSWORD_RESET_LINK') -eq 'true') {
  Warn "OSS_RETURN_PASSWORD_RESET_LINK=true hands password-reset links to anyone who can reach the API, so anyone can take over any account. Delete it from .env unless every user of this host is trusted (INSTALLATION.md > Reset a password)."
}

function Test-TelemetryOff {
  $value = Get-ComposeEnvValue 'FUTURE_AGI_TELEMETRY_DISABLED'
  return ("$value" -match '^(1|true|yes|on)$')
}

# What deployment telemetry sends, before any account (and so any admin
# email) exists. Worded as the telemetry page,
# https://docs.futureagi.com/docs/self-hosting/configuration/telemetry.
function Show-TelemetryNotice {
  $url = Get-EnvValue 'FUTURE_AGI_TELEMETRY_URL'
  if (-not $url) { $url = 'https://api.futureagi.com' }
  $telemetryHost = ($url -replace '^[a-zA-Z][a-zA-Z0-9+.-]*://', '') -replace '/.*$', ''
  $hours = Get-EnvValue 'FUTURE_AGI_TELEMETRY_INTERVAL_HOURS'
  if (-not $hours) { $hours = 6 }
  Step "Telemetry"
  if (Test-TelemetryOff) {
    Ok "Off (FUTURE_AGI_TELEMETRY_DISABLED=true). One minimal registration still goes to ${telemetryHost}: instance id, version, deployment type, timestamp."
  } else {
    Say "  When the first account is created, this install registers with ${telemetryHost}:"
    Say "  instance id, version, deployment type, and the emails and domains of owners,"
    Say "  admins and staff/superuser accounts. Then it"
    Say "  sends usage counts every $hours h; never traces, prompts or other content."
    Say "  Opt out: -NoTelemetry, or FUTURE_AGI_TELEMETRY_DISABLED=true in .env. One minimal"
    Say "  registration remains: instance id, version, deployment type, timestamp."
  }
  Say "  Details: https://docs.futureagi.com/docs/self-hosting/configuration/telemetry"
}

# ---- this project's existing state ----
# Compose names volumes <project>_<volume> and labels containers with their
# project and service, so both show whether this project already holds an
# install, and of which stack. Deletion happens only behind the explicit
# -WipeVolumes switch and targets exact Compose volume names; no wildcard or
# broad Docker cleanup command is used.
# The environment, then .env, then the compose files' `name:`.
$projectName = Get-ComposeEnvValue 'COMPOSE_PROJECT_NAME' 'futureagi'
$persistentVolumeSuffixes = @(
  'app-data',
  'postgres-data',
  'clickhouse-data',
  'minio-data',
  'redis-data',
  'rabbitmq-data',
  'peerdb-catalog-data',
  'peerdb-minio-data',
  'property-catalog-kafka-data',
  'fi-collector-data'
)
# Volumes and services only the distributed stack has. rabbitmq-data and a rabbitmq
# container come from releases before Redis carried live updates.
$distributedOnlyVolumeSuffixes = @('minio-data', 'redis-data', 'rabbitmq-data', 'peerdb-catalog-data',
                            'peerdb-minio-data', 'property-catalog-kafka-data', 'fi-collector-data')
$distributedOnlyServices = @('backend', 'worker', 'frontend', 'minio', 'redis', 'rabbitmq', 'temporal',
                      'fi-collector', 'agentcc-gateway', 'peerdb-server')
$existingVolumes = @()
foreach ($suffix in $persistentVolumeSuffixes) {
  $volumeName = "${projectName}_${suffix}"
  Invoke-Probe { docker volume inspect $volumeName } | Out-Null
  if ($LASTEXITCODE -eq 0) { $existingVolumes += $volumeName }
}
$wipe = $WipeVolumes -and $existingVolumes.Count -gt 0

# Postgres and object storage keep the password they were first started with
# in their volumes; CHANGEME-* placeholders next to existing volumes mean .env
# no longer holds it.
if (-not $WipeVolumes -and $existingVolumes.Count -gt 0 -and ((Test-Placeholder 'PG_PASSWORD') -or (Test-Placeholder 'MINIO_ROOT_PASSWORD'))) {
  Die "Existing project volumes use prior credentials. Set matching passwords or explicitly re-run with -WipeVolumes."
}

# ---- mode ----
# The standalone install is docker-compose.yml. The distributed stack is recorded as
# COMPOSE_FILE=docker-compose.distributed.yml in .env, which Compose reads by itself,
# so re-runs and a plain `docker compose up -d` stay on it. An existing
# install never changes stack: the two keep workflows, stored files and the
# Postgres -> ClickHouse sync in different places, so its data would not
# carry over. A refused switch leaves .env unchanged.
$composeFileSetting = Get-EnvValue 'COMPOSE_FILE'
$IsDistributed = [bool]$Distributed
$DistributedDetected = $false

# Services of this project's containers, running or not. '{{.Labels}}' keeps
# double quotes off the command line (PowerShell 5.1 drops them).
$projectServices = @()
foreach ($labels in @(Invoke-Probe { docker ps -a --filter "label=com.docker.compose.project=$projectName" --format '{{.Labels}}' })) {
  if ($labels -match '(^|,)com\.docker\.compose\.service=([^,]+)') { $projectServices += $matches[2] }
}

# What marks this project as a distributed install, if anything does.
$distributedSignal = ''
foreach ($suffix in $distributedOnlyVolumeSuffixes) {
  Invoke-Probe { docker volume inspect "${projectName}_${suffix}" } | Out-Null
  if ($LASTEXITCODE -eq 0) { $distributedSignal = "volume ${projectName}_${suffix}"; break }
}
if (-not $distributedSignal) {
  foreach ($svc in $distributedOnlyServices) {
    if ($projectServices -contains $svc) { $distributedSignal = "its $svc container"; break }
  }
}
$standaloneAppContainer = $projectServices -contains 'app'
Invoke-Probe { docker volume inspect "${projectName}_app-data" } | Out-Null
$standaloneAppVolume = ($LASTEXITCODE -eq 0)

if (-not $IsDistributed -and $composeFileSetting -like '*docker-compose.distributed.yml*') {
  $IsDistributed = $true
} elseif (-not $IsDistributed -and $distributedSignal) {
  # Installs made before the default became the single-app stack ran the
  # distributed stack from docker-compose.yml. Keep them there.
  $IsDistributed = $true
  $DistributedDetected = $true
}

# COMPOSE_FILE for the distributed stack is written by Save-Mode: at once for an
# existing distributed install, otherwise only once the preflight checks pass, so a
# refused -Distributed leaves .env as it was.
$newComposeSetting = ''
function Save-Mode {
  if ($script:newComposeSetting) {
    Set-EnvValue 'COMPOSE_FILE' $script:newComposeSetting
    $script:newComposeSetting = ''
  }
}

# A COMPOSE_FILE setting with its docker-compose.yml entry swapped for
# docker-compose.distributed.yml, other entries (overrides) kept. Returns ''
# when the setting lists no docker-compose.yml. Compose on Windows separates
# COMPOSE_FILE entries with ';'.
function Get-DistributedComposeSetting {
  param([string]$Setting)
  $found = $false
  $entries = @()
  foreach ($entry in ($Setting -split ';')) {
    if ($entry -match '(^|[\\/])docker-compose\.yml$') {
      $entry = $entry -replace 'docker-compose\.yml$', 'docker-compose.distributed.yml'
      $found = $true
    }
    $entries += $entry
  }
  if ($found) { $entries -join ';' } else { '' }
}

# The stack a COMPOSE_FILE setting starts: 'standalone' when it lists
# docker-compose.yml, 'distributed' when it lists docker-compose.distributed.yml,
# '' when it lists both (Compose merges them) or neither.
function Get-ComposeSettingStack {
  param([string]$Setting)
  $names = @($Setting -split ';' | ForEach-Object { ($_ -split '[\\/]')[-1] })
  $standalone = $names -contains 'docker-compose.yml'
  $distributed = $names -contains 'docker-compose.distributed.yml'
  if ($standalone -and -not $distributed) { 'standalone' }
  elseif ($distributed -and -not $standalone) { 'distributed' }
  else { '' }
}

$distributedSetting = ''
if ($IsDistributed) {
  $distributedSetting = $composeFileSetting
  if (-not $composeFileSetting) {
    $distributedSetting = 'docker-compose.distributed.yml'
    # An explicit COMPOSE_FILE switches off Compose's automatic loading of
    # docker-compose.override.yml, so carry an existing override along.
    if (Test-Path 'docker-compose.override.yml') { $distributedSetting += ';docker-compose.override.yml' }
    $newComposeSetting = $distributedSetting
  } elseif ($composeFileSetting -notlike '*docker-compose.distributed.yml*') {
    $distributedSetting = Get-DistributedComposeSetting $composeFileSetting
    if (-not $distributedSetting) {
      Die "COMPOSE_FILE=$composeFileSetting in .env lists no docker-compose.yml, so the installer cannot point it at the distributed stack. Add docker-compose.distributed.yml to it yourself, then re-run .\bin\install.ps1."
    }
    $newComposeSetting = $distributedSetting
  }
}

# A wipe removes this project's containers and volumes first, so there is
# nothing left to strand.
if ($IsDistributed -and -not $wipe) {
  if ($standaloneAppContainer -and $distributedSignal) {
    # The data is a distributed install's: keep plain `docker compose` on it.
    Save-Mode
    Die "Project $projectName holds a distributed install ($distributedSignal) and also a Standalone app container. That happens when a plain 'docker compose up -d' starts the new docker-compose.yml against an older distributed install. Your data is in the distributed stack's volumes, and .env now records COMPOSE_FILE=$distributedSetting. Remove the stray container, then re-run .\bin\install.ps1: docker compose -f docker-compose.yml -p $projectName rm -sf app"
  } elseif (($standaloneAppContainer -or $standaloneAppVolume) -and -not $distributedSignal) {
    $hint = ''
    if (-not $Distributed) { $hint = ' If you did not ask for the distributed stack, delete the COMPOSE_FILE line from .env and re-run .\bin\install.ps1.' }
    Die "Project $projectName already holds a standalone install. Moving an existing install to the distributed stack is not supported: its data would not carry over. Back up first (INSTALLATION.md > Backups), then re-run with -Distributed -WipeVolumes, which deletes this install's data. To run the distributed stack next to this install, use a second checkout with another COMPOSE_PROJECT_NAME.$hint"
  }
}

# Compose takes COMPOSE_FILE from the shell over .env, so a value set there
# picks the stack of every compose command below, whatever .env records. It
# has to start the stack chosen above, or the install stops before it records
# the stack, wipes volumes or generates secrets. The refusals above come
# first: whatever the shell sets, they stand.
$shellComposeFile = [Environment]::GetEnvironmentVariable('COMPOSE_FILE')
if ($null -ne $shellComposeFile) {
  $installStack, $stackFile, $otherFile = 'standalone', 'docker-compose.yml', 'docker-compose.distributed.yml'
  if ($IsDistributed) {
    $installStack, $stackFile, $otherFile = 'distributed', 'docker-compose.distributed.yml', 'docker-compose.yml'
  }
  $shellStack = Get-ComposeSettingStack $shellComposeFile
  if ($shellStack -ne $installStack) {
    # A plain re-run chooses from .env and the volumes, which may not record
    # -Distributed yet.
    $rerun = '.\bin\install.ps1'
    if ($Distributed) { $rerun += ' -Distributed' }
    $shellHint = ''
    if ($shellStack -eq 'standalone') {
      $shellHint = " To keep your other compose files, set COMPOSE_FILE=$(Get-DistributedComposeSetting $shellComposeFile) instead."
    } elseif ($shellStack -eq 'distributed' -and -not $standaloneAppContainer -and -not $standaloneAppVolume) {
      $shellHint = ' For the distributed stack, run .\bin\install.ps1 -Distributed.'
    }
    Die "COMPOSE_FILE=$shellComposeFile is set in your shell, and Docker Compose uses it instead of .env. The installer chose the $installStack stack for project $projectName, which takes $stackFile without $otherFile. Run 'Remove-Item Env:COMPOSE_FILE' (and remove it wherever your profile or system settings set it), then re-run $rerun.$shellHint"
  }
}

if ($IsDistributed) {
  $ComposePath = 'docker-compose.distributed.yml'
  $AppService = 'backend'
  # An existing distributed install is recorded at once, so that a plain
  # `docker compose up -d` can no longer start the default file against it.
  if ($distributedSignal) { Save-Mode }
  if ($DistributedDetected) {
    Warn "Existing distributed install detected ($distributedSignal); staying on the distributed stack."
    Warn "  Recorded COMPOSE_FILE=$distributedSetting in .env, so plain 'docker compose' commands use it too."
    Warn "  Moving an existing install to the standalone stack is not supported: see INSTALLATION.md > Switching between Standalone and Distributed."
  }
  if ($standaloneAppVolume -and -not $wipe) {
    Warn "Volume ${projectName}_app-data belongs to a standalone install; the distributed stack does not use it."
  }
  Ok "Mode: Distributed (COMPOSE_FILE=$distributedSetting)"
} else {
  $ComposePath = 'docker-compose.yml'
  $AppService = 'app'
  if ($composeFileSetting) {
    Ok "Mode: Standalone, with COMPOSE_FILE=$composeFileSetting from .env"
  } else {
    Ok "Mode: Standalone (docker-compose.yml) -- pass -Distributed for one container per service"
  }
}
if ($shellComposeFile) {
  Ok "Compose uses COMPOSE_FILE=$shellComposeFile from your shell, which selects the same stack"
}
if (-not (Test-Path $ComposePath)) {
  Die "$ComposePath missing -- are you running this from a complete checkout?"
}

if ($WipeVolumes) {
  if ($existingVolumes.Count -eq 0) {
    Ok "No existing project volumes found; nothing to wipe"
  } else {
    Step "Wiping explicitly requested project volumes"
    # --remove-orphans also takes the other stack's containers.
    Invoke-Compose down --remove-orphans
    if ($LASTEXITCODE -ne 0) {
      Die "Could not stop the existing Compose project before the requested volume wipe."
    }
    foreach ($volumeName in $existingVolumes) {
      $removeOutput = & docker volume rm $volumeName 2>&1
      if ($LASTEXITCODE -ne 0) {
        Die "Could not remove requested volume $volumeName`: $removeOutput"
      }
      Append-Log @("removed volume: $volumeName")
    }
    Ok "Removed $($existingVolumes.Count) inventoried project volume(s), including Kafka/spool state when present"
  }
}

# ---- secrets ----
# A fresh install has no volumes of this project (bin/lib/secrets.ps1 says
# what each kind of install gets).
$freshInstall = ($existingVolumes.Count -eq 0) -or $wipe
Write-Secrets $freshInstall $IsDistributed $projectName

# ---- sandbox profile ----
# With COMPOSE_PROFILES=sandbox the app sends code evals to the nsjail
# executor. It reads the setting from .env inside the container, so a value
# given only in the shell starts the executor but leaves evals in the app.
if (-not $IsDistributed) {
  $shellProfiles = @("$env:COMPOSE_PROFILES" -split ',' | ForEach-Object { $_.Trim() })
  $envProfiles = @((Get-EnvValue 'COMPOSE_PROFILES') -split ',' | ForEach-Object { $_.Trim() })
  if ($shellProfiles -contains 'sandbox' -and -not ($envProfiles -contains 'sandbox')) {
    Warn "COMPOSE_PROFILES=sandbox is set in your shell but not in .env. Add it to .env: the app reads it there to send code evals to the nsjail executor."
  }
}

# ---- port preflight ----
Step "Checking host ports"
Test-HostPorts $IsDistributed $projectName

if ($NoUp) {
  Save-Mode
  Show-TelemetryNotice
  Step "Done (-NoUp)"
  if ($FromSource) { Warn "-FromSource builds nothing with -NoUp; re-run without -NoUp to build." }
  Say "  .env is ready. Bring up the stack with:"
  Say "    docker compose up -d"
  exit 0
}

# ---- Docker VM preflight ----
# Measured from inside a throwaway container: Docker Desktop runs in a VM
# (WSL 2 or Hyper-V) and the stack gets the VM's memory, not the host's.
Step "Checking the Docker VM"

$ProbeImage = 'busybox:1.37'
if ($IsDistributed) {
  $needMb = 5600; $wantMb = 11500; $wantCpu = 4; $needGb = 6; $wantGb = 12; $modeLabel = 'The distributed stack'
} else {
  $needMb = 2800; $wantMb = 3584; $wantCpu = 2; $needGb = 3; $wantGb = 4; $modeLabel = 'The standalone install'
}
# A VM reports a little less than its configured size (kernel reserve), so
# the thresholds sit just under a 3 / 4 GB (default) and 6 / 12 GB (distributed) VM.

function Get-ResizeHint {
  param([int]$Cpus, [int]$Gb)
  "Docker Desktop on WSL 2: set memory=${Gb}GB and processors=$Cpus under [wsl2] in %UserProfile%\.wslconfig, then 'wsl --shutdown'. On Hyper-V: Settings > Resources"
}

$vmMemMb = $null
$vmCpus = $null
$VmArch = ''
$probe = @(Invoke-Probe { docker run --rm $ProbeImage sh -c 'free -m; echo nproc $(nproc); echo arch $(uname -m)' })
$vmProbeRan = ($LASTEXITCODE -eq 0 -and $probe.Count -gt 0)
if ($vmProbeRan) {
  foreach ($line in $probe) {
    if ($line -match '^Mem:\s+(\d+)') { $vmMemMb = [int]$matches[1] }
    elseif ($line -match '^nproc\s+(\d+)') { $vmCpus = [int]$matches[1] }
    elseif ($line -match '^arch\s+(\S+)') { $VmArch = $matches[1] }
  }
} else {
  Warn "Could not run a $ProbeImage probe container (offline?); skipping the memory, CPU and path checks"
}

switch -Regex ($VmArch) {
  '^(x86_64|amd64)$' { $VmArch = 'amd64'; break }
  '^(aarch64|arm64)$' { $VmArch = 'arm64'; break }
  '^$' { break }
  default { Fail-Preflight "Docker runs on $VmArch; Future AGI runs on amd64 and arm64 only" }
}

if ($vmProbeRan) {
  if ($null -eq $vmMemMb) {
    Warn "Could not read the Docker VM's memory; skipping the memory check"
  } elseif ($vmMemMb -lt $needMb) {
    $tooSmall = "Docker has $vmMemMb MB of memory. $modeLabel needs at least $needGb GB ($wantGb GB recommended). Fix: $(Get-ResizeHint $wantCpu $wantGb)"
    if ($IsDistributed -and -not $freshInstall) {
      # Never block an upgrade of an install that already runs here.
      Warn "$tooSmall. Continuing, since this project already holds a distributed install; expect containers to be killed for lack of memory."
    } else {
      $alt = ''
      if ($Distributed) {
        $alt = ', or run the standalone install (.\bin\install.ps1 without -Distributed), which needs 3 GB'
      } elseif ($IsDistributed) {
        $alt = ', or install the standalone stack instead: delete the COMPOSE_FILE line from .env and re-run .\bin\install.ps1'
      }
      Fail-Preflight "$tooSmall$alt"
    }
  } elseif ($vmMemMb -lt $wantMb) {
    Warn "Docker has $vmMemMb MB of memory; $wantGb GB is recommended. $(Get-ResizeHint $wantCpu $wantGb)"
  } else {
    Ok "Docker VM: $vmMemMb MB memory, $vmCpus CPU(s), $VmArch"
  }
  if ($null -ne $vmCpus -and $vmCpus -lt $wantCpu) {
    Warn "Docker has $vmCpus CPU(s); $wantCpu+ recommended. $(Get-ResizeHint $wantCpu $wantGb)"
  }
  # The frontend build alone can take up to 8 GB of Node heap.
  if ($FromSource -and $null -ne $vmMemMb -and $vmMemMb -lt 7600) {
    Warn "Building from source wants an 8 GB Docker VM (the frontend build is memory-hungry); $vmMemMb MB may run out."
  }

  # Bind mounts come from this checkout. Docker Desktop shares only the paths
  # in its file-sharing list; anything else fails or mounts empty.
  Invoke-Probe { docker run --rm -v "${Root}:/probe:ro" $ProbeImage test -f /probe/.env.example } | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Ok "Checkout is visible to Docker"
  } else {
    Fail-Preflight "Docker cannot see $Root, so the stack's bind mounts would be empty. Move the checkout under your user folder, or add the path under Docker Desktop > Settings > Resources > File sharing"
  }
}

# Every preflight check passed, so -Distributed can be recorded now.
Save-Mode

# ---- collect first-user creds (up-front so the rest runs unattended) ----
# The telemetry notice comes first: the first account's email is part of the
# registration.
Show-TelemetryNotice

# Terminal input carries raw bytes, so a stray escape sequence (Shift+Tab emits
# ESC [ Z) travels into create_user and the sign-in banner.
$EmailPattern = '^[A-Za-z0-9.!#$%&''*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$'

function Remove-ControlChars {
  param([string]$Value)
  if (-not $Value) { return $Value }
  return ($Value -replace '[\p{Cc}]', '')
}

$UserEmail = $null
$UserName  = $null
$UserPass  = $null
$AccountSkipped = 'skipped'
$AccountCreated = 'created'
$AccountExists  = 'exists'
$AccountFailed  = 'failed'
$GateContainers = 'containers to start'
$AccountState = $AccountSkipped

function Read-Plain {
  param([string]$Prompt, [switch]$Secret)
  if ($Secret) {
    $sec = Read-Host -Prompt $Prompt -AsSecureString
    [System.Net.NetworkCredential]::new('', $sec).Password
  } else {
    Read-Host -Prompt $Prompt
  }
}

if (-not $SkipUserCreation -and -not $NonInteractive) {
  Step "Create your first account"
  Say "  Press Enter on email to skip and create the user later via:"
  Say "    $DcText exec $AppService python manage.py create_user"
  Say ""

  while ($true) {
    $UserEmail = Remove-ControlChars (Read-Plain "  Email")
    if (-not $UserEmail) { Say "  skipped -- no user will be created"; break }
    if ($UserEmail -match $EmailPattern) { break }
    Warn "  '$UserEmail' doesn't look like an email -- try again"
  }

  if ($UserEmail) {
    while (-not $UserName) {
      $UserName = Remove-ControlChars (Read-Plain "  Name")
      if (-not $UserName) { Warn "  name can't be empty" }
    }
    while ($true) {
      $UserPass = Read-Plain "  Password" -Secret
      if ($UserPass.Length -lt 8) {
        Warn "  password must be at least 8 characters"
        $UserPass = $null
        continue
      }
      $confirm = Read-Plain "  Confirm" -Secret
      if ($UserPass -ne $confirm) {
        Warn "  passwords don't match -- try again"
        $UserPass = $null
        continue
      }
      break
    }
    Ok "Captured. Account will be created once the backend is healthy."
  }
} elseif ($NonInteractive) {
  if ($env:FAGI_ADMIN_EMAIL -and $env:FAGI_ADMIN_NAME -and $env:FAGI_ADMIN_PASSWORD) {
    $UserEmail = Remove-ControlChars $env:FAGI_ADMIN_EMAIL
    $UserName  = Remove-ControlChars $env:FAGI_ADMIN_NAME
    $UserPass  = $env:FAGI_ADMIN_PASSWORD
    Step "Using FAGI_ADMIN_* from environment for first-user creation"
  } else {
    Step "Non-interactive: skipping first-user creation"
    Say "  Set FAGI_ADMIN_EMAIL, FAGI_ADMIN_NAME, FAGI_ADMIN_PASSWORD to auto-create."
  }
}

# ---- build from source (-FromSource) ----
# Published images are built from main, so a checkout of another branch
# needs its own: build each Future AGI image from this checkout, tag it
# `local` (bin/lib/build-local.ps1), and point the stack at that tag.
if ($FromSource) {
  Step "Building images from this checkout"
  Build-LocalImages $IsDistributed
  Set-EnvValue 'FUTURE_AGI_VERSION' 'local'
  if ($IsDistributed) {
    # The distributed stack tags these images separately.
    Set-EnvValue 'FRONTEND_VERSION' 'local'
    Set-EnvValue 'AGENTCC_GATEWAY_VERSION' 'local'
    Set-EnvValue 'FI_COLLECTOR_VERSION' 'local'
  }
  Ok "FUTURE_AGI_VERSION=local written to .env"
}

# ---- pull ----
Step "Pulling images"

# Service and image of every service in the resolved compose file.
$serviceImages = @()
$inServices = $false
$service = $null
foreach ($line in @(Invoke-Probe { & $DcCmd @DcArgs config })) {
  if ($line -match '^services:') { $inServices = $true; continue }
  if ($line -match '^\S') { $inServices = $false }
  if (-not $inServices) { continue }
  if ($line -match '^  ([^\s:][^:]*):\s*$') {
    $service = $matches[1]
  } elseif ($service -and $line -match '^    image: (.+)$') {
    $serviceImages += [pscustomobject]@{ Service = $service; Image = $matches[1].Trim().Trim('"') }
  }
}

# Images tagged `local` are built from this checkout (-FromSource, or the
# distributed stack's fi-collector) and are never pulled. The distributed stack also skips
# anything it builds itself; the standalone install pulls every other image, so
# a failed pull can never turn into a surprise source build.
$pullArgs = @('pull')
if ($IsDistributed) {
  $pullHelp = (& $DcCmd @DcArgs pull --help 2>&1 | Out-String)
  if ($pullHelp -match '--ignore-buildable') {
    $pullArgs += '--ignore-buildable'
  }
}
$pullServices = @()
foreach ($entry in $serviceImages) {
  if (-not $entry.Image.EndsWith(':local')) {
    $pullServices += $entry.Service
    continue
  }
  # fi-collector:local has a build: section in the distributed stack; anything
  # else tagged `local` exists only after -FromSource.
  if ($entry.Image -eq 'futureagi/fi-collector:local') { continue }
  Invoke-Probe { docker image inspect $entry.Image } | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Fail-Preflight "$($entry.Image) is not built yet. Re-run with -FromSource to build it from this checkout, or set its tag in .env (FUTURE_AGI_VERSION) back to a published release"
  }
}
if ((Get-EnvValue 'FUTURE_AGI_VERSION') -eq 'local' -and -not $FromSource) {
  Say "  FUTURE_AGI_VERSION=local: using the images last built by -FromSource"
}
if ($pullServices.Count -eq 0) {
  Warn "Could not list the stack's images; docker compose up will pull any that are missing"
} else {
  $pullArgs += $pullServices
  Append-Log @("running: $DcText $($pullArgs -join ' ')")
  Invoke-Compose @pullArgs
  if ($LASTEXITCODE -ne 0) {
    # A release that predates an image (futureagi/standalone, say) has no tag
    # for it on Docker Hub. Building from the checkout still works.
    $appPull = $serviceImages | Where-Object { $_.Service -eq $AppService } | Select-Object -First 1
    if ($appPull -and -not $appPull.Image.EndsWith(':local')) {
      Invoke-Probe { docker manifest inspect $appPull.Image } | Out-Null
      if ($LASTEXITCODE -ne 0) {
        $fromSourceCmd = '.\bin\install.ps1 -FromSource'
        if ($Distributed) { $fromSourceCmd += ' -Distributed' }
        Die "docker compose pull failed: Docker Hub has no $($appPull.Image) (not published for this release, or Docker Hub is unreachable). Build the images from this checkout instead: $fromSourceCmd"
      }
    }
    Die "docker compose pull failed; the output above names the image. Check the network, Docker Hub's pull rate limit and disk space (docker system df), then try again."
  }
  Ok "Images pulled"
}

# Published images may not match the Docker VM's CPU; Docker then emulates
# them, which is much slower (first boot can take 20+ minutes).
$appEntry = $serviceImages | Where-Object { $_.Service -eq $AppService } | Select-Object -First 1
if ($appEntry -and $VmArch) {
  $imageArch = (Invoke-Probe { docker image inspect --format '{{.Architecture}}' $appEntry.Image } | Select-Object -First 1)
  if ($imageArch -and $imageArch -ne $VmArch) {
    Warn "$($appEntry.Image) is built for $imageArch but Docker runs on $VmArch, so it runs under emulation."
    Warn "  Expect a slower stack and a first boot of 20+ minutes. .\bin\install.ps1 -FromSource builds native images."
  }
}

# ---- bring up ----
Step "Starting the stack"
if ($IsDistributed) {
  # One attempt, as in bin/e2e: replaying compose up can rerun an exited schema
  # or mirror job after an uncertain write. Inspect retained state before resuming.
  # Preserve services omitted by an upgrade; legacy retirement is an explicit step.
  Invoke-Compose up -d --build --wait --wait-timeout 1200
  if ($LASTEXITCODE -ne 0) {
    Die "docker compose startup failed or timed out; partial state retained, no automatic retry. Inspect 'docker compose ps -a' and 'docker compose logs' before explicitly resuming."
  }
} else {
  # No --wait: the app's first boot runs the migrations, and the readiness
  # loop below reports their progress and fails fast on a crash loop.
  Invoke-Compose up -d
  if ($LASTEXITCODE -ne 0) {
    Die "docker compose up failed; partial state retained. Inspect 'docker compose ps -a' and 'docker compose logs app'."
  }
}
Ok "Containers started"

# Services an upgrade dropped keep running until removed (`up` runs without
# --remove-orphans on purpose). RabbitMQ left the distributed stack when Redis took
# over live updates.
if ($IsDistributed -and ($projectServices -contains 'rabbitmq')) {
  Warn "RabbitMQ is no longer part of the distributed stack (Redis carries live updates), but its container is still here."
  Warn "  Once the stack is healthy, remove it with: $DcText up -d --remove-orphans"
  Warn "  Its data volume stays until you delete it: docker volume rm ${projectName}_rabbitmq-data"
}

# ---- readiness wait ----
if ($IsDistributed) {
  Step "Waiting for the application and property catalog to become ready"
} else {
  Step "Waiting for the application to become ready"
}

function Get-BoundedEnvInt {
  param(
    [string]$Var,
    [int]$Default,
    [int]$Min,
    [int]$Max
  )
  $raw = Get-EnvValue $Var
  if (-not $raw) { $raw = [string]$Default }
  $value = 0
  if (-not [int]::TryParse($raw, [ref]$value) -or $value -lt $Min -or $value -gt $Max) {
    Die "$Var must be an integer in [$Min,$Max] (got '$raw')"
  }
  return $value
}

function Get-ComposeServiceSnapshot {
  param([string]$Service)
  $containerId = (& $DcCmd @DcArgs ps -a -q $Service 2>$null | Select-Object -First 1)
  if (-not $containerId) {
    return [pscustomobject]@{
      Id = 'missing'; Status = 'missing'; ExitCode = -1; RestartCount = 0
      Health = 'none'; StartedAt = 'never'
    }
  }
  $format = '{{.State.Status}}|{{.State.ExitCode}}|{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.StartedAt}}'
  $raw = (& docker inspect --format $format $containerId 2>$null | Select-Object -First 1)
  if (-not $raw) {
    return [pscustomobject]@{
      Id = $containerId; Status = 'missing'; ExitCode = -1; RestartCount = 0
      Health = 'none'; StartedAt = 'never'
    }
  }
  $parts = $raw -split '\|', 5
  return [pscustomobject]@{
    Id = $containerId
    Status = $parts[0]
    ExitCode = [int]$parts[1]
    RestartCount = [int]$parts[2]
    Health = $parts[3]
    StartedAt = $parts[4]
  }
}

function Save-ReadinessDiagnostics {
  if ($IsDistributed) {
    $services = @(
      'property-catalog-kafka',
      'property-catalog-kafka-volume-init',
      'property-catalog-runtime-volume-init',
      'property-catalog-topic-init',
      'property-catalog-clickhouse-bootstrap',
      'fi-collector',
      'fi-property-catalog-consumer',
      'backend'
    )
  } else {
    $services = @('app', 'postgres', 'clickhouse')
  }
  $psOutput = @(& $DcCmd @DcArgs ps -a 2>&1 | ForEach-Object { [string]$_ })
  Append-Log @('', '--- compose ps -a ---')
  Append-Log $psOutput
  $logOutput = @(& $DcCmd @DcArgs logs --tail 80 @services 2>&1 | ForEach-Object { [string]$_ })
  Append-Log @('', '--- readiness logs ---')
  Append-Log $logOutput
}

function Test-HttpOk {
  param([string]$Uri)
  try {
    $null = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    return $true
  } catch {
    return $false
  }
}

$BackendPort = Get-EnvValue 'BACKEND_PORT'
if (-not $BackendPort) { $BackendPort = 8000 }
$FrontendPort = Get-EnvValue 'FRONTEND_PORT'
if (-not $FrontendPort) { $FrontendPort = 3000 }
$readyTimeout = Get-BoundedEnvInt 'INSTALL_READY_TIMEOUT_SECONDS' 600 60 1800
$stabilitySeconds = Get-BoundedEnvInt 'INSTALL_STABILITY_SECONDS' 15 5 120
# As long as the app's healthcheck start_period (3600s): Docker keeps calling a
# slow first boot "starting" that long, so the installer does not give up first.
$readyMax = Get-BoundedEnvInt 'INSTALL_READY_MAX_SECONDS' 3600 300 7200

function Get-AppliedMigrationCount {
  $log = (Invoke-Compose logs --tail 2000 $AppService 2>&1 | Out-String)
  return @($log -split "`n" | Where-Object { $_ -match 'Applying ' }).Count
}
$deadline = (Get-Date).AddSeconds($readyTimeout)
$hardDeadline = (Get-Date).AddSeconds($readyMax)
$readySince = $null
$lastMigrationsApplied = Get-AppliedMigrationCount
$pendingGate = $GateContainers
$lastReadySignature = ''
$firstAppRestarts = $null
$catalogJobs = @(
  'property-catalog-kafka-volume-init',
  'property-catalog-runtime-volume-init',
  'property-catalog-topic-init',
  'property-catalog-clickhouse-bootstrap'
)
$catalogServices = @(
  'fi-collector',
  'fi-property-catalog-consumer'
)

while ($true) {
  $now = Get-Date
  $allReady = $true
  $fatalReason = $null
  $signatureParts = @()
  $pendingGate = ''

  if ($IsDistributed) {
    foreach ($service in $catalogJobs) {
      $snapshot = Get-ComposeServiceSnapshot $service
      if ($snapshot.Status -eq 'exited' -and $snapshot.ExitCode -eq 0) { continue }
      $allReady = $false
      if (-not $pendingGate) { $pendingGate = "bootstrap job $service" }
      if ($snapshot.Status -eq 'dead' -or ($snapshot.Status -eq 'exited' -and $snapshot.ExitCode -ne 0)) {
        $fatalReason = "$service failed with status=$($snapshot.Status) exit_code=$($snapshot.ExitCode)"
        break
      }
    }

    if (-not $fatalReason) {
      $kafka = Get-ComposeServiceSnapshot 'property-catalog-kafka'
      $signatureParts += "kafka:$($kafka.Id):$($kafka.RestartCount):$($kafka.StartedAt)"
      if ($kafka.Status -ne 'running' -or $kafka.Health -ne 'healthy') {
        $allReady = $false
        if (-not $pendingGate) { $pendingGate = 'property-catalog-kafka to report healthy' }
        if ($kafka.Status -eq 'dead') { $fatalReason = 'property-catalog-kafka entered dead state' }
      }
    }

    if (-not $fatalReason) {
      foreach ($service in $catalogServices) {
        $snapshot = Get-ComposeServiceSnapshot $service
        $signatureParts += "$service`:$($snapshot.Id):$($snapshot.RestartCount):$($snapshot.StartedAt)"
        if ($snapshot.Status -ne 'running') {
          $allReady = $false
          if (-not $pendingGate) { $pendingGate = "$service to report healthy" }
          if ($snapshot.Status -eq 'dead') { $fatalReason = "$service entered dead state" }
        }
      }
    }
  } else {
    # One app container runs every process under a supervisor. It exiting,
    # or restarting over and over (restart: unless-stopped), is fatal.
    $app = Get-ComposeServiceSnapshot 'app'
    $signatureParts += "app:$($app.Id):$($app.RestartCount):$($app.StartedAt)"
    if ($null -eq $firstAppRestarts -and $app.Id -ne 'missing') { $firstAppRestarts = $app.RestartCount }
    if ($app.Status -ne 'running') {
      $allReady = $false
      if (-not $pendingGate) { $pendingGate = 'the app container to run' }
    }
    if ($app.Status -eq 'dead' -or $app.Status -eq 'exited') {
      $fatalReason = "The app container stopped (status=$($app.Status) exit_code=$($app.ExitCode))"
    } elseif ($null -ne $firstAppRestarts -and ($app.RestartCount - $firstAppRestarts) -ge 2) {
      $fatalReason = "The app container keeps restarting ($($app.RestartCount - $firstAppRestarts) restarts during this install)"
    }
  }

  $backendHealthy = Test-HttpOk "http://localhost:$BackendPort/health/"
  if (-not $backendHealthy) {
    $allReady = $false
    if (-not $pendingGate) { $pendingGate = "backend /health/ on port $BackendPort" }
  }

  # The distributed stack gates on the frontend container above; the default
  # install's UI comes from the app container, so ask it directly.
  if (-not $IsDistributed -and -not (Test-HttpOk "http://localhost:$FrontendPort/")) {
    $allReady = $false
    if (-not $pendingGate) { $pendingGate = "the UI on port $FrontendPort" }
  }

  # A first install spends most of its readiness budget applying migrations.
  # Extend only while the backend is itself the unmet gate and its migration
  # count is still climbing, so a stuck peer service can never hide behind it.
  if (-not $backendHealthy) {
    $migrationsApplied = Get-AppliedMigrationCount
    if ($migrationsApplied -gt $lastMigrationsApplied) {
      $lastMigrationsApplied = $migrationsApplied
      Say "  migrations in progress ($migrationsApplied applied), extending the readiness window"
      $deadline = $now.AddSeconds($readyTimeout)
      if ($deadline -gt $hardDeadline) { $deadline = $hardDeadline }
    }
  }

  if ($fatalReason) {
    Save-ReadinessDiagnostics
    Die "$fatalReason. Relevant service logs were appended to $LogFile"
  }

  $readySignature = $signatureParts -join ';'
  if ($allReady) {
    if ($readySignature -ne $lastReadySignature) {
      $lastReadySignature = $readySignature
      $readySince = $now
    } elseif ($readySince -and ($now - $readySince).TotalSeconds -ge $stabilitySeconds) {
      if ($IsDistributed) {
        Ok "Kafka healthy; observation topic and isolated catalog bootstrap completed"
        Ok "Collector and observation consumer stable for ${stabilitySeconds}s"
      } else {
        Ok "App container stable for ${stabilitySeconds}s; UI answering on port $FrontendPort"
      }
      Ok "Backend healthy at http://localhost:$BackendPort"
      break
    }
  } else {
    $readySince = $null
    $lastReadySignature = ''
  }

  if ($now -ge $deadline) {
    Save-ReadinessDiagnostics
    $gate = if ($pendingGate) { $pendingGate } else { $GateContainers }
    Die "Stack did not become fully ready, still waiting on $gate. Relevant service logs were appended to $LogFile. The stack keeps starting: on a slow host, check 'docker compose logs -f $AppService', or raise INSTALL_READY_MAX_SECONDS (up to 7200) in .env and run the installer again."
  }
  Start-Sleep -Seconds 5
}

# ---- create user ----
if ($UserEmail) {
  Step "Creating your account"
  # create_user exits only once the telemetry registration it starts is done
  # (up to three requests). Unless a timeout is configured, cap each request
  # at 2 s, so blocked egress costs seconds instead of half a minute; the
  # scheduled telemetry job retries a registration that failed here.
  $telemetryTimeout = Get-ComposeEnvValue 'FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS' '2'
  # The password goes on stdin, never on a command line: in Standalone this
  # runs in the app container, where eval code can read /proc/*/cmdline.
  # Piped as UTF-8 (Windows PowerShell 5.1 pipes ASCII); stderr is output
  # here, not an error.
  $savedEncoding = $OutputEncoding
  $savedPreference = $ErrorActionPreference
  $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
  $ErrorActionPreference = 'Continue'
  try {
    $cuOut = $UserPass | & $DcCmd @DcArgs exec -T -e "FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS=$telemetryTimeout" $AppService python manage.py create_user `
      --email $UserEmail --name $UserName 2>&1
    $cuRc = $LASTEXITCODE
  } finally {
    $OutputEncoding = $savedEncoding
    $ErrorActionPreference = $savedPreference
  }
  if ($cuRc -eq 0) {
    $AccountState = $AccountCreated
    Ok "Account created for $UserEmail"
  } elseif ($cuOut -match '(?i)already exists|UNIQUE constraint') {
    $AccountState = $AccountExists
    Ok "Account already exists for $UserEmail -- sign in normally"
  } else {
    $AccountState = $AccountFailed
    Warn "create_user failed (exit $cuRc). Last 6 lines:"
    ($cuOut | Out-String).Split([char]10) | Select-Object -Last 6 | ForEach-Object { Say "      $_" }
  }
}

# ---- done ----
Show-Summary $FrontendPort $BackendPort

if ($AccountState -eq $AccountFailed) { exit 1 }
exit 0
