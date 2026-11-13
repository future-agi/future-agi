import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Button, Checkbox, FormControlLabel, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { paths } from 'src/routes/paths';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import StatusBadge from 'src/components/status-badge';
import { useLinkedSkillUpstream } from 'src/api/skills/context-skill-inventory';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import { AuthoringError, useAuthoringOperation } from "../editor/HotfixHeadReview";

// Intent: accept a specific dependency plan without following moving upstream bytes.
// Hierarchy: included version versus upstream, explicit desired set, reviewed apply.
// Palette/depth/surfaces: existing neutral/divider and uncertainty roles, flat groups.
// Typography/spacing: m3/s1/s3 and compact 12px spacing; no dependency graph.
const MAX_LINKS = 20;
const selectorLabel = (selector) => selector?.version ? `Pinned to v${selector.version}` : `Follows ${selector?.label}`;
// Proposals only seed an empty draft's unaccepted set; nothing is applied until
// the owner previews the exact resolution and explicitly accepts it.
const seedLinks = (accepted, proposed) => (accepted.length || !Array.isArray(proposed) ? accepted
  : proposed.filter((link, index) => typeof link?.skillId === "string" && Number.isSafeInteger(link.version) && link.version > 0 && proposed.findIndex((other) => other.skillId === link.skillId) === index)
    .slice(0, 20).map((link) => ({ skillId: link.skillId, selector: { version: link.version } })));
