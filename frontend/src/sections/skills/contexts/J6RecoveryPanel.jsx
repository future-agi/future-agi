import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import {
  useContextRecovery,
  useIssueRecoveryCredential,
  usePreviewRecoveryRelease,
  useRecoveryClosure,
  useRecoveryReviewers,
  useSubmitRecoveryRequest,
} from "src/api/skills/j6-recovery";
import { IdentityValue } from "../review/ReleaseReviewBody";

// Intent: an operator turns a retained incident into one reviewed recovery
// choice, then recovers local locks and applications without a false
// "recovered" claim. Hierarchy: incident cohort, eligible release, reviewed
// request, then local/application work. Neutral surfaces; status only on badges.
const stateNames = {
  matches_target: "Matches target",
  pinned_as_intended: "Pinned as intended",
  update_available: "Update available",
  local_modifications: "Local modifications",
  mixed_sessions: "Mixed sessions",
  old_thread_pinned: "Old thread retained",
  stale_observation: "Stale observation",
  unknown: "Runtime loading not observed",
  quarantined: "Quarantined",
};
const reasonNames = {
  current_production: "Current production",
  quarantined: "Quarantined",
  context_quarantined: "Closure quarantined",
  dependency_unavailable: "Dependency unavailable",
  resource_archived: "Context archived",
  distribution_blocked: "Distribution blocked",
};
const quote = (value) => `'${String(value).replaceAll("'", "'\"'\"'")}'`;
const uuid = () => crypto.randomUUID();

export function restoreCommands({ name, version, lockPath }) {
  return [
    `python -m fi.context restore --lock ${quote(lockPath)} --project . --plan ${quote("restore-plan.json")}`,
    `python -m fi.context inspect --plan ${quote("restore-plan.json")} --diff`,
    `python -m fi.context apply --plan ${quote("restore-plan.json")} --quiescent`,
    "python -m fi.context verify --project .",
    `# Expect ${name} v${version}. Local files outside the managed paths are preserved.`,
  ].join("\n");
}

