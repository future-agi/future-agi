# shellcheck shell=bash
# Starting the distributed stack for bin/install (bin/lib/startup.ps1 is the
# installer's PowerShell copy). Sourced, with the repository root as the
# working directory; needs DC, bin/install's output helpers (say, ok, warn,
# die and log_only), DIM and RESET set, and fd 3 open on the install log.
#
# An upgrade's schema migrations run in the one-shot postgres-schema-bootstrap
# service, and the app services wait for it to complete successfully. On a
# large database that takes longer than any fixed Compose wait (one upgrade
# applied 185 migrations over about 40 minutes, while bin/install used to give
# the whole start 20). So the stack starts with ONE `compose up`, in the
# background, while the installer watches the bootstrap: a progress line at
# least every minute while it runs, and a failure only when it exits non-zero,
# when INSTALL_MIGRATION_TIMEOUT_SECONDS passes, or when the rest of the stack
# misses its own bound after the migrations finished. Never start a second
# `compose up` here: Compose starts a service's stopped containers on every
# `up`, so it would run the exited bootstrap (or mirror job) again.

STARTUP_BOOTSTRAP_SERVICE=postgres-schema-bootstrap

# The clock and the pause between polls; the tests replace both.
startup_now() { date +%s; }
startup_sleep() { sleep "$1"; }

# id|status|exit_code|started_at of SERVICE's container, or
# missing|missing|-1|never when it has none.
startup_service_state() { # SERVICE
  local id state
  id=$($DC ps -a -q "$1" 2>/dev/null | head -1 || true)
  if [[ -z "$id" ]]; then
    printf 'missing|missing|-1|never'
    return 0
  fi
  state=$(docker inspect --format '{{.State.Status}}|{{.State.ExitCode}}|{{.State.StartedAt}}' "$id" 2>/dev/null || true)
  if [[ -z "$state" ]]; then
    printf '%s|missing|-1|never' "$id"
  else
    printf '%s|%s' "$id" "$state"
  fi
}

# Stops this installer's own `compose up` client, PID, and nothing else: the
# containers keep the state they have, as when Compose's own wait times out.
# The one place that signals Compose.
startup_stop_compose() { # PID
  kill "$1" 2>/dev/null || true
  wait "$1" 2>/dev/null || true
}

# The project's containers and the bootstrap's last log lines, to the install log.
startup_save_diagnostics() {
  log_only ""
  log_only "--- compose ps -a ---"
  $DC ps -a >&3 2>&1 || true
  log_only "--- $STARTUP_BOOTSTRAP_SERVICE logs ---"
  $DC logs --tail 200 "$STARTUP_BOOTSTRAP_SERVICE" >&3 2>&1 || true
}

startup_bootstrap_failed() { # STATUS EXIT_CODE
  startup_save_diagnostics
  die "The schema migrations failed: $STARTUP_BOOTSTRAP_SERVICE ended with status=$1 exit_code=$2; partial state retained, no automatic retry. Read 'docker compose logs $STARTUP_BOOTSTRAP_SERVICE' before explicitly resuming."
}

# SECONDS as whole minutes, rounded, at least 1.
startup_minutes() { # SECONDS
  local minutes=$(( ($1 + 30) / 60 ))
  if (( minutes < 1 )); then minutes=1; fi
  printf '%s' "$minutes"
}

