import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Checkbox, FormControlLabel, Stack, Typography } from "@mui/material";
import { isDefiniteRejection, operationStatus } from "src/api/skills/operation-errors";
import TextDiff from "../components/TextDiff";

// Intent: a release author acknowledges the current head, not a silent rebase.
// Hierarchy: origin/head consequence first, exact diffs second, one explicit action.
// Palette/depth/surfaces: existing neutral paper, divider and warning roles.
// Typography/spacing: m3/s1/s3, 4px rhythm, compact 12px control groups.

/** UI operation lifecycle only. Transport, mapping and invalidation stay in L7.
 * An unknown outcome keeps the original operation key and exact request body.
 * scopeKey must include organization, workspace, principal and resource identity.
 */
export function useAuthoringOperation(scopeKey) {
  const [state, setState] = useState({ pending: false, error: null, result: null, uncertain: false });
  const current = useRef(scopeKey);
  const mounted = useRef(true);
  const request = useRef(null);
  const running = useRef(false);
  if (current.current !== scopeKey) {
    current.current = scopeKey;
    request.current = null;
    running.current = false;
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const run = async (operation, input, { retainUnknown = true } = {}) => {
    if (running.current || !operation) return null;
    const scope = scopeKey;
    const body = JSON.stringify(input);
    if (request.current?.uncertain && request.current.body !== body) return null;
    if (!request.current || request.current.body !== body) {
      request.current = { body, key: crypto.randomUUID(), operation, retainUnknown };
    }
    const ticket = request.current;
    running.current = true;
    setState({ pending: true, error: null, result: null });
    try {
      const result = await ticket.operation(JSON.parse(ticket.body), { idempotencyKey: ticket.key });
      if (!mounted.current || current.current !== scope || request.current !== ticket) return null;
      const acknowledged = result === undefined ? { acknowledged: true } : result;
      setState({ pending: false, error: null, result: acknowledged, uncertain: false });
      return acknowledged;
    } catch (error) {
      if (mounted.current && current.current === scope && request.current === ticket) {
        const uncertain = ticket.retainUnknown && (ticket.uncertain || !isDefiniteRejection(error));
        ticket.uncertain = uncertain;
        if (!uncertain) request.current = null;
        setState({ pending: false, error, result: null, uncertain });
      }
      return null;
    } finally {
      if (current.current === scope) running.current = false;
    }
  };
  return { ...state, run, retry: () => request.current ? run(request.current.operation, JSON.parse(request.current.body), { retainUnknown: request.current.retainUnknown }) : null, canRetry: (input) => Boolean(state.error && request.current?.body === JSON.stringify(input)) };
}

export function AuthoringError({ error }) {
  if (!error) return null;
  const code = error.code || error.response?.data?.code;
  const status = operationStatus(error);
  let message = "The operation could not be confirmed. Your input is retained. Retry uses the same operation key.";
  if (code === "name_taken") {
    message = "This name is already in use. Choose a different immutable name; your instructions and purpose are preserved.";
  } else if (status === 400 || status === 422) {
    message = (typeof error.message === "string" && error.message) || (typeof error.detail === "string" && error.detail) || "The request was rejected. Correct the input and submit again; your input is preserved.";
  } else if (status === 409 || ["draft_conflict", "stale_base", "dependency_plan_changed", "candidate_changed", "release_conflict"].includes(code)) {
    message = "The reviewed state changed. Your input is retained. Reload and review the current state before trying again.";
  } else if (status === 410 || code?.endsWith("_expired")) {
    message = "This retained preview or checkpoint expired. Your input is retained; create a new preview before continuing.";
  } else if (status === 403 || status === 401) {
    message = "Your current access does not allow this action. No replacement or release was confirmed.";
  }
  return <Alert severity="error">{message}</Alert>;
}
AuthoringError.propTypes = { error: PropTypes.object };

export function useExpired(expiresAt) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!expiresAt) return undefined;
    const timer = setTimeout(() => tick((n) => n + 1), Math.min(Math.max(Date.parse(expiresAt) - Date.now(), 0) + 1, 2147483647));
    return () => clearTimeout(timer);
  }, [expiresAt]);
  return !expiresAt || !Number.isFinite(Date.parse(expiresAt)) || Date.parse(expiresAt) <= Date.now();
}

