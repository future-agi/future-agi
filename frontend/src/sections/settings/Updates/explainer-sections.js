// Fixed product copy only. Never render host-supplied messages, commands or URLs.
export const LABELS = {
  mode: {
    manual: "Manual",
    notify: "Notify only",
    download: "Download only",
    apply: "Apply",
  },
  automation: {
    not_implemented: "Not implemented on this host",
    disabled: "Disabled",
    manual: "Manual — no unattended work",
    enabled: "Enabled",
    paused: "Paused",
    blocked: "Blocked",
  },
  discovery: {
    not_checked: "Not checked",
    current: "Current within the selected policy at the last verified check",
    available: "Verified release available",
    blocked: "Release blocked",
    failed: "Release check failed",
  },
  verification: {
    not_checked: "Not verified",
    verified: "Signature verified",
    rejected: "Signature rejected",
  },
  download: {
    not_downloaded: "Not downloaded",
    downloading: "Downloading images",
    downloaded: "Downloaded, not installed",
    failed: "Download failed",
    unknown: "Download state unknown",
  },
  operation: {
    idle: "No operation reported",
    checking: "Checking releases",
    downloading: "Downloading images",
    backing_up: "Backing up",
    staged: "Staged, not applied",
    awaiting_initialization: "Awaiting production initialization",
    applying: "Applying update",
    verifying: "Verifying readiness",
    succeeded: "Operation succeeded",
    failed: "Operation failed",
    interrupted: "Operation interrupted",
    rolling_back: "Restoring prior images",
    rolled_back: "Prior images restored; review readiness",
    restoring: "Restoring the cold backup and previous version",
    restored: "Cold backup and previous version restored",
    recovery_required: "Manual recovery required; installed version unverified",
    blocked: "Operation blocked",
    unknown: "Operation state unknown",
  },
  stage: {
    planning: "Planning",
    backup: "Backup",
    image_fetch: "Image download",
    image_verification: "Image verification",
    env_selection: "Version selection",
    production_initialization: "Production initialization",
    rollout: "Service replacement",
    readiness: "Readiness checks",
    recovery: "Recovery",
    quiescence: "Stopping services for a cold backup",
    restore: "Cold-backup restoration",
    stability: "Readiness stability checks",
  },
  phase: {
    attempt_reserved: "Attempt reserved",
    quiescing: "Stopping services",
    quiesced: "Services stopped",
    archiving: "Creating the cold backup",
    backup_sealed: "Cold backup sealed",
    env_commit_pending: "Preparing version selection",
    env_selected: "Target version selected",
    target_create_pending: "Preparing target services",
    target_created: "Target services created",
    target_starting: "Starting target services",
    target_verifying: "Checking target readiness and stability",
    success_decided: "Target acceptance recorded",
    success_metadata_committed: "Target acceptance finalized",
    succeeded: "Update succeeded",
    restore_decided: "Automatic restoration selected",
    restore_quiescing: "Stopping failed-target services",
    restore_quiesced: "Failed-target services stopped",
    restore_inputs_verified: "Restoration inputs verified",
    restoring_volumes: "Restoring persistent data",
    restore_files_verified: "Restored files verified",
    config_revert_pending: "Preparing previous configuration",
    config_reverted: "Previous configuration restored",
    restore_data_committed: "Restored data verified before startup",
    previous_create_pending: "Preparing previous-version services",
    previous_created: "Previous-version services created",
    previous_start_armed:
      "Previous-version startup authorized; no more data extraction",
    previous_starting: "Starting the previous version",
    previous_verifying: "Checking previous-version readiness",
    restore_verified: "Previous version and restored data verified",
    restore_metadata_committed: "Restoration finalized",
    restored: "Cold backup and previous version restored",
    abort_quiescence: "Resuming the unchanged previous version before backup",
    failed_available:
      "Update failed before mutation; previous version available",
  },
  kind: {
    check: "Check",
    download: "Download",
    apply: "Apply",
    rollback: "Rollback",
    restore: "Cold-backup restoration",
  },
  strategy: {
    none: "No automatic restoration reported",
    restore_backup:
      "Restore the cold backup and previous version automatically",
  },
  restore: {
    not_needed: "Not needed",
    running: "Restoration running; installed version unverified",
    verified: "Cold backup and previous version verified",
    failed: "Restoration failed; installed version unverified",
    recovery_required: "Manual recovery required; installed version unverified",
  },
  forward: {
    not_started: "Not started",
    running: "Running",
    succeeded: "Succeeded",
    failed: "Failed",
    interrupted: "Interrupted",
  },
  loss_window: {
    not_applicable: "Not applicable",
    bounded: "Bounded by the reported timestamps",
    conservative: "Conservative bounds; exact exposure is unknown",
    unknown: "Unknown; no zero-loss guarantee",
  },
  runtime: {
    consistent: "Consistent observations",
    mixed: "Mixed running versions",
    unknown: "Running versions unknown",
  },
  observation: {
    known: "Observed",
    unknown: "Unknown",
    not_deployed: "Not deployed",
  },
  component: {
    standalone: "Standalone app",
    backend: "API backend",
    frontend: "Web frontend",
    fi_collector: "Trace collector",
    agentcc_gateway: "AgentCC gateway",
    serving: "Model serving",
    serving_gpu: "GPU model serving",
    code_executor: "Code executor",
    simulation_runner: "Simulation runner",
  },
  channel: { stable: "Stable", beta: "Beta" },
  policy: {
    patch: "Patch releases",
    minor: "Minor and patch releases",
    major: "Major, minor and patch releases",
  },
  backup: {
    not_evaluated: "Not evaluated",
    required: "Backup required",
    reported_complete: "Backup hook reported completion",
    failed: "Backup failed",
    explicitly_skipped: "Explicitly skipped on the host",
    pending: "Cold backup pending",
    quiescing: "Stopping services for the cold backup",
    sealed: "Cold backup sealed for this attempt",
  },
  readiness: {
    unknown: "Unknown",
    pending: "Pending",
    passed: "Passed at the reported time",
    failed: "Failed",
  },
  rollback: {
    unsupported: "Unsupported",
    disabled: "Disabled",
    eligible: "Eligible, subject to host checks",
    ineligible: "Ineligible",
  },
  outcome: {
    succeeded: "Succeeded",
    staged: "Staged",
    rolled_back: "Rolled back",
    failed_before_rollout: "Failed before service replacement",
    failed_during_rollout: "Failed during service replacement",
    interrupted_before_rollout: "Interrupted before service replacement",
    interrupted_during_rollout: "Interrupted during service replacement",
    blocked: "Blocked",
  },
  fallback: {
    none: "No fallback reported",
    succeeded: "Prior-image fallback succeeded",
    failed: "Prior-image fallback failed",
    ineligible: "Prior-image fallback ineligible",
  },
  freshness: {
    fresh: "Fresh",
    stale: "Stale — current state unknown",
    unknown: "Freshness unknown",
  },
};

