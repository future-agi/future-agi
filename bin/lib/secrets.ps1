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

$installSecrets = @('SECRET_KEY', 'PG_PASSWORD', 'MINIO_ROOT_PASSWORD', 'AGENTCC_INTERNAL_API_KEY', 'AGENTCC_ADMIN_TOKEN', 'CH_PASSWORD')

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
# every value it has: Postgres stores its password in its volume, and a new
# SECRET_KEY or gateway key would sign everyone out. Three keys hold no state
# and are filled on any install: without INTEGRATION_ENCRYPTION_KEY every
# process starts with a random key of its own, the standalone install's Redis
# keeps nothing across restarts (the distributed stack's Redis takes none),
# and the gateway and the app read AGENTCC_WEBHOOK_SECRET afresh on every start.
function Write-Secrets {
  param([bool]$Fresh, [bool]$Distributed, [string]$ProjectName)
  if ($Fresh) {
    foreach ($var in $installSecrets) {
      if (Test-SecretUnset $var) {
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
  } else {
    $publishedDefaults = @($installSecrets | Where-Object { Test-SecretUnset $_ })
    if ($publishedDefaults.Count -gt 0) {
      Warn "Existing install: $($publishedDefaults -join ' ') still use the defaults published in this repository."
      Warn "  The installer never changes an existing install's secrets. See https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer"
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
  if (Test-SecretUnset 'INTEGRATION_ENCRYPTION_KEY') {
    Set-EnvValue 'INTEGRATION_ENCRYPTION_KEY' (New-FernetKey)
    Ok "Generated INTEGRATION_ENCRYPTION_KEY"
  }
  if (Test-SecretUnset 'AGENTCC_WEBHOOK_SECRET') {
    Set-EnvValue 'AGENTCC_WEBHOOK_SECRET' (New-HexSecret 32)
    Ok "Generated AGENTCC_WEBHOOK_SECRET"
  }
  if (-not $Distributed -and (Test-SecretUnset 'REDIS_PASSWORD')) {
    Set-EnvValue 'REDIS_PASSWORD' (New-HexSecret 32)
    Ok "Generated REDIS_PASSWORD"
  }
}
