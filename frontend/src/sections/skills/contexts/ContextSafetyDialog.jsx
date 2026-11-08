import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Checkbox, DialogActions, DialogContent, DialogTitle, FormControlLabel, Stack, TextField, Typography } from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import { AuthoringError, useAuthoringOperation } from "../editor/HotfixHeadReview";

// Intent: an administrator reviews original identity and retained consequences.
// Hierarchy: exact action/version, impact, current authority, explicit confirmation.
// Palette/depth/surfaces: shared dialog with warning/error roles only for consequences.
// Typography/spacing: m3/s1/s3, 12px groups and shared dialog actions.
const labels = { archive: "Archive context", unarchive: "Recover original identity", quarantine: "Quarantine version", clear: "Clear quarantine" };
function Safety({ scopeKey, open, onClose, action, resource, review, canAct = false, onApply, onApplied }) {
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const operation = useAuthoringOperation(scopeKey);
  const quarantine = action === "quarantine" || action === "clear";
  const ready = canAct && review && review.subjectId === resource.id && (quarantine ? Number.isInteger(review.securityRevision) && Boolean(review.versionId) : Number.isInteger(review.releaseRevision)) && (action !== "archive" || Boolean(review.impactFingerprint)) && !review.blocked;
  const apply = async () => {
    if (!ready || !confirmed || !reason.trim()) return;
    const input = quarantine
      ? { revision: review.securityRevision, quarantined: action === "quarantine", reason: reason.trim(), replacementVersionId: review.replacementVersionId || null }
      : { expectedReleaseRevision: review.releaseRevision, note: reason.trim(), ...(action === "archive" ? { expectedImpactFingerprint: review.impactFingerprint } : {}) };
    const result = await operation.run(onApply, input);
    if (result) onApplied?.(result);
  };
  return <ClosableDialog open={open} onClose={(...args) => { if (!operation.pending) onClose(...args); }} fullWidth maxWidth="sm" aria-labelledby="context-safety-title">
    <DialogTitle id="context-safety-title">{labels[action]}</DialogTitle>
    <DialogContent><Stack spacing={1.5} sx={{ pt: 1 }}>
      <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>{resource.name} · {resource.id}</Typography>
      <Alert severity="warning">{action === "archive" ? "Existing parent releases retain this content. New direct resolution and selection stop; offline copies are not deleted." : action === "unarchive" ? "Recover the original UUID, history and retained labels. A new resource with the same name is never substituted. Protected-label authority is checked again." : action === "quarantine" ? "Future managed loads will be blocked for this version and affected parents. Offline copies cannot be erased; requests already sent cannot be retracted." : "Clearing quarantine permits future managed loads again. It does not change immutable content or certify its behavior."}</Alert>
      {review && <>
        {review.versionId && <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>Version {review.versionId}</Typography>}
        <Typography variant="s1">Affected retained parents: {review.affectedContextCount ?? "Not available"}</Typography>
        {(review.labels || []).map((label) => <Typography key={label.name} variant="s1">Retained {label.name} → v{label.version}{label.isProtected ? " · protected" : ""}</Typography>)}
        {(review.blockers || []).map((blocker) => <Alert key={blocker} severity="error">{blocker}</Alert>)}
      </>}
      {!canAct && <Alert severity="info">Current administrator authority is required. No action is available.</Alert>}
      {!review && <Alert severity="info">Load a current consequence review before acting.</Alert>}
      <AuthoringError error={operation.error} />
      <TextField label="Reason" size="small" fullWidth multiline minRows={2} value={reason} onChange={(event) => setReason(event.target.value)} inputProps={{ maxLength: 500 }} disabled={operation.pending} />
      <FormControlLabel control={<Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={!ready || operation.pending} />} label="I reviewed this exact identity and its retained-label and load consequences." />
    </Stack></DialogContent>
    <DialogActions><Button disabled={operation.pending} onClick={onClose}>Cancel</Button><Button variant="contained" color={action === "archive" || action === "quarantine" ? "error" : "primary"} disabled={!ready || !confirmed || !reason.trim() || !onApply || operation.pending || Boolean(operation.result)} onClick={apply}>{labels[action]}</Button></DialogActions>
  </ClosableDialog>;
}
Safety.propTypes = { scopeKey: PropTypes.string.isRequired, open: PropTypes.bool.isRequired, onClose: PropTypes.func.isRequired, action: PropTypes.oneOf(Object.keys(labels)).isRequired, resource: PropTypes.object.isRequired, review: PropTypes.object, canAct: PropTypes.bool, onApply: PropTypes.func, onApplied: PropTypes.func };
export default function ContextSafetyDialog(props) { return <Safety key={`${props.scopeKey}:${props.open}:${props.action}:${JSON.stringify(props.review)}`} {...props} />; }
ContextSafetyDialog.propTypes = Safety.propTypes;
