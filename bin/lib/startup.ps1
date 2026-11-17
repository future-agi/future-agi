# Starting the distributed stack for bin/install.ps1 (bin/lib/startup.sh is the
# bash copy and says why). Dot-sourced from the repository root; needs $DcCmd
# and $DcArgs, the installer's output helpers (Say, Ok, Warn, Die, Append-Log)
# and Invoke-Probe.
#
# ONE `compose up`, in the background, while the installer watches the schema
# migrations in the one-shot postgres-schema-bootstrap service: a progress line
# at least every minute while they run, and a failure only when they exit
# non-zero, when INSTALL_MIGRATION_TIMEOUT_SECONDS passes, or when the rest of
# the stack misses its own bound after they finished. Never start a second
# `compose up` here: it would run the exited bootstrap (or mirror job) again.

$StartupBootstrapService = 'postgres-schema-bootstrap'

# The clock and the pause between polls; the tests replace both.
function Get-StartupNow { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
function Invoke-StartupSleep { param([int]$Seconds) Start-Sleep -Seconds $Seconds }

# Id, Status, ExitCode and StartedAt of Service's container; Id and Status are
# 'missing' when it has none. A probe that prints nothing reads as '': "$(...)"
# is never $null, while [string] of an empty pipeline is $null in PowerShell 7.
function Get-StartupServiceState {
  param([string]$Service)
  $id = "$(@(Invoke-Probe { & $DcCmd @DcArgs ps -a -q $Service }) | Select-Object -First 1)".Trim()
  if (-not $id) {
    return [pscustomobject]@{ Id = 'missing'; Status = 'missing'; ExitCode = '-1'; StartedAt = 'never' }
  }
  $format = '{{.State.Status}}|{{.State.ExitCode}}|{{.State.StartedAt}}'
  $raw = "$(@(Invoke-Probe { & docker inspect --format $format $id }) | Select-Object -First 1)".Trim()
  $parts = @($raw -split '\|', 3)
  if ($parts.Count -lt 3) {
    return [pscustomobject]@{ Id = $id; Status = 'missing'; ExitCode = '-1'; StartedAt = 'never' }
  }
  [pscustomobject]@{ Id = $id; Status = $parts[0]; ExitCode = $parts[1]; StartedAt = $parts[2] }
}

# Stops this installer's own `compose up` client and nothing else: the
# containers keep the state they have, as when Compose's own wait times out.
# The one place that stops Compose. Kill($true) also ends the compose plugin
# the docker CLI started (PowerShell 7); Windows PowerShell 5.1 has only Kill().
function Stop-StartupCompose {
  param([System.Diagnostics.Process]$Process)
  if ($Process.HasExited) { return }
  try { $Process.Kill($true) } catch { try { $Process.Kill() } catch { } }
  try { $Process.WaitForExit() } catch { }
}

# The project's containers and the bootstrap's last log lines, to the install log.
function Save-StartupDiagnostics {
  $savedPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $psOutput = @(& $DcCmd @DcArgs ps -a 2>&1 | ForEach-Object { [string]$_ })
    $logOutput = @(& $DcCmd @DcArgs logs --tail 200 $StartupBootstrapService 2>&1 | ForEach-Object { [string]$_ })
    Append-Log (@('', '--- compose ps -a ---') + $psOutput + @("--- $StartupBootstrapService logs ---") + $logOutput)
  } catch {
  } finally {
    $ErrorActionPreference = $savedPreference
  }
}

function Fail-StartupBootstrap {
  param([string]$Status, [string]$ExitCode)
  Save-StartupDiagnostics
  Die "The schema migrations failed: $StartupBootstrapService ended with status=$Status exit_code=$ExitCode; partial state retained, no automatic retry. Read 'docker compose logs $StartupBootstrapService' before explicitly resuming."
}

# Seconds as whole minutes, rounded, at least 1.
function Get-StartupMinutes {
  param([int64]$Seconds)
  $minutes = [int64][math]::Floor(($Seconds + 30) / 60)
  if ($minutes -lt 1) { $minutes = 1 }
  $minutes
}