function Cohort({ cohort, numbers }) {
  return (
    <Section title="Incident and declared cohort">
      <Stack spacing={1.5}>
        <Alert severity="info">
          Declared bindings only. A label move is not recovery: consumers stay
          unknown until each runtime reports delivery of the recovered release,
          and threads under expired authority or a lost mapping are never shown
          as recovered.
        </Alert>
        <TableContainer sx={{ maxWidth: 1 }}>
          <Table size="small" aria-label="Recovery cohort">
            <TableHead>
              <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                <TableCell>Consumer</TableCell>
                <TableCell>Kind</TableCell>
                <TableCell>Loaded</TableCell>
                <TableCell>Retained incident</TableCell>
                <TableCell>State</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {cohort.map((row) => (
                <TableRow
                  key={row.binding.id}
                  hover
                  sx={{ height: ROW_HEIGHT.default }}
                >
                  <TableCell>
                    <Typography variant="s1">{row.binding.name}</Typography>
                    <Typography variant="s3">
                      {row.binding.selector.version
                        ? `Pinned v${row.binding.selector.version}`
                        : `Follow ${row.binding.selector.label}`}
                    </Typography>
                  </TableCell>
                  <TableCell>{row.binding.kind}</TableCell>
                  <TableCell>
                    {row.loaded
                      ? `v${numbers[row.loaded.versionId] ?? "?"}`
                      : "Not observed"}
                  </TableCell>
                  <TableCell>
                    {row.latestFailure ? (
                      <Stack spacing={0.25}>
                        <Typography variant="s1">
                          Failed on v
                          {numbers[row.latestFailure.versionId] ?? "?"} ·{" "}
                          {row.latestFailure.diagnosticCodes.join(", ") ||
                            "no code"}
                        </Typography>
                        <Typography
                          variant="s3"
                          sx={{ overflowWrap: "anywhere" }}
                        >
                          Trace{" "}
                          {row.latestFailure.traceRef?.trace_id ?? "not linked"}
                        </Typography>
                      </Stack>
                    ) : (
                      "None retained"
                    )}
                  </TableCell>
                  <TableCell>
                    <Stack spacing={0.5}>
                      {row.states.map((state) => (
                        <StatusBadge
                          key={state}
                          status={state}
                          label={stateNames[state] ?? state}
                        />
                      ))}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Stack>
    </Section>
  );
}
Cohort.propTypes = {
  cohort: PropTypes.array.isRequired,
  numbers: PropTypes.object.isRequired,
};

function Candidates({ candidates, chosen, onChoose }) {
  return (
    <Section title="Choose a recovery release">
      <TableContainer sx={{ maxWidth: 1 }}>
        <Table size="small" aria-label="Recovery releases">
          <TableHead>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              <TableCell>Release</TableCell>
              <TableCell>Eligibility</TableCell>
              <TableCell>Retained children</TableCell>
              <TableCell>Choose</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {candidates.map((row) => (
              <TableRow
                key={row.id}
                hover
                selected={chosen === row.id}
                sx={{ height: ROW_HEIGHT.default }}
              >
                <TableCell>
                  <Typography variant="s1">v{row.number}</Typography>
                  <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>
                    {row.digest}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Stack spacing={0.5}>
                    {row.eligible ? (
                      <StatusBadge status="completed" label="Eligible" />
                    ) : (
                      row.reasons.map((reason) => (
                        <StatusBadge
                          key={reason}
                          status={
                            reason === "current_production"
                              ? "current_only"
                              : "failed"
                          }
                          label={reasonNames[reason] ?? reason}
                        />
                      ))
                    )}
                    {row.lowerThanProduction && row.eligible && (
                      <Typography variant="s3">
                        Lower-number rollback
                      </Typography>
                    )}
                    {row.quarantineReason && (
                      <Typography variant="s3">{row.quarantineReason}</Typography>
                    )}
                  </Stack>
                </TableCell>
                <TableCell>
                  <Typography variant="s1">
                    {row.children.length} linked
                  </Typography>
                  {row.unavailableChildren.map((child) => (
                    <Typography
                      key={child.skillId}
                      variant="s3"
                      sx={{ display: "block", overflowWrap: "anywhere" }}
                    >
                      Source retired: {child.mount} · exact bytes retained
                    </Typography>
                  ))}
                </TableCell>
                <TableCell>
                  <Button
                    size="small"
                    variant={chosen === row.id ? "contained" : "outlined"}
                    disabled={!row.selectable}
                    onClick={() => onChoose(row.id)}
                  >
                    Choose v{row.number}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Section>
  );
}
Candidates.propTypes = {
  candidates: PropTypes.array.isRequired,
  chosen: PropTypes.string,
  onChoose: PropTypes.func.isRequired,
};

const changeNames = {
  added: "Added",
  changed: "Changed",
  removed: "Removed",
  unchanged: "Unchanged",
};
const changeStatus = { added: "current_only", changed: "current_only", removed: "failed", unchanged: "completed" };
const shortDigest = (value) => String(value).slice(0, 12);

function ClosureReview({ candidate, production, closure }) {
  if (closure.isLoading) return <ContentSkeleton />;
  if (closure.error || !closure.data) {
    return (
      <ErrorState
        title="Closure review unavailable"
        detail="The complete release could not be read. No rollback can be requested from a partial review."
        onRetry={closure.refetch}
      />
    );
  }
  const { rows, changes } = closure.data;
  const counts = rows.reduce((total, row) => ({ ...total, [row.status]: (total[row.status] ?? 0) + 1 }), {});
  return (
    <Section title={`Closure review: v${candidate.number} against production v${production?.number ?? "?"}`}>
      <Stack spacing={1.5}>
        <Typography variant="s1" role="status">
          {rows.length} files in the complete closure: {counts.changed ?? 0} changed, {counts.added ?? 0} added,{" "}
          {counts.removed ?? 0} removed, {counts.unchanged ?? 0} unchanged. {candidate.children.length} linked skills keep their pinned versions;{" "}
          {candidate.unavailableChildren.length} source{candidate.unavailableChildren.length === 1 ? "" : "s"} retired.
        </Typography>
        <TableContainer sx={{ maxWidth: 1 }}>
          <Table size="small" aria-label="Recovery closure files">
            <TableHead>
              <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                <TableCell>File</TableCell>
                <TableCell>Against production</TableCell>
                <TableCell>Size</TableCell>
                <TableCell>SHA-256</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.status}:${row.path}`} hover sx={{ height: ROW_HEIGHT.default }}>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>{row.path}</TableCell>
                  <TableCell>
                    <StatusBadge status={changeStatus[row.status]} label={changeNames[row.status]} />
                  </TableCell>
                  <TableCell>{row.sizeBytes} B</TableCell>
                  <TableCell>{shortDigest(row.sha256)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        {changes.map((change) => (
          <Stack key={change.path} spacing={0.5}>
            <Typography variant="m3">
              {changeNames[change.status]}: {change.path}
            </Typography>
            <Typography
              component="pre"
              variant="s1"
              aria-label={`${change.path} change from production`}
              tabIndex={0}
              sx={{ m: 0, p: 1.5, border: 1, borderColor: "divider", borderRadius: 1, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {change.lines.map((line, index) => (
                // The sign is text, not color, so the change reads in both themes.
                <Typography
                  // eslint-disable-next-line react/no-array-index-key
                  key={index}
                  component="span"
                  variant="s1"
                  sx={{ display: "block", bgcolor: line.kind === "same" ? "transparent" : "action.selected" }}
                >
                  {line.kind === "added" ? "+ " : line.kind === "removed" ? "- " : "  "}
                  {line.text}
                </Typography>
              ))}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Section>
  );
}
ClosureReview.propTypes = {
  candidate: PropTypes.object.isRequired,
  production: PropTypes.object,
  closure: PropTypes.object.isRequired,
};

function RollbackRequest({ contextId, candidate, production, canPromote, closureReady }) {
  const reviewers = useRecoveryReviewers();
  const preview = usePreviewRecoveryRelease();
  const submit = useSubmitRecoveryRequest(contextId);
  const [reviewed, setReviewed] = useState(false);
  const [reviewerId, setReviewerId] = useState("");
  const [key] = useState(uuid);
  const packet = preview.data;
  return (
    <Section title={`Request rollback to v${candidate.number}`}>
      <Stack spacing={1.5}>
        <IdentityValue
          label={`Production v${production?.number ?? "?"} → v${candidate.number}`}
          value={`${candidate.id} · ${candidate.digest}`}
        />
        <FormControlLabel
          control={
            <Checkbox
              checked={reviewed}
              disabled={Boolean(packet) || !closureReady}
              onChange={(_, checked) => setReviewed(checked)}
            />
          }
          label={`I reviewed the complete v${candidate.number} closure, its retained children and the excluded alternatives.`}
        />
        <Button
          variant="outlined"
          disabled={!reviewed || preview.isPending || Boolean(packet)}
          onClick={() =>
            preview.mutate({ contextId, candidateVersionId: candidate.id })
          }
        >
          Preview rollback packet
        </Button>
        {preview.error && (
          <Alert severity="error">
            The release service refused this packet. Recheck eligibility; no
            request was created.
          </Alert>
        )}
        {packet && (
          <Stack spacing={1}>
            <IdentityValue label="Packet fingerprint" value={packet.fingerprint} />
            <TextField
              select
              size="small"
              label="Reviewer"
              value={reviewerId}
              disabled={submit.isPending || Boolean(submit.data)}
              onChange={(event) => setReviewerId(event.target.value)}
            >
              {(reviewers.data ?? []).map((item) => (
                <MenuItem key={item.id} value={item.id}>
                  {item.name}
                </MenuItem>
              ))}
            </TextField>
            <Button
              variant="contained"
              disabled={
                !reviewerId ||
                !canPromote ||
                submit.isPending ||
                Boolean(submit.data)
              }
              onClick={() =>
                submit.mutate({ preview: packet, reviewerId, idempotencyKey: key })
              }
            >
              Submit rollback for review
            </Button>
            {!canPromote && (
              <Typography variant="s3">
                You cannot request production changes for this context.
              </Typography>
            )}
            {submit.error && (
              <Alert severity="error">
                Submission failed. Preview a fresh packet before retrying.
              </Alert>
            )}
            {submit.data && (
              <Alert severity="success" role="status">
                Rollback request submitted. The reviewer approves and moves
                production from Skills Reviews; production is unchanged until
                then.
              </Alert>
            )}
          </Stack>
        )}
      </Stack>
    </Section>
  );
}
RollbackRequest.propTypes = {
  contextId: PropTypes.string.isRequired,
  candidate: PropTypes.object.isRequired,
  production: PropTypes.object,
  canPromote: PropTypes.bool,
  closureReady: PropTypes.bool,
};

function LocalRecovery({ contextId, contextName, candidate, canIssue }) {
  const issue = useIssueRecoveryCredential(contextId);
  const [key] = useState(uuid);
  const [hours, setHours] = useState(24);
  const [lockPath, setLockPath] = useState(".futureagi/retained-context.lock");
  const commands = restoreCommands({
    name: contextName,
    version: candidate.number,
    lockPath,
  });
  return (
    <Section title="Recover locks and applications">
      <Stack spacing={1.5}>
        <Typography variant="m3">Replacement application credential</Typography>
        <Typography variant="s3">
          Expired or revoked credentials are never extended. A new consume
          credential follows production and is pinned to v{candidate.number};
          threads opened under the old credential cannot be resumed with it.
        </Typography>
        <TextField
          select
          size="small"
          label="Credential lifetime"
          value={hours}
          disabled={Boolean(issue.data)}
          onChange={(event) => setHours(Number(event.target.value))}
        >
          {[1, 24, 168].map((value) => (
            <MenuItem key={value} value={value}>
              {value} hours
            </MenuItem>
          ))}
        </TextField>
        <Button
          variant="outlined"
          disabled={!canIssue || issue.isPending || Boolean(issue.data)}
          onClick={() =>
            issue.mutate({
              label: "production",
              versionIds: [candidate.id],
              expiresAt: new Date(Date.now() + hours * 3_600_000).toISOString(),
              idempotencyKey: key,
            })
          }
        >
          Issue replacement credential
        </Button>
        {!canIssue && (
          <Typography variant="s3">
            An administrator must issue application credentials.
          </Typography>
        )}
        {issue.error && (
          <Alert severity="error">The credential was not issued.</Alert>
        )}
        {issue.data?.secret && (
          <TextField
            size="small"
            label="New credential (shown once)"
            value={issue.data.secret}
            InputProps={{ readOnly: true }}
            helperText="Store it in the application's secret manager now. It is not shown again."
          />
        )}
        <Typography variant="m3">Restore the checked-in lock</Typography>
        <TextField
          size="small"
          label="Retained lock path"
          value={lockPath}
          onChange={(event) => setLockPath(event.target.value)}
          helperText="Export the lock from the last reviewed commit; the restore refuses a lock whose identity differs."
        />
        <Typography
          component="pre"
          variant="s1"
          aria-label="Lock restore commands"
          sx={{
            m: 0,
            p: 1.5,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
          }}
        >
          {commands}
        </Typography>
        <Typography variant="m3">Application threads</Typography>
        <Typography variant="s1" component="div">
          Resume a paused thread only under its original session and
          checkpoint. Reconcile persisted business receipts before the next
          model call; never replay a completed action. A thread whose mapping
          or authority is lost needs an explicitly reviewed clean thread, and
          the abandoned thread must be closed so it is not counted as
          recovered.
        </Typography>
      </Stack>
    </Section>
  );
}
LocalRecovery.propTypes = {
  contextId: PropTypes.string.isRequired,
  contextName: PropTypes.string.isRequired,
  candidate: PropTypes.object.isRequired,
  canIssue: PropTypes.bool,
};

export default function J6RecoveryPanel({ contextId, contextName }) {
  const query = useContextRecovery(contextId);
  const [chosen, setChosen] = useState(null);
  const data = query.data;
  const chosenCandidate = data?.candidates.find((row) => row.id === chosen);
  const closure = useRecoveryClosure(
    contextId,
    chosenCandidate && !chosenCandidate.isProduction ? chosenCandidate : undefined,
    data?.candidates.find((row) => row.isProduction),
  );
  const numbers = useMemo(
    () =>
      Object.fromEntries(
        (data?.candidates ?? []).map((row) => [row.id, row.number]),
      ),
    [data],
  );
  if (query.isLoading) return <ContentSkeleton />;
  if (query.error || !data)
    return (
      <ErrorState
        title="Recovery view unavailable"
        detail="The release, safety or consumer facts could not be read completely. No partial recovery choice is shown."
        onRetry={query.refetch}
      />
    );
  const candidate = data.candidates.find((row) => row.id === chosen);
  const production = data.candidates.find((row) => row.isProduction);
  return (
    <Stack spacing={3} sx={{ minWidth: 0 }}>
      <Cohort cohort={data.cohort} numbers={numbers} />
      <Candidates
        candidates={data.candidates}
        chosen={chosen}
        onChoose={setChosen}
      />
      {candidate?.isProduction && (
        <Alert severity="info">
          v{candidate.number} is in production. Nothing is left to roll back;
          recover local locks and applications below.
        </Alert>
      )}
      {candidate && !candidate.isProduction && (
        <ClosureReview candidate={candidate} production={production} closure={closure} />
      )}
      {candidate && !candidate.isProduction && (
        <RollbackRequest
          // A moved release revision makes an earlier packet stale: start a fresh review.
          key={`${candidate.id}:${data.releaseRevision}`}
          contextId={contextId}
          candidate={candidate}
          production={production}
          canPromote={data.canPromote}
          closureReady={Boolean(closure.data)}
        />
      )}
      {candidate && (
        <LocalRecovery
          key={`local-${candidate.id}`}
          contextId={contextId}
          contextName={contextName}
          candidate={candidate}
          canIssue={data.canIssueCredentials}
        />
      )}
      <Button variant="text" onClick={() => query.refetch()}>
        Refresh recovery facts
      </Button>
    </Stack>
  );
}
J6RecoveryPanel.propTypes = {
  contextId: PropTypes.string.isRequired,
  contextName: PropTypes.string.isRequired,
};
