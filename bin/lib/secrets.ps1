# The secrets bin/install.ps1 writes to .env. Dot-sourced; needs env.ps1 and
# the installer's output helpers (Ok, Warn, Die), Fail-Preflight and Invoke-Probe.

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

function New-FernetKey {
  # 32 random bytes, URL-safe base64: the format of INTEGRATION_ENCRYPTION_KEY.
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $buf = [byte[]]::new(32)
    $rng.GetBytes($buf)
    [Convert]::ToBase64String($buf).Replace('+', '-').Replace('/', '_')
  } finally {
    $rng.Dispose()
  }
}

function Test-Placeholder { param([string]$Var) ((Get-EnvValue $Var) -match '^CHANGEME-') }
function Test-SecretUnset { param([string]$Var) $v = Get-EnvValue $Var; (-not $v) -or ($v -match '^CHANGEME-') }

# The gateway refuses to start on the admin token the compose files publish,
# so that one counts as unset too.
function Test-AdminTokenUnset {
  (Test-SecretUnset 'AGENTCC_ADMIN_TOKEN') -or ((Get-EnvValue 'AGENTCC_ADMIN_TOKEN') -ceq 'local-dev-only-admin-token-replace-me')
}

# FI_API_SECRET_PEPPER is not here: Write-Secrets rotates it on every install.
$installSecrets = @('SECRET_KEY', 'PG_PASSWORD', 'MINIO_ROOT_PASSWORD', 'AGENTCC_INTERNAL_API_KEY', 'CH_PASSWORD')

function Stop-PepperDotenv {
  throw 'Cannot safely decode FI_API_SECRET_PEPPER / FI_API_SECRET_PEPPERS_RETIRED from .env; use single-line literal assignments before rotation (no interpolation or escaped quotes). Peppers unchanged'
}