# Starts the distributed stack with one `compose up` and waits for it, as
# start_distributed_stack in bin/lib/startup.sh does. Dies, naming the
# bootstrap, when it fails or outlasts MigrationTimeout seconds of running.
# Returns $true once `compose up` succeeds, and $false when it fails otherwise
# or the rest of the stack misses ReadyTimeout seconds after the migrations;
# the caller then reports the failure. Writes nothing else to the pipeline.
function Start-DistributedStack {
  param([string]$BuildFlag, [int]$MigrationTimeout, [int]$ReadyTimeout)
  $service = $StartupBootstrapService
  $poll = 5
  $progressEvery = 55
  $composeTimeout = $MigrationTimeout + $ReadyTimeout
  $limitMinutes = [int][math]::Floor($MigrationTimeout / 60)

  # The container an earlier run left behind. Until this `up` restarts or
  # replaces it (a new StartedAt or Id), its exit says nothing about this
  # run's migrations. A running one is this run's: Compose leaves it running.
  $before = Get-StartupServiceState $service
  $previousKey = if ($before.Status -ne 'running') { "$($before.Id)|$($before.StartedAt)" } else { '' }

  # Compose's own wait outlasts both bounds below; it only backs them up.
  # --ansi never selects plain progress output: Compose's redrawn display
  # would overwrite the progress lines printed here.
  $upArgs = @($DcArgs) + @('--ansi', 'never', 'up', '-d', $BuildFlag, '--wait', '--wait-timeout', [string]$composeTimeout)
  Append-Log @("running: $DcCmd $($upArgs -join ' ')")
  $up = Start-Process -FilePath $DcCmd -ArgumentList $upArgs -WorkingDirectory (Get-Location).Path -NoNewWindow -PassThru
  # Opening the handle now keeps ExitCode readable once the process has exited
  # (Windows); where it cannot be opened, ExitCode is readable anyway.
  try { $null = $up.Handle } catch { }

  $runningSince = -1
  $lastProgress = -1
  $finishedAt = -1
  while (-not $up.HasExited) {
    $now = Get-StartupNow
    $state = Get-StartupServiceState $service
    $status = $state.Status
    if ($previousKey -and ("$($state.Id)|$($state.StartedAt)" -eq $previousKey)) { $status = 'previous' }
    if ($status -in @('running', 'restarting', 'paused')) {
      if ($runningSince -lt 0) {
        $runningSince = $now
        $lastProgress = $now
        Say "  Schema migrations started ($service). Large databases take longer; waiting up to $limitMinutes min (INSTALL_MIGRATION_TIMEOUT_SECONDS)."
      } elseif ($now - $lastProgress -ge $progressEvery) {
        $lastProgress = $now
        Say "  Migrations still running ($(Get-StartupMinutes ($now - $runningSince)) min). Large databases take longer; waiting up to $limitMinutes min (INSTALL_MIGRATION_TIMEOUT_SECONDS)."
      }
      if ($now - $runningSince -ge $MigrationTimeout) {
        Stop-StartupCompose $up
        Save-StartupDiagnostics
        Die "The schema migrations ($service) were still running after $limitMinutes min, the INSTALL_MIGRATION_TIMEOUT_SECONDS limit ($MigrationTimeout s). The installer stopped waiting, not the migrations: partial state retained, no automatic retry. Follow them with 'docker compose logs -f $service'; once 'docker compose ps -a $service' shows it exited (0), run .\bin\install.ps1 again. To wait longer, raise INSTALL_MIGRATION_TIMEOUT_SECONDS (up to 86400) in .env."
      }
    } elseif ($status -in @('exited', 'dead')) {
      if ($status -eq 'exited' -and $state.ExitCode -eq '0') {
        if ($finishedAt -lt 0) {
          $finishedAt = $now
          Ok "Schema migrations finished ($service)"
        } elseif ($now - $finishedAt -ge $ReadyTimeout) {
          Stop-StartupCompose $up
          Warn "The schema migrations finished, but the rest of the stack did not report running/healthy within $ReadyTimeout s after that."
          return $false
        }
      } else {
        Stop-StartupCompose $up
        Fail-StartupBootstrap $status $state.ExitCode
      }
    }
    Invoke-StartupSleep $poll
  }

  $up.WaitForExit()
  $upRc = $up.ExitCode
  if ($upRc -ne 0) {
    # Compose stops at a failed bootstrap too, sometimes before a poll saw it.
    $state = Get-StartupServiceState $service
    $previous = $previousKey -and ("$($state.Id)|$($state.StartedAt)" -eq $previousKey)
    if (-not $previous -and ($state.Status -eq 'dead' -or ($state.Status -eq 'exited' -and $state.ExitCode -ne '0'))) {
      Fail-StartupBootstrap $state.Status $state.ExitCode
    }
    Append-Log @("[fail] compose up returned $upRc")
    return $false
  }
  if ($runningSince -ge 0 -and $finishedAt -lt 0) { Ok "Schema migrations finished ($service)" }
  return $true
}
