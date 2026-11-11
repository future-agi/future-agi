import { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { isDefiniteRejection } from "src/api/skills/operation-errors";
import { FileChanges } from "../editor/HotfixHeadReview";
import { classifyMigrationItem } from "./migration-batch";

// Intent: one reviewed decision over every retained item, with each exception
// still decided explicitly. Nothing here moves production or merges content.
// Hierarchy: complete item table (identity, destination, decision) → explicit
// exception acknowledgments → one rights confirmation → one apply action.
// Palette/depth/surfaces: flat table on the page, warning only for exceptions.
// Typography/spacing: m3/s1/s3, shared row heights, 12px groups.

const rowKey = (plan, item) => `${plan.id}/${item.key}`;
const outcomeText = (outcome) => {
  if (!outcome) return "Not started";
  if (outcome.error) return outcome.error;
  if (outcome.kept) return "Kept Hub content";
  const replay = outcome.replayed ? " · lost response settled by exact replay" : "";
  return outcome.version ? `Published v${outcome.version}${replay}` : `Draft imported${replay}`;
};

export default function MigrationBatchReview({ plans, onAccept, onKeep, onPublish, onPreserveDraft, onBusyChange, onCreateContext }) {
  const formId = useId();
  const [choices, setChoices] = useState({});
  const [publish, setPublish] = useState(true);
  const [excludeAcknowledged, setExcludeAcknowledged] = useState(false);
  const [rights, setRights] = useState(false);
  const [running, setRunning] = useState(false);
  const [outcomes, setOutcomes] = useState({});
  // One operation key per exact item decision, kept across replays and reruns.
  const keys = useRef(new Map());
  useEffect(() => { onBusyChange?.(running); }, [running, onBusyChange]);
  const rows = plans.flatMap((plan) => plan.items.map((item) => ({ plan, item, key: rowKey(plan, item), ...classifyMigrationItem(item) })));
  const excluded = rows.filter((row) => row.kind === "excluded");
  const held = rows.filter((row) => row.kind === "held");
  const choiceRows = rows.filter((row) => row.kind === "choice");
  const decisionOf = (row) => (row.kind === "default" ? "replace" : choices[row.key] || "");
  const pending = rows.filter((row) => ["default", "choice"].includes(row.kind) && !outcomes[row.key]?.done);
  const undecided = choiceRows.filter((row) => !choices[row.key] && !outcomes[row.key]?.done);
  const keeps = choiceRows.some((row) => choices[row.key] === "keep");
  const ready = pending.length > 0 && !undecided.length && rights && (!excluded.length || excludeAcknowledged) && Boolean(onAccept) && (!keeps || Boolean(onKeep)) && (!publish || Boolean(onPublish));
  const operationKey = (row, decision) => {
    const id = `${row.plan.fingerprint}:${row.key}:${decision}`;
    if (!keys.current.has(id)) keys.current.set(id, crypto.randomUUID());
    return keys.current.get(id);
  };
  // An unknown outcome is replayed once with the original body and key. The
  // server returns the recorded receipt; a definite rejection is never replayed.
  const exact = async (send, input, idempotencyKey) => {
    try { return { value: await send(input, { idempotencyKey }), replayed: false }; } catch (error) {
      if (isDefiniteRejection(error)) throw error;
      return { value: await send(input, { idempotencyKey }), replayed: true };
    }
  };
  const apply = async () => {
    if (!ready || running) return;
    setRunning(true);
    try {
      for (const row of pending) {
        const decision = decisionOf(row);
        const previous = outcomes[row.key] || {};
        const input = { planId: row.plan.id, expectedFingerprint: row.plan.fingerprint, itemKey: row.item.key,
          expectedHeadVersion: row.item.expectedHeadVersion, expectedHeadDigest: row.item.expectedHeadDigest, rightsConfirmed: true };
        try {
          let receipt = previous.receipt;
          let replayed = previous.replayed || false;
          if (!receipt) {
            const sent = decision === "keep" ? await exact(onKeep, input, operationKey(row, decision)) : await exact(onAccept, { ...input, mode: "snapshot" }, operationKey(row, decision));
            receipt = sent.value;
            replayed = sent.replayed;
          }
          if (decision === "keep") { setOutcomes((current) => ({ ...current, [row.key]: { done: true, kept: true, receipt } })); continue; }
          setOutcomes((current) => ({ ...current, [row.key]: { done: !publish, receipt, replayed } }));
          if (publish) {
            const version = await onPublish({ resourceId: receipt.resourceId, revision: receipt.draftRevision, digest: receipt.digest });
            setOutcomes((current) => ({ ...current, [row.key]: { done: true, receipt, replayed, version: version.version, resourceId: receipt.resourceId } }));
          }
        } catch (error) {
          const message = error?.message || "This item could not be confirmed. Its decision is retained for an exact retry.";
          setOutcomes((current) => ({ ...current, [row.key]: { ...(current[row.key] || {}), done: false, error: message } }));
          break;
        }
      }
    } finally { setRunning(false); }
  };
  const published = rows.map((row) => outcomes[row.key]).filter((outcome) => outcome?.version);
  const failed = rows.some((row) => outcomes[row.key]?.error);
  const decided = rows.filter((row) => ["default", "choice"].includes(row.kind) && (row.kind === "default" || choices[row.key]));
  const verb = publish ? "Import and publish" : "Import";
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Review all items</Typography>
    <Typography variant="s1">One decision applies to every item listed with a decision. Exceptions stay visible and need your explicit choice. Source applications and production labels are never changed.</Typography>
    <TableContainer sx={{ maxWidth: "100%" }}>
      <Table size="small" aria-label="Retained migration items">
        <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
          <TableCell>Item and source identity</TableCell><TableCell>Destination</TableCell><TableCell>Files</TableCell><TableCell>Decision</TableCell><TableCell>Result</TableCell>
        </TableRow></TableHead>
        <TableBody>{rows.map((row) => <TableRow hover key={row.key} sx={{ height: ROW_HEIGHT.default, verticalAlign: "top" }}>
          <TableCell sx={{ overflowWrap: "anywhere" }}>
            <Typography variant="s1" display="block">{row.item.name || row.item.root}</Typography>
            <Typography variant="s3" display="block" color="text.secondary">{[row.item.sourceOrigin, row.item.sourceProject, row.item.sourceResource, row.item.sourceRevision].filter(Boolean).join(" / ") || row.item.root}</Typography>
          </TableCell>
          <TableCell sx={{ overflowWrap: "anywhere" }}><Typography variant="s3">{row.item.destinationId ? `${row.item.destinationId} · v${row.item.expectedHeadVersion ?? "—"}` : "New identity"}</Typography></TableCell>
          <TableCell><Typography variant="s1" display="block">{row.item.fileCount ?? "—"}</Typography><Typography variant="s3" display="block">{row.item.totalBytes ?? "—"} bytes</Typography></TableCell>
          <TableCell sx={{ minWidth: 200 }}>
            {row.kind === "choice" ? <TextField id={`${formId}-${row.key}`} SelectProps={{ SelectDisplayProps: { "aria-labelledby": `${formId}-${row.key}-label` } }} select size="small" fullWidth label={`Decision for ${row.item.name || row.item.root}`} value={choices[row.key] || ""} disabled={running || Boolean(outcomes[row.key]?.receipt)} onChange={(event) => setChoices((current) => ({ ...current, [row.key]: event.target.value }))}>
              <MenuItem value="">Choose</MenuItem><MenuItem value="keep">Keep Hub content</MenuItem><MenuItem value="replace">Replace with reviewed source</MenuItem>
            </TextField> : <Typography variant="s1">{row.kind === "default" ? row.note : row.kind === "excluded" ? "Left out" : "Held"}</Typography>}
            {row.kind !== "default" && <Typography variant="s3" display="block" color="text.secondary">{row.note}</Typography>}
            {row.kind === "held" && row.item.hasDraft && onPreserveDraft && <Button size="small" disabled={running} onClick={() => onPreserveDraft(row.item)}>{`Preserve draft for ${row.item.name || row.item.root}`}</Button>}
            {row.kind === "choice" && <Box component="details"><Typography component="summary" variant="s3" sx={{ cursor: "pointer" }}>Inspect Base, Source and Hub differences</Typography>
              {Array.isArray(row.item.sourceHubChanges) && <FileChanges files={row.item.sourceHubChanges} beforeTitle="New source" afterTitle="Current Hub" />}
              {row.item.basisPayloadAvailable === false ? <Typography variant="s3">No historical source text is available; only Source and Hub are compared.</Typography> : <>
                <FileChanges files={row.item.sourceChanges} beforeTitle="Accepted source basis" afterTitle="New source" />
                <FileChanges files={row.item.hubChanges} beforeTitle="Accepted source basis" afterTitle="Current Hub" />
              </>}
            </Box>}
          </TableCell>
          <TableCell><Typography variant="s3">{outcomeText(outcomes[row.key])}</Typography></TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </TableContainer>
    {held.length > 0 && <Alert severity="warning">{held.length} held {held.length === 1 ? "item is" : "items are"} not part of this decision. Resolve {held.length === 1 ? "it" : "them"}, then preview again.</Alert>}
    {excluded.length > 0 && <FormControlLabel control={<Checkbox checked={excludeAcknowledged} disabled={running} onChange={(event) => setExcludeAcknowledged(event.target.checked)} />} label={`Leave out ${excluded.length} blocked ${excluded.length === 1 ? "member" : "members"}. This is a supported-subset trial, not a complete migration.`} />}
    {undecided.length > 0 && <Alert severity="info">Choose keep or replace for {undecided.length} diverged {undecided.length === 1 ? "item" : "items"} to continue.</Alert>}
    <FormControlLabel control={<Checkbox checked={publish} disabled={running} onChange={(event) => setPublish(event.target.checked)} />} label="Publish each imported draft as a new version without labels" />
    <FormControlLabel control={<Checkbox checked={rights} disabled={running} onChange={(event) => setRights(event.target.checked)} />} label={`I confirm my rights to copy all ${decided.length} items, including their licenses and retained notices.`} />
    <Box><Button variant="contained" disabled={!ready || running} onClick={apply}>{failed ? `Retry ${pending.length} remaining with the same decisions` : `${verb} ${pending.length} ${pending.length === 1 ? "item" : "items"}`}</Button></Box>
    {published.length > 0 && !pending.length && <Alert severity="success" action={onCreateContext && <Button disabled={running} onClick={() => onCreateContext(published.map((outcome) => ({ skillId: outcome.resourceId, version: outcome.version })))}>Create agent context from these skills</Button>}>
      {published.length} {published.length === 1 ? "version" : "versions"} published without labels. Production was not changed.
    </Alert>}
  </Stack>;
}
MigrationBatchReview.propTypes = { plans: PropTypes.array.isRequired, onAccept: PropTypes.func, onKeep: PropTypes.func, onPublish: PropTypes.func, onPreserveDraft: PropTypes.func, onBusyChange: PropTypes.func, onCreateContext: PropTypes.func };
