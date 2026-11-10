import { useEffect, useId, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from "@mui/material";
import CompatibilityInventory from "./CompatibilityInventory";
import { AuthoringError, FileChanges, useAuthoringOperation, useExpired } from "../editor/HotfixHeadReview";

// Intent: reconcile retained source/Hub versions without a destructive second import.
// Hierarchy: immutable mapping and three-way differences before per-item consent.
// Palette/depth/surfaces: neutral flat review, warning for replacement, no success inference.
// Typography/spacing: m3/s1/s3, 4px base with 12px groups.
function Plan({ scopeKey, plan, onAccept, onKeep, onPreserveDraft, onAccepted, onBusyChange }) {
  const formId = useId();
  // Name selects by their label alone, not their current option. Chromium's
  // accessible-name computation includes MUI's self-reference otherwise.
  const selectProps = (name) => ({ id: `${formId}-${name}`, SelectProps: { SelectDisplayProps: { "aria-labelledby": `${formId}-${name}-label` } } });
  const [selected, setSelected] = useState("");
  const [decision, setDecision] = useState("keep");
  const [mode, setMode] = useState("snapshot");
  const [rights, setRights] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [tagsMode, setTagsMode] = useState("preserve");
  const [tags, setTags] = useState("");
  const [receipts, setReceipts] = useState({});
  const operation = useAuthoringOperation(scopeKey);
  const locked = operation.pending || operation.uncertain;
  useEffect(() => { onBusyChange?.(locked); }, [locked, onBusyChange]);
  const [subsetAcknowledged, setSubsetAcknowledged] = useState(false);
  const hasExclusions = Boolean(plan.rejected?.length || plan.items.some((entry) => entry.blocked));
  const expired = useExpired(plan.expiresAt);
  const item = plan.items.find((entry) => entry.key === selected);
  const tagValues = tags.split(",").map((tag) => tag.trim()).filter(Boolean);
  const tagsValid = tagValues.length <= 20 && new Set(tagValues).size === tagValues.length && tagValues.every((tag) => /^[a-z0-9][a-z0-9._-]{0,39}$/.test(tag));
  const input = { planId: plan.id, expectedFingerprint: plan.fingerprint, itemKey: item?.key, expectedHeadVersion: item?.expectedHeadVersion, expectedHeadDigest: item?.expectedHeadDigest };
  const needsRights = decision !== "keep" || plan.requiresKeepRights;
  if (needsRights) input.rightsConfirmed = rights;
  if (decision === "replace") {
    input.mode = mode;
    if (tagsMode === "replace") input.tags = tagValues;
    if (tagsMode === "clear") input.tags = [];
  }
  // Keep is a separate service operation. Never send it through the replacement
  // mapper, which intentionally has no decision field and could drop that intent.
  const commitDecision = decision === "keep" ? onKeep : onAccept;
  const blocked = !item || item.blocked || (hasExclusions && !subsetAcknowledged) || (expired && !operation.canRetry(input)) || Boolean(receipts[selected]) || (decision !== "keep" && item.hasDraft) || (decision === "replace" && !Array.isArray(item.sourceHubChanges) && (!Array.isArray(item.sourceChanges) || !Array.isArray(item.hubChanges))) || decision === "manual";
  const accept = async () => {
    if (blocked || !reviewed || (needsRights && !rights)) return;
    const result = await operation.run(commitDecision, input);
    if (result) { setReceipts((previous) => ({ ...previous, [item.key]: result })); onAccepted?.(result); }
  };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Review retained migration plan</Typography>
    <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>Plan {plan.id} · {plan.fingerprint} · expires {plan.expiresAt}</Typography>
    {expired && <Alert severity="warning">This plan expired. Preview the source again; no current bytes will be substituted.</Alert>}
    <AuthoringError error={operation.error} />
    {operation.uncertain && <Alert severity="warning">The response was lost. Keep this review open and retry the exact decision; do not start a new import.</Alert>}
    {hasExclusions && <FormControlLabel control={<Checkbox checked={subsetAcknowledged} disabled={locked} onChange={(event) => setSubsetAcknowledged(event.target.checked)} />} label="I explicitly exclude the blocked members listed below. This is a supported-subset trial, not a complete migration." />}
    <CompatibilityInventory items={plan.items} selectedKeys={selected ? [selected] : []} readOnly rejected={plan.rejected} skipped={plan.skipped} closure={plan.closure} />
    <TextField {...selectProps("item")} select size="small" label="Review item" value={selected} disabled={locked} onChange={(event) => { setSelected(event.target.value); setRights(false); setReviewed(false); setDecision("keep"); setTagsMode("preserve"); setTags(""); }}>
      <MenuItem value="">Choose an item</MenuItem>
      {plan.items.map((entry) => <MenuItem key={entry.key} value={entry.key}>{entry.name || entry.root}</MenuItem>)}
    </TextField>
    {item && <>
      <Typography variant="s1" sx={{ overflowWrap: "anywhere" }}>Source {item.sourceOrigin} / {item.sourceProject} / {item.sourceResource} / {item.sourceRevision}</Typography>
      <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>Destination {item.destinationId || "new identity"} · reviewed head {item.expectedHeadVersion == null ? "unpublished" : `v${item.expectedHeadVersion}`} · {item.expectedHeadDigest || "no digest"}</Typography>
      {Array.isArray(item.sourceHubChanges) && <FileChanges files={item.sourceHubChanges} beforeTitle="New source" afterTitle="Current Hub" />}
      {item.basisPayloadAvailable === false ? <Alert severity="info">No historical source text is available. Review exact Source/Hub differences; no historical text or semantic merge is inferred.</Alert> : <>
        <FileChanges files={item.sourceChanges} beforeTitle="Accepted source basis" afterTitle="New source" />
        <FileChanges files={item.hubChanges} beforeTitle="Accepted source basis" afterTitle="Current Hub" />
      </>}
      {item.diverged && <Alert severity="warning">Source and Hub both changed. Replacement is not a semantic merge; review both differences.</Alert>}
      {item.hasDraft && <Alert severity="warning" action={<Button onClick={() => onPreserveDraft?.(item)} disabled={!onPreserveDraft || locked}>Preserve draft</Button>}>Your existing draft blocks replacement. Preserve and explicitly suspend it, then preview again.</Alert>}
      <TextField {...selectProps("decision")} select size="small" label="Source update decision" value={decision} disabled={locked} onChange={(event) => { setDecision(event.target.value); setReviewed(false); }}>
        <MenuItem value="keep">Keep Hub content</MenuItem><MenuItem value="replace">Replace with reviewed source</MenuItem><MenuItem value="manual">Reconcile manually in the editor</MenuItem>
      </TextField>
      {decision === "manual" && <Alert severity="info">Export or preserve both alternatives and edit the destination draft. This panel will not merge or overwrite them.</Alert>}
      <TextField {...selectProps("semantics")} select size="small" label="File semantics" value={mode} disabled={locked || decision !== "replace"} onChange={(event) => { setMode(event.target.value); setReviewed(false); }}>
        <MenuItem value="snapshot">Complete snapshot — omitted files are removed</MenuItem><MenuItem value="patch" disabled={plan.snapshotOnly}>Patch — omitted files are preserved</MenuItem>
      </TextField>
      <TextField {...selectProps("tags")} select size="small" label="Destination tags" value={tagsMode} disabled={locked || decision !== "replace"} onChange={(event) => { setTagsMode(event.target.value); setReviewed(false); }}>
        <MenuItem value="preserve">Preserve existing tags</MenuItem><MenuItem value="replace">Replace tags explicitly</MenuItem><MenuItem value="clear">Clear every tag</MenuItem>
      </TextField>
      {tagsMode === "replace" && <TextField size="small" label="Tags, separated by commas" value={tags} disabled={locked} onChange={(event) => { setTags(event.target.value); setReviewed(false); }} error={!tagsValid} helperText="At most 20 unique lowercase tags; empty replaces with no tags." />}
      <FormControlLabel control={<Checkbox checked={rights} disabled={locked} onChange={(event) => setRights(event.target.checked)} />} label="I have reviewed source rights, licenses and retained notices." />
      <FormControlLabel control={<Checkbox checked={reviewed} disabled={locked} onChange={(event) => setReviewed(event.target.checked)} />} label="Apply this exact decision to the reviewed identity and head, without changing production." />
      <Button variant="contained" onClick={accept} disabled={blocked || operation.pending || !commitDecision || !reviewed || (needsRights && !rights) || !tagsValid}>{operation.uncertain ? "Retry exact decision" : decision === "keep" ? "Record keep decision" : "Import reviewed item as draft"}</Button>
      {receipts[selected] && <Alert severity="success">Decision recorded for this item. Receipt {receipts[selected].id || receipts[selected].receiptId}. Production was not changed.</Alert>}
    </>}
    <Typography variant="s3" color="text.secondary">Mappings use immutable source identity, never a matching name. Changed Hub content is derived content, not an unchanged external version.</Typography>
  </Stack>;
}
Plan.propTypes = { scopeKey: PropTypes.string.isRequired, plan: PropTypes.object.isRequired, onAccept: PropTypes.func, onKeep: PropTypes.func, onPreserveDraft: PropTypes.func, onAccepted: PropTypes.func, onBusyChange: PropTypes.func };
export default function MigrationPlanPanel(props) { return <Plan key={`${props.scopeKey}:${props.plan.id}:${props.plan.fingerprint}`} {...props} />; }
MigrationPlanPanel.propTypes = Plan.propTypes;
