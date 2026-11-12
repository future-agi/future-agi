# The secrets bin/install.ps1 writes to .env. Dot-sourced; needs env.ps1 and
# the installer's output helpers (Ok, Warn), Fail-Preflight and Invoke-Probe.

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

$installSecrets = @('SECRET_KEY', 'FI_API_SECRET_PEPPER', 'PG_PASSWORD', 'MINIO_ROOT_PASSWORD', 'AGENTCC_INTERNAL_API_KEY', 'CH_PASSWORD')

function Stop-PepperDotenv {
  throw 'Cannot safely decode FI_API_SECRET_PEPPER / FI_API_SECRET_PEPPERS_RETIRED from .env; use single-line literal assignments before rotation (no interpolation or escaped quotes). Peppers unchanged'
}

# Same bounded Compose dotenv grammar as pepper_env_value in secrets.sh. Never
# evaluate the file. Refuse unsupported syntax before changing either pepper;
# check other lines for multiline quotes that could hide a target assignment.
function Get-PepperEnvValue {
  param([string]$Var)
  $value = ''
  foreach ($line in (Read-EnvLines)) {
    if ($line -match '^\s*(#.*)?$') { continue }
    if ($line -cnotmatch '^\s*(export\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.*)$') { Stop-PepperDotenv }
    $key = $Matches[2]
    $raw = $Matches[3]
    if ($key -ceq $Var -and -not $line.StartsWith("$Var=", [System.StringComparison]::Ordinal)) { Stop-PepperDotenv }
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

# Upgrades may already have API keys sealed under the compose default. Any
# other nonempty effective value belongs to the operator (including CHANGEME-*).
function Rotate-PublishedApiSecretPepper {
  $published = 'local-dev-only-api-secret-pepper-replace-me'
  # Presence matters: Compose also prefers an exported empty value to .env.
  if (Test-Path Env:FI_API_SECRET_PEPPER) {
    $current = [Environment]::GetEnvironmentVariable('FI_API_SECRET_PEPPER')
    if ($current -and -not [string]::Equals($current, $published, [System.StringComparison]::Ordinal)) { return }
    throw 'Unset FI_API_SECRET_PEPPER in the shell before rotation; it overrides .env. Peppers unchanged'
  }
  $current = Get-PepperEnvValue 'FI_API_SECRET_PEPPER'
  if ($current -and -not [string]::Equals($current, $published, [System.StringComparison]::Ordinal)) { return }
  # Keep one persisted source of truth instead of changing only the masked file.
  if (Test-Path Env:FI_API_SECRET_PEPPERS_RETIRED) {
    throw 'Unset FI_API_SECRET_PEPPERS_RETIRED in the shell before rotating FI_API_SECRET_PEPPER; it overrides .env. Peppers unchanged'
  }
  # Both verifiers trim entries; keep first-occurrence order. List[string]'s
  # Contains uses ordinal equality, not PowerShell's culture-based comparison.
  $retired = [System.Collections.Generic.List[string]]::new()
  $entries = @((Get-PepperEnvValue 'FI_API_SECRET_PEPPERS_RETIRED') -split ',') + @($published)
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
  Ok 'Rotated FI_API_SECRET_PEPPER (published default moved to retired)'
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
# SECRET_KEY or gateway key would sign everyone out. Its empty or published API
# pepper is rotated with the default retired, preserving existing API keys.
# Five other keys are safe to fill on any install: without
# INTEGRATION_ENCRYPTION_KEY every process starts with a
# random integration key of its own, so no integration credential encrypted
# before survives a restart anyway, and the system API keys sealed meanwhile
# under the key derived from SECRET_KEY still decrypt once it is set
# (futureagi/accounts/api_secrets.py); the standalone install's Redis keeps
# nothing across restarts (the distributed stack's Redis takes none); the
# gateway and the app read AGENTCC_WEBHOOK_SECRET and AGENTCC_ADMIN_TOKEN afresh
# on every start, and so do the app and the Hub checker OMEGA_HUB_CHECKER_TOKEN.
# The gateway refuses to start on the admin token the compose files publish, so
# that default is replaced too.
function Write-Secrets {
  param([bool]$Fresh, [bool]$Distributed, [string]$ProjectName)
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
    $publishedDefaults = @($installSecrets | Where-Object { $_ -ne 'FI_API_SECRET_PEPPER' -and (Test-SecretUnset $_) })
    if ($publishedDefaults.Count -gt 0) {
      Warn "Existing install: $($publishedDefaults -join ' ') still use the defaults published in this repository."
      Warn "  The installer keeps these existing secrets. An empty or published-default FI_API_SECRET_PEPPER is rotated with the default retired. See https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer"
    }
    # ClickHouse takes CH_PASSWORD at every start, while Distributed's PeerDB
    # peer keeps the password it was set up with, and the bootstrap re-creates
    # the dictionaries with a new password but not without one. Neither value
    # is printed.
    $runningPassword = Get-ClickHouseRunningPassword $ProjectName
    if ($null -ne $runningPassword -and $runningPassword -cne (Get-ComposeEnvValue 'CH_PASSWORD')) {
      Fail-Preflight "CH_PASSWORD differs from the password this install's ClickHouse runs with. Starting now would change that password, while what was set up with the old one keeps it: on Distributed the Postgres -> ClickHouse sync, and, when CH_PASSWORD is now empty, the dictionaries every span insert reads. Set CH_PASSWORD back to the password ClickHouse runs with (empty on installs made before the installer generated one), or see INSTALLATION.md > Secrets that must be changed"
    }
    Rotate-PublishedApiSecretPepper
  }
  if (Test-SecretUnset 'INTEGRATION_ENCRYPTION_KEY') {
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
  if (-not $Distributed -and (Test-SecretUnset 'REDIS_PASSWORD')) {
    Set-EnvValue 'REDIS_PASSWORD' (New-HexSecret 32)
    Ok "Generated REDIS_PASSWORD"
  }
}
