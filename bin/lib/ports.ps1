# bin/install.ps1's host-port preflight. Dot-sourced; needs env.ps1 and the
# installer's output helpers and Invoke-Probe.

# All host-bound ports of the chosen stack, as the compose files publish them
# (futureagi/tests/test_oss_install_hardening.py compares them). The
# standalone install publishes no Postgres, ClickHouse, Redis or Temporal port.
function Get-StackPorts {
  param([bool]$Distributed, [string[]]$Profiles)
  if ($Distributed) {
    $ports = [ordered]@{
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
      'FI_COLLECTOR_OTLP_PORT'      = 4317
      'FI_COLLECTOR_OTLP_HTTP_PORT' = 4318
      'FI_COLLECTOR_ADMIN_PORT'     = 9464
      'PEERDB_PORT'          = 9900
    }
    # UIs that only run under a COMPOSE_PROFILES entry.
    if ($Profiles -contains 'all' -or $Profiles -contains 'full' -or $Profiles -contains 'peerdb') { $ports['PEERDB_UI_PORT'] = 3001 }
    if ($Profiles -contains 'all' -or $Profiles -contains 'full' -or $Profiles -contains 'observability') { $ports['TEMPORAL_UI_PORT'] = 8085 }
  } else {
    $ports = [ordered]@{
      'FRONTEND_PORT'               = 3000
      'BACKEND_PORT'                = 8000
      'FI_COLLECTOR_OTLP_PORT'      = 4317
      'FI_COLLECTOR_OTLP_HTTP_PORT' = 4318
      'AGENTCC_GATEWAY_PORT'        = 8090
      'MINIO_API_PORT'              = 9005
    }
  }
  return $ports
}

function Test-PortFree {
  param([int]$Port)
  $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
  return ($null -eq $listener)
}

# Stops the install while a port of the stack is taken.
function Test-HostPorts {
  param([bool]$Distributed, [string]$ProjectName)
  $profiles = @((Get-EnvValue 'COMPOSE_PROFILES') -split ',' | ForEach-Object { $_.Trim() })
  $portsToCheck = Get-StackPorts $Distributed $profiles

  # Ports published by this project's own running containers are fine on a re-run.
  $ownPorts = @(Invoke-Probe { docker ps --filter "label=com.docker.compose.project=$ProjectName" --format '{{.Ports}}' }) -join ','

  $conflicts = @()
  # Two vars on one port collide inside the stack whatever else is listening:
  # compose fails with "port is already allocated", and leaves nothing bound.
  $portOwners = @{}
  foreach ($var in $portsToCheck.Keys) {
    $val = Get-EnvValue $var
    if (-not $val) { $val = $portsToCheck[$var] }
    $port = [int]$val
    if ($portOwners.ContainsKey($port)) {
      Warn "  $var=$port  is taken: $($portOwners[$port]) is set to the same port"
      $conflicts += "$var=$port (same port as $($portOwners[$port]))"
      continue
    }
    $portOwners[$port] = $var
    if (Test-PortFree $port) {
      Ok "  $var=$port  free"
    } elseif ($ownPorts -match ":$port->") {
      Ok "  $var=$port  (held by this project's container -- fine)"
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
}