export const OPERATOR_ACTIONS = {
  none: "No operator action reported. This is not a health guarantee.",
  inspect_host:
    "Ask the host operator to inspect the update status and prerequisites on the host before continuing.",
  prepare_installation:
    "Ask the host operator to prepare the approved checkout, required configuration and dependencies. The updater does not check out code, merge environment files or install dependencies automatically.",
  initialize_then_apply:
    "Host operator action required: keep the backup, complete the explicitly approved production initialization, then apply the staged release on the host. Staging alone does not update running services.",
  review_recovery:
    "Host operator action required: inspect the interrupted or failed operation and recovery evidence on the host. Do not retry blindly. Image rollback does not restore databases.",
  repair_status_reporting:
    "Ask the host operator to inspect the dedicated read-only status integration. The app cannot create, repair or write the report.",
};

export const UNAVAILABLE_REASONS = {
  not_configured:
    "Host update reporting is not connected. Ask the host operator to configure the optional status integration.",
  not_reported:
    "No host report yet. Ask the host operator to inspect reporting on the host.",
  unreadable:
    "The host report cannot be read. Ask the host operator to inspect reporting permissions.",
  malformed:
    "The host report could not be validated. Ask the host operator to inspect reporting.",
  unsupported_schema: "This reporting format is unsupported by this app.",
  untrusted_file:
    "The host report did not pass the file safety checks. Ask the host operator to inspect reporting.",
  clock_skew:
    "The report timestamps could not be trusted. Ask the host operator to check the host clock.",
  installation_mismatch:
    "The report does not match this installation. Ask the host operator to inspect the status integration.",
  unsupported_installation:
    "Host update reporting is not supported by this installation or server version. This integration is for OSS Compose deployments, not cloud, licensed EE or Helm.",
  request_failed:
    "The latest host report could not be read. The app may be temporarily disconnected during an update. Reconnection does not prove success or rollback.",
  request_rejected:
    "The server could not provide a host report. Ask the host operator to inspect reporting.",
  permission_denied:
    "Host update reporting is not available to this account in the selected organization.",
};