# Same bounded Compose dotenv grammar as pepper_env_value in secrets.sh. Never
# evaluate the file. Refuse unsupported syntax before changing either pepper;
# check other lines for multiline quotes that could hide a target assignment.
function Get-PepperEnvValue {
  param([string]$Var)
  $value = ''
  $mcpSeen = $false
  foreach ($line in (Read-EnvLines)) {
    if ($line -match '^\s*(#.*)?$') { continue }
    if ($line -cnotmatch '^\s*(export\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.*)$') { Stop-PepperDotenv }
    $key = $Matches[2]
    $raw = $Matches[3]
    if ($key -ceq $Var -and -not $line.StartsWith("$Var=", [System.StringComparison]::Ordinal)) { Stop-PepperDotenv }
    if ($Var -ceq 'AGENTCC_MCP_STATE_KEYS' -and $key -ceq $Var) {
      if ($mcpSeen) { Stop-PepperDotenv }
      $mcpSeen = $true
    }
    $quote = if ($raw.Length) { $raw.Substring(0, 1) } else { '' }
    if ($quote -ceq "'") {
      if ($raw -notmatch "^'([^']*)'\s*(#.*)?$") { Stop-PepperDotenv }
      $decoded = $Matches[1]
      if ($decoded.EndsWith('\', [System.StringComparison]::Ordinal)) { Stop-PepperDotenv }
    } elseif ($quote -ceq '"') {
      if ($raw -notmatch '^"(([^"\\]|\\.)*)"\s*(#.*)?$') { Stop-PepperDotenv }
      $decoded = $Matches[1]
    } else {
      # A leading hash is literal; only whitespace inside the value starts a comment.
      $decoded = ($raw -replace '\s+#.*$', '').TrimEnd()
    }
    if ($key -cne $Var) { continue }
    if (($quote -cne "'" -and $decoded.Contains('$')) -or
        ($quote -ceq '"' -and $decoded.Contains('\')) -or
        ($quote -ceq "'" -and $decoded.Contains('\\')) -or
        $decoded.Contains("`r")) { Stop-PepperDotenv }
    $value = $decoded
  }
  return $value
}

# Validate before any credentials can change; an empty result means preserve
# the configured list byte-for-byte, including order, whitespace and empty entries.
function Get-McpStateKeysFill {
  if ([Environment]::GetEnvironmentVariables().Contains('AGENTCC_MCP_STATE_KEYS')) {
    throw 'Unset AGENTCC_MCP_STATE_KEYS in the shell; it overrides .env. Credentials unchanged'
  }
  try {
    $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    if (Test-Path '.env') {
      $text = $utf8.GetString([System.IO.File]::ReadAllBytes((Join-Path (Get-Location) '.env')))
      if ($text.Contains([string][char]0)) { throw 'invalid input' }
    }
    $current = Get-PepperEnvValue 'AGENTCC_MCP_STATE_KEYS'
  } catch {
    throw 'Cannot safely read AGENTCC_MCP_STATE_KEYS; credentials unchanged'
  }
  if ($current.Length -gt 0) {
    $count = 0
    foreach ($part in ($current -split ',')) {
      $part = $part.Trim()
      if (-not $part) { continue }
      if ($utf8.GetByteCount($part) -lt 32) {
        throw 'Invalid AGENTCC_MCP_STATE_KEYS; credentials unchanged'
      }
      $count++
    }
    if ($count -eq 0) { throw 'Invalid AGENTCC_MCP_STATE_KEYS; credentials unchanged' }
    return ''
  }
  try { $generated = New-HexSecret 32 } catch {
    throw 'Could not generate AGENTCC_MCP_STATE_KEYS; credentials unchanged'
  }
  if ($generated -isnot [string] -or $generated -cnotmatch '\A[0-9a-f]{64}\z') {
    throw 'Could not generate AGENTCC_MCP_STATE_KEYS; credentials unchanged'
  }
  return $generated
}

# True for a pepper the installer replaces: empty, published or CHANGEME-*
# (case-sensitive, as in secrets.sh).
function Test-ReplaceablePepper {
  param([string]$Value, [string]$Published)
  (-not $Value) -or [string]::Equals($Value, $Published, [System.StringComparison]::Ordinal) -or
    $Value.StartsWith('CHANGEME-', [System.StringComparison]::Ordinal)
}

# Any install may already have API keys sealed under the pepper Compose used:
# the compose default when .env has none, else .env's value. An empty,
# published or CHANGEME-* pepper is replaced, with that value kept for
# verification first; any other nonempty effective value belongs to the operator.
function Rotate-PublishedApiSecretPepper {
  $published = 'local-dev-only-api-secret-pepper-replace-me'
  # Presence matters: Compose also prefers an exported empty value to .env.
  if (Test-Path Env:FI_API_SECRET_PEPPER) {
    $current = [Environment]::GetEnvironmentVariable('FI_API_SECRET_PEPPER')
    if (-not (Test-ReplaceablePepper $current $published)) { return }
    throw 'Unset FI_API_SECRET_PEPPER in the shell before rotation; it overrides .env. Peppers unchanged'
  }
  $current = Get-PepperEnvValue 'FI_API_SECRET_PEPPER'
  if (-not (Test-ReplaceablePepper $current $published)) { return }
  $used = if ($current) { $current } else { $published }
  # Keep one persisted source of truth instead of changing only the masked file.
  if (Test-Path Env:FI_API_SECRET_PEPPERS_RETIRED) {
    throw 'Unset FI_API_SECRET_PEPPERS_RETIRED in the shell before rotating FI_API_SECRET_PEPPER; it overrides .env. Peppers unchanged'
  }
  # Both verifiers trim entries; keep first-occurrence order. List[string]'s
  # Contains uses ordinal equality, not PowerShell's culture-based comparison.
  $retired = [System.Collections.Generic.List[string]]::new()
  $entries = @((Get-PepperEnvValue 'FI_API_SECRET_PEPPERS_RETIRED') -split ',') + @($used)
  foreach ($part in $entries) {
    $part = $part.Trim()
    if ($part -and -not $retired.Contains($part)) {
      # A literal single-quoted serialization, without ambiguous quote escapes.
      if ($part.Contains("'") -or $part.Contains('\\') -or $part.EndsWith('\', [System.StringComparison]::Ordinal)) { Stop-PepperDotenv }
      $retired.Add($part)
    }
  }
  $newPepper = New-HexSecret 32
  if ($newPepper -cnotmatch '^[0-9a-f]{64}$') {
    throw 'Could not generate FI_API_SECRET_PEPPER; .env unchanged'
  }
  # Retire first so an interrupted rotation is safe to finish on the next run.
  # Single quotes keep hashes/dollars literal; Set-EnvValue preserves permissions.
  Set-EnvValue 'FI_API_SECRET_PEPPERS_RETIRED' ("'" + ($retired -join ',') + "'")
  Set-EnvValue 'FI_API_SECRET_PEPPER' $newPepper
  Ok 'Rotated FI_API_SECRET_PEPPER (the previous value moved to retired)'
}

# One integration key per install. A Standalone container that finds none in
# .env makes its own once and keeps it in its app-data volume
# (deploy/standalone/bin/start). Adopt that one when .env has none; stop when
# .env or the shell holds a different one, -Force included: continuing would
# write new secrets under one key while the stored ones need the other. Runs
# before anything else is written. Returns $false when no key may be filled in
# because the volume cannot be checked or read. The value is never printed.
function Sync-KeptIntegrationKey {
  param([string]$ProjectName)
  $volume = "${ProjectName}_app-data"
  # Docker's own answer for a missing volume means there is none, and so does
  # a volume list that answers without it (other engines word that answer
  # otherwise). Any other failure (daemon down, no permission) may hide a kept
  # key. Docker's message is read as text (-match ignores case); 'Stop' would
  # throw on it. Volume names are case-sensitive, hence -ccontains.
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { $inspect = (& docker volume inspect $volume 2>&1 | Out-String) } finally { $ErrorActionPreference = $saved }
  if ($LASTEXITCODE -ne 0) {
    if ($inspect -match 'no such volume') { return $true }
    $ErrorActionPreference = 'Continue'
    try { $listed = @(& docker volume ls -q 2>$null | ForEach-Object { "$_".Trim() }) } finally { $ErrorActionPreference = $saved }
    if ($LASTEXITCODE -eq 0 -and -not ($listed -ccontains $volume)) { return $true }
    Warn "Could not tell whether the volume $volume exists (docker volume inspect $volume failed; run it to see why), so INTEGRATION_ENCRYPTION_KEY is not added to .env and the app keeps using its own"
    return $false
  }
  # busybox:1.37 is the installer's probe image (bin/install). No double quotes
  # in the script: Windows PowerShell 5.1 does not escape them for docker.exe.
  $kept = (@(Invoke-Probe { docker run --rm --network none -v "${volume}:/data:ro" busybox:1.37 sh -c 'f=/data/.integration-encryption-key; if [ -s $f ]; then cat $f; fi' }) -join '').Trim()
  if ($LASTEXITCODE -ne 0) {
    Warn "Could not read the volume $volume to compare INTEGRATION_ENCRYPTION_KEY with the key the app keeps there; no key is added to .env, and the app keeps using its own"
    return $false
  }
  if (-not $kept) { return $true }
  foreach ($configured in @([Environment]::GetEnvironmentVariable('INTEGRATION_ENCRYPTION_KEY'), (Get-EnvValue 'INTEGRATION_ENCRYPTION_KEY'))) {
    if ($configured -and -not $configured.StartsWith('CHANGEME-', [System.StringComparison]::Ordinal) -and
        -not [string]::Equals($configured, $kept, [System.StringComparison]::Ordinal)) {
      Die "Two different integration keys: INTEGRATION_ENCRYPTION_KEY in .env or your shell, and the key the app keeps in the volume $volume (/data/.integration-encryption-key). Stored secrets can be read with only one of them. Put the key that was in use into .env, unset INTEGRATION_ENCRYPTION_KEY in your shell if you set it there, and run the installer again"
    }
  }
  if (Test-SecretUnset 'INTEGRATION_ENCRYPTION_KEY') {
    Set-EnvValue 'INTEGRATION_ENCRYPTION_KEY' $kept
    Ok "Adopted the INTEGRATION_ENCRYPTION_KEY the app keeps in the volume $volume"
  }
  return $true
}

# The password this project's clickhouse container runs with: empty when its
# environment has none, as on installs whose release ignored CH_PASSWORD.
# $null when there is no container to tell by.
function Get-ClickHouseRunningPassword {
  param([string]$ProjectName)
  $id = @(Invoke-Probe { docker ps -aq --filter "label=com.docker.compose.project=$ProjectName" --filter 'label=com.docker.compose.service=clickhouse' }) | Select-Object -First 1
  if (-not $id) { return $null }
  # Never empty for a container (PATH at least): empty means inspect failed.
  $containerEnv = @(Invoke-Probe { docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' $id })
  if (-not ($containerEnv -join '')) { return $null }
  $line = $containerEnv | Where-Object { $_ -like 'CLICKHOUSE_PASSWORD=*' } | Select-Object -Last 1
  if ($line) { return $line.Substring('CLICKHOUSE_PASSWORD='.Length) }
  return ''
}

# A fresh install (no volumes of this project) gets its own secrets instead
# of the defaults published in the compose files. An existing install keeps
# operator-set values: Postgres stores its password in its volume, and a new
# SECRET_KEY or gateway key would sign everyone out. The API pepper is decided
# the same way on every install, because an install whose volumes were not
# seen also counts as fresh and may have API keys hashed under the pepper
# Compose used: an unset, empty, published or CHANGEME-* pepper is rotated with
# that value retired first, and any other value is the operator's. On a new
# install the retired value verifies nothing, since no key is stored yet.
# Five other keys are safe to fill on any install: INTEGRATION_ENCRYPTION_KEY,
# once Sync-KeptIntegrationKey has adopted the key a Standalone container kept
# for itself, since otherwise no integration credential was stored under a
# lasting key, and the system API keys sealed meanwhile
# under the key derived from SECRET_KEY still decrypt once it is set
# (futureagi/accounts/api_secrets.py); the standalone install's Redis keeps
# nothing across restarts (the distributed stack's Redis takes none); the
# gateway and the app read AGENTCC_WEBHOOK_SECRET and AGENTCC_ADMIN_TOKEN afresh
# on every start, and so do the app and the Hub checker OMEGA_HUB_CHECKER_TOKEN.
# The gateway refuses to start on the admin token the compose files publish, so
# that default is replaced too.
function Write-Secrets {
  param([bool]$Fresh, [bool]$Distributed, [string]$ProjectName)
  $mcpStateKey = Get-McpStateKeysFill
  $fillKey = Sync-KeptIntegrationKey $ProjectName
  if ($Fresh) {
    foreach ($var in $installSecrets) {
      if (Test-SecretUnset $var) {
        Set-EnvValue $var (New-HexSecret 32)
        Ok "Generated $var"
      }
    }
    # S3 client speaks to MinIO; the secret must match. The line's text is
    # copied as written, so Compose reads the same value.
    if (Test-Placeholder 'S3_SECRET_KEY') {
      if (Get-EnvValue 'MINIO_ROOT_PASSWORD') {
        Set-EnvValue 'S3_SECRET_KEY' (Get-EnvText 'MINIO_ROOT_PASSWORD')
        Ok "Aligned S3_SECRET_KEY with MINIO_ROOT_PASSWORD"
      }
    }
  } else {
    $publishedDefaults = @($installSecrets | Where-Object { Test-SecretUnset $_ })
    if ($publishedDefaults.Count -gt 0) {
      Warn "Existing install: $($publishedDefaults -join ' ') still use the defaults published in this repository."
      Warn "  The installer keeps these existing secrets. An empty, published-default or CHANGEME- FI_API_SECRET_PEPPER is rotated with the previous value retired. See https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer"
    }
    # ClickHouse takes CH_PASSWORD at every start, while Distributed's PeerDB
    # peer keeps the password it was set up with, and the bootstrap re-creates
    # the dictionaries with a new password but not without one. Neither value
    # is printed.
    $runningPassword = Get-ClickHouseRunningPassword $ProjectName
    if ($null -ne $runningPassword -and $runningPassword -cne (Get-ComposeEnvValue 'CH_PASSWORD')) {
      Fail-Preflight "CH_PASSWORD differs from the password this install's ClickHouse runs with. Starting now would change that password, while what was set up with the old one keeps it: on Distributed the Postgres -> ClickHouse sync, and, when CH_PASSWORD is now empty, the dictionaries every span insert reads. Set CH_PASSWORD back to the password ClickHouse runs with (empty on installs made before the installer generated one), or see INSTALLATION.md > Secrets that must be changed"
    }
  }
  if ($mcpStateKey) {
    Set-EnvValue 'AGENTCC_MCP_STATE_KEYS' $mcpStateKey -CaseSensitive
    Ok 'Generated AGENTCC_MCP_STATE_KEYS'
  }
  Rotate-PublishedApiSecretPepper
  if ($fillKey -and (Test-SecretUnset 'INTEGRATION_ENCRYPTION_KEY')) {
    Set-EnvValue 'INTEGRATION_ENCRYPTION_KEY' (New-FernetKey)
    Ok "Generated INTEGRATION_ENCRYPTION_KEY"
  }
  if (Test-SecretUnset 'AGENTCC_WEBHOOK_SECRET') {
    Set-EnvValue 'AGENTCC_WEBHOOK_SECRET' (New-HexSecret 32)
    Ok "Generated AGENTCC_WEBHOOK_SECRET"
  }
  if (Test-AdminTokenUnset) {
    Set-EnvValue 'AGENTCC_ADMIN_TOKEN' (New-HexSecret 32)
    Ok "Generated AGENTCC_ADMIN_TOKEN"
  }
  # The app and the Hub checker read it on every start: safe on any install.
  if (Test-SecretUnset 'OMEGA_HUB_CHECKER_TOKEN') {
    Set-EnvValue 'OMEGA_HUB_CHECKER_TOKEN' (New-HexSecret 32)
    Ok "Generated OMEGA_HUB_CHECKER_TOKEN"
  }
  # Distributed's PeerDB UI reads its sign-in password and its session key at
  # every start and stores neither: safe on any Distributed install, the
  # published "peerdb" included; a new session key only signs the UI's users in
  # again. Standalone runs no PeerDB, so its .env gets neither.
  if ($Distributed -and ((Test-SecretUnset 'PEERDB_UI_PASSWORD') -or ((Get-EnvValue 'PEERDB_UI_PASSWORD') -ceq 'peerdb'))) {
    Set-EnvValue 'PEERDB_UI_PASSWORD' (New-HexSecret 32)
    Ok "Generated PEERDB_UI_PASSWORD"
  }
  if ($Distributed -and (Test-SecretUnset 'PEERDB_UI_NEXTAUTH_SECRET')) {
    Set-EnvValue 'PEERDB_UI_NEXTAUTH_SECRET' (New-HexSecret 32)
    Ok "Generated PEERDB_UI_NEXTAUTH_SECRET"
  }
  if (-not $Distributed -and (Test-SecretUnset 'REDIS_PASSWORD')) {
    Set-EnvValue 'REDIS_PASSWORD' (New-HexSecret 32)
    Ok "Generated REDIS_PASSWORD"
  }
}
