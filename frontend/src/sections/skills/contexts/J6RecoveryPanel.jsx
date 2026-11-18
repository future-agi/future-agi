import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import {
  Alert,
  Box,
  Button,
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
  useOpenContextRequests,
  usePreviewRecoveryRelease,
  useRecoveryClosure,
  useRefreshContextFacts,
  DESCRIPTOR_PATH,
  useSubmitRecoveryRequest,
} from "src/api/skills/j6-recovery";
import EmptyState from 'src/components/empty-state';
import { useReviewOptions } from 'src/api/skills/review-authoring';
import { useExecuteReleaseRequest, useReleaseRequest } from 'src/api/skills/review-production';
import IdentityChip from '../components/IdentityChip';
import PeoplePicker from '../components/PeoplePicker';
import EvidenceSummary from '../components/EvidenceSummary';
import TechnicalDetails from '../components/TechnicalDetails';
import OperationFeedback from '../components/OperationFeedback';
import CapabilityNotice from '../components/CapabilityNotice';
import TextDiff from '../components/TextDiff';
import Acknowledgment, { useAcknowledgment } from '../components/Acknowledgment';
import { AsOf } from './ContextConsumersView';
import CodeBlock from 'src/components/code-block';
import Iconify from 'src/components/iconify';
import { consumerNextStep, humanizeCode, needsDeliveryVerification } from './consumerPresentation';
import { paths } from "src/routes/paths";
import { restoreCommands } from "./restoreCommands";
import { suggestRelease, suggestReviewer, initialReleaseState, checkedFiles, rollbackAcknowledgment, rollbackProgress, trackerSteps, canMoveFromTracker, resumedRollback } from "./recoverySuggestion";
import { INBOX_OPEN_REQUEST, OPEN_CONNECTED_APPS } from "../utils/terms";
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
  unknown: "No report from this app yet",
  quarantined: "Quarantined",
};
const reasonNames = {
  current_production: "In production",
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
    <Section title="Check apps updated">
      <Stack spacing={1.5}>
        <Typography variant="s3" color="text.secondary">As of <AsOf value={asOf} />. Moving production doesn&apos;t update running apps. Each app must report the new version before it counts as recovered.</Typography>
        {active && missing > 0 && <Alert severity="warning">{missing} of {cohort.length} {cohort.length === 1 ? 'app' : 'apps'} still {missing === 1 ? 'needs' : 'need'} delivery verification.</Alert>}
        {!cohort.length && <EmptyState title="No apps connected yet" description="An app can only confirm what it received once it is connected and reports back." />}
        <TableContainer sx={{ maxWidth: 1 }}>
          <Table size="small" aria-label="Recovery cohort">
            <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}>
              <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                <TableCell>App</TableCell>
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
                          role={state === 'unknown' ? 'neutral' : undefined}
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