export const REASON_TEXT = {
  manifest_unavailable: "Release metadata unavailable",
  signature_rejected: "Release signature rejected",
  channel_mismatch: "Release does not match the selected channel",
  version_policy_blocked: "Release blocked by version policy",
  release_incomplete: "Release artifacts incomplete",
  discovery_incomplete: "Release discovery incomplete",
  immutable_release_changed: "Published release changed",
  network_unavailable: "Host network unavailable",
  rate_limited: "Release service rate limit reached",
  cache_invalid: "Release cache invalid",
  ownership_unconfirmed: "Installation ownership unconfirmed",
  deployment_files_mismatch: "Deployment files do not match the release",
  required_env_missing: "Required host configuration missing",
  unsupported_contract: "Release contract unsupported",
  uncertified_release: "Release not certified for unattended use",
  configuration_changed: "Host configuration changed",
  backup_required: "Backup required",
  backup_failed: "Backup failed",
  backup_proof_invalid: "Backup proof invalid",
  backup_expired: "Backup proof expired",
  stale_stage: "Staged release needs review",
  env_conflict: "Environment selection conflict",
  initialization_required: "Explicit production initialization required",
  manual_stage_present: "Manual stage requires operator review",
  recovery_required: "Host recovery review required",
  readiness_failed: "Readiness checks failed",
  operation_interrupted: "Operation interrupted",
  operation_timeout: "Operation timed out",
  rollback_ineligible: "Automatic image rollback ineligible",
  rollback_failed: "Image rollback failed",
  failed_target_latched: "Failed target quarantined; operator review required",
  running_versions_unknown: "Running versions unknown",
  mixed_running_versions: "Mixed running versions",
  outside_window: "Waiting for the next permitted maintenance start",
  insufficient_window: "Not enough time to start in the maintenance window",
  clock_skew: "Host clock needs review",
  timezone_unavailable: "Maintenance timezone unavailable",
  host_verifier_required: "Host signature verifier required",
  image_digest_mismatch: "Image digest mismatch",
  disk_space_insufficient: "Insufficient host disk space",
  status_reporting_unavailable: "Host status reporting unavailable",
  scheduler_unregistered: "Host scheduler not registered",
  unsupported_installation: "Installation unsupported",
  control_invalid: "Host automation controls invalid",
  process_failed: "Host update process failed",
  restore_not_certified:
    "Automatic restoration is not certified for this release",
  transition_not_rehearsed: "This release transition has not been rehearsed",
  platform_not_rehearsed: "This platform has not been rehearsed",
  restore_procedure_unsupported: "Restoration procedure unsupported",
  migration_inventory_unsupported: "Migration inventory layout unsupported",
  migration_inventory_mismatch:
    "Applied migrations do not match the expected inventory",
  backup_inventory_mismatch: "Cold-backup inventory mismatch",
  restore_capacity_unknown: "Restoration capacity could not be verified",
  restore_capacity_insufficient: "Insufficient capacity for restoration",
  restore_child_ambiguous:
    "A restoration process could not be safely reconciled",
  restore_verification_failed:
    "Restored data or previous-version verification failed",
  automatic_recovery_pending:
    "Automatic recovery is pending; keep the backup and safety scheduler",
  restored_after_update_failure:
    "Update failed; the cold backup and previous version were restored",
  migration_exceeds_window:
    "Notify-only: migration exceeds the maintenance window or update budget",
  insufficient_disk_for_migration:
    "Notify-only: insufficient disk space for migration and protected restoration",
  backup_exceeds_window:
    "Notify-only: cold backup or restoration exceeds the maintenance window or update budget",
  insufficient_disk_for_backup:
    "Notify-only: insufficient disk space for the cold backup and restoration",
  migration_stalled:
    "Migration stopped making progress; automatic restoration is required after backup",
  disk_watermark_abort:
    "Protected restoration disk reserve reached; update aborted",
};

