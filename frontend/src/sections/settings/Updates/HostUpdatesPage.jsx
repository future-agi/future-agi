import PropTypes from "prop-types";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Card,
  Divider,
  Stack,
  Typography,
} from "@mui/material";

import {
  humanBytes,
  humanDuration,
  useHostUpdateStatus,
} from "src/api/hostUpdates/host-update-status";
import {
  ACTIVE_STATES,
  EXPLAINER_SECTIONS,
  LABELS,
  OPERATOR_ACTIONS,
  REASON_TEXT,
  UNAVAILABLE_REASONS,
} from "./explainer-sections";

function ReportTime({ value }) {
  return value ? (
    <time dateTime={value}>{value.replace("T", " ").replace("Z", " UTC")}</time>
  ) : (
    "Not reported"
  );
}
ReportTime.propTypes = { value: PropTypes.string };

function Field({ label, children, testId }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "minmax(140px, 1fr) 2fr" },
        gap: 0.5,
        py: 0.75,
      }}
    >
      <Typography component="dt" variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography
        component="dd"
        data-testid={testId}
        variant="body2"
        sx={{ m: 0, overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </Box>
  );
}
Field.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  testId: PropTypes.string,
};

function ReportSection({ title, children }) {
  return (
    <Card
      component="section"
      variant="outlined"
      sx={{ p: { xs: 2, sm: 3 }, borderRadius: 2, minWidth: 0 }}
    >
      <Typography component="h2" variant="h6" sx={{ mb: 1 }}>
        {title}
      </Typography>
      {children}
    </Card>
  );
}
ReportSection.propTypes = {
  title: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function Reasons({ codes }) {
  return (
    codes.length > 0 && (
      <Box component="ul" sx={{ pl: 2.5, mb: 0 }}>
        {codes.map((code) => (
          <Typography component="li" variant="body2" key={code}>
            {REASON_TEXT[code]}
          </Typography>
        ))}
      </Box>
    )
  );
}
Reasons.propTypes = { codes: PropTypes.arrayOf(PropTypes.string).isRequired };

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function maintenanceWindow(window, generatedAt) {
  if (!window) return "Not configured";
  // Offset is tied to the report date, not assumed constant across DST changes.
  const offset = new Intl.DateTimeFormat("en", {
    timeZone: window.timezone,
    timeZoneName: "shortOffset",
  })
    .formatToParts(new Date(generatedAt))
    .find((part) => part.type === "timeZoneName")?.value;
  const zone = `${window.timezone} (${offset} at report time)`;
  if (window.kind === "always") return `Any time — ${zone}`;
  return `${window.days_of_week.map((day) => DAYS[day - 1]).join(", ")}, ${window.start}–${window.end} — ${zone}`;
}

export default function HostUpdatesPage() {
  const query = useHostUpdateStatus();
  if (query.pending)
    return (
      <Typography role="status">Loading update reporting access…</Typography>
    );
  if (!query.allowed || query.data?.availability === "forbidden") {
    return (
      <Typography role="status">
        Host update reporting is not available to this account or deployment.
      </Typography>
    );
  }
  const data = query.data;
  const snapshot = data?.availability === "available" ? data.snapshot : null;
  const old = snapshot && data.freshness !== "fresh";

  return (
    <Stack
      data-testid="host-updates-page"
      spacing={2}
      sx={{
        width: "100%",
        maxWidth: 960,
        minWidth: 0,
        mx: "auto",
        px: { xs: 2, sm: 0 },
        pb: 4,
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
      >
        <Box>
          <Typography component="h1" variant="h5">
            Updates
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Reported by the host. Read-only in the app.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          color="inherit"
          onClick={query.refresh}
          disabled={!query.canRefresh || query.isFetching}
          aria-describedby="refresh-report-help"
        >
          {query.isFetching ? "Reading report…" : "Refresh report"}
        </Button>
      </Stack>
      <Typography
        id="refresh-report-help"
        variant="caption"
        color="text.secondary"
      >
        Rereads the latest host report. Does not start a release check or an
        update.
      </Typography>

      <Box
        data-testid="host-updates-summary"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        sx={{ p: 2, bgcolor: "background.neutral", borderRadius: 1 }}
      >
        {query.isLoading && "Reading the host report…"}
        {!query.isLoading && !snapshot && (
          <>
            <Typography
              variant="subtitle2"
              data-testid="host-updates-unavailable"
            >
              {data?.availability === "unsupported"
                ? "Reporting unsupported"
                : "Reporting unavailable"}
            </Typography>
            <Typography variant="body2">
              {UNAVAILABLE_REASONS[data?.reason_code] ||
                UNAVAILABLE_REASONS.request_failed}
            </Typography>
            <Typography variant="body2">
              Update availability, running versions and automation are unknown.
            </Typography>
          </>
        )}
        {snapshot && (
          <>
            <Typography variant="subtitle2">
              {old
                ? "Stale report or unknown freshness — current state unknown"
                : "Latest host report"}
            </Typography>
            <Typography variant="body2">
              As of <ReportTime value={snapshot.generated_at} />. Report age
              when read: {data.age_seconds} seconds.
            </Typography>
            {query.automaticNotice && (
              <Typography variant="body2">
                {query.automaticNotice.text}
              </Typography>
            )}
            {old && (
              <Typography variant="body2">
                The values below are historical observations, not a current
                health assessment.
              </Typography>
            )}
          </>
        )}
      </Box>

      {snapshot && (
        <>
          <ReportSection title="Selected and running">
            <Typography variant="body2" color="text.secondary">
              Selected configuration can change before running services. Neither
              a download nor a stage proves the release is running.
            </Typography>
            <Box component="dl" sx={{ m: 0 }}>
              <Field label="Selected release">
                {snapshot.versions.selected_release || "Unknown"}
              </Field>
              <Field
                label="Running release (last observed)"
                testId="host-updates-installed-version"
              >
                {snapshot.versions.running_release || "Unknown"}
              </Field>
              <Field label="Runtime observations">
                {LABELS.runtime[snapshot.versions.runtime_state]}
              </Field>
              <Field label="Versions observed at">
                <ReportTime value={snapshot.versions.observed_at} />
              </Field>
              <Field
                label="Version freshness"
                testId="host-updates-version-freshness"
              >
                {LABELS.freshness[data.versions_freshness]}
              </Field>
              <Field label="Deployment">
                {snapshot.installation.topology === "standalone"
                  ? "Standalone"
                  : "Distributed"}
                {snapshot.installation.production ? " · Production" : ""}
              </Field>
            </Box>
            {snapshot.versions.components.length > 0 && (
              <Accordion disableGutters elevation={0}>
                <AccordionSummary
                  expandIcon={<span aria-hidden="true">+</span>}
                  id="components-heading"
                  aria-controls="components-content"
                >
                  <Typography variant="subtitle2">
                    Component versions
                  </Typography>
                </AccordionSummary>
                <AccordionDetails id="components-content">
                  {snapshot.versions.components.map((item) => (
                    <Box key={item.component} sx={{ mb: 2 }}>
                      <Typography component="h3" variant="subtitle2">
                        {LABELS.component[item.component]}
                      </Typography>
                      <Box component="dl" sx={{ m: 0 }}>
                        <Field label="Selected">
                          {item.selected_version || "Unknown"}
                        </Field>
                        <Field label="Observed running">
                          {item.observed_versions.join(", ") ||
                            LABELS.observation[item.observation]}
                        </Field>
                      </Box>
                    </Box>
                  ))}
                </AccordionDetails>
              </Accordion>
            )}
          </ReportSection>

          <ReportSection title="Release check">
            <Box component="dl" sx={{ m: 0 }}>
              <Field label="Last check result">
                {snapshot.discovery.state === "current" &&
                (old || data.discovery_freshness !== "fresh")
                  ? "Previously current at the last verified check; availability now unknown"
                  : LABELS.discovery[snapshot.discovery.state]}
              </Field>
              <Field
                label="Verified candidate (last check)"
                testId="host-updates-available-version"
              >
                {snapshot.discovery.candidate_version ||
                  "No verified candidate reported"}
              </Field>
              <Field label="Verification">
                {LABELS.verification[snapshot.discovery.verification]}
              </Field>
              <Field label="Last verified check">
                <ReportTime value={snapshot.discovery.checked_at} />
              </Field>
              <Field label="Last check attempt">
                <ReportTime value={snapshot.discovery.last_attempt_at} />
              </Field>
              <Field
                label="Check freshness"
                testId="host-updates-check-freshness"
              >
                {LABELS.freshness[data.discovery_freshness]}
              </Field>
              <Field label="Channel and policy">
                {LABELS.channel[snapshot.policy.channel]} ·{" "}
                {LABELS.policy[snapshot.policy.version_policy]}
              </Field>
              <Field label="Host check interval">
                {snapshot.policy.check_interval_seconds} seconds
              </Field>
            </Box>
            <Reasons codes={snapshot.discovery.reason_codes} />
          </ReportSection>

          <ReportSection title="Automation and maintenance">
            <Box component="dl" sx={{ m: 0 }}>
              <Field label="Configured mode">
                {LABELS.mode[snapshot.automation.mode]}
              </Field>
              <Field label="Unattended opt-in">
                {snapshot.automation.enabled
                  ? "Enabled in host configuration"
                  : "Disabled in host configuration"}
              </Field>
              <Field label="Effective state (as reported)">
                {LABELS.automation[snapshot.automation.state]}
              </Field>
              <Field label="Pause">
                {snapshot.automation.pause_indefinite ? (
                  "Indefinite — host operator must resume"
                ) : snapshot.automation.paused_until ? (
                  <ReportTime value={snapshot.automation.paused_until} />
                ) : (
                  "No pause reported"
                )}
              </Field>
              <Field label="Maintenance window">
                {maintenanceWindow(
                  snapshot.automation.maintenance_window,
                  snapshot.generated_at,
                )}
              </Field>
              <Field label="Last scheduler tick">
                <ReportTime value={snapshot.automation.last_tick_at} />
              </Field>
              <Field label="Next check (as reported)">
                <ReportTime value={snapshot.automation.next_check_at} />
              </Field>
              <Field label="Next action (as reported)">
                <ReportTime value={snapshot.automation.next_action_at} />
              </Field>
            </Box>
            <Typography variant="body2" color="text.secondary">
              A window permits starting work, not a guaranteed finish time.
              Pause and disable prevent new automatic attempts, not required
              safety recovery. Keep the backup and safety scheduler until
              recovery is resolved. Explicit host commands remain possible.
            </Typography>
          </ReportSection>

          <ReportSection title="Download and operation">
            <Box component="dl" sx={{ m: 0 }}>
              <Field label="Download">
                {LABELS.download[snapshot.download.state]}
              </Field>
              <Field label="Download target">
                {snapshot.download.target_version || "Not reported"}
              </Field>
              <Field label="Download completed">
                <ReportTime value={snapshot.download.completed_at} />
              </Field>
              <Field label="Verified cached components">
                {snapshot.download.verified_components
                  .map((name) => LABELS.component[name])
                  .join(", ") || "None reported"}
              </Field>
              <Field label="Operation (last reported)">
                {LABELS.operation[snapshot.operation.state]}
              </Field>
              <Field label="Operation kind">
                {LABELS.kind[snapshot.operation.kind] || "None reported"}
              </Field>
              <Field label="Stage">
                {LABELS.stage[snapshot.operation.stage] || "Not reported"}
              </Field>
              {Object.hasOwn(snapshot.operation, "phase") && (
                <Field label="Last verified phase">
                  {LABELS.phase[snapshot.operation.phase] || "Not reported"}
                </Field>
              )}
              <Field label="Started">
                <ReportTime value={snapshot.operation.started_at} />
              </Field>
              <Field label="Finished">
                <ReportTime value={snapshot.operation.finished_at} />
              </Field>
              <Field label="Progress reported at">
                <ReportTime value={snapshot.operation.reported_at} />
              </Field>
              <Field label="Progress freshness">
                {LABELS.freshness[data.progress_freshness]}
              </Field>
            </Box>
            {ACTIVE_STATES.includes(snapshot.operation.state) && (
              <Typography variant="body2">
                This is the last reported progress, not a live percentage. Old
                progress does not prove failure. The API may disconnect during
                service replacement.
              </Typography>
            )}
            <Reasons codes={snapshot.download.reason_codes} />
            <Reasons codes={snapshot.operation.reason_codes} />
          </ReportSection>

          <ReportSection title="Host operator next step">
            <Typography variant="body2" sx={{ mb: 2 }}>
              {OPERATOR_ACTIONS[snapshot.safety.operator_action]}
            </Typography>
            {["staged", "awaiting_initialization"].includes(
              snapshot.operation.state,
            ) && (
              <Typography variant="body2" sx={{ mb: 2 }}>
                Stage is not apply. In production, the host operator must keep
                the backup, explicitly initialize the approved release, then
                apply.
              </Typography>
            )}
            <Box component="dl" sx={{ m: 0 }}>
              <Field label="Backup prerequisite">
                {snapshot.safety.backup_required
                  ? "Required"
                  : "Not required by reported policy"}
              </Field>
              <Field label="Backup result">
                {LABELS.backup[snapshot.safety.backup_state]}
              </Field>
              <Field label="Readiness">
                {LABELS.readiness[snapshot.safety.readiness]}
              </Field>
              {snapshot.safety.fallback_strategy ? (
                <>
                  <Field label="Automatic recovery strategy">
                    {LABELS.strategy[snapshot.safety.fallback_strategy]}
                  </Field>
                  <Field label="Restoration (last reported)">
                    {LABELS.restore[snapshot.safety.restore_state]}
                  </Field>
                  <Field label="Snapshot taken at">
                    <ReportTime value={snapshot.safety.snapshot_at} />
                  </Field>
                  <Field label="Loss window start">
                    <ReportTime value={snapshot.safety.loss_window_start} />
                  </Field>
                  <Field label="Loss window end">
                    <ReportTime value={snapshot.safety.loss_window_end} />
                  </Field>
                  <Field label="Loss window qualification">
                    {
                      LABELS.loss_window[
                        snapshot.safety.loss_window_qualification
                      ]
                    }
                  </Field>
                  <Field label="Failed target (not automatically retried)">
                    {snapshot.safety.failed_target_version || "None reported"}
                  </Field>
                  <Field label="Recovery guidance">
                    {OPERATOR_ACTIONS[snapshot.safety.guidance_id]}
                  </Field>
                </>
              ) : (
                <Field label="Automatic image rollback">
                  {LABELS.rollback[snapshot.safety.automatic_rollback]}
                </Field>
              )}
            </Box>
            <Typography variant="body2" color="text.secondary">
              {snapshot.safety.fallback_strategy === "restore_backup" ? (
                <>
                  After a failed update, automatic restoration discards writes
                  to the new version, resets in-flight jobs and Redis cache, and
                  cannot undo calls to outside services. A sealed backup is not
                  a completed restore: check the separate restoration result.
                  Manual recovery may still be required.
                </>
              ) : (
                <>
                  Backup hook completion does not prove a successful restore.
                  Image rollback does not restore databases or undo migrations.
                </>
              )}
            </Typography>
            {snapshot.safety.estimates && (
              <>
                <Typography component="h3" variant="subtitle2" sx={{ mt: 2 }}>
                  Host preflight estimates
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Estimates are admission budgets, not live progress or a
                  finish-time guarantee. Releases too large for the time or disk
                  budget stay notify-only for this installation.
                </Typography>
                <Box component="dl" sx={{ m: 0 }}>
                  {[
                    ["backup_seconds", "Estimated cold backup"],
                    ["migration_seconds", "Estimated migrations"],
                    ["restore_seconds", "Estimated restoration"],
                    ["forward_seconds", "Estimated forward budget"],
                    ["recovery_seconds", "Protected recovery budget"],
                    ["total_seconds", "Estimated total budget"],
                    ["migration_deadline_seconds", "Migration deadline"],
                    [
                      "migration_stall_timeout_seconds",
                      "Migration no-progress timeout",
                    ],
                  ].map(([key, label]) => (
                    <Field key={key} label={label}>
                      {humanDuration(snapshot.safety.estimates[key])}
                    </Field>
                  ))}
                  {[
                    ["extra_disk_bytes", "Estimated extra migration disk"],
                    [
                      "backup_required_free_bytes",
                      "Required backup free space",
                    ],
                    [
                      "docker_required_free_bytes",
                      "Required Docker free space",
                    ],
                    [
                      "protected_restore_reserve_bytes",
                      "Protected restoration disk reserve",
                    ],
                  ].map(([key, label]) => (
                    <Field key={key} label={label}>
                      {humanBytes(snapshot.safety.estimates[key])}
                    </Field>
                  ))}
                </Box>
              </>
            )}
            <Divider sx={{ my: 2 }} />
            <Typography component="h3" variant="subtitle2">
              Last run
            </Typography>
            {!snapshot.last_run ? (
              <Typography variant="body2">
                No completed run reported.
              </Typography>
            ) : (
              <Box component="dl" sx={{ m: 0 }}>
                <Field label="Kind">
                  {LABELS.kind[snapshot.last_run.kind]} ·{" "}
                  {snapshot.last_run.automatic
                    ? "Automatic"
                    : "Explicit host command"}
                </Field>
                <Field label="Forward outcome">
                  {LABELS.outcome[snapshot.last_run.outcome]}
                </Field>
                <Field label="From release">
                  {snapshot.last_run.from_version || "Unknown"}
                </Field>
                <Field label="Target release">
                  {snapshot.last_run.target_version || "Unknown"}
                </Field>
                <Field label="Run finished">
                  <ReportTime value={snapshot.last_run.finished_at} />
                </Field>
                {snapshot.last_run.forward_result ? (
                  <>
                    <Field label="Forward result">
                      {LABELS.forward[snapshot.last_run.forward_result]}
                    </Field>
                    <Field label="Restoration result">
                      {LABELS.restore[snapshot.last_run.restore_result]}
                    </Field>
                    <Field label="Effective release after this run">
                      {snapshot.last_run.effective_version || "Unverified"}
                    </Field>
                    <Field label="Failed target after this run">
                      {snapshot.last_run.failed_target_version ||
                        "None reported"}
                    </Field>
                  </>
                ) : (
                  <Field label="Fallback result">
                    {LABELS.fallback[snapshot.last_run.fallback_outcome]}
                  </Field>
                )}
              </Box>
            )}
          </ReportSection>
        </>
      )}

      <Box component="section" aria-labelledby="how-updates-work">
        <Typography
          id="how-updates-work"
          component="h2"
          variant="h6"
          sx={{ mb: 1 }}
        >
          How updates work
        </Typography>
        {EXPLAINER_SECTIONS.map((section) => (
          <Accordion
            key={section.id}
            disableGutters
            elevation={0}
            sx={{ borderBottom: 1, borderColor: "divider" }}
          >
            <AccordionSummary
              id={`explainer-${section.id}`}
              aria-controls={`explainer-${section.id}-content`}
              expandIcon={<span aria-hidden="true">+</span>}
            >
              <Typography component="h3" variant="subtitle2">
                {section.title}
              </Typography>
            </AccordionSummary>
            <AccordionDetails id={`explainer-${section.id}-content`}>
              <Typography variant="body2" paragraph>
                {section.text}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {section.mechanism}
              </Typography>
            </AccordionDetails>
          </Accordion>
        ))}
      </Box>
    </Stack>
  );
}