# Starts the distributed stack with one `compose up` and waits for it.
# BUILD_FLAG is --build or --no-build. The schema migrations may run for
# MIGRATION_TIMEOUT seconds from when they are first seen running (image
# builds come first and do not count); the rest of the stack then has
# READY_TIMEOUT seconds to report running/healthy. Dies, naming the bootstrap,
# when it fails or outlasts its limit. Returns non-zero when `compose up`
# fails otherwise or the rest of the stack misses its bound; the caller then
# reports the failure. Called as `if ! ...`, so errexit is off in here and
# every status is checked.
start_distributed_stack() { # BUILD_FLAG MIGRATION_TIMEOUT READY_TIMEOUT
  local build_flag="$1" migration_timeout="$2" ready_timeout="$3"
  local service="$STARTUP_BOOTSTRAP_SERVICE" poll=5 progress_every=55
  local compose_timeout=$(( migration_timeout + ready_timeout ))
  local limit_minutes=$(( migration_timeout / 60 ))
  local before_id before_status before_started previous_key=""
  local id status code started now up_pid up_rc
  local running_since=-1 last_progress=-1 finished_at=-1
  local -a up_args

  # The container an earlier run left behind. Until this `up` restarts or
  # replaces it (a new StartedAt or id), its exit says nothing about this
  # run's migrations. A running one is this run's: Compose leaves it running.
  IFS='|' read -r before_id before_status _ before_started <<<"$(startup_service_state "$service")"
  if [[ "$before_status" != running ]]; then previous_key="$before_id|$before_started"; fi

  # Compose's own wait outlasts both bounds below; it only backs them up.
  # --ansi never selects plain progress output: Compose's redrawn display
  # would overwrite the progress lines printed here.
  up_args=(--ansi never up -d "$build_flag" --wait --wait-timeout "$compose_timeout")
  log_only "running: $DC ${up_args[*]}"
  ( trap - ERR; exec $DC "${up_args[@]}" ) &
  up_pid=$!

  while kill -0 "$up_pid" 2>/dev/null; do
    now=$(startup_now)
    IFS='|' read -r id status code started <<<"$(startup_service_state "$service")"
    if [[ -n "$previous_key" && "$id|$started" == "$previous_key" ]]; then status=previous; fi
    case "$status" in
      running|restarting|paused)
        if (( running_since < 0 )); then
          running_since=$now
          last_progress=$now
          say "  ${DIM}Schema migrations started ($service). Large databases take longer; waiting up to ${limit_minutes} min (INSTALL_MIGRATION_TIMEOUT_SECONDS).${RESET}"
        elif (( now - last_progress >= progress_every )); then
          last_progress=$now
          say "  ${DIM}Migrations still running ($(startup_minutes $(( now - running_since ))) min). Large databases take longer; waiting up to ${limit_minutes} min (INSTALL_MIGRATION_TIMEOUT_SECONDS).${RESET}"
        fi
        if (( now - running_since >= migration_timeout )); then
          startup_stop_compose "$up_pid"
          startup_save_diagnostics
          die "The schema migrations ($service) were still running after ${limit_minutes} min, the INSTALL_MIGRATION_TIMEOUT_SECONDS limit (${migration_timeout} s). The installer stopped waiting, not the migrations: partial state retained, no automatic retry. Follow them with 'docker compose logs -f $service'; once 'docker compose ps -a $service' shows it exited (0), run ./bin/install again. To wait longer, raise INSTALL_MIGRATION_TIMEOUT_SECONDS (up to 86400) in .env."
        fi
        ;;
      exited|dead)
        if [[ "$status" == exited && "$code" == 0 ]]; then
          if (( finished_at < 0 )); then
            finished_at=$now
            ok "Schema migrations finished ($service)"
          elif (( now - finished_at >= ready_timeout )); then
            startup_stop_compose "$up_pid"
            warn "The schema migrations finished, but the rest of the stack did not report running/healthy within ${ready_timeout} s after that."
            return 1
          fi
        else
          startup_stop_compose "$up_pid"
          startup_bootstrap_failed "$status" "$code"
        fi
        ;;
    esac
    startup_sleep "$poll"
  done

  if wait "$up_pid"; then up_rc=0; else up_rc=$?; fi
  if (( up_rc != 0 )); then
    # Compose stops at a failed bootstrap too, sometimes before a poll saw it.
    IFS='|' read -r id status code started <<<"$(startup_service_state "$service")"
    if [[ -z "$previous_key" || "$id|$started" != "$previous_key" ]] && \
       [[ "$status" == dead || ( "$status" == exited && "$code" != 0 ) ]]; then
      startup_bootstrap_failed "$status" "$code"
    fi
    log_only "[fail] compose up returned $up_rc"
    return "$up_rc"
  fi
  if (( running_since >= 0 && finished_at < 0 )); then ok "Schema migrations finished ($service)"; fi
  return 0
}