export const ACTIVE_STATES = [
  "checking",
  "downloading",
  "backing_up",
  "applying",
  "verifying",
  "rolling_back",
  "restoring",
];

export function noticeForStatus(data) {
  if (data?.availability !== "available" || !data.snapshot) return null;
  const { operation, safety, discovery, download } = data.snapshot;
  let text;
  let attention = false;
  if (
    safety.operator_action !== "none" ||
    [
      "failed",
      "interrupted",
      "blocked",
      "rolled_back",
      "recovery_required",
    ].includes(operation.state)
  ) {
    text = "Update needs host operator attention.";
    attention = true;
  } else if (["staged", "awaiting_initialization"].includes(operation.state)) {
    text = "Update staged, not applied. Host operator action required.";
    attention = true;
  } else if (ACTIVE_STATES.includes(operation.state)) {
    text = `Last reported: ${LABELS.operation[operation.state].toLowerCase()}.`;
  } else if (download.state === "downloaded") {
    text = `Release ${download.target_version} downloaded, not installed.`;
  } else if (discovery.state === "available") {
    text = `Verified release ${discovery.candidate_version} available at the last check.`;
  } else {
    return null;
  }
  const old =
    data.freshness !== "fresh" ||
    (ACTIVE_STATES.includes(operation.state) &&
      data.progress_freshness !== "fresh") ||
    (discovery.state === "available" && data.discovery_freshness !== "fresh");
  return {
    text: old
      ? `${text} Report is stale or freshness is unknown; current state is unknown.`
      : text,
    attention,
  };
}

