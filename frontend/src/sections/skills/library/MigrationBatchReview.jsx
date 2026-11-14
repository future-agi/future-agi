import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import { Button, Checkbox, FormControl, FormLabel, FormControlLabel, MenuItem, TextField, Radio, RadioGroup, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from "@mui/material";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { isDefiniteRejection } from "src/api/skills/operation-errors";
import { stackedTableSx } from "../components/responsiveTable";
import StatusBadge from "src/components/status-badge";
import EmptyState from "src/components/empty-state";
import IdentityChip from "../components/IdentityChip";
import TechnicalDetails from "../components/TechnicalDetails";
import ChecksList from "../components/ChecksList";
import { FileChanges } from "../editor/HotfixHeadReview";
import { classifyMigrationItem, importBlockerSummary } from "./migration-batch";
import MigrationContextAssembly from "./MigrationContextAssembly";

// Intent: one reviewed decision over every retained item, with each exception
// still decided explicitly. Nothing here moves production or merges content.
// Hierarchy: complete item table (identity, destination, decision) → explicit
// exception acknowledgments → one rights confirmation → one apply action.
// Palette/depth/surfaces: flat table on the page, warning only for exceptions.
// Typography/spacing: m3/s1/s3, shared row heights, 12px groups.

const rowKey = (plan, item) => `${plan.id}/${item.key}`;
const errorCode = (error) => error?.response?.data?.code || error?.code;
const outcomeText = (outcome) => {
  if (!outcome) return "Not started";
  if (outcome.error) return outcome.error;
  if (outcome.warnings && !outcome.done) return "Imported as a draft; its publication warnings need your acknowledgment";
  if (outcome.kept) return "Kept Hub content";
  const replay = outcome.replayed ? " · lost response settled by exact replay" : "";
  return outcome.version ? `Published v${outcome.version}${replay}` : `Draft imported${replay}`;
};

export default function MigrationBatchReview({ plans, onAccept, onKeep, onPublish, onPreserveDraft, onBusyChange, onCreateContext, contextProposal, footerTarget, onConsequenceChange, onClose }) {
  const formId = useId();
  const reviewKey = plans.map((plan) => `${plan.id}:${plan.fingerprint}`).join("|");
  const [choices, setChoices] = useState({});
  const [publish, setPublish] = useState(true);
  const [filter, setFilter] = useState("all");
  const [excludeAcknowledged, setExcludeAcknowledged] = useState(false);
  const [rights, setRights] = useState(false);
  const [rightsKey, setRightsKey] = useState(null);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [running, setRunning] = useState(false);
  const [outcomes, setOutcomes] = useState({});
  // One operation key per exact item decision, kept across replays and reruns.
  const keys = useRef(new Map());
  useEffect(() => { onBusyChange?.(running); }, [running, onBusyChange]);
  useEffect(() => {
    setRights(false); setExcludeAcknowledged(false); setWarningsAcknowledged(false); setChoices({});
  }, [reviewKey]);
  const rows = plans.flatMap((plan) => plan.items.map((item) => ({ plan, item, key: rowKey(plan, item), ...classifyMigrationItem(item) })));
  const excluded = rows.filter((row) => row.kind === "excluded");
  const held = rows.filter((row) => row.kind === "held");
  const choiceRows = rows.filter((row) => row.kind === "choice");
  const decisionOf = (row) => (row.kind === "default" ? "replace" : choices[row.key] || "");
  const pending = rows.filter((row) => ["default", "choice"].includes(row.kind) && !outcomes[row.key]?.done);
  const undecided = choiceRows.filter((row) => !choices[row.key] && !outcomes[row.key]?.done);
  const keeps = choiceRows.some((row) => choices[row.key] === "keep");
  // Publication warnings are exceptions found after import; each listed item is
  // published only after the owner explicitly acknowledges its warnings.
  const warningRows = pending.filter((row) => outcomes[row.key]?.warnings);
  const ready = pending.length > 0 && !undecided.length && rights && rightsKey === reviewKey && (!excluded.length || excludeAcknowledged) && Boolean(onAccept) && (!keeps || Boolean(onKeep)) && (!publish || Boolean(onPublish)) && (!warningRows.length || warningsAcknowledged);
  const importCount = pending.filter((row) => decisionOf(row) !== "keep").length;
  const needsDecision = held.length + undecided.length;
  const started = running || Object.keys(outcomes).length > 0;
  const completed = rows.filter((row) => outcomes[row.key]?.done && !outcomes[row.key]?.kept);
  const done = started && !running && !pending.length && !held.length && completed.length > 0;
  const consequence = done ? `Imported ${completed.length} ${completed.length === 1 ? "skill" : "skills"}. Production labels did not change.` : !importCount && needsDecision ? `${needsDecision} ${needsDecision === 1 ? 'item needs' : 'items need'} your decision before anything can be imported.` : publish
    ? `Imports ${importCount} items as new published versions. Production labels don't change.`
    : `Imports ${importCount} items as unpublished drafts.`;
  useEffect(() => { onConsequenceChange?.(consequence); }, [consequence, onConsequenceChange]);
  const visibleRows = rows.filter((row) => filter === 'all' || row.kind === filter);
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
            let version;
            try {
              version = await onPublish({ resourceId: receipt.resourceId, revision: receipt.draftRevision, digest: receipt.digest, acknowledgeWarnings: Boolean(previous.warnings) && warningsAcknowledged });
            } catch (error) {
              if (errorCode(error) !== "warnings_unacknowledged") throw error;
              const warnings = error?.response?.data?.detail ?? error?.detail ?? null;
              setOutcomes((current) => ({ ...current, [row.key]: { done: false, receipt, replayed, warnings: warnings || { summary: { warnings: 1, errors: 0 }, items: [] } } }));
              continue;
            }
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
  const warningsOnly = warningRows.length > 0 && warningRows.length === pending.length;
  const label = warningsOnly ? `Publish ${pending.length} ${pending.length === 1 ? "item" : "items"} with reviewed warnings`
    : failed ? `Retry ${pending.length} remaining with the same decisions` : `${verb} ${pending.length} ${pending.length === 1 ? "item" : "items"}`;
  const disabledReason = !pending.length ? held.length ? 'Resolve the held items, then preview again' : 'No items ready to import' : undecided.length ? 'Choose keep or replace to continue' : excluded.length && !excludeAcknowledged ? 'Confirm the excluded items to continue' : !rights || rightsKey !== reviewKey ? 'Confirm your rights to continue' : warningRows.length && !warningsAcknowledged ? 'Review the publication warnings to continue' : !ready ? 'Import is unavailable for this selection' : '';
  const footer = <Stack spacing={0.5} sx={{ borderTop: 1, borderColor: "divider", pt: 1.5, bgcolor: "background.paper" }}>
    {pending.length > 0 && <FormControl disabled={running}>
      <FormLabel id={`${formId}-import-as`}>Import as</FormLabel>
      <RadioGroup aria-labelledby={`${formId}-import-as`} value={publish ? "published" : "drafts"} onChange={(event) => setPublish(event.target.value === "published")}>
        <FormControlLabel value="published" control={<Radio />} label="Published v1, no labels" />
        <FormControlLabel value="drafts" control={<Radio />} label="Drafts to review first" />
      </RadioGroup>
    </FormControl>}
    {excluded.map((row) => <Typography key={row.key} variant="s3" sx={{ overflowWrap: 'anywhere' }}>Left out: {importBlockerSummary(row.item)}.</Typography>)}
    {excluded.length > 0 && <FormControlLabel control={<Checkbox checked={excludeAcknowledged} disabled={running} onChange={(event) => setExcludeAcknowledged(event.target.checked)} />} label={`Leave out ${excluded.length} ${excluded.length === 1 ? "skill" : "skills"} that can't be imported. The others import without ${excluded.length === 1 ? "it" : "them"}.`} />}
    {decided.length > 0 && <FormControlLabel control={<Checkbox checked={rights} disabled={running} onChange={(event) => { setRights(event.target.checked); setRightsKey(reviewKey); }} />} label={`I confirm my rights to copy all ${decided.length} items, including their licenses and retained notices.`} />}
    {warningRows.length > 0 && <FormControlLabel control={<Checkbox checked={warningsAcknowledged} disabled={running} onChange={(event) => setWarningsAcknowledged(event.target.checked)} />} label={`I reviewed the publication warnings for ${warningRows.length} ${warningRows.length === 1 ? "item" : "items"} and want to publish ${warningRows.length === 1 ? "it" : "them"}.`} />}
    {!onConsequenceChange && <Typography variant="s1" role="status">{consequence}</Typography>}
    <Stack direction="row" justifyContent="flex-end" useFlexGap flexWrap="wrap" spacing={1} alignItems="center">{!ready && <Typography variant="s3" color="text.secondary" role="status">{disabledReason}</Typography>}{onClose && <Button variant="outlined" disabled={running} onClick={onClose}>Cancel</Button>}{pending.length > 0 && <Button variant="contained" color="primary" disabled={!ready || running} onClick={apply}>{label}</Button>}</Stack>
  </Stack>;
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" spacing={1}>
      <Stack spacing={0.5}><Typography variant="m3" component="h2">{done ? `Imported ${completed.length} ${completed.length === 1 ? "skill" : "skills"}` : "Review all items"}</Typography><Typography variant="s3" color="text.secondary">{done ? published.length ? `${published.length} ${published.length === 1 ? "version" : "versions"} published without labels. Production was not changed.` : "Your skills are saved as drafts. Production labels did not change." : [`${pending.length - undecided.length} ready to import`, excluded.length > 0 && `${excluded.length} can't be imported`, undecided.length > 0 && `${undecided.length} need a decision`, held.length > 0 && `${held.length} held`].filter(Boolean).join(" · ")}</Typography></Stack>
      <TextField id={`${formId}-status`} SelectProps={{ SelectDisplayProps: { "aria-labelledby": `${formId}-status-label` } }} select size="small" label="Item status" value={filter} onChange={(event) => setFilter(event.target.value)} sx={{ minWidth: 180 }}>
        <MenuItem value="all">All items ({rows.length})</MenuItem><MenuItem value="choice">Needs a decision ({choiceRows.length})</MenuItem><MenuItem value="held">Held ({held.length})</MenuItem><MenuItem value="excluded">Excluded ({excluded.length})</MenuItem>
      </TextField>
    </Stack>
    <TableContainer sx={{ maxWidth: "100%" }}>
      <Table size="small" aria-label="Retained migration items" sx={[stackedTableSx, { "& td": { py: { sm: 0.5 } } }]}>
        <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
          <TableCell>Item</TableCell><TableCell>Destination and decision</TableCell><TableCell>Details</TableCell>{started && <TableCell>Result</TableCell>}
        </TableRow></TableHead>
        <TableBody>{visibleRows.map((row) => <TableRow hover key={row.key} sx={{ height: ROW_HEIGHT.default, verticalAlign: "top" }}>
          <TableCell sx={{ overflowWrap: "anywhere" }}>
            <Typography variant="s1" display="block">{row.item.name || row.item.root}</Typography>
            {row.item.root && row.item.root !== row.item.name && <Typography variant="s3" display="block" color="text.secondary">{row.item.root}</Typography>}
          </TableCell>
          <TableCell data-cell="actions" sx={{ minWidth: { sm: 200 } }}>
            {row.item.destinationId && <Typography variant="s1" display="block">{`${row.item.destinationName || row.item.name || row.item.root} v${row.item.expectedHeadVersion ?? "—"}`}</Typography>}
            {/* One click per exception; nothing is preselected for a diverged item. */}
            {row.kind === "choice" ? <RadioGroup id={`${formId}-${row.key}`} aria-label={`Decision for ${row.item.name || row.item.root}`} value={choices[row.key] || ""} onChange={(event) => setChoices((current) => ({ ...current, [row.key]: event.target.value }))}>
              {[["keep", "Keep Hub content"], ["replace", "Replace with reviewed source"]].map(([value, label]) => <FormControlLabel key={value} value={value} label={label} disabled={running || Boolean(outcomes[row.key]?.receipt)} control={<Radio size="small" />} />)}
            </RadioGroup> : <Typography variant="s1">{row.kind === "default" ? row.note : row.kind === "excluded" ? "Left out" : "Held"}</Typography>}
            {row.kind !== "default" && <Typography variant="s3" display="block" color="text.secondary">{row.note}</Typography>}
            {row.kind === "held" && row.item.hasDraft && onPreserveDraft && <Button size="small" disabled={running} onClick={() => onPreserveDraft(row.item)}>{`Preserve draft for ${row.item.name || row.item.root}`}</Button>}
            {row.kind === "choice" && <TechnicalDetails title="Review file differences" defaultOpen>
              {Array.isArray(row.item.sourceHubChanges) && <FileChanges files={row.item.sourceHubChanges} beforeTitle="New source" afterTitle="Current Hub" />}
              {row.item.basisPayloadAvailable === false ? <Typography variant="s3">No historical source text is available; only Source and Hub are compared.</Typography> : <>
                <FileChanges files={row.item.sourceChanges} beforeTitle="Accepted source basis" afterTitle="New source" />
                <FileChanges files={row.item.hubChanges} beforeTitle="Accepted source basis" afterTitle="Current Hub" />
              </>}
            </TechnicalDetails>}
          </TableCell>
          <TableCell data-cell="actions"><TechnicalDetails><Stack spacing={1}>
            <Typography variant="s3">{row.item.fileCount ?? "—"} files · {row.item.totalBytes ?? "—"} bytes</Typography>
            <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{[row.item.sourceOrigin, row.item.sourceProject, row.item.sourceResource, row.item.sourceRevision].filter(Boolean).join(' / ')}</Typography>
            {row.kind === 'excluded' && <><StatusBadge status="blocked" label="Left out" /><Typography variant="s3">{row.note}</Typography></>}
            {row.item.destinationId && <IdentityChip value={row.item.destinationId} kind="id" label="Destination" />}
            <IdentityChip value={row.plan.fingerprint} label="Fingerprint" />
            {row.item.expectedHeadDigest && <IdentityChip value={row.item.expectedHeadDigest} label="Digest" />}
            {row.kind === "default" && Array.isArray(row.item.sourceHubChanges) && (row.item.destinationId ? <FileChanges files={row.item.sourceHubChanges} beforeTitle="Reviewed source" afterTitle="Current Hub" /> : <FileChanges files={row.item.sourceHubChanges.map((file) => ({ path: file.path, status: "added", fromText: "", toText: file.fromText }))} beforeTitle="Not in Hub yet" afterTitle="Reviewed source" />)}
          </Stack></TechnicalDetails></TableCell>
          {started && <TableCell data-cell="actions"><Typography variant="s3">{row.kind === "excluded" ? "Left out" : outcomeText(outcomes[row.key])}</Typography>
            {outcomes[row.key]?.warnings && !outcomes[row.key]?.done && <TechnicalDetails title="Inspect warnings"><ChecksList checks={outcomes[row.key].warnings} dense /></TechnicalDetails>}
          </TableCell>}
        </TableRow>)}
          {!visibleRows.length && <TableRow><TableCell colSpan={started ? 4 : 3}><EmptyState variant="no-results" title="No items match this status" onClearFilters={() => setFilter('all')} /></TableCell></TableRow>}
        </TableBody>
      </Table>
    </TableContainer>
    {!done && (footerTarget ? createPortal(footer, footerTarget) : footer)}
    {done && <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
      {published.length > 0 && onCreateContext && !contextProposal && <Button variant="contained" color="primary" onClick={() => onCreateContext(published.map((outcome) => ({ skillId: outcome.resourceId, version: outcome.version })))}>Create agent context from these skills</Button>}
      {onClose && <Button variant="outlined" onClick={onClose}>Done</Button>}
    </Stack>}
    {/* A packet from the local inventory carries the proposed context, so the
        published set is assembled here instead of on another screen. */}
    {contextProposal && published.length > 0 && !pending.length && <MigrationContextAssembly proposal={contextProposal} onBusyChange={onBusyChange}
      published={published.map((outcome) => ({ skillId: outcome.resourceId, version: outcome.version }))} />}
  </Stack>;
}
MigrationBatchReview.propTypes = { onClose: PropTypes.func, footerTarget: PropTypes.any, onConsequenceChange: PropTypes.func, contextProposal: PropTypes.shape({ name: PropTypes.string, description: PropTypes.string, agents: PropTypes.string }), plans: PropTypes.array.isRequired, onAccept: PropTypes.func, onKeep: PropTypes.func, onPublish: PropTypes.func, onPreserveDraft: PropTypes.func, onBusyChange: PropTypes.func, onCreateContext: PropTypes.func };