function Links({ scopeKey, session, availableSkills = [], upstream = {}, onPreview, onApply, proposedLinks, readOnly = false }) {
  const accepted = session.contextSpec?.links || [];
  const [links, setLinks] = useState(() => seedLinks(accepted, proposedLinks));
  const upstreamQueries = useLinkedSkillUpstream(links);
  const proposed = !accepted.length && links.length > 0 && Array.isArray(proposedLinks);
  const autoPreviewed = useRef(false);
  const [refresh, setRefresh] = useState([]);
  const [selected, setSelected] = useState("");
  const [policy, setPolicy] = useState("label");
  const [selector, setSelector] = useState("production");
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const operation = useAuthoringOperation(scopeKey);
  const lock = session.resolvedLock?.links || [];
  const remaining = availableSkills.filter((skill) => skill.kind === "skill" && skill.latestVersion && !links.some((link) => link.skillId === skill.id));
  const invalidate = () => { setPreview(null); setConfirmed(false); };
  const validSelector = policy === "version" ? /^[1-9]\d*$/.test(selector) && Number.isSafeInteger(Number(selector)) : /^[a-z0-9][a-z0-9-]{0,39}$/.test(selector);
  const current = preview && Array.isArray(preview.changes) && Boolean(preview.fingerprint) && Boolean(preview.candidateDigest) && Number.isInteger(preview.checks?.summary?.errors) && !session.dirty && !session.mutationError && !session.pendingMutations && preview.revision === session.revision;
  const prepare = async () => {
    invalidate();
    const result = await operation.run(async () => {
      const saved = await session.flush();
      if (!saved) throw new Error("Save blocked");
      return onPreview({ revision: saved.revision, links, refreshSkillIds: refresh });
    }, { action: "preview-links", links, refresh, editSequence: session.editSequence }, { retainUnknown: false });
    if (result) setPreview(result);
  };
  const apply = async () => {
    if (!current || !confirmed) return;
    const result = await operation.run(async (input, options) => {
      const saved = await session.mutateDraft((draft) => {
        if (draft.revision !== preview.revision) throw Object.assign(new Error("Draft changed"), { code: "draft_conflict" });
        return onApply(input, options);
      }, { operationKey: options.idempotencyKey });
      if (!saved) throw new Error("Apply blocked");
      return saved;
    }, { revision: preview.revision, links, refreshSkillIds: refresh, expectedFingerprint: preview.fingerprint });
    if (result) { setLinks(result.contextSpec.links); setRefresh([]); invalidate(); }
  };
  // Previewing is read-only, so seeded migration proposals are resolved once on
  // arrival. Applying them still requires the explicit acceptance below.
  useEffect(() => {
    if (!proposed || readOnly || !onPreview || autoPreviewed.current) return;
    autoPreviewed.current = true;
    prepare();
  });
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Linked skills · {lock.length} accepted locks</Typography>
    <Typography variant="s1">Following offers updates. Published contexts stay unchanged. Linked paths are read-only; edit their source skill separately.</Typography>
    <AuthoringError error={operation.error} />
    {session.mutationError && operation.error && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const result = await operation.retry(); if (result) { setLinks(result.contextSpec.links); setRefresh([]); invalidate(); } }}>Retry dependency update</Button>}
    {!links.length && <Typography variant="s3">No linked skills. AGENTS.md remains standing context.</Typography>}
    {proposed && !readOnly && <Alert severity="info">{links.length} skills from your migration are proposed, each pinned to its published version. Preview the dependency changes and accept them explicitly.</Alert>}
    {links.length > 0 && <TableContainer><Table size="small" aria-label="Linked skills">
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{['Skill', 'Update policy', 'Locked version', 'Update status', 'Actions'].map((heading) => <TableCell key={heading}>{heading}</TableCell>)}</TableRow></TableHead>
      <TableBody>{links.map((link) => {
        const included = lock.find((entry) => entry.skillId === link.skillId);
        const query = upstreamQueries[link.skillId];
        const skill = availableSkills.find((entry) => entry.id === link.skillId) || query?.data;
        const newer = query?.data?.labels?.find((label) => label.name === link.selector.label) || upstream[link.skillId];
        const pinned = Boolean(link.selector.version);
        const checking = !pinned && (query?.isLoading || query?.isFetching);
        const unavailable = !pinned && (query?.isError || !newer);
        const changed = !pinned && !unavailable && included && (newer.version !== included.version || (newer.versionId && included.versionId && newer.versionId !== included.versionId));
        const name = skill?.name || included?.mount?.replace(/^skills\//, '').replace(/\/$/, '') || 'Linked skill';
        return <TableRow hover key={link.skillId} sx={{ height: ROW_HEIGHT.default }}>
          <TableCell><Typography variant="s1" display="block">{name}</Typography><Typography variant="s3" display="block" color="text.secondary">{included?.mount || 'Mount assigned on acceptance'}</Typography></TableCell>
          <TableCell>{selectorLabel(link.selector)}</TableCell>
          <TableCell>{included ? `v${included.version}` : 'Not yet accepted'}<TechnicalDetails title="Details"><IdentityChip value={link.skillId} kind="id" label="Skill" />{included?.digest && <IdentityChip value={included.digest} label="Locked digest" />}</TechnicalDetails></TableCell>
          <TableCell><StatusBadge status={pinned ? 'pinned' : checking ? 'checking' : unavailable ? 'unavailable' : changed ? 'update_available' : included ? 'up_to_date' : 'draft'} label={pinned ? 'Pinned intentionally' : checking ? 'Checking…' : unavailable ? "Couldn't check" : changed ? `v${newer.version} available` : included ? 'Up to date' : 'Preview required'} />
            {unavailable && !checking && query?.refetch && <Button size="small" onClick={() => query.refetch()}>Retry</Button>}
          </TableCell>
          <TableCell><Stack spacing={0.5}>
            {readOnly ? <Button component={RouterLink} to={`${paths.dashboard.skills.detail(link.skillId)}?tab=dependents`} size="small">Review update</Button> : <>
              {link.selector.label && <FormControlLabel control={<Checkbox checked={refresh.includes(link.skillId)} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => { setRefresh(event.target.checked ? [...refresh, link.skillId] : refresh.filter((id) => id !== link.skillId)); invalidate(); }} />} label={`Review update for ${skill?.name || link.skillId}`} />}
              <Button disabled={operation.pending || Boolean(session.mutationError)} onClick={() => { setLinks(links.filter((entry) => entry.skillId !== link.skillId)); setRefresh(refresh.filter((id) => id !== link.skillId)); invalidate(); }}>Remove from context</Button>
            </>}
          </Stack></TableCell>
        </TableRow>;
      })}</TableBody>
    </Table></TableContainer>}
    {!readOnly && <>
      <TechnicalDetails title="Changing update policies"><Typography variant="s3">Remove from this context; keep the shared skill. To change a selector, remove and re-add the skill before previewing.</Typography></TechnicalDetails>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField select fullWidth size="small" label="Published leaf skill" value={selected} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => setSelected(event.target.value)}>
          <MenuItem value="">Choose a skill</MenuItem>
          {remaining.map((skill) => <MenuItem key={skill.id} value={skill.id}>{skill.name}</MenuItem>)}
        </TextField>
        <TextField select size="small" label="Update policy" value={policy} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => { setPolicy(event.target.value); setSelector(event.target.value === "version" ? "1" : "production"); }} sx={{ minWidth: 140 }}><MenuItem value="label">Follow label</MenuItem><MenuItem value="version">Pin version</MenuItem></TextField>
        <TextField size="small" label={policy === "version" ? "Version" : "Label"} value={selector} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => setSelector(event.target.value)} error={!validSelector} />
      </Stack>
      <Button variant="outlined" disabled={!selected || !validSelector || operation.pending || Boolean(session.mutationError) || links.length >= MAX_LINKS} onClick={() => { setLinks([...links, { skillId: selected, selector: policy === "version" ? { version: Number(selector) } : { label: selector } }]); setSelected(""); setPolicy("label"); setSelector("production"); invalidate(); }}>Add to proposed links</Button>
      {/* Batch add: one decision over every remaining published skill, using the selector policy shown above. */}
      {remaining.length > 1 && <Button variant="outlined" disabled={!validSelector || operation.pending || Boolean(session.mutationError) || links.length + remaining.length > MAX_LINKS} onClick={() => { setLinks([...links, ...remaining.map((skill) => ({ skillId: skill.id, selector: policy === "version" ? { version: Number(selector) } : { label: selector } }))]); setSelected(""); invalidate(); }}>{`Add all ${remaining.length} remaining published skills`}</Button>}
      {remaining.length > 1 && links.length + remaining.length > MAX_LINKS && <Typography variant="s3">A context holds at most {MAX_LINKS} linked skills; add fewer at a time.</Typography>}
      <Button variant="outlined" disabled={!onPreview || operation.pending || Boolean(session.mutationError)} onClick={prepare}>Preview dependency changes</Button>
      {preview && <>
        <IdentityChip value={preview.candidateDigest} label="Compiled candidate" />
        {(preview.changes || []).map((change) => <Typography key={change.skillId} variant="s1">{change.mount} · {change.change} · {change.before ? `v${change.before.version}` : "absent"} → {change.after ? `v${change.after.version}` : "removed"}</Typography>)}
        {preview.checks?.summary?.errors > 0 && <Alert severity="error">The compiled context has blocking checks. No partial dependency set can be applied.</Alert>}
        {!current && <Alert severity="warning">{session.mutationError ? "Recover the original dependency update with Retry dependency update before previewing or publishing." : "The draft changed. Preview again before applying."}</Alert>}
        <FormControlLabel control={<Checkbox checked={confirmed} disabled={!current || operation.pending} onChange={(event) => setConfirmed(event.target.checked)} />} label="Accept these exact resolved versions and selector policies." />
        <Button variant="contained" disabled={!onApply || !current || !confirmed || operation.pending || preview.checks?.summary?.errors > 0} onClick={apply}>Apply selected updates to draft</Button>
      </>}
    </>}
  </Stack>;
}
Links.propTypes = { proposedLinks: PropTypes.array, scopeKey: PropTypes.string.isRequired, session: PropTypes.object.isRequired, availableSkills: PropTypes.array, upstream: PropTypes.object, onPreview: PropTypes.func, onApply: PropTypes.func, readOnly: PropTypes.bool };
export default function ContextLinksPanel(props) { return <Links key={`${props.scopeKey}:${JSON.stringify(props.session.contextSpec?.links)}:${JSON.stringify(props.session.resolvedLock)}`} {...props} />; }
ContextLinksPanel.propTypes = Links.propTypes;