export const EXPLAINER_SECTIONS = [
  {
    id: "ownership",
    title: "The host owns updates",
    text: "This page only reads a report. The host operator uses bin/update outside the app; organization admin access does not grant host access. Manual mode and unattended updates disabled are the defaults.",
    mechanism:
      "The browser reads HostUpdateStatusView through Axios and React Query. read_public_status validates a sanitized file. Separately, Installation, Verifier, build_plan and Updater handle host-side checks and Docker Compose changes.",
  },
  {
    id: "versions",
    title: "Selected is not the same as running",
    text: "Selected versions are configured for the next start. Running versions are observed from containers. Staging can select a new release while the previous release is still running. Mixed or missing observations are not a healthy result.",
    mechanism:
      "Installation and host container observations supply separate values. A download only caches verified images; it is not a stage and does not change selected configuration.",
  },
  {
    id: "trust",
    title: "A version tag is not enough",
    text: "The host verifies release signatures, artifacts and the selected stable or beta channel and version policy. An incomplete or rejected release cannot be used. A current result applies only to that policy and the time of the last successful check.",
    mechanism:
      "Release discovery and fetch_manifest obtain metadata; Verifier checks trust and build_plan checks compatibility. Release publication has its own image, chart and validation gates.",
  },
  {
    id: "automation",
    title: "Automation stays on the host",
    text: "Manual does no unattended work. Notify checks and reports. Download caches verified images without restarting services. Apply requires explicit host opt-in and every safety prerequisite. The reported effective state, not the configured mode alone, tells you whether automation is enabled.",
    mechanism:
      "Settings, runner_state and schedule manage host controls. A maintenance window permits a start, not a promise to finish before its end. Pause and disable prevent new automatic attempts, not required safety recovery; explicit host commands remain possible. Not implemented or unregistered means automation must not be assumed to be running.",
  },
  {
    id: "backup",
    title: "Cold backups protect enrolled automatic updates",
    text: "After explicit host opt-in, qualified Standalone automatic updates stop the app, seal a cold backup and keep the old version stopped while the new version starts and may migrate the database. A legacy manual backup-hook completion alone is not restore proof. Backups and recovery evidence are retained, not automatically pruned.",
    mechanism:
      "The host checks signed transition evidence, the backup seal and installation identities. Large databases or volumes that cannot fit the time or disk budgets stay notify-only. Hook commands, output and backup locations never appear in this page.",
  },
  {
    id: "production",
    title: "Production has three separate steps",
    text: "Stage first. Perform explicitly approved release initialization second. Apply third. The app cannot perform any of these steps, and staging does not restart running services.",
    mechanism:
      "Updater.apply preserves the production boundary. PostgreSQL, ClickHouse and PeerDB initialization jobs stay check-only until the operator deliberately invokes approved initialization on the host.",
  },
  {
    id: "readiness",
    title: "Updates require an outage and health checks",
    text: "Automatic updates make the app unavailable during the cold backup and new-version startup, usually for several minutes and longer for large databases. This page may disconnect. Reconnecting does not prove success or restoration. Read the host readiness and stability result and its observation time; no percentage is estimated here.",
    mechanism:
      "Readiness checks the Standalone app or the Distributed API and service rollout. A liveness response alone is not proof of migration compatibility or every service being ready.",
  },
  {
    id: "recovery",
    title: "Automatic restoration is not image-only rollback",
    text: "For enrolled, qualified automatic updates, a failure after the cold backup restores that backup and the previous version automatically, including after database migrations. The forward update remains failed even when restoration succeeds, and the failed target will not be retried automatically. Anything written to the new version before rollback is discarded; in-flight jobs and Redis cache are reset, and outside calls such as webhooks or model-provider requests cannot be undone. Some failures require manual recovery: the updater stops rather than guessing, and the installed version stays unverified.",
    mechanism:
      "The recovery ledger, backup seal, envcommit and verified storage identities control restoration. Keep the backup and safety scheduler until recovery is resolved; pause or disable does not cancel required recovery. Legacy manual image rollback does not restore databases. Private recovery evidence remains on the host.",
  },
  {
    id: "privacy",
    title: "Only a sanitized report crosses into the app",
    text: "Active human organization Owners and Admins can read reporting when the server allows it. Members, workspace-only admins and API keys do not gain access. There is no update, pause or rollback button here.",
    mechanism:
      "CanReadHostUpdateStatus checks organization authority. The API reads only a dedicated read-only status directory, never private journals, environment values, backup secrets or the Docker socket.",
  },
  {
    id: "freshness",
    title: "Missing is not up to date",
    text: "A missing optional integration means reporting is unavailable, not that the installation is current or healthy. Release checks, running observations and active progress have separate freshness labels. Old progress does not diagnose a failed update.",
    mechanism:
      "Refresh report only rereads the host report through the API; it does not contact a release registry or start a host check. Visible tabs poll once a minute; failures do not block the rest of the app.",
  },
  {
    id: "services",
    title: "Where the report is read",
    text: "Standalone uses the app service, which contains the API and embedded processes. Distributed uses the backend service; the frontend only calls its API. Production adds the explicit initialization boundary.",
    mechanism:
      "Workers, the collector, AgentCC gateway, Temporal and PeerDB do not control host updates. Neither Falcon AI nor other agents receive update tools or privileges. This integration does not install a Helm updater.",
  },
  {
    id: "gates",
    title: "Status is not release approval",
    text: "A report describes observations, not permission to bypass release validation. If required checkout files, configuration, dependencies, backup or compatibility evidence are missing, the operator must resolve them on the host before applying.",
    mechanism:
      "The host does not automatically check out code, merge environment files or install dependencies. Only a qualifying signed, rehearsed transition permits restore-backed automatic apply; missing proof stays notify-only. Release and deployment validation remain separate gates.",
  },
];
