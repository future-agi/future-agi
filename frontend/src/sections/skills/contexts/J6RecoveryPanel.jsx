import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  IconButton,
  Tooltip,
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
  useRefreshContextFacts,
  DESCRIPTOR_PATH,
  useSubmitRecoveryRequest,
} from "src/api/skills/j6-recovery";
import EmptyState from 'src/components/empty-state';
import { useReviewOptions } from 'src/api/skills/review-authoring';
import IdentityChip from '../components/IdentityChip';
import PeoplePicker from '../components/PeoplePicker';
import EvidenceSummary from '../components/EvidenceSummary';
import TechnicalDetails from '../components/TechnicalDetails';
import OperationFeedback from '../components/OperationFeedback';
import CapabilityNotice from '../components/CapabilityNotice';
import TextDiff from '../components/TextDiff';
import When from '../components/When';
import CodeBlock from 'src/components/code-block';
import { reviewComposerUrl } from '../utils/review-links';
import Iconify from 'src/components/iconify';
import { consumerNextStep, humanizeCode, needsDeliveryVerification } from './consumerPresentation';
import { paths } from "src/routes/paths";
import { restoreCommands } from "./restoreCommands";
import { suggestRelease, suggestReviewer, initialReleaseState } from "./recoverySuggestion";
import { recipientsOf } from "../utils/conclusions";

// Intent: an operator turns a retained incident into one reviewed recovery
// choice, then recovers local locks and applications without a false
// "recovered" claim. Hierarchy: release review beside its request, then approval
// and local/application work. Neutral surfaces, hairline borders, m3/s1/s3 type
// and an 8px spacing unit match the workspace; green means released, not delivered.
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
  current_production: "Production",
  quarantined: "Quarantined",
  context_quarantined: "Closure quarantined",
  dependency_unavailable: "Dependency unavailable",
  resource_archived: "Context archived",
  distribution_blocked: "Distribution blocked",
};
const uuid = () => crypto.randomUUID();
const mobileLabel = (label) => ({ '&::before': { content: { xs: `"${label}"`, sm: 'none' }, display: 'block', typography: 's3', color: 'text.secondary', mb: 0.5 } });