// Below sm each release is one stacked card made from the same cells: line 1 is the version with
// its status badge at the right, line 2 is the linked-skill count and one short note with the
// Choose button at the right. Labels repeat only in the header the desktop table shows.
const quarantined = (row) => row.reasons.includes('quarantined');
// A reason kept on a release that is no longer quarantined belongs to the review that lifted it.
const approvalNote = (row) => Boolean(row.quarantineReason) && !quarantined(row);
const cardNote = (row) => (approvalNote(row) ? 'Approval complete' : row.quarantineReason ? row.quarantineReason : row.lowerThanProduction && row.eligible ? 'Earlier version' : '');

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
                sx={(theme) => ({
                  height: { xs: 'auto', sm: ROW_HEIGHT.default },
                  [theme.breakpoints.down('sm')]: {
                    display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', columnGap: 1, rowGap: 0.5, alignItems: 'center', py: 1.5, px: 1, borderBottom: `1px solid ${theme.palette.divider}`,
                    '& > td': { p: 0, border: 0, minWidth: 0 },
                    '& > td:nth-of-type(1)': { gridColumn: 1, gridRow: 1 },
                    '& > td:nth-of-type(2)': { gridColumn: 2, gridRow: 1, justifySelf: 'end' },
                    '& > td:nth-of-type(3)': { gridColumn: 1, gridRow: 2 },
                    '& > td:nth-of-type(4)': { gridColumn: 2, gridRow: 2, justifySelf: 'end' },
                  },
                })}
              >
                <TableCell>
                  <Typography variant="s1">v{row.number}</Typography>
                </TableCell>
                <TableCell>
                  <Stack spacing={0.5} alignItems={{ xs: 'flex-end', sm: 'flex-start' }}>
                    {row.eligible ? (
                      <StatusBadge status="completed" label="Can roll back to" />
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
                    <Box sx={{ display: { xs: 'none', sm: 'block' } }}>
                      {row.lowerThanProduction && row.eligible && (
                        <Typography variant="s3">
                          Earlier version
                        </Typography>
                      )}
                      {approvalNote(row) && <Typography variant="s3">{`Approval for v${row.number} is complete.`}</Typography>}
                      {row.quarantineReason && quarantined(row) && (
                        <Typography variant="s3">{row.quarantineReason}</Typography>
                      )}
                    </Box>
                  </Stack>
                </TableCell>
                <TableCell>
                  <Typography variant="s1" sx={{ typography: { xs: 's3', sm: 's1' }, color: { xs: 'text.secondary', sm: 'text.primary' } }}>
                    {row.children.length} linked skill{row.children.length === 1 ? "" : "s"}
                    {cardNote(row) && <Box component="span" sx={{ display: { xs: 'inline', sm: 'none' } }}>{` · ${cardNote(row)}`}</Box>}
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
                  {row.isProduction ? <Typography variant="s3" color="text.secondary" aria-hidden="true">—</Typography> : <Button
                    size="small"
                    variant="text"
                    color={chosen === row.id ? "primary" : "inherit"}
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
  return bare ? table : <Section title="Choose a version to roll back to">{table}</Section>;
}
Candidates.propTypes = {
  candidates: PropTypes.array.isRequired,
  chosen: PropTypes.string,
  onChoose: PropTypes.func.isRequired,
  bare: PropTypes.bool,
};

// The server keeps the note of the last safety review on a release. Once a quarantine is lifted the
// table says "Approval complete"; the note itself stays readable here.
function ReleaseNotes({ candidates }) {
  return candidates.filter(approvalNote).map((row) => <Typography key={row.id} variant="s3" sx={{ overflowWrap: 'anywhere' }}>{`Safety review note for v${row.number}: ${row.quarantineReason}`}</Typography>);
}
ReleaseNotes.propTypes = { candidates: PropTypes.array.isRequired };

const changeNames = {
  added: "Added",
  changed: "Changed",
  removed: "Removed",
  unchanged: "Unchanged",
};
const changeStatus = { added: "current_only", changed: "current_only", removed: "failed", unchanged: "completed" };

const linkSentence = (links) => links.map((link) => (link.from == null ? `${link.name} v${link.to} added` : link.to == null ? `${link.name} v${link.from} removed` : `${link.name} v${link.from} → v${link.to}`)).join(', ');

// UXF-05: what a person is asked to tick is open. Changed files are open by default: all of them when
// there are 3 or fewer, otherwise the first one plus "Show N more changed files". Unchanged files are
// collapsed behind "Show N unchanged files". Nothing is gated on scrolling. The same rule the release
// flow uses (review/ReleaseReviewBody.jsx, owned by another lane), kept here so the lanes stay independent.
const OPEN_CHANGED_LIMIT = 3;
const countOf = (count, one, many) => `${count} ${count === 1 ? one : many}`;

function ClosureReview({ candidate, production, closure }) {
  const [moreChanged, setMoreChanged] = useState(false);
  const [showUnchanged, setShowUnchanged] = useState(false);
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
  // Changed files first, so a collapsed table never hides a difference under the acknowledgment.
  const changedRows = rows.filter((row) => row.status !== 'unchanged');
  const unchangedRows = rows.filter((row) => row.status === 'unchanged');
  const changedCount = changedRows.length;
  const descriptor = changes.find((change) => change.path === DESCRIPTOR_PATH);
  // The main instructions file comes first, so a long list opens on the file people expect to read.
  const content = changes.filter((change) => change.path !== DESCRIPTOR_PATH).sort((x, y) => Number(y.path === 'AGENTS.md') - Number(x.path === 'AGENTS.md'));
  const links = descriptor?.links ?? [];
  const openContent = content.length <= OPEN_CHANGED_LIMIT || moreChanged ? content : content.slice(0, 1);
  const hiddenContent = content.length - openContent.length;
  const shown = showUnchanged ? [...changedRows, ...unchangedRows] : changedRows;
  const differing = checkedFiles(rows, DESCRIPTOR_PATH).map((row) => row.path);
  const settingsChanged = rows.some((row) => row.path === DESCRIPTOR_PATH && row.status !== 'unchanged');
  const differs = differing.length ? `Only ${differing.length > 3 ? `${differing.length} files` : differing.join(', ')} ${differing.length === 1 ? 'differs' : 'differ'}.` : settingsChanged ? 'Only the generated settings file differs.' : 'Nothing differs from production.';
  const diff = (change) => <TextDiff fromText={change.lines.filter((line) => line.kind !== 'added').map((line) => line.text).join('\n')} toText={change.lines.filter((line) => line.kind !== 'removed').map((line) => line.text).join('\n')} fromTitle={`Production v${production?.number ?? '?'}`} toTitle={`v${candidate.number}`} />;
  return (
    <Section title={`What changes: everything in v${candidate.number} against production v${production?.number ?? '?'}`}>
      <Stack spacing={1.5}>
        {/* Conclusion first: the readable consequence, then the files people read, then machine records. */}
        <Typography variant="s1" role="status">
          {links.length ? linkSentence(links) : `Roll back to v${candidate.number}`} · {changedCount} file{changedCount === 1 ? '' : 's'} differ{changedCount === 1 ? 's' : ''}
        </Typography>
        {/* Every changed file is listed and its change is open; unchanged files are one click away. */}
        <TableContainer sx={{ maxWidth: 1 }}>
          <Table size="small" aria-label="Recovery closure files">
            <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}>
              <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                <TableCell>File</TableCell>
                <TableCell>Against production</TableCell>
                <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>Size</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.map((row) => (
                <TableRow key={`${row.status}:${row.path}`} hover sx={{ height: { xs: 'auto', sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr) auto', py: { xs: 1, sm: 0 }, '& > td': { borderBottom: { xs: 0, sm: undefined } } }}>
                  {/* The generated settings file reads as that; its path stays the cell's name and sits in Technical details. */}
                  <TableCell aria-label={row.path} sx={{ overflowWrap: "anywhere" }}>{row.path === DESCRIPTOR_PATH ? 'Context settings (generated)' : row.path}</TableCell>
                  <TableCell>
                    <StatusBadge status={changeStatus[row.status]} label={changeNames[row.status]} />
                  </TableCell>
                  <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>{row.sizeBytes} B</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        {unchangedRows.length > 0 && <Button size="small" variant="text" color="inherit" sx={{ alignSelf: 'flex-start' }} aria-expanded={showUnchanged} onClick={() => setShowUnchanged((open) => !open)}>{showUnchanged ? 'Hide unchanged files' : `Show ${countOf(unchangedRows.length, 'unchanged file', 'unchanged files')}`}</Button>}
        <Typography variant="s3" color="text.secondary">
          {differs}{settingsChanged && differing.length > 0 && ' The generated settings file changes with it.'}
          {candidate.children.length > 0 && ` Linked skills stay at the versions locked in v${candidate.number}.`}
          {candidate.unavailableChildren.length > 0 && ` ${candidate.unavailableChildren.length} source${candidate.unavailableChildren.length === 1 ? " is" : "s are"} retired; their exact files are kept.`}
        </Typography>
        <Stack spacing={1.5} sx={{ minWidth: 0 }} aria-label="Recovery file changes">
        {openContent.map((change) => <Stack key={change.path} spacing={0.5} data-file-open="true">
          <Typography variant="m3">{changeNames[change.status]}: {change.path}</Typography>
          <Box aria-label={`${change.path} change from production`} tabIndex={0}>{diff(change)}</Box>
        </Stack>)}
        {hiddenContent > 0 && <Button size="small" variant="text" color="inherit" sx={{ alignSelf: 'flex-start' }} onClick={() => setMoreChanged(true)}>Show {countOf(hiddenContent, 'more changed file', 'more changed files')}</Button>}
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

const REQUEST_CLOSED_NOTE = {
  changes_requested: 'The reviewer asked for changes.',
  superseded: 'A newer request replaced this one.',
  cancelled: 'This request was cancelled.',
  canceled: 'This request was cancelled.',
  rejected: 'The reviewer declined this request.',
  declined: 'The reviewer declined this request.',
  expired: 'This request expired.',
};

// The server says the approved request still applies and this person may move production.
const readIsCurrent = (data) => Boolean(data) && data.canExecute === true && data.preview?.current === true && !data.staleCode && !data.renewalRequired;

// After the request is sent, the same panel is a status tracker (UXF-08): waiting for the reviewer,
// approved (production moves from one button here), then checking that apps updated. The server
// decides every step; this panel only reads the request and, once it is approved, sends the same
// execute request the release page sends. Anything it cannot prove is this session's request, or that
// this person may move production now, sends the person to the request page, which asks for its own check.
function RollbackTracker({ candidate, submitted, read, link, evidenceItems, onRefresh }) {
  const execute = useExecuteReleaseRequest();
  const [key] = useState(uuid);
  const [copy, setCopy] = useState({ busy: false, done: false, error: "" });
  const status = read.data?.request?.status;
  const progress = rollbackProgress({ awaiting: true, status });
  const canMove = canMoveFromTracker({ read: read.data, submitted });
  const move = () => execute.mutate({
    requestId: submitted.requestId,
    revision: submitted.requestRevision ?? read.data.request.revision,
    expectedFingerprint: submitted.fingerprint,
    idempotencyKey: key,
  });
  return (
    <Stack spacing={1.5}>
      {progress.phase === 'waiting' && <Alert severity="success" role="status">{`Rollback to v${candidate.number} requested from ${submitted.reviewer || 'your reviewer'}. Production has not moved.`}</Alert>}
      {progress.phase === 'approved' && <Alert severity="success" role="status">{`${submitted.reviewer || 'Your reviewer'} approved the rollback to v${candidate.number}. Production has not moved yet.`}</Alert>}
      {progress.phase === 'moved' && <Alert severity="success" role="status">{`Production moved to v${candidate.number}. Checking that apps updated is next.`}</Alert>}
      {progress.phase === 'closed' && <Alert severity="warning" role="status">{`${REQUEST_CLOSED_NOTE[status] ?? 'This request is closed.'} Production has not moved.`}</Alert>}
      {read.isError && <Alert severity="warning" role="status" action={<Button size="small" color="inherit" onClick={() => read.refetch?.()}>Check again</Button>}>Couldn&apos;t check the request status. Production has not moved.</Alert>}
      {progress.phase === 'approved' && (canMove ? (
        <Stack spacing={1} alignItems="flex-start">
          <Typography variant="s3" color="text.secondary">Moving production doesn&apos;t update running apps. Each app must report the new version before it counts as recovered.</Typography>
          <Button color="primary" variant="contained" disabled={execute.isPending || Boolean(execute.data)} onClick={move}>{`Move production to v${candidate.number}`}</Button>
          <OperationFeedback error={execute.error} action="move production" onRefresh={onRefresh} onRetry={move} onReconcile={move} />
        </Stack>
      ) : (
        <Typography variant="s3" color="text.secondary">{submitted.resumed && readIsCurrent(read.data)
          // A current request found again after a reload was not checked in this page session: the request page confirms it first.
          ? 'Open the request to move production. It shows the change for you to confirm before production moves.'
          : 'Open the request to move production: it has changed since it was sent, or moving production needs someone else.'}</Typography>
      ))}
      <TechnicalDetails title="Attached test evidence"><EvidenceSummary items={evidenceItems} emptyText="No matching test evidence attached. Not tested is not a pass." /></TechnicalDetails>
      {link && (
        <Stack spacing={1}>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center">
            <Button variant="outlined" color="inherit" href={link}>{INBOX_OPEN_REQUEST}</Button>
            <Button
              variant="outlined"
              disabled={copy.busy}
              onClick={async () => {
                setCopy({ busy: true, done: false, error: "" });
                try {
                  if (!navigator.clipboard?.writeText) throw new Error("Unavailable");
                  await navigator.clipboard.writeText(link);
                  setCopy({ busy: false, done: true, error: "" });
                } catch {
                  setCopy({ busy: false, done: false, error: "Could not copy. Select and copy the link manually." });
                }
              }}
            >
              Copy request link
            </Button>
          </Stack>
          {copy.done && <Typography variant="s3" role="status">Copied the request link.</Typography>}
          {copy.error && <Alert severity="error">{copy.error}<Typography component="code" variant="s3" sx={{ display: 'block', overflowWrap: 'anywhere', userSelect: 'all' }}>{link}</Typography></Alert>}
        </Stack>
      )}
    </Stack>
  );
}
RollbackTracker.propTypes = {
  candidate: PropTypes.object.isRequired,
  submitted: PropTypes.object.isRequired,
  read: PropTypes.object.isRequired,
  link: PropTypes.string,
  evidenceItems: PropTypes.array.isRequired,
  onRefresh: PropTypes.func,
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
  acknowledgmentLabel,
  requestRead,
  resumed,
}) {
  const options = useReviewOptions('agent_context', contextId, candidate.id);
  // Reviewers come from the server's eligible recipients for this request (the same list the
  // release composer uses), which any author can read; the author is never offered.
  const reviewers = { data: recipientsOf(options.data), isLoading: options.isLoading, error: options.error, refetch: options.refetch };
  const [selectedEvidence, setSelectedEvidence] = useState([]);
  const matchingEvidence = (options.data?.evidence ?? []).filter((item) => item.subjectId === contextId && item.candidateDigest === candidate.digest);
  const evidence = matchingEvidence.filter((item) => item.visibility === 'workspace' && selectedEvidence.includes(`${item.source}:${item.sourceId}`));
  const evidenceRows = (items) => items.map((item) => ({ id: item.sourceId, source: item.source, kind: item.source === 'simulation_native' ? 'application_verification' : 'text_comparison', title: item.source === 'simulation_native' ? 'Application test' : 'Answer check', candidateNumber: candidate.number, status: item.status, visibility: item.visibility, expiresAt: item.payloadExpiresAt }));
  const preview = usePreviewRecoveryRelease();
  const submit = useSubmitRecoveryRequest(contextId);
  // One tick, bound to this release and the release revision it was made against (UXF-05, UXF-08).
  const ack = useAcknowledgment(`${candidate.id}:${candidate.digest ?? ''}:${releaseRevision ?? 0}`);
  const reviewed = ack.acknowledged;
  const [key] = useState(uuid);
  const packet = preview.data;
  const originalSubmission = useRef(null);
  const sendRequest = () => {
    // A lost response is reconciled with the same recipient, packet and operation key.
    originalSubmission.current ??= { preview: packet, reviewerId, idempotencyKey: key };
    submit.mutate(originalSubmission.current);
  };
  // The packet is computed from the reviewed closure the moment the review is
  // acknowledged; there is nothing further to decide before sending the request.
  const requested = useRef(false);
  const originalPreview = useRef(null);
  const startPreview = preview.mutate;
  const resetPreview = preview.reset;
  useEffect(() => {
    if (!reviewed) {
      // Unticking after a refused packet starts the review again.
      if (requested.current) {
        requested.current = false;
        originalPreview.current = null;
        resetPreview?.();
      }
      return;
    }
    if (requested.current) return;
    requested.current = true;
    originalPreview.current = { contextId, candidateVersionId: candidate.id, evidence, external_pipeline: null };
    startPreview(originalPreview.current);
  }, [reviewed, contextId, candidate.id, startPreview, resetPreview, evidence]);
  const recipient = originalSubmission.current?.reviewerId ?? reviewerId;
  const listed = (reviewers.data ?? []).some((item) => item.id === recipient);
  const reviewerName = (reviewers.data ?? []).find((person) => person.id === recipient)?.name || 'the selected reviewer';
  const sentFingerprint = originalSubmission.current?.preview?.fingerprint ?? packet?.fingerprint;
  useEffect(() => {
    if (submit.data?.id) onSubmitted({ candidateId: candidate.id, from: production?.number, revision: releaseRevision, reviewer: reviewerName, requestId: submit.data.id, requestRevision: submit.data.revision, fingerprint: sentFingerprint });
  }, [submit.data?.id, submit.data?.revision, onSubmitted, candidate.id, production?.number, releaseRevision, reviewerName, sentFingerprint]);
  const trackedId = submit.data?.id ?? resumed?.requestId;
  const link = trackedId
    ? `${window.location.origin}${paths.dashboard.skills.contextReview(contextId)}?request=${trackedId}`
    : "";
  // After sending, or when the server already has this person's rollback request (a reload, another
  // tab), the panel is the tracker. A resumed request carries no fingerprint from this session.
  if (submit.data || resumed) {
    const submitted = submit.data
      ? { requestId: submit.data.id, requestRevision: submit.data.revision, fingerprint: sentFingerprint, reviewer: reviewerName }
      : resumed;
    const sentEvidence = submit.data ? (originalPreview.current?.evidence ?? evidence) : (requestRead.data?.preview?.packet?.evidence ?? []);
    return (
      <Section title="Rollback request">
        <RollbackTracker candidate={candidate} submitted={submitted} read={requestRead} link={link} onRefresh={onRefresh}
          evidenceItems={evidenceRows(sentEvidence)} />
      </Section>
    );
  }
  const tickLocked = Boolean(packet) || preview.isPending || !closureReady || options.isLoading || options.isError;
  const reason = !canPromote ? 'You cannot request production changes for this context.'
    : !listed ? 'Choose an authorized reviewer to continue.'
      : !reviewed ? 'Check the changes to continue.' : '';
  return (
    <Section title="Ask for approval">
      <Stack spacing={1.5}>
        <Typography variant="m3">Production v{production?.number ?? "?"} → v{candidate.number}</Typography>
        <Section title="Test evidence"><Stack spacing={1}>
          {options.isLoading ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" /> : options.isError ? <ErrorState title="Couldn't load test evidence" onRetry={options.refetch} /> : <>
            <EvidenceSummary items={evidenceRows(matchingEvidence.filter((item) => item.visibility === 'workspace'))} selectable selected={selectedEvidence}
              onToggle={reviewed || packet ? undefined : (item) => { const id = `${item.source}:${item.id}`; setSelectedEvidence((old) => old.includes(id) ? old.filter((value) => value !== id) : [...old, id]); }}
              emptyText="No matching test evidence attached. Not tested is not a pass." />
            {matchingEvidence.some((item) => item.visibility === 'private') && <><Typography variant="s3">Private evidence is not attached. Share it from Tests before including it in a workspace review.</Typography><EvidenceSummary items={evidenceRows(matchingEvidence.filter((item) => item.visibility === 'private'))} /></>}
          </>}
          <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} href={`?tab=tests&version=${candidate.number}`}>Run tests</Button>
        </Stack></Section>
        {/* The revision this review is bound to stays in view: it is what tells the author a restart happened. */}
        <Typography variant="s3" color="text.secondary" data-release-revision={releaseRevision}>Reviewed against the release that made v{production?.number ?? "?"} production. If production changes, review and acknowledge the rollback again.</Typography>
        <TechnicalDetails title="Request details" keepMounted><Stack spacing={0.5}><Typography variant="s3">Release revision {releaseRevision}</Typography>{packet && <IdentityChip label="Packet fingerprint" value={packet.fingerprint} />}</Stack></TechnicalDetails>
        {/* One reviewer choice, preselected; the server still checks the reviewer's access when the request is sent. */}
        <PeoplePicker label="Reviewer" people={reviewers.data ?? []} value={listed ? recipient : ''} onChange={onReviewer} loading={reviewers.isLoading} error={reviewers.error} onRetry={reviewers.refetch} disabled={submit.isPending || Boolean(originalSubmission.current)} helperText="Suggested: whoever last moved production. The reviewer's access is checked when you submit." />
        <Acknowledgment acknowledgment={ack} label={acknowledgmentLabel} disabled={tickLocked || submit.isPending} />
        {preview.isPending && <Box role="status"><StatusBadge status="checking" label="Preparing the rollback request…" /></Box>}
        <OperationFeedback error={preview.error} action="prepare a recovery review" onRefresh={onRefresh} onRetry={() => { if (originalPreview.current) startPreview(originalPreview.current); }} />
        <Button color="primary"
          sx={{ alignSelf: 'flex-start' }}
          variant="contained"
          // The packet exists only after the tick (it is prepared from the checked changes), so it is the proof of the tick.
          disabled={!listed || !canPromote || !packet || submit.isPending}
          onClick={sendRequest}
        >
          Request rollback to v{candidate.number}
        </Button>
        {reason && <Typography variant="s3" color="text.secondary">{reason}</Typography>}
        <OperationFeedback error={submit.error} action="request a recovery release" onRefresh={onRefresh} onRetry={sendRequest} onReconcile={sendRequest} />
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
  acknowledgmentLabel: PropTypes.string.isRequired,
  requestRead: PropTypes.object.isRequired,
  resumed: PropTypes.object,
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
        <Section title="Replace the application key"><Stack spacing={1.5}>
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
        </Stack></Section>
        <TechnicalDetails title="Advanced recovery"><Stack spacing={3}>
        <Section title="Restore the reviewed repository version"><Stack spacing={1.5}>
        <Typography variant="s3" color="text.secondary">One command reads the lock from the commit you reviewed, checks it and applies it while nothing else is changing the folder. Local files outside the managed paths are kept.</Typography>
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
        </Stack></Section>
        <Section title="Check apps that are mid-conversation">
        <Stack component="ul" spacing={1} sx={{ m: 0, pl: 2.5 }}>
          <Typography component="li" variant="s1">Resume paused threads from their original saved state.</Typography>
          <Typography component="li" variant="s1">Check completed actions before continuing. Never run them twice.</Typography>
          <Typography component="li" variant="s1">If access or a thread link is lost, review a new thread and close the old one.</Typography>
          <Typography component="li" variant="s1">Review every thread in one plan. If anything changes, review it again.</Typography>
        </Stack>
        <TechnicalDetails title="Mid-conversation checks">
          <Typography variant="s3">Resume only under the original session and checkpoint. Reconcile persisted business receipts before the next model call. Threads with expired authority or lost mappings are not recovered; a clean thread needs explicit review and the abandoned thread must be closed. Plan every registered thread once and apply exactly the reviewed plan. A different plan digest or a thread that moved after review is refused.</Typography>
        </TechnicalDetails></Section>
        </Stack></TechnicalDetails>
      </Stack>
  );
}
LocalRecovery.propTypes = {
  contextId: PropTypes.string.isRequired,
  contextName: PropTypes.string.isRequired,
  candidate: PropTypes.object.isRequired,
  canIssue: PropTypes.bool,
};

// The status tracker after the request is sent (UXF-08): "Waiting for <reviewer>", "Approved: move
// production", "Check apps updated". Same numbered look as the release request composer's stepper: the
// current step is bold, filled and marked for assistive technology; earlier steps are done.
// Below sm the steps collapse to one line ("Step 1 of 3 · Waiting for Priya Shah"): three labels and
// their connectors cannot sit on one 390px row without one wrapping alone.
function RecoverySteps({ step, names }) {
  return <>
    <Typography variant="s3" color="text.secondary" sx={{ display: { xs: 'block', sm: 'none' } }}>{`Step ${step + 1} of ${names.length} · ${names[step]}`}</Typography>
    <Stack component="ol" direction="row" alignItems="center" flexWrap="wrap" columnGap={1.5} rowGap={1} aria-label="Rollback progress" sx={{ m: 0, p: 0, listStyle: 'none', display: { xs: 'none', sm: 'flex' } }}>
    {names.map((name, index) => {
      const current = index === step;
      const done = index < step;
      return <Stack component="li" key={name} direction="row" alignItems="center" gap={1} aria-current={current ? 'step' : undefined}>
        {index > 0 && <Box aria-hidden="true" sx={{ width: 24, height: '1px', bgcolor: 'divider' }} />}
        <Box component="span" aria-hidden="true" sx={{ width: 20, height: 20, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', typography: 's3', fontWeight: 600, bgcolor: current ? 'primary.main' : 'transparent', color: current ? 'primary.contrastText' : 'text.secondary', border: 1, borderColor: current || done ? 'primary.main' : 'divider' }}>{done ? <Iconify icon="solar:check-circle-linear" width={14} /> : index + 1}</Box>
        <Typography component="span" variant="s3" sx={{ fontWeight: current ? 600 : 400, color: current ? 'text.primary' : 'text.secondary' }}>{name}</Typography>
      </Stack>;
    })}
    </Stack>
  </>;
}
RecoverySteps.propTypes = { step: PropTypes.number.isRequired, names: PropTypes.arrayOf(PropTypes.string).isRequired };

// The notice ends in an action: the request an admin needs to act on, ready to paste.
function RollbackDenied({ contextName, version }) {
  const [copying, setCopying] = useState(false);
  const request = `Please start a rollback of ${contextName}${version ? ` to v${version}` : ''}, or give me reviewer access: ${window.location.href}`;
  const copy = async () => {
    setCopying(true);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Unavailable');
      await navigator.clipboard.writeText(request);
      enqueueSnackbar('Copied the request for an admin', { variant: 'success' });
    } catch {
      enqueueSnackbar("Couldn't copy the request. Ask an admin to start the rollback.", { variant: 'error' });
    } finally {
      setCopying(false);
    }
  };
  return <CapabilityNotice title="Rollback access" description="Only admins and reviewers can start a rollback." actions={<Button variant="outlined" size="small" disabled={copying} onClick={copy}>Copy request for admin</Button>} />;
}
RollbackDenied.propTypes = { contextName: PropTypes.string.isRequired, version: PropTypes.number };

const RECOVERY_FACTS_REFRESH_MS = 5000;
const REQUEST_STATUS_REFRESH_MS = 15000;

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
  const data = query.data;
  // UXF-08: the tracker comes from the server. This person's open rollback request for the release
  // in production now is resumed after a reload, so the page never offers a second request beside it.
  const openRequests = useOpenContextRequests(contextId, data?.canPromote !== false);
  const resumed = useMemo(
    () => (data && openRequests.data ? resumedRollback({ requests: openRequests.data, candidates: data.candidates, releaseRevision: data.releaseRevision }) : null),
    [data, openRequests.data],
  );
  const tracked = submitted ?? resumed;
  // The server's own read of the tracked request; it decides every step of the tracker.
  const requestRead = useReleaseRequest(tracked?.requestId, contextId);
  const latestRead = useRef(requestRead);
  latestRead.current = requestRead;
  const requestStatus = requestRead.data?.request?.status;
  // A request read is heavier than the recovery facts, so it follows more slowly (and only while it can still change).
  const watching = Boolean(tracked?.requestId) && !['released', 'changes_requested', 'superseded', 'cancelled'].includes(requestStatus);
  useEffect(() => {
    if (!watching) return undefined;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") latestRead.current.refetch?.();
    }, REQUEST_STATUS_REFRESH_MS);
    return () => clearInterval(timer);
  }, [watching]);
  const previousProduction = useRef(null);
  const movedFrom = useRef(null);
  // The reviewer stays selected across a restarted review (the release moved), so a
  // fresh packet is one acknowledgement and one submit. Until one is picked, the person
  // who last moved production (or the only reviewer) is suggested.
  const [reviewerId, setReviewerId] = useState("");
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
  // A resumed rollback request selects its own release; the choice waits until that lookup settles.
  const lookingUp = openRequests.isLoading;
  useEffect(() => {
    if (picked === null && !lookingUp && (resumed || suggestion || liveProduction)) setChosen(resumed?.candidateId ?? suggestion?.candidateId ?? liveProduction.id);
  }, [resumed, suggestion, picked, liveProduction, lookingUp]);
  const chosen = picked ?? resumed?.candidateId ?? suggestion?.candidateId ?? liveProduction?.id ?? null;
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
  if (previewState === 'inventory-limit' || query.error?.code === 'recovery_inventory_limit') return <CapabilityNotice title="This context is too large to review here. Use the CLI restore." description="Rollback reviews every version and every connected app, and this context has more than 100 of them." actions={<Button href="?tab=use" variant="outlined">Open CLI restore instructions</Button>} details={query.error?.message} />;
  if (previewState === 'denied' || query.error?.statusCode === 403) return <RollbackDenied contextName={contextName} />;
  if (previewState === 'off') return <CapabilityNotice title="Recovery is not available" description="Ask a workspace administrator to check recovery access." />;
  // The preview names the version the header shows, not a fixed one.
  if (previewState === 'empty') return <EmptyState title="Nothing to roll back to yet" description={`${liveProduction ? `v${liveProduction.number}` : 'This version'} is the only released version, so there's nothing to roll back to yet.`} />;
  if (previewState === 'error' || query.error || !data)
    return (
      <ErrorState
        title="Couldn't load recovery details."
        detail="Nothing was changed."
        onRetry={query.refetch}
      />
    );
  const candidate = data.candidates.find((row) => row.id === chosen);
  const production = data.candidates.find((row) => row.isProduction);
  const awaiting = tracked?.candidateId === chosen && tracked?.revision === data.releaseRevision && !candidate?.isProduction;
  const confirmed = data.cohort.filter((row) => row.observationsComplete === true && row.states.includes('matches_target') && row.loaded?.versionId === row.desired?.versionId && row.states.every((state) => ['matches_target', 'pinned_as_intended'].includes(state))).length;
  const from = previousProduction.current?.id !== production?.id ? previousProduction.current?.number : movedFrom.current;
  // -1 until a request is sent, then the tracker's step (UXF-08): waiting, approved, check apps.
  const { step, phase } = rollbackProgress({ awaiting, isProduction: Boolean(candidate?.isProduction), status: requestStatus });
  const acknowledgmentLabel = rollbackAcknowledgment({
    files: checkedFiles(closure.data?.rows, DESCRIPTOR_PATH).length,
    linkedSkillChanges: (closure.data?.changes ?? []).some((change) => change.path === DESCRIPTOR_PATH && (change.links?.length ?? 0) > 0),
  });
  // An initial release is not a recovery task. Link to delivery facts, but do not
  // invite an impossible rollback or expose a replacement credential form.
  const initial = initialReleaseState(data.candidates, data.releaseRevision);
  if (initial && !from && !tracked) return <EmptyState title="Nothing to roll back to yet"
    description={<>{`v${initial.production.number} is the only released version, so there's nothing to roll back to yet.`}{initial.next && <Typography component="span" variant="s1" sx={{ display: 'block', mt: 1 }}>{`To change production, send a release request for v${initial.next.number}.`}</Typography>}</>}
    // The header carries "Request release for vN" for a newer release, so the body does not repeat it.
    action={initial.next ? undefined : <Button variant="outlined" href="?tab=consumers">{OPEN_CONNECTED_APPS}</Button>} />;
  return (
    <Stack spacing={3} sx={{ minWidth: 0 }}>
      <Stack spacing={1}>
        {/* UXF-08: one title names the target; the target is preselected, so the question is not asked again. */}
        {!candidate?.isProduction && <Typography component="h2" variant="m1" sx={{ overflowWrap: 'anywhere' }}>{candidate ? `Roll back ${contextName} to v${candidate.number}` : `Roll back ${contextName}`}</Typography>}
        {!awaiting && <Typography variant="m3" role="status">{candidate?.isProduction ? `${from ? `Production moved from v${from} to v${candidate.number}` : `Production is v${candidate.number}`} · ${confirmed} of ${data.cohort.length} ${data.cohort.length === 1 ? 'app' : 'apps'} confirmed` : `Production is ${production ? `v${production.number}` : 'not set'}.${candidate ? '' : ' Choose an eligible version to request a rollback.'}`}</Typography>}
        {step >= 0 && <RecoverySteps step={step} names={trackerSteps(tracked?.reviewer, step)} />}
      </Stack>
      {data.canPromote === false && <RollbackDenied contextName={contextName} version={candidate && !candidate.isProduction ? candidate.number : undefined} />}
      {query.isFetching && <StatusBadge status="checking" label="Updating…" sx={{ alignSelf: 'flex-start' }} />}
      {!data.candidates.some((row) => row.eligible) && !candidate?.isProduction && <EmptyState title="No eligible recovery release" description="Excluded releases cannot be requested. Review their restrictions below." />}
      {!candidate?.isProduction && <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', lg: awaiting ? 'minmax(0, 1fr)' : 'minmax(0, 3fr) minmax(300px, 2fr)' }, gap: 3, alignItems: 'start' }}>
      {!awaiting && <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        <Section title="Version to roll back to">
          {suggestion && <Typography variant="s3" color="text.secondary">v{suggestion.number} is the newest eligible release before v{suggestion.failing}. Review it below or choose another.</Typography>}
          {/* The excluded alternatives stay visible: the closure acknowledgment covers them, and a closed disclosure would add a gesture to every recovery. */}
          <Candidates bare candidates={data.candidates} chosen={chosen} onChoose={setChosen} />
        </Section>
        {candidate && <ClosureReview candidate={candidate} production={production} closure={closure} />}
        <TechnicalDetails title="Technical details">
          <Stack spacing={1}>
            {data.candidates.map((release) => <IdentityChip key={release.id} label={`Release v${release.number}`} value={release.digest} />)}
            <ReleaseNotes candidates={data.candidates} />
            {(closure.data?.rows ?? []).map((row) => <Stack key={`${row.status}:${row.path}`} spacing={0.5}><Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{row.path} · {row.sizeBytes} B</Typography><IdentityChip label="File digest" value={row.sha256} /></Stack>)}
          </Stack>
        </TechnicalDetails>
      </Stack>}
      {candidate && !candidate.isProduction && !submitted && lookingUp && <Section title="Ask for approval"><ContentSkeleton variant="rows" rows={3} label="Checking for an open rollback request" sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} /></Section>}
      {candidate && !candidate.isProduction && !submitted && !lookingUp && openRequests.isError && <ErrorState title="Couldn't check for an open rollback request" detail="Nothing was sent. Check again before requesting a rollback." onRetry={() => openRequests.refetch()} />}
      {candidate && !candidate.isProduction && (submitted || (!lookingUp && !openRequests.isError)) && (
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
          acknowledgmentLabel={acknowledgmentLabel}
          requestRead={requestRead}
          resumed={awaiting && !submitted ? resumed : null}
        />
      )}
      </Box>}
      {candidate?.isProduction && <TechnicalDetails title="Choose another recovery release"><Candidates candidates={data.candidates} chosen={chosen} onChoose={setChosen} /><TechnicalDetails title="Technical details">{data.candidates.map((release) => <IdentityChip key={release.id} label={`Release v${release.number}`} value={release.digest} />)}<ReleaseNotes candidates={data.candidates} /></TechnicalDetails></TechnicalDetails>}
      {/* J6 inspects the incident cohort before requesting a rollback. Keep its facts visible,
          but defer the delivery warning and local tasks until verification is active. */}
      {(phase === 'moved' || data.cohort.some((row) => row.latestFailure)) && <Cohort cohort={data.cohort} numbers={numbers} asOf={data.asOf} active={phase === 'moved'} />}
      {candidate && (candidate.isProduction ? <LocalRecovery key={`local-${candidate.id}`} contextId={contextId} contextName={contextName} candidate={candidate} canIssue={data.canIssueCredentials} /> : <TechnicalDetails title="Prepare recovery on your machine"><LocalRecovery key={`local-${candidate.id}`} contextId={contextId} contextName={contextName} candidate={candidate} canIssue={data.canIssueCredentials} /></TechnicalDetails>)}
      <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} onClick={() => { refreshFacts(); openRequests.refetch?.(); if (tracked?.requestId) requestRead.refetch?.(); }}>
        Check again
      </Button>
    </Stack>
  );
}
J6RecoveryPanel.propTypes = {
  contextId: PropTypes.string.isRequired,
  contextName: PropTypes.string.isRequired,
};
