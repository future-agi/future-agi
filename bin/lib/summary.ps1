# bin/install.ps1's closing summary. Dot-sourced; needs env.ps1, the
# installer's output helpers and the settings Show-Summary reads from it:
# $DcText, $AppService, $IsDistributed, $FromSource, $LogFile, $UserEmail and
# $AccountState with its $Account* values.

function Show-Summary {
  param($FrontendPort, $BackendPort)
  $collectorHttpPort = Get-EnvValue 'FI_COLLECTOR_OTLP_HTTP_PORT'
  if (-not $collectorHttpPort) { $collectorHttpPort = 4318 }
  # The URL SDKs send traces to, as the compose files give it to the app.
  $localCollectorUrl = "http://localhost:$collectorHttpPort"
  $collectorUrl = Get-ComposeEnvValue 'FI_COLLECTOR_PUBLIC_URL' $localCollectorUrl
  $gatewayPort = Get-EnvValue 'AGENTCC_GATEWAY_PORT'
  if (-not $gatewayPort) { $gatewayPort = 8090 }
  $uiUrl = "http://localhost:$FrontendPort"
  $upgradeCmd = 'git pull; .\bin\install.ps1'
  if ($FromSource -or (Get-EnvValue 'FUTURE_AGI_VERSION') -eq 'local') { $upgradeCmd += ' -FromSource' }
  $setupLine = if ($IsDistributed) { 'Distributed setup: one container per service' } else { 'Standalone setup: one app container, next to Postgres and ClickHouse' }

  Write-Host ""
  Write-Host "  +-------------------------------------------+" -ForegroundColor Green
  Write-Host "  |   Future AGI is up!                       |" -ForegroundColor Green
  Write-Host "  +-------------------------------------------+" -ForegroundColor Green
  Append-Log @("Future AGI is up")
  Say "  $setupLine"
  Say ""
  if ($AccountState -eq $AccountCreated -or $AccountState -eq $AccountExists) {
    Say "  1. Sign in"
    Say "     $uiUrl/auth/jwt/login   as $UserEmail"
  } elseif ($AccountState -eq $AccountFailed) {
    Say "  1. Create your account (ACTION REQUIRED)"
    Say "     The stack is running, but no account was created. Create one with:"
    Say "     $DcText exec $AppService python manage.py create_user"
    Say "     or sign up at $uiUrl"
  } else {
    Say "  1. Create your account"
    Say "     $uiUrl   (a short setup check, then sign-up)"
  }
  Say ""
  Say "  2. Get your API keys"
  Say "     In the app: Keys, in the left sidebar  ->  $uiUrl/dashboard/keys"
  Say "     Copy the API key and the secret key."
  Say ""
  Say "  3. Send your first trace (on this machine; paste with your keys from step 2)"
  Say ""
  # Flush left, so the here-string and the Python indentation survive a copy-paste.
  Say "pip install fi-instrumentation-otel"
  Say "`$env:FI_API_KEY = `"<your API key>`"; `$env:FI_SECRET_KEY = `"<your secret key>`"; `$env:FI_BASE_URL = `"$collectorUrl`""
  Say "@'"
  Say "from fi_instrumentation import register"
  Say "from fi_instrumentation.fi_types import ProjectType"
  Say "tracer_provider = register(project_name=`"my-first-project`", project_type=ProjectType.OBSERVE)"
  Say "with tracer_provider.get_tracer(`"quickstart`").start_as_current_span(`"hello-future-agi`") as span:"
  Say "    span.set_attribute(`"input.value`", `"Hello, Future AGI`")"
  Say "tracer_provider.force_flush()"
  Say "'@ | python -"
  Say ""
  Say "     Then open Tracing in the sidebar: my-first-project holds your first span."
  Say ""
  Say "  Endpoints"
  Say "     UI           $uiUrl"
  Say "     API          http://localhost:$BackendPort"
  if ($collectorUrl -eq $localCollectorUrl) {
    Say "     Traces       $collectorUrl  (OTLP/HTTP; this machine only)"
  } else {
    Say "     Traces       $collectorUrl  (OTLP/HTTP)"
  }
  Say "     LLM gateway  http://localhost:$gatewayPort"
  if ($IsDistributed) {
    $profiles = @((Get-EnvValue 'COMPOSE_PROFILES') -split ',' | ForEach-Object { $_.Trim() })
    if ($profiles -contains 'all' -or $profiles -contains 'full' -or $profiles -contains 'peerdb') {
      $peerdbUiPort = Get-EnvValue 'PEERDB_UI_PORT'
      if (-not $peerdbUiPort) { $peerdbUiPort = 3001 }
      Say "     PeerDB UI    http://localhost:$peerdbUiPort  (peerdb / peerdb)"
    }
  }
  Say ""
  Say "  Manage"
  Say "     Logs         $DcText logs -f $AppService"
  Say "     Stop         $DcText down  (keeps your data)"
  Say "     Start        $DcText up -d"
  Say "     Upgrade      $upgradeCmd"
  Say "     Uninstall    $DcText down -v  (deletes all data)"
  Say "     Settings     .env  (every variable: https://docs.futureagi.com/docs/self-hosting/configuration/reference)"
  Say "     Install log  $LogFile"
  if ($IsDistributed) {
    Say ""
    Say "  Existing-data catalog backfill: restarts do not scan historical data."
    Say "  After an upgrade, see fi-collector/PROPERTY_CATALOG_OSS.md."
  }
  Say ""
  Say "  Star us: https://github.com/future-agi/future-agi"
  Say ""
}