function Cohort({ cohort, numbers, asOf, active = false }) {
  const missing = cohort.filter(needsDeliveryVerification).length;
  return (
    <Section title="Verify consumers">
      <Stack spacing={1.5}>
        <Typography variant="s3" color="text.secondary">As of <When value={asOf} fallback="Time not reported" />. Moving production doesn&apos;t update running apps. Each app must report the new version before it counts as recovered.</Typography>
        {active && missing > 0 && <Alert severity="warning">{missing} of {cohort.length} consumers still need delivery verification.</Alert>}
        {!cohort.length && <EmptyState title="No declared consumers" description="No runtime delivery can be verified until consumers are registered and report it." />}
        <TableContainer sx={{ maxWidth: 1 }}>
          <Table size="small" aria-label="Recovery cohort">
            <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}>
              <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                <TableCell>Consumer</TableCell>
                <TableCell>Last delivered release</TableCell>
                <TableCell>Report</TableCell>
                <TableCell>Recovery verification</TableCell>
                <TableCell>Next step</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {cohort.map((row) => (
                <TableRow
                  key={row.binding.id}
                  hover
                  sx={{ height: { xs: 'auto', sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr)', py: { xs: 1, sm: 0 }, '& > td': { borderBottom: { xs: 0, sm: undefined } } }}
                >
                  <TableCell>
                    <Typography component="div" variant="s1">{row.binding.name}</Typography>
                    <Typography variant="s3">
                      {row.binding.selector.version
                        ? `Pinned v${row.binding.selector.version}`
                        : `Follow ${row.binding.selector.label}`}
                    </Typography>
                  </TableCell>
                  <TableCell sx={mobileLabel("Last delivered release")}>
                    {row.loaded
                      ? `v${numbers[row.loaded.versionId] ?? "?"}`
                      : "Not observed"}
                  </TableCell>
                  <TableCell sx={mobileLabel("Report")}>
                    {row.latestFailure ? (
                      <Stack spacing={0.25}>
                        <Typography variant="s1">
                          Failed on v
                          {numbers[row.latestFailure.versionId] ?? "?"} ·{" "}
                          {row.latestFailure.diagnosticCodes.map(humanizeCode).join(", ") ||
                            "no code"}
                        </Typography>
                        <TechnicalDetails title="Report details"><Typography variant="s3">{row.latestFailure.diagnosticCodes.join(", ")}</Typography>{row.latestFailure.traceRef?.trace_id ? <IdentityChip kind="id" label="Trace" value={row.latestFailure.traceRef.trace_id} /> : <Typography variant="s3">Trace not linked</Typography>}</TechnicalDetails>
                      </Stack>
                    ) : (
                      "None retained"
                    )}
                  </TableCell>
                  <TableCell sx={mobileLabel("Recovery verification")}>
                    <Stack spacing={0.5} alignItems="flex-start">
                      {row.states.map((state) => (
                        <StatusBadge
                          key={state}
                          status={state}
                          label={stateNames[state] ?? humanizeCode(state)}
                        />
                      ))}
                    </Stack>
                  </TableCell>
                  <TableCell sx={mobileLabel("Next step")}><Typography variant="s3">{consumerNextStep(row.nextActionCode)}</Typography>{consumerNextStep(row.nextActionCode) === 'Unknown' && row.nextActionCode && <TechnicalDetails><Typography variant="s3" component="code">{row.nextActionCode}</Typography></TechnicalDetails>}</TableCell>
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
  asOf: PropTypes.string,
  active: PropTypes.bool,
};

function Candidates({ candidates, chosen, onChoose, bare = false }) {
  const table = (
      <TableContainer sx={{ maxWidth: 1 }}>
        <Table size="small" aria-label="Recovery releases">
          <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              <TableCell>Release</TableCell>
              <TableCell>Eligibility</TableCell>
              <TableCell>Linked skills</TableCell>
              <TableCell>Choose</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {candidates.map((row) => (
              <TableRow
                key={row.id}
                hover
                selected={chosen === row.id}
                aria-current={chosen === row.id ? "true" : undefined}
                sx={{ height: { xs: 'auto', sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr) auto', py: { xs: 1, sm: 0 }, '& > td': { borderBottom: { xs: 0, sm: undefined } } }}
              >
                <TableCell>
                  <Typography variant="s1">v{row.number}</Typography>
                </TableCell>
                <TableCell>
                  <Stack spacing={0.5} alignItems="flex-start">
                    {row.eligible ? (
                      <StatusBadge status="completed" label="Eligible" />
                    ) : (
                      row.reasons.map((reason) => (
                        <StatusBadge
                          key={reason}
                          status={
                            reason === "current_production"
                              ? "released"
                              : "failed"
                          }
                          label={reasonNames[reason] ?? humanizeCode(reason)}
                        />
                      ))
                    )}
                    {row.lowerThanProduction && row.eligible && (
                      <Typography variant="s3">
                        Earlier version
                      </Typography>
                    )}
                    {row.quarantineReason && (
                      <Typography variant="s3">{row.quarantineReason}</Typography>
                    )}
                  </Stack>
                </TableCell>
                <TableCell>
                  <Typography variant="s1">
                    {row.children.length} linked skill{row.children.length === 1 ? "" : "s"}
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
                  {row.isProduction ? <Typography variant="s3" color="text.secondary">In production</Typography> : <Button
                    size="small"
                    variant={chosen === row.id ? "text" : "outlined"}
                    aria-pressed={chosen === row.id}
                    startIcon={chosen === row.id ? <Iconify icon="solar:check-circle-linear" width={16} /> : undefined}
                    disabled={!row.selectable}
                    onClick={() => onChoose(row.id)}
                  >
                    Choose v{row.number}
                  </Button>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
  );
  // Inside the release review the table needs no section of its own.
  return bare ? table : <Section title="Choose a recovery release">{table}</Section>;
}
Candidates.propTypes = {
  candidates: PropTypes.array.isRequired,
  chosen: PropTypes.string,
  onChoose: PropTypes.func.isRequired,
  bare: PropTypes.bool,
};

const changeNames = {
  added: "Added",
  changed: "Changed",
  removed: "Removed",
  unchanged: "Unchanged",
};
const changeStatus = { added: "current_only", changed: "current_only", removed: "failed", unchanged: "completed" };

const linkSentence = (links) => links.map((link) => (link.from == null ? `${link.name} v${link.to} added` : link.to == null ? `${link.name} v${link.from} removed` : `${link.name} v${link.from} → v${link.to}`)).join(', ');

function ClosureReview({ candidate, production, closure }) {
  if (closure.isLoading) return <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} />;
  if (closure.error || !closure.data) {
    return (
      <ErrorState
        title={`Couldn't read everything in v${candidate.number}`}
        detail="Every file must be read before a rollback can be requested. Nothing is requested from a partial review."
        onRetry={closure.refetch}
      />
    );
  }
  const { rows, changes } = closure.data;
  const counts = rows.reduce((total, row) => ({ ...total, [row.status]: (total[row.status] ?? 0) + 1 }), {});
  const changedCount = rows.length - (counts.unchanged ?? 0);
  const descriptor = changes.find((change) => change.path === DESCRIPTOR_PATH);
  const content = changes.filter((change) => change.path !== DESCRIPTOR_PATH);
  const links = descriptor?.links ?? [];
  const diff = (change) => <TextDiff fromText={change.lines.filter((line) => line.kind !== 'added').map((line) => line.text).join('\n')} toText={change.lines.filter((line) => line.kind !== 'removed').map((line) => line.text).join('\n')} fromTitle={`Production v${production?.number ?? '?'}`} toTitle={`v${candidate.number}`} />;
  return (
    <Section title={`What changes: everything in v${candidate.number} against production v${production?.number ?? '?'}`}>
      <Stack spacing={1.5}>
        {/* Conclusion first: the readable consequence, then the files people read, then machine records. */}
        <Typography variant="m3" role="status">
          {links.length ? linkSentence(links) : `Roll back to v${candidate.number}`} · {changedCount} file{changedCount === 1 ? '' : 's'}
        </Typography>
        <Typography variant="s3" color="text.secondary">
          {counts.unchanged ?? 0} file{counts.unchanged === 1 ? ' stays' : 's stay'} the same.
          {counts.added > 0 && ` ${counts.added} added.`}
          {counts.removed > 0 && ` ${counts.removed} removed.`}
          {candidate.children.length > 0 && ` The rollback restores the linked-skill versions locked in context v${candidate.number}; it doesn't fetch newer skill versions.`}
          {candidate.unavailableChildren.length > 0 && ` ${candidate.unavailableChildren.length} source${candidate.unavailableChildren.length === 1 ? " is" : "s are"} retired; their exact files are kept.`}
        </Typography>
        {/* Every unchanged row stays visible: the measured J6 review inspects every file. */}
        <TableContainer sx={{ maxWidth: 1, maxHeight: 240 }}>
          <Table size="small" stickyHeader aria-label="Recovery closure files">
            <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}>
              <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                <TableCell>File</TableCell>
                <TableCell>Against production</TableCell>
                <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>Size</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.status}:${row.path}`} hover sx={{ height: { xs: 'auto', sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr) auto', py: { xs: 1, sm: 0 }, '& > td': { borderBottom: { xs: 0, sm: undefined } } }}>
                  <TableCell aria-label={row.path} sx={{ overflowWrap: "anywhere" }}>{row.path}</TableCell>
                  <TableCell>
                    <StatusBadge status={changeStatus[row.status]} label={changeNames[row.status]} />
                  </TableCell>
                  <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>{row.sizeBytes} B</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <Stack spacing={1.5} sx={{ maxHeight: 360, overflowY: 'auto', minWidth: 0 }} aria-label="Recovery file changes" tabIndex={0}>
        {content.map((change) => <Stack key={change.path} spacing={0.5}>
          <Typography variant="m3">{changeNames[change.status]}: {change.path}</Typography>
          <Box aria-label={`${change.path} change from production`} tabIndex={0}>{diff(change)}</Box>
        </Stack>)}
        {descriptor && <Stack spacing={0.5}>
          <Typography variant="m3">Linked skill versions</Typography>
          {/* The descriptor is a machine record: its readable meaning leads, its exact bytes stay one click away. */}
          <Box aria-label={`${descriptor.path} change from production`} tabIndex={0}>
            <Typography variant="s1">{links.length ? linkSentence(links) : descriptor.links === null ? 'The context description changed.' : 'No linked skill changes version.'}</Typography>
            <TechnicalDetails title={`Raw ${descriptor.path} change`} keepMounted>{diff(descriptor)}</TechnicalDetails>
          </Box>
        </Stack>}
        </Stack>
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
  onRefresh,
  onSubmitted,
}) {
  const options = useReviewOptions('agent_context', contextId, candidate.id);
  // Reviewers come from the server's eligible recipients for this request (the same list the
  // release composer uses), which any author can read; the author is never offered.
  const reviewers = { data: recipientsOf(options.data), isLoading: options.isLoading, error: options.error, refetch: options.refetch };
  const [selectedEvidence, setSelectedEvidence] = useState([]);
  const matchingEvidence = (options.data?.evidence ?? []).filter((item) => item.subjectId === contextId && item.candidateDigest === candidate.digest);
  const evidence = matchingEvidence.filter((item) => item.visibility === 'workspace' && selectedEvidence.includes(`${item.source}:${item.sourceId}`));
  const evidenceRows = (items) => items.map((item) => ({ id: item.sourceId, source: item.source, kind: item.source === 'simulation_native' ? 'application_verification' : 'text_comparison', title: item.source === 'simulation_native' ? 'Application verification' : 'Text comparison', candidateNumber: candidate.number, status: item.status, visibility: item.visibility, expiresAt: item.payloadExpiresAt }));
  const preview = usePreviewRecoveryRelease();
  const submit = useSubmitRecoveryRequest(contextId);
  const [reviewed, setReviewed] = useState(false);
  const [key] = useState(uuid);
  const [copy, setCopy] = useState({ busy: false, done: false, error: "" });
  const packet = preview.data;
  const originalSubmission = useRef(null);
  const sendRequest = () => {
    // A lost response is reconciled with the same recipient, packet and operation key.
    originalSubmission.current ??= { preview: packet, reviewerId, idempotencyKey: key };
    submit.mutate(originalSubmission.current);
  };
  // The packet is computed from the reviewed closure the moment the review is
  // acknowledged; there is nothing further to decide before choosing a reviewer.
  const requested = useRef(false);
  const originalPreview = useRef(null);
  const startPreview = preview.mutate;
  useEffect(() => {
    if (!reviewed || requested.current) return;
    requested.current = true;
    originalPreview.current = { contextId, candidateVersionId: candidate.id, evidence, external_pipeline: null };
    startPreview(originalPreview.current);
  }, [reviewed, contextId, candidate.id, startPreview, evidence]);
  const recipient = originalSubmission.current?.reviewerId ?? reviewerId;
  const listed = (reviewers.data ?? []).some((item) => item.id === recipient);
  const reviewerName = (reviewers.data ?? []).find((person) => person.id === recipient)?.name || 'the selected reviewer';
  useEffect(() => {
    if (submit.data?.id) onSubmitted({ candidateId: candidate.id, from: production?.number, revision: releaseRevision, reviewer: reviewerName });
  }, [submit.data?.id, onSubmitted, candidate.id, production?.number, releaseRevision, reviewerName]);
  const link = submit.data?.id
    ? `${window.location.origin}${paths.dashboard.skills.contextReview(contextId)}?request=${submit.data.id}`
    : "";
  return (
    <Section title={submit.data ? "Review request" : `Request rollback to v${candidate.number}`}>
      <Stack spacing={1.5}>
        <Typography variant="m3">Production v{production?.number ?? "?"} → v{candidate.number}</Typography>
        {submit.data && <><Alert severity="success" role="status">Rollback request submitted. Production has not moved. The reviewer approves and releases it from Skills Reviews.</Alert><TechnicalDetails title="Attached test evidence"><EvidenceSummary items={evidenceRows(originalPreview.current?.evidence ?? evidence)} emptyText="No matching test evidence attached. Not tested is not a pass." /></TechnicalDetails></>}
        {!submit.data && <Section title="Test evidence"><Stack spacing={1}>
          {options.isLoading ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" /> : options.isError ? <ErrorState title="Couldn't load test evidence" onRetry={options.refetch} /> : <>
            <EvidenceSummary items={evidenceRows(matchingEvidence.filter((item) => item.visibility === 'workspace'))} selectable selected={selectedEvidence}
              onToggle={reviewed || packet ? undefined : (item) => { const id = `${item.source}:${item.id}`; setSelectedEvidence((old) => old.includes(id) ? old.filter((value) => value !== id) : [...old, id]); }}
              emptyText="No matching test evidence attached. Not tested is not a pass." />
            {matchingEvidence.some((item) => item.visibility === 'private') && <><Typography variant="s3">Private evidence is not attached. Share it from Tests before including it in a workspace review.</Typography><EvidenceSummary items={evidenceRows(matchingEvidence.filter((item) => item.visibility === 'private'))} /></>}
          </>}
          <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} href={`?tab=tests&version=${candidate.number}`}>Run tests</Button>
        </Stack></Section>}
        {/* The revision this review is bound to stays in view: it is what tells the author a restart happened. */}
        <Typography variant="s3" color="text.secondary">Reviewed against release revision {releaseRevision}. If production changes, review and acknowledge the rollback again.</Typography>
        {packet && <TechnicalDetails title="Request details" keepMounted><IdentityChip label="Packet fingerprint" value={packet.fingerprint} /></TechnicalDetails>}
        {submit.data ? <FormControlLabel control={<Checkbox checked inputProps={{ readOnly: true }} />} label={`I reviewed everything v${candidate.number} contains, including linked skill versions and files left out.`} /> : <FormControlLabel
          control={
            <Checkbox
              checked={reviewed}
              disabled={Boolean(packet) || preview.isPending || !closureReady || options.isLoading || options.isError}
              onChange={(_, checked) => {
                // Unchecking after a refused packet starts the review again.
                if (!checked) {
                  requested.current = false;
                  originalPreview.current = null;
                  preview.reset?.();
                }
                setReviewed(checked);
              }}
            />
          }
          label={`I reviewed everything v${candidate.number} contains, including linked skill versions and files left out.`}
        />}
        {!submit.data && !reviewed && <Typography variant="s3">Tick the acknowledgment to continue.</Typography>}
        {!packet && !submit.data && <Stack spacing={1} alignItems="flex-start">
          <PeoplePicker label="Reviewer" people={reviewers.data ?? []} value={listed ? recipient : ''} onChange={onReviewer} loading={reviewers.isLoading} error={reviewers.error} onRetry={reviewers.refetch} helperText="Choose an independent reviewer. Sending a request does not move production." />
          <Button color="primary" variant="contained" disabled>Submit rollback for review</Button>
          {preview.isPending && <Box role="status"><StatusBadge status="checking" label="Preparing the rollback request…" /></Box>}
        </Stack>}
        <OperationFeedback error={preview.error} action="prepare a recovery review" onRefresh={onRefresh} onRetry={() => { if (originalPreview.current) startPreview(originalPreview.current); }} />
        {packet && (
          <Stack spacing={1}>
            {!submit.data && <Typography variant="s1" role="status">Rollback request ready. Choose who reviews it.</Typography>}
            {submit.data ? <Typography variant="s1">Requested from {reviewerName}</Typography> : <PeoplePicker label="Reviewer" people={reviewers.data ?? []} value={listed ? recipient : ''} onChange={onReviewer} loading={reviewers.isLoading} error={reviewers.error} onRetry={reviewers.refetch} disabled={submit.isPending || Boolean(originalSubmission.current) || Boolean(submit.data)} helperText="Suggested: whoever last moved production. The server checks review authority on submit." />}
            {!submit.data && <Button color="primary"
              sx={{ alignSelf: 'flex-start' }}
              variant="contained"
              disabled={
                !listed ||
                !canPromote ||
                submit.isPending ||
                Boolean(submit.data)
              }
              onClick={sendRequest}
            >
              Submit rollback for review
            </Button>}
            {!submit.data && !listed && <Typography variant="s3">Choose an authorized reviewer to continue.</Typography>}
            {!canPromote && (
              <Typography variant="s3">
                You cannot request production changes for this context.
              </Typography>
            )}
            <OperationFeedback error={submit.error} action="request a recovery release" onRefresh={onRefresh} onRetry={sendRequest} onReconcile={sendRequest} />
            {link && (
              <Stack spacing={1}>
                <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center">
                  <Button variant="outlined" color="inherit" href={link} aria-label="Review link">Open review request</Button>
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
                </Button></Stack>
                {copy.done && (
                  <Typography variant="s3" role="status">
                    Copied the review link.
                  </Typography>
                )}
                {copy.error && <Alert severity="error">{copy.error}<Typography component="code" variant="s3" sx={{ display: 'block', overflowWrap: 'anywhere', userSelect: 'all' }}>{link}</Typography></Alert>}
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
  onRefresh: PropTypes.func,
  onSubmitted: PropTypes.func.isRequired,
};

function LocalRecovery({ contextId, contextName, candidate, canIssue }) {
  const issue = useIssueRecoveryCredential(contextId);
  const [key] = useState(uuid);
  const [hours, setHours] = useState(24);
  const [commit, setCommit] = useState("");
  const validCommit = /^[a-f0-9]{40}$/.test(commit.trim());
  const [copyStatus, setCopyStatus] = useState("");
  const commands = restoreCommands({
    name: contextName,
    version: candidate.number,
    commit: commit.trim(),
  });
  return (
    <Stack spacing={3}>
        <Section title="Replace the application credential"><Stack spacing={1.5}>
        <Typography variant="s3">
          Create a new access key for apps using production, limited to v{candidate.number}.
          Expired or revoked keys stay invalid. This key cannot resume threads opened with an older key.
        </Typography>
        <TextField
          select
          size="small"
          label="Expires after"
          sx={{ width: 1, maxWidth: 480 }}
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
          sx={{ alignSelf: 'flex-start' }}
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
        </Stack></Section><Section title="Restore the reviewed repository version"><Stack spacing={1.5}>
        <TextField
          size="small"
          label="Last reviewed commit"
          sx={{ width: 1, maxWidth: 480 }}
          value={commit}
          onChange={(event) => setCommit(event.target.value)}
          error={Boolean(commit) && !validCommit}
          helperText={validCommit ? "The restore verifies this reviewed lock and preserves local work." : "Enter the reviewed 40-character commit"}
        />
        <CodeBlock code={commands} wrap label="Lock restore commands" copyLabel="Copy restore commands"
          action={<Tooltip title={validCommit ? 'Copy restore commands' : 'Enter the reviewed 40-character commit'}><span><IconButton aria-label="Copy restore commands" disabled={!validCommit} onClick={async () => {
            try {
              if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
              await navigator.clipboard.writeText(commands);
              setCopyStatus('Copied restore commands.');
            } catch { setCopyStatus("Couldn't copy. Select the commands below."); }
          }}><Iconify icon="solar:copy-linear" width={16} /></IconButton></span></Tooltip>} />
        {copyStatus && <Typography role="status" variant="s3">{copyStatus}</Typography>}
        </Stack></Section><Section title="Review application threads">
        <Stack component="ul" spacing={1} sx={{ m: 0, pl: 2.5 }}>
          <Typography component="li" variant="s1">Resume paused threads from their original saved state.</Typography>
          <Typography component="li" variant="s1">Check completed actions before continuing. Never run them twice.</Typography>
          <Typography component="li" variant="s1">If access or a thread link is lost, review a new thread and close the old one.</Typography>
          <Typography component="li" variant="s1">Review every thread in one plan. If anything changes, review it again.</Typography>
        </Stack>
        <TechnicalDetails title="Thread recovery checks">
          <Typography variant="s3">Resume only under the original session and checkpoint. Reconcile persisted business receipts before the next model call. Threads with expired authority or lost mappings are not recovered; a clean thread needs explicit review and the abandoned thread must be closed. Plan every registered thread once and apply exactly the reviewed plan. A different plan digest or a thread that moved after review is refused.</Typography>
        </TechnicalDetails></Section>
      </Stack>
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
  const previewState = new URLSearchParams(window.location.search).get("state");
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
  const [submitted, setSubmitted] = useState(null);
  const onSubmitted = useCallback((receipt) => setSubmitted(receipt), []);
  const previousProduction = useRef(null);
  const movedFrom = useRef(null);
  // The reviewer stays selected across a restarted review (the release moved), so a
  // fresh packet is one acknowledgement and one submit. Until one is picked, the person
  // who last moved production (or the only reviewer) is suggested.
  const [reviewerId, setReviewerId] = useState("");
  const data = query.data;
  const liveProduction = data?.candidates.find((row) => row.isProduction);
  useEffect(() => {
    if (!liveProduction) return;
    if (previousProduction.current && previousProduction.current.id !== liveProduction.id) movedFrom.current = previousProduction.current.number;
    previousProduction.current = liveProduction;
  }, [liveProduction]);
  // The newest eligible release before the failing one is preselected, never auto-submitted.
  const suggestion = useMemo(
    () => (data ? suggestRelease({ candidates: data.candidates, cohort: data.cohort }) : null),
    [data],
  );
  // The suggestion becomes the actual selection once, so it stays selected after production
  // moves to it (it is then the release whose locks and applications are recovered next).
  useEffect(() => {
    if (picked === null && (suggestion || liveProduction)) setChosen(suggestion?.candidateId ?? liveProduction.id);
  }, [suggestion, picked, liveProduction]);
  const chosen = picked ?? suggestion?.candidateId ?? liveProduction?.id ?? null;
  const chosenCandidate = data?.candidates.find((row) => row.id === chosen);
  const reviewerOptions = useReviewOptions('agent_context', contextId, chosenCandidate && !chosenCandidate.isProduction ? chosenCandidate.id : undefined);
  const suggestedReviewer = suggestReviewer({
    reviewers: recipientsOf(reviewerOptions.data),
    lastReleaserId: data?.lastReleaserId,
  });
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
  if (previewState === 'loading' || (query.isLoading && !data)) return <Stack spacing={3} aria-label="Loading recovery">
    <Typography variant="m3"><Skeleton width="65%" sx={{ bgcolor: 'action.selected' }} /></Typography>
    <Typography variant="m3"><Skeleton width="24%" sx={{ bgcolor: 'action.selected' }} /></Typography>
    <ContentSkeleton variant="rows" rows={4} sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} />
    <Typography variant="m3"><Skeleton width="28%" sx={{ bgcolor: 'action.selected' }} /></Typography>
    <ContentSkeleton variant="panel" rows={2} sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} />
  </Stack>;
  if (previewState === 'inventory-limit' || query.error?.code === 'recovery_inventory_limit') return <CapabilityNotice title="Recovery needs a complete inventory" description="This context has more than 100 releases or consumers, which recovery can't review completely here. Use the CLI restore for contexts this large." actions={<Button href="?tab=use" variant="outlined">Open CLI restore instructions</Button>} details={query.error?.message} />;
  if (previewState === 'denied' || query.error?.statusCode === 403) return <CapabilityNotice title="Rollback access" description="Only admins and reviewers can start a rollback. Ask a workspace admin." />;
  if (previewState === 'off') return <CapabilityNotice title="Recovery is not available" description="Ask a workspace administrator to check recovery access." />;
  if (previewState === 'empty') return <EmptyState title="Nothing to roll back to yet" description="v1 is the only released version, so there's nothing to roll back to yet." />;
  if (previewState === 'error' || query.error || !data)
    return (
      <ErrorState
        title="Couldn't load recovery"
        detail="The release, safety or consumer facts could not be read completely. No partial recovery choice is shown."
        onRetry={query.refetch}
      />
    );
  const candidate = data.candidates.find((row) => row.id === chosen);
  const production = data.candidates.find((row) => row.isProduction);
  const awaiting = submitted?.candidateId === chosen && submitted?.revision === data.releaseRevision && !candidate?.isProduction;
  const confirmed = data.cohort.filter((row) => row.observationsComplete === true && row.states.includes('matches_target') && row.loaded?.versionId === row.desired?.versionId && row.states.every((state) => ['matches_target', 'pinned_as_intended'].includes(state))).length;
  const from = previousProduction.current?.id !== production?.id ? previousProduction.current?.number : movedFrom.current;
  const step = candidate?.isProduction ? 2 : awaiting ? 1 : 0;
  // An initial release is not a recovery task. Link to delivery facts, but do not
  // invite an impossible rollback or expose a replacement credential form.
  const initial = initialReleaseState(data.candidates, data.releaseRevision);
  if (initial && !from && !submitted) return <EmptyState title="Nothing to roll back to yet"
    description={<>{`v${initial.production.number} is the only released version, so there's nothing to roll back to yet.`}{initial.next && <Typography component="span" variant="s1" sx={{ display: 'block', mt: 1 }}>{`To change production, request a release review for v${initial.next.number}.`}</Typography>}</>}
    action={initial.next ? <Button variant="outlined" href={reviewComposerUrl({ kind: 'agent_context', subjectId: contextId, name: contextName, candidateId: initial.next.id, number: initial.next.number, digest: initial.next.digest, label: 'production' })}>Request release review for v{initial.next.number}</Button> : <Button variant="outlined" href="?tab=consumers">Open consumers</Button>} />;
  return (
    <Stack spacing={3} sx={{ minWidth: 0 }}>
      <Stack spacing={1}>
        <Typography variant="m3" role="status">{candidate?.isProduction ? `${from ? `Production moved from v${from} to v${candidate.number}` : `Production is v${candidate.number}`} · ${confirmed} of ${data.cohort.length} consumers confirmed` : awaiting ? `Rollback to v${candidate?.number} requested from ${submitted.reviewer}. Production has not moved.` : `Production is ${production ? `v${production.number}` : 'not set'}.${candidate ? ` Roll back to v${candidate.number}?` : ' Choose an eligible version to request a rollback.'}`}</Typography>
        <Stack component="ol" direction="row" spacing={1} useFlexGap flexWrap="wrap" aria-label="Recovery progress" sx={{ listStyle: 'none', m: 0, p: 0 }}>
          {['Request', 'Approve', 'Confirm apps updated'].map((label, index) => <Stack component="li" key={label} direction="row" spacing={1} alignItems="center" aria-current={step === index ? 'step' : undefined}>
            {index > 0 && <Typography variant="s1" color="text.secondary" aria-hidden="true">→</Typography>}
            <Box aria-hidden="true" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: step === index ? 'text.primary' : 'divider' }} />
            <Typography variant="s1" color={step === index ? 'text.primary' : 'text.secondary'} sx={{ fontWeight: step === index ? 600 : 400 }}>{label}</Typography>
          </Stack>)}
        </Stack>
      </Stack>
      {data.canPromote === false && <CapabilityNotice title="Rollback access" description="Only admins and reviewers can start a rollback. Ask a workspace admin." />}
      {query.isFetching && <StatusBadge status="checking" label="Updating…" sx={{ alignSelf: 'flex-start' }} />}
      {!data.candidates.some((row) => row.eligible) && !candidate?.isProduction && <EmptyState title="No eligible recovery release" description="Excluded releases cannot be requested. Review their restrictions below." />}
      {!candidate?.isProduction && <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', lg: awaiting ? 'minmax(0, 1fr)' : 'minmax(0, 3fr) minmax(300px, 2fr)' }, gap: 3, alignItems: 'start' }}>
      {!awaiting && <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        <Section title={candidate ? `Recovery release: v${candidate.number}${suggestion?.candidateId === candidate.id ? ' (suggested)' : ''}` : 'Choose a recovery release'}>
          {suggestion && <Typography variant="s3" color="text.secondary">v{suggestion.number} is the newest eligible release before v{suggestion.failing}. Review it below or choose another.</Typography>}
          {/* The excluded alternatives stay visible: the closure acknowledgment covers them, and a closed disclosure would add a gesture to every recovery. */}
          <Candidates bare candidates={data.candidates} chosen={chosen} onChoose={setChosen} />
        </Section>
        {candidate && <ClosureReview candidate={candidate} production={production} closure={closure} />}
        <TechnicalDetails title="Exact identities">
          <Stack spacing={1}>
            {data.candidates.map((release) => <IdentityChip key={release.id} label={`Release v${release.number}`} value={release.digest} />)}
            {(closure.data?.rows ?? []).map((row) => <Stack key={`${row.status}:${row.path}`} spacing={0.5}><Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{row.path} · {row.sizeBytes} B</Typography><IdentityChip label="File digest" value={row.sha256} /></Stack>)}
          </Stack>
        </TechnicalDetails>
      </Stack>}
      {candidate && !candidate.isProduction && (
        <RollbackRequest
          // A moved release revision makes an earlier packet stale: start a fresh review.
          key={`${candidate.id}:${data.releaseRevision}`}
          contextId={contextId}
          candidate={candidate}
          production={production}
          canPromote={data.canPromote}
          closureReady={Boolean(closure.data) && !closure.error}
          releaseRevision={data.releaseRevision}
          reviewerId={reviewerId || suggestedReviewer}
          onReviewer={setReviewerId}
          onSubmitted={onSubmitted}
          onRefresh={() => refreshFacts()}
        />
      )}
      </Box>}
      {candidate?.isProduction && <TechnicalDetails title="Choose another recovery release"><Candidates candidates={data.candidates} chosen={chosen} onChoose={setChosen} /><TechnicalDetails title="Exact identities">{data.candidates.map((release) => <IdentityChip key={release.id} label={`Release v${release.number}`} value={release.digest} />)}</TechnicalDetails></TechnicalDetails>}
      {step < 1 && <Typography variant="s1" color="text.secondary" sx={{ py: 1, borderTop: 1, borderColor: 'divider' }}>2 · Await approval — submit the rollback for independent review.</Typography>}
      {step < 2 && <Typography variant="s1" color="text.secondary" sx={{ py: 1, borderTop: 1, borderColor: 'divider' }}>3 · Confirm apps updated — available after production moves.</Typography>}
      {/* J6 inspects the incident cohort before requesting a rollback. Keep its facts visible,
          but defer the delivery warning and local tasks until verification is active. */}
      {(step === 2 || data.cohort.some((row) => row.latestFailure)) && <Cohort cohort={data.cohort} numbers={numbers} asOf={data.asOf} active={step === 2} />}
      {candidate && (candidate.isProduction ? <LocalRecovery key={`local-${candidate.id}`} contextId={contextId} contextName={contextName} candidate={candidate} canIssue={data.canIssueCredentials} /> : <TechnicalDetails title="Prepare local recovery in advance"><LocalRecovery key={`local-${candidate.id}`} contextId={contextId} contextName={contextName} candidate={candidate} canIssue={data.canIssueCredentials} /></TechnicalDetails>)}
      <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} onClick={() => refreshFacts()}>
        Refresh recovery facts
      </Button>
    </Stack>
  );
}
J6RecoveryPanel.propTypes = {
  contextId: PropTypes.string.isRequired,
  contextName: PropTypes.string.isRequired,
};
