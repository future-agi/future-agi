<#
.SYNOPSIS
  Future AGI - self-hosted installer for Windows / PowerShell.

.DESCRIPTION
  PowerShell counterpart of bin/install. Sets up .env (with rotated
  secrets), runs docker compose pull + up, waits for health, and prints
  the URLs to open. Re-running is idempotent.

.PARAMETER Full
  Add the PeerDB CDC stack (~22 containers). Default is light mode.

.PARAMETER SkipUserCreation
  Don't prompt for the first admin user.

.PARAMETER NoUp
  Bootstrap .env only; don't pull or start the stack.

.PARAMETER WipeVolumes
  Explicitly stop this Compose project and remove only its inventoried named
  volumes, including Kafka and collector spool state. Existing data is deleted.

.PARAMETER NonInteractive
  CI / unattended. Reads FAGI_ADMIN_EMAIL, FAGI_ADMIN_NAME,
  FAGI_ADMIN_PASSWORD from env if you want a user auto-created.

.EXAMPLE
  .\bin\install.ps1
  .\bin\install.ps1 -Full
  .\bin\install.ps1 -NoUp
  .\bin\install.ps1 -WipeVolumes
  .\bin\install.ps1 -NonInteractive
#>

[CmdletBinding()]
param(
  [switch]$Full,
  [switch]$SkipUserCreation,
  [switch]$NoUp,
  [switch]$WipeVolumes,
  [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

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

# ---- welcome ----
Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Blue
Write-Host "  |   Future AGI . self-hosted installer       |" -ForegroundColor Blue
Write-Host "  +-------------------------------------------+" -ForegroundColor Blue
if ($Full) {
  Write-Host "  mode: full . adds PeerDB CDC stack" -ForegroundColor DarkGray
} else {
  Write-Host "  mode: light . pass -Full to add PeerDB CDC" -ForegroundColor DarkGray
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

# Read/write .env as UTF-8 no BOM. PS 5.1's default Set-Content writes a
# UTF-8 BOM that some env-file readers choke on.
function Read-EnvLines {
  if (Test-Path '.env') {
    [System.IO.File]::ReadAllLines('.env')
  } else {
    @()
  }
}
function Write-EnvLines {
  param([string[]]$Lines)
  $utf8 = [System.Text.UTF8Encoding]::new($false)
  [System.IO.File]::WriteAllLines((Join-Path $Root '.env'), [string[]]$Lines, $utf8)
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

function Get-EnvValue {
  param([string]$Var)
  $line = Read-EnvLines | Where-Object { $_ -match "^$Var=" } | Select-Object -First 1
  if ($line) { ($line -split '=', 2)[1] } else { '' }
}

function Set-EnvValue {
  param([string]$Var, [string]$Val)
  $lines = Read-EnvLines
  $found = $false
  $out = @()
  foreach ($line in $lines) {
    if ($line -match "^$Var=") {
      $out += "$Var=$Val"
      $found = $true
    } else {
      $out += $line
    }
  }
  if (-not $found) { $out += "$Var=$Val" }
  Write-EnvLines $out
}

function New-HexSecret {
  param([int]$Bytes = 32)
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $buf = [byte[]]::new($Bytes)
    $rng.GetBytes($buf)
    -join ($buf | ForEach-Object { $_.ToString('x2') })
  } finally {
    $rng.Dispose()
  }
}

function Test-Placeholder { param([string]$Var) ((Get-EnvValue $Var) -match '^CHANGEME-') }

# Persistent-state inventory. Deletion happens only behind the explicit
# -WipeVolumes switch and targets exact Compose volume names; no wildcard or
# broad Docker cleanup command is used.
$projectName = Get-EnvValue 'COMPOSE_PROJECT_NAME'
if (-not $projectName) { $projectName = 'futureagi' }
$persistentVolumeSuffixes = @(
  'postgres-data',
  'clickhouse-data',
  'minio-data',
  'redis-data',
  'rabbitmq-data',
  'peerdb-catalog-data',
  'peerdb-minio-data',
  'property-catalog-kafka-data',
  'property-catalog-sequencer-data',
  'fi-collector-data'
)
$existingVolumes = @()
foreach ($suffix in $persistentVolumeSuffixes) {
  $volumeName = "${projectName}_${suffix}"
  & docker volume inspect $volumeName *> $null
  if ($LASTEXITCODE -eq 0) { $existingVolumes += $volumeName }
}

if ($WipeVolumes) {
  if ($existingVolumes.Count -eq 0) {
    Ok "No existing project volumes found; nothing to wipe"
  } else {
    Step "Wiping explicitly requested project volumes"
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
} elseif ($existingVolumes.Count -gt 0 -and ((Test-Placeholder 'PG_PASSWORD') -or (Test-Placeholder 'MINIO_ROOT_PASSWORD'))) {
  Die "Existing project volumes use prior credentials. Set matching passwords or explicitly re-run with -WipeVolumes."
}

foreach ($var in 'SECRET_KEY','PG_PASSWORD','MINIO_ROOT_PASSWORD','AGENTCC_INTERNAL_API_KEY','AGENTCC_ADMIN_TOKEN') {
  if (Test-Placeholder $var) {
    Set-EnvValue $var (New-HexSecret 32)
    Ok "Generated $var"
  }
}

# S3 client speaks to MinIO; the secret must match.
if (Test-Placeholder 'S3_SECRET_KEY') {
  $minio = Get-EnvValue 'MINIO_ROOT_PASSWORD'
  if ($minio) {
    Set-EnvValue 'S3_SECRET_KEY' $minio
    Ok "Aligned S3_SECRET_KEY with MINIO_ROOT_PASSWORD"
  }
}

# Mode
if ($Full) {
  $lines = Read-EnvLines
  $found = $false
  $out = @()
  foreach ($line in $lines) {
    if ($line -match '^# *COMPOSE_PROFILES=' -or $line -match '^COMPOSE_PROFILES=') {
      $out += 'COMPOSE_PROFILES=full'
      $found = $true
    } else {
      $out += $line
    }
  }
  if (-not $found) { $out += 'COMPOSE_PROFILES=full' }
  Write-EnvLines $out
  Ok "Mode: full (adds PeerDB CDC stack)"
} else {
  Ok "Mode: light (default -- pass -Full to add the PeerDB stack)"
}

# ---- port preflight ----
Step "Checking host ports"

$portsToCheck = [ordered]@{
  'FRONTEND_PORT'        = 3000
  'BACKEND_PORT'         = 8000
  'AGENTCC_GATEWAY_PORT' = 8090
  'SERVING_PORT'         = 8080
  'CODE_EXECUTOR_PORT'   = 8060
  'PG_PORT'              = 5432
  'CH_HTTP_PORT'         = 8123
  'CH_PORT'              = 9000
  'REDIS_PORT'           = 6379
  'MINIO_API_PORT'       = 9005
  'MINIO_CONSOLE_PORT'   = 9006
  'TEMPORAL_PORT'        = 7233
  'PROPERTY_CATALOG_KAFKA_PORT' = 29092
}
if ($Full) {
  $portsToCheck['PEERDB_UI_PORT'] = 3001
  $portsToCheck['PEERDB_PORT']    = 9900
}

function Test-PortFree {
  param([int]$Port)
  $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
  return ($null -eq $listener)
}

$conflicts = @()
foreach ($var in $portsToCheck.Keys) {
  $val = Get-EnvValue $var
  if (-not $val) { $val = $portsToCheck[$var] }
  $port = [int]$val
  if (Test-PortFree $port) {
    Ok "  $var=$port  free"
  } else {
    Warn "  $var=$port  is taken"
    $conflicts += "$var=$port"
  }
}

if ($conflicts.Count -gt 0) {
  Say ""
  Warn "Port conflicts detected. Set the affected vars in .env to free ports, or stop the conflicting processes."
  foreach ($c in $conflicts) { Say "    . $c" }
  Die "Re-run after resolving the conflicts above."
}

if ($NoUp) {
  Step "Done (-NoUp)"
  Say "  .env is ready. Bring up the stack with:"
  Say "    docker compose up -d"
  exit 0
}

# ---- collect first-user creds (up-front so the rest runs unattended) ----
$UserEmail = $null
$UserName  = $null
$UserPass  = $null

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
  Say "    docker exec -it futureagi-backend-1 python manage.py create_user"
  Say ""

  while ($true) {
    $UserEmail = Read-Plain "  Email"
    if (-not $UserEmail) { Say "  skipped -- no user will be created"; break }
    if ($UserEmail -match '^[^@\s]+@[^@\s]+\.[^@\s]+$') { break }
    Warn "  '$UserEmail' doesn't look like an email -- try again"
  }

  if ($UserEmail) {
    while (-not $UserName) {
      $UserName = Read-Plain "  Name"
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
    $UserEmail = $env:FAGI_ADMIN_EMAIL
    $UserName  = $env:FAGI_ADMIN_NAME
    $UserPass  = $env:FAGI_ADMIN_PASSWORD
    Step "Using FAGI_ADMIN_* from environment for first-user creation"
  } else {
    Step "Non-interactive: skipping first-user creation"
    Say "  Set FAGI_ADMIN_EMAIL, FAGI_ADMIN_NAME, FAGI_ADMIN_PASSWORD to auto-create."
  }
}

# ---- pull ----
Step "Pulling images"
$pullArgs = @('pull')
$pullHelp = (& $DcCmd @DcArgs pull --help 2>&1 | Out-String)
if ($pullHelp -match '--ignore-buildable') {
  $pullArgs += '--ignore-buildable'
} else {
  $activeServices = @(& $DcCmd @DcArgs config --services)
  $pullArgs += @($activeServices | Where-Object {
    $_ -and $_ -notin @('fi-collector', 'fi-property-catalog-sequencer', 'fi-property-catalog-consumer')
  })
}
Append-Log @("running: $DcCmd $($DcArgs -join ' ') $($pullArgs -join ' ')")
Invoke-Compose @pullArgs
if ($LASTEXITCODE -ne 0) {
  Die "docker compose pull failed. Check disk space (docker system df) and try again."
}
Ok "Images pulled"

# ---- bring up ----
Step "Starting the stack"
$attempt = 0
while ($true) {
  Invoke-Compose up -d --build --remove-orphans
  if ($LASTEXITCODE -eq 0) { break }
  $attempt++
  if ($attempt -ge 3) {
    Die "docker compose up failed after $attempt attempts. Check 'docker compose logs'."
  }
  Warn "compose up failed (attempt $attempt) -- retrying in 30s..."
  Start-Sleep -Seconds 30
}
Ok "Containers started"

# ---- readiness wait ----
Step "Waiting for the application and property catalog to become ready"

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
  $services = @(
    'property-catalog-kafka',
    'property-catalog-kafka-volume-init',
    'property-catalog-runtime-volume-init',
    'property-catalog-topic-init',
    'property-catalog-clickhouse-bootstrap',
    'property-catalog-postgres-bootstrap',
    'fi-collector',
    'fi-property-catalog-sequencer',
    'fi-property-catalog-consumer',
    'property-catalog-supervisor',
    'backend'
  )
  $psOutput = @(& $DcCmd @DcArgs ps -a 2>&1 | ForEach-Object { [string]$_ })
  Append-Log @('', '--- compose ps -a ---')
  Append-Log $psOutput
  $logOutput = @(& $DcCmd @DcArgs logs --tail 80 @services 2>&1 | ForEach-Object { [string]$_ })
  Append-Log @('', '--- readiness logs ---')
  Append-Log $logOutput
}

$BackendPort = Get-EnvValue 'BACKEND_PORT'
if (-not $BackendPort) { $BackendPort = 8000 }
$readyTimeout = Get-BoundedEnvInt 'INSTALL_READY_TIMEOUT_SECONDS' 600 60 1800
$stabilitySeconds = Get-BoundedEnvInt 'INSTALL_STABILITY_SECONDS' 15 5 120
$deadline = (Get-Date).AddSeconds($readyTimeout)
$readySince = $null
$lastReadySignature = ''
$catalogJobs = @(
  'property-catalog-kafka-volume-init',
  'property-catalog-runtime-volume-init',
  'property-catalog-topic-init',
  'property-catalog-clickhouse-bootstrap',
  'property-catalog-postgres-bootstrap'
)
$catalogServices = @(
  'fi-collector',
  'fi-property-catalog-sequencer',
  'fi-property-catalog-consumer',
  'property-catalog-supervisor'
)

while ($true) {
  $now = Get-Date
  $allReady = $true
  $fatalReason = $null
  $signatureParts = @()

  foreach ($service in $catalogJobs) {
    $snapshot = Get-ComposeServiceSnapshot $service
    if ($snapshot.Status -eq 'exited' -and $snapshot.ExitCode -eq 0) { continue }
    $allReady = $false
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
      if ($kafka.Status -eq 'dead') { $fatalReason = 'property-catalog-kafka entered dead state' }
    }
  }

  if (-not $fatalReason) {
    foreach ($service in $catalogServices) {
      $snapshot = Get-ComposeServiceSnapshot $service
      $signatureParts += "$service`:$($snapshot.Id):$($snapshot.RestartCount):$($snapshot.StartedAt)"
      if ($snapshot.Status -ne 'running' -or ($service -eq 'property-catalog-supervisor' -and $snapshot.Health -ne 'healthy')) {
        $allReady = $false
        if ($snapshot.Status -eq 'dead') { $fatalReason = "$service entered dead state" }
      }
    }
  }

  try {
    $null = Invoke-WebRequest -Uri "http://localhost:$BackendPort/health/" `
      -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
  } catch {
    $allReady = $false
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
      Ok "Kafka healthy; candidate and ordered topics plus catalog bootstraps completed"
      Ok "Collector, sequencer, property-catalog consumer, and supervisor stable for ${stabilitySeconds}s"
      Ok "Backend healthy at http://localhost:$BackendPort"
      break
    }
  } else {
    $readySince = $null
    $lastReadySignature = ''
  }

  if ($now -ge $deadline) {
    Save-ReadinessDiagnostics
    Die "Stack did not become fully ready within ${readyTimeout}s. Relevant service logs were appended to $LogFile"
  }
  Start-Sleep -Seconds 5
}

# ---- create user ----
if ($UserEmail) {
  Step "Creating your account"
  $cuOut = Invoke-Compose exec -T backend python manage.py create_user `
    --email $UserEmail --name $UserName --password $UserPass 2>&1
  $cuRc = $LASTEXITCODE
  if ($cuRc -eq 0) {
    Ok "Account created for $UserEmail"
  } elseif ($cuOut -match '(?i)already exists|UNIQUE constraint') {
    Ok "Account already exists for $UserEmail -- sign in normally"
  } else {
    Warn "create_user failed (exit $cuRc). Last 6 lines:"
    ($cuOut | Out-String).Split([char]10) | Select-Object -Last 6 | ForEach-Object { Say "      $_" }
    Warn "Run it manually after the stack settles:"
    Warn "  docker exec -it futureagi-backend-1 python manage.py create_user"
  }
}

# ---- done ----
$FrontendPort = Get-EnvValue 'FRONTEND_PORT'
if (-not $FrontendPort) { $FrontendPort = 3000 }

Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Green
Write-Host "  |   Future AGI is up!                       |" -ForegroundColor Green
Write-Host "  +-------------------------------------------+" -ForegroundColor Green
Say ""
Say "  Open in browser"
Say "    Local       ->  http://localhost:$FrontendPort"
Say ""
Say "  APIs"
Say "    Backend     ->  http://localhost:$BackendPort"
if ($Full) {
  Say "    PeerDB UI   ->  http://localhost:3001  (peerdb / peerdb)"
}
Say ""
Say "  Existing-data catalog backfill"
Say "    Restarts do not scan historical data automatically. After an upgrade:"
Say "    .\bin\property-catalog-backfill.ps1 -Execute"
if ($UserEmail) {
  Say ""
  Say "  Sign in as $UserEmail"
  Say "    ->  http://localhost:$FrontendPort/auth/jwt/login"
}
Say ""
Say "  Stop:        $DcCmd $($DcArgs -join ' ') down"
Say "  Wipe data:   $DcCmd $($DcArgs -join ' ') down -v"
Say "  Tail logs:   $DcCmd $($DcArgs -join ' ') logs -f"
Say "  Install log: $LogFile"
Say ""
