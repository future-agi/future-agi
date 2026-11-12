import { useEffect, useMemo, useRef, useState } from "react";
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
  useRefreshContextFacts,
  useSubmitRecoveryRequest,
} from "src/api/skills/j6-recovery";
import { IdentityValue } from "../review/ReleaseReviewBody";
import { paths } from "src/routes/paths";
import { restoreCommands } from "./restoreCommands";
import { suggestRelease, suggestReviewer } from "./recoverySuggestion";

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
const uuid = () => crypto.randomUUID();

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

function RollbackRequest({
  contextId,
  candidate,
  production,
  canPromote,
  closureReady,
  releaseRevision,
  reviewerId,
  onReviewer,
}) {
  const reviewers = useRecoveryReviewers();
  const preview = usePreviewRecoveryRelease();
  const submit = useSubmitRecoveryRequest(contextId);
  const [reviewed, setReviewed] = useState(false);
  const [key] = useState(uuid);
  const [copy, setCopy] = useState({ busy: false, done: false, error: "" });
  const packet = preview.data;
  // The packet is computed from the reviewed closure the moment the review is
  // acknowledged; there is nothing further to decide before choosing a reviewer.
  const requested = useRef(false);
  const startPreview = preview.mutate;
  useEffect(() => {
    if (!reviewed || requested.current) return;
    requested.current = true;
    startPreview({ contextId, candidateVersionId: candidate.id });
  }, [reviewed, contextId, candidate.id, startPreview]);
  const listed = (reviewers.data ?? []).some((item) => item.id === reviewerId);
  const link = submit.data?.id
    ? `${window.location.origin}${paths.dashboard.skills.contextReview(contextId)}?request=${submit.data.id}`
    : "";
  return (
    <Section title={`Request rollback to v${candidate.number}`}>
      <Stack spacing={1.5}>
        <IdentityValue
          label={`Production v${production?.number ?? "?"} → v${candidate.number}`}
          value={`${candidate.id} · ${candidate.digest}`}
        />
        <Typography variant="s3">
          Reviewed against release revision {releaseRevision}. A change to the
          release restarts this review.
        </Typography>
        <FormControlLabel
          control={
            <Checkbox
              checked={reviewed}
              disabled={Boolean(packet) || preview.isPending || !closureReady}
              onChange={(_, checked) => {
                // Unchecking after a refused packet starts the review again.
                if (!checked) {
                  requested.current = false;
                  preview.reset?.();
                }
                setReviewed(checked);
              }}
            />
          }
          label={`I reviewed the complete v${candidate.number} closure, its retained children and the excluded alternatives.`}
        />
        {preview.isPending && (
          <Typography variant="s3" role="status">
            Preparing the rollback packet.
          </Typography>
        )}
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
              value={listed ? reviewerId : ""}
              disabled={submit.isPending || Boolean(submit.data)}
              onChange={(event) => onReviewer(event.target.value)}
              helperText="Suggested: whoever last moved production. The server checks the reviewer's authority on submit."
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
                !listed ||
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
                Submission failed. Submit again; the same request key prevents
                a duplicate.
              </Alert>
            )}
            {submit.data && (
              <Alert severity="success" role="status">
                Rollback request submitted. The reviewer approves and moves
                production from Skills Reviews; production is unchanged until
                then.
              </Alert>
            )}
            {link && (
              <Stack spacing={1}>
                <TextField
                  size="small"
                  label="Review link"
                  value={link}
                  InputProps={{ readOnly: true }}
                  helperText="Send this link to the reviewer; it opens the exact request."
                />
                <Button
                  variant="outlined"
                  disabled={copy.busy}
                  onClick={async () => {
                    setCopy({ busy: true, done: false, error: "" });
                    try {
                      if (!navigator.clipboard?.writeText)
                        throw new Error("Unavailable");
                      await navigator.clipboard.writeText(link);
                      setCopy({ busy: false, done: true, error: "" });
                    } catch {
                      setCopy({
                        busy: false,
                        done: false,
                        error: "Could not copy. Select and copy the link manually.",
                      });
                    }
                  }}
                >
                  Copy review link
                </Button>
                {copy.done && (
                  <Typography variant="s3" role="status">
                    Copied the review link.
                  </Typography>
                )}
                {copy.error && <Alert severity="error">{copy.error}</Alert>}
              </Stack>
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
  releaseRevision: PropTypes.number,
  reviewerId: PropTypes.string.isRequired,
  onReviewer: PropTypes.func.isRequired,
};

function LocalRecovery({ contextId, contextName, candidate, canIssue }) {
  const issue = useIssueRecoveryCredential(contextId);
  const [key] = useState(uuid);
  const [hours, setHours] = useState(24);
  const [commit, setCommit] = useState("");
  const commands = restoreCommands({
    name: contextName,
    version: candidate.number,
    commit: commit.trim(),
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
          label="Last reviewed commit"
          value={commit}
          onChange={(event) => setCommit(event.target.value)}
          helperText="The full commit id whose lock was reviewed. The restore reads that lock, refuses one whose identity differs and verifies the result."
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
          recovered. Plan every registered thread once, review that plan, and
          apply exactly the reviewed plan: an applied plan is refused if its
          digest differs or any thread moved after the review.
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

const RECOVERY_FACTS_REFRESH_MS = 5000;

export default function J6RecoveryPanel({ contextId, contextName }) {
  const query = useContextRecovery(contextId);
  const refreshFacts = useRefreshContextFacts(contextId);
  // The recovery facts follow a release or consumer change made elsewhere (a
  // reviewer moving production, a consumer reporting) while this tab is visible,
  // so the operator never decides from a view that has already moved.
  const latestRefresh = useRef(refreshFacts);
  latestRefresh.current = refreshFacts;
  useEffect(() => {
    const timer = setInterval(() => {
      // A load slower than the interval must finish, not be cancelled and restarted forever.
      if (document.visibilityState === "visible")
        latestRefresh.current({ cancelRefetch: false });
    }, RECOVERY_FACTS_REFRESH_MS);
    return () => clearInterval(timer);
  }, []);
  const [picked, setChosen] = useState(null);
  // The reviewer stays selected across a restarted review (the release moved), so a
  // fresh packet is one acknowledgement and one submit. Until one is picked, the person
  // who last moved production (or the only reviewer) is suggested.
  const reviewers = useRecoveryReviewers();
  const [reviewerId, setReviewerId] = useState("");
  const data = query.data;
  const suggestedReviewer = suggestReviewer({
    reviewers: reviewers.data,
    lastReleaserId: data?.lastReleaserId,
  });
  // The newest eligible release before the failing one is preselected, never auto-submitted.
  const suggestion = useMemo(
    () => (data ? suggestRelease({ candidates: data.candidates, cohort: data.cohort }) : null),
    [data],
  );
  // The suggestion becomes the actual selection once, so it stays selected after production
  // moves to it (it is then the release whose locks and applications are recovered next).
  useEffect(() => {
    if (suggestion && picked === null) setChosen(suggestion.candidateId);
  }, [suggestion, picked]);
  const chosen = picked ?? suggestion?.candidateId ?? null;
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
      {suggestion && (
        <Alert severity="info">
          Suggested: v{suggestion.number}, the newest eligible release older than
          the failing v{suggestion.failing}. It is preselected so its complete
          closure is shown for review; choose another release if the incident
          needs one.
        </Alert>
      )}
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
          releaseRevision={data.releaseRevision}
          reviewerId={reviewerId || suggestedReviewer}
          onReviewer={setReviewerId}
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
      <Button variant="text" onClick={() => refreshFacts()}>
        Refresh recovery facts
      </Button>
    </Stack>
  );
}
J6RecoveryPanel.propTypes = {
  contextId: PropTypes.string.isRequired,
  contextName: PropTypes.string.isRequired,
};