export function FileChanges({ files, beforeTitle = "Before", afterTitle = "After" }) {
  if (!Array.isArray(files)) return <Alert severity="info">The complete file comparison is unavailable. Load it before acknowledging a replacement or publication.</Alert>;
  const ordered = [...files].filter((file) => file.status !== "unchanged").sort((a, b) => {
    const root = (path) => ["AGENTS.md", "SKILL.md"].includes(path) ? 0 : 1;
    return root(a.path) - root(b.path) || a.path.localeCompare(b.path);
  });
  return <Stack spacing={1.5}>
    {!ordered.length && <Typography variant="s3" color="text.secondary">No file changes.</Typography>}
    {ordered.map((file) => <Box key={file.path} sx={{ minWidth: 0 }}>
      <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>{file.path} · {file.status}</Typography>
      <TextDiff fromText={file.fromText || ""} toText={file.toText || ""} fromTitle={beforeTitle} toTitle={afterTitle} />
    </Box>)}
  </Stack>;
}
FileChanges.propTypes = { files: PropTypes.array, beforeTitle: PropTypes.string, afterTitle: PropTypes.string };

function HeadReview({ scopeKey, session, onPreview, onAcknowledge, onAcknowledged }) {
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const operation = useAuthoringOperation(scopeKey);
  const review = preview?.review;
  const current = review && Array.isArray(preview.originChanges) && Array.isArray(preview.headChanges) && !session.dirty && !session.pendingMutations && !session.mutationError && session.revision === review.draftRevision;
  const prepare = async () => {
    setConfirmed(false);
    setPreview(null);
    const result = await operation.run(async () => {
      const saved = await session.flush();
      if (!saved) throw new Error("Save blocked");
      return onPreview(saved);
    }, { action: "preview-head", editSequence: session.editSequence }, { retainUnknown: false });
    if (result) setPreview(result);
  };
  const acknowledge = async () => {
    if (!current || !confirmed) return;
    const result = await operation.run(async (input, options) => {
      const saved = await session.mutateDraft((draft) => {
        if (draft.revision !== review.draftRevision) throw Object.assign(new Error("Changed draft"), { code: "draft_conflict" });
        return onAcknowledge(input, options);
      }, { operationKey: options.idempotencyKey });
      if (!saved) throw new Error("Acknowledgment blocked");
      return saved;
    }, { revision: review.draftRevision, expectedDigest: review.candidateDigest, expectedHeadVersion: review.expectedHeadVersion, reviewFingerprint: review.reviewFingerprint });
    if (result) { setPreview(null); setConfirmed(false); onAcknowledged?.(result); }
  };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Review publication head</Typography>
    <Typography variant="s1">Content origin {session.baseVersion == null ? "is unpublished" : `is v${session.baseVersion}`}. Reviewing a newer head never copies its files into this draft.</Typography>
    <AuthoringError error={operation.error} />
    {session.mutationError && operation.error && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const result = await operation.retry(); if (result) { setPreview(null); setConfirmed(false); onAcknowledged?.(result); } }}>Retry head acknowledgment</Button>}
    <Button variant="outlined" onClick={prepare} disabled={!onPreview || operation.pending || Boolean(session.mutationError)}>Review against current latest</Button>
    {review && <>
      <Alert severity="warning">Publishing this {review.baseVersion == null ? "new" : `v${review.baseVersion}-derived`} candidate will make a new latest version after {review.expectedHeadVersion == null ? "the unpublished head" : `v${review.expectedHeadVersion}`}. Production does not move.</Alert>
      <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>Candidate {review.candidateDigest} · draft r{review.draftRevision}</Typography>
      <FileChanges files={preview.originChanges} beforeTitle={`Origin v${review.baseVersion}`} afterTitle="Candidate" />
      <FileChanges files={preview.headChanges} beforeTitle={`Latest v${review.expectedHeadVersion}`} afterTitle="Candidate" />
      {!current && <Alert severity="info">{session.mutationError ? "Recover the original acknowledgment with Retry head acknowledgment before reviewing or publishing." : "The draft changed. Review the head again before acknowledging it."}</Alert>}
      <FormControlLabel control={<Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={!current || operation.pending} />} label="I reviewed both comparisons and the new-latest consequence." />
      <Button variant="contained" disabled={!current || !confirmed || !onAcknowledge || operation.pending} onClick={acknowledge}>Acknowledge reviewed head</Button>
    </>}
  </Stack>;
}
HeadReview.propTypes = { scopeKey: PropTypes.string.isRequired, session: PropTypes.object.isRequired, onPreview: PropTypes.func, onAcknowledge: PropTypes.func, onAcknowledged: PropTypes.func };
export default function HotfixHeadReview(props) { return <HeadReview key={props.scopeKey} {...props} />; }
HotfixHeadReview.propTypes = HeadReview.propTypes;
