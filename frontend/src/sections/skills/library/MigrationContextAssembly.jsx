import { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Checkbox, FormControlLabel, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { pinnedLinks, usePrepareMigrationContext, usePublishMigrationContext } from "src/api/skills/j1-migration-context";
import ChecksList from "../components/ChecksList";
import ContextPilotCredentials from "../contexts/ContextPilotCredentials";

// Intent: turn the set the owner just published into one agent context without
// leaving the migration review, and without weakening its publication review.
// Hierarchy: prefilled proposal (name, purpose, AGENTS.md) → exact pinned links
// and compiled candidate → one acceptance → one publish → pilot credential.
// Palette/depth/surfaces: flat table on the page; warning only for warnings.
// Typography/spacing: m3 heading, s1 values, s3 identities; 12px groups.

const nameValid = (name) => /^[a-z0-9]+(-[a-z0-9]+)*$/.test(name) && name.length <= 64;

export default function MigrationContextAssembly({ published, proposal, onBusyChange }) {
  const [name, setName] = useState(proposal.name);
  const [description, setDescription] = useState(proposal.description);
  const [agents, setAgents] = useState(proposal.agents);
  const [prepared, setPrepared] = useState(null);
  const [accepted, setAccepted] = useState(false);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  // One key per operation, reused when the owner retries an unknown outcome.
  const keys = useRef({ create: crypto.randomUUID(), apply: crypto.randomUUID(), publish: crypto.randomUUID() });
  const prepare = usePrepareMigrationContext();
  const publish = usePublishMigrationContext();
  const busy = prepare.isPending || publish.isPending;
  const links = pinnedLinks(published);
  const run = async (operation, input, onDone) => {
    setError("");
    onBusyChange?.(true);
    try { onDone(await operation.mutateAsync(input)); } catch (failure) {
      setError(failure?.message || "The context operation did not complete. Retry it with the same decision.");
    } finally { onBusyChange?.(false); }
  };
  const lock = prepared?.preview.resolvedLock.links || [];
  const checks = prepared?.preview.checks;
  const warnings = checks?.summary.warnings > 0;
  const ready = prepared && accepted && checks?.summary.errors === 0 && (!warnings || warningsAcknowledged) && !busy && !result;
  const canPrepare = !prepared && !busy && nameValid(name) && description.trim() && agents.trim() && links.length > 0 && links.length <= 20;
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Agent context from this migration</Typography>
    <Typography variant="s1">The proposal comes from your local inventory. Each skill is pinned to the exact version just published. Nothing is linked or published until you accept it below.</Typography>
    <TextField label="Immutable name" size="small" value={name} disabled={Boolean(prepared) || busy} onChange={(event) => setName(event.target.value)} inputProps={{ maxLength: 64 }} error={Boolean(name) && !nameValid(name)} helperText="Lowercase letters, digits and single hyphens." />
    <TextField label="Purpose" size="small" fullWidth multiline minRows={2} value={description} disabled={Boolean(prepared) || busy} onChange={(event) => setDescription(event.target.value)} inputProps={{ maxLength: 1024 }} />
    <TextField label="AGENTS.md" size="small" fullWidth multiline minRows={4} value={agents} disabled={Boolean(prepared) || busy} onChange={(event) => setAgents(event.target.value)} helperText="Standing instructions. Linked skills load when needed." />
    {!prepared && <Box><Button variant="contained" color="primary" disabled={!canPrepare} onClick={() => run(prepare, { name, description, agents, links, keys: keys.current }, setPrepared)}>Preview agent context</Button></Box>}
    {error && <Alert severity="error">{error}</Alert>}
    {prepared && <>
      <Typography variant="s1">{`Compiled candidate ${prepared.preview.candidateDigest} · ${lock.length} pinned ${lock.length === 1 ? 'link' : 'links'} · private draft of ${prepared.name}`}</Typography>
      <TableContainer sx={{ maxWidth: "100%" }}>
        <Table size="small" aria-label="Pinned links">
          <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Mount</TableCell><TableCell>Version</TableCell><TableCell>Identity</TableCell></TableRow></TableHead>
          <TableBody>{lock.map((link) => <TableRow hover key={link.skillId} sx={{ height: ROW_HEIGHT.default }}>
            <TableCell><Typography variant="s1">{link.mount}</Typography></TableCell>
            <TableCell><Typography variant="s1">{`v${link.version}`}</Typography></TableCell>
            <TableCell sx={{ overflowWrap: "anywhere" }}><Typography variant="s3">{`${link.versionId} · ${link.digest}`}</Typography></TableCell>
          </TableRow>)}</TableBody>
        </Table>
      </TableContainer>
      {checks && <ChecksList checks={checks} dense />}
      {!result && <>
        <FormControlLabel control={<Checkbox checked={accepted} disabled={busy} onChange={(event) => setAccepted(event.target.checked)} />} label={`Accept these ${lock.length} exact pinned versions and this compiled candidate.`} />
        {warnings && <FormControlLabel control={<Checkbox checked={warningsAcknowledged} disabled={busy || !accepted} onChange={(event) => setWarningsAcknowledged(event.target.checked)} />} label="I've read the check warnings for this version." />}
        <Box><Button variant="contained" color="primary" disabled={!ready} onClick={() => run(publish, { prepared, acknowledgeWarnings: warningsAcknowledged, keys: keys.current }, setResult)}>Publish agent context</Button></Box>
      </>}
    </>}
    {result && <>
      <Alert severity="success">{`Published ${prepared.name} v${result.receipt.version.number} · ${result.receipt.version.digest}. No labels moved; production is unchanged.`}</Alert>
      <Alert severity="warning">A released package and a verified host/version profile are required. This capability is not qualified yet. A pilot install of this exact version is a preview, not delivery evidence.</Alert>
      <ContextPilotCredentials contextId={result.contextId} canIssue={result.canIssueCredentials}
        snapshot={{ kind: "agent_context", name: prepared.name, version: result.receipt.version.number, versionId: result.receipt.version.id, digest: result.receipt.version.digest }} />
    </>}
  </Stack>;
}
MigrationContextAssembly.propTypes = {
  published: PropTypes.arrayOf(PropTypes.shape({ skillId: PropTypes.string, version: PropTypes.number })).isRequired,
  proposal: PropTypes.shape({ name: PropTypes.string, description: PropTypes.string, agents: PropTypes.string }).isRequired,
  onBusyChange: PropTypes.func,
};
