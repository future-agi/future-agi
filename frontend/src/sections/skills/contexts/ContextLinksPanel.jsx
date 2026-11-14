import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from 'react-router-dom';
import { visuallyHidden } from '@mui/utils';
import { Alert, Box, Button, Checkbox, FormControlLabel, IconButton, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from '@mui/material/styles';
import { paths } from 'src/routes/paths';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import StatusBadge from 'src/components/status-badge';
import { useLinkedSkillUpstream } from 'src/api/skills/context-skill-inventory';
import Iconify from 'src/components/iconify';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import VersionLabel from '../components/VersionLabel';
import '../components/skillIcons';
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
  const theme = useTheme();
  const compact = useMediaQuery(theme.breakpoints.down('md'));
  const accepted = session.contextSpec?.links || [];
  const [links, setLinks] = useState(() => seedLinks(accepted, proposedLinks));
  const upstreamQueries = useLinkedSkillUpstream(links);
  const proposed = !accepted.length && links.length > 0 && Array.isArray(proposedLinks);
  const autoPreviewed = useRef(false);
  const [refresh, setRefresh] = useState([]);
  const [expanded, setExpanded] = useState([]);
  const toggleExpanded = (skillId) => setExpanded((open) => (open.includes(skillId) ? open.filter((id) => id !== skillId) : [...open, skillId]));
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
  const availableUpdates = links.flatMap((link) => {
    const included = lock.find((entry) => entry.skillId === link.skillId);
    const query = upstreamQueries[link.skillId];
    const latest = query?.data?.labels?.find((label) => label.name === link.selector.label) || upstream[link.skillId];
    return !link.selector.version && included && latest && !query?.isError && (latest.version !== included.version || (latest.versionId && included.versionId && latest.versionId !== included.versionId))
      ? [`${query?.data?.name || included.mount?.replace(/^skills\//, '').replace(/\/$/, '') || 'A linked skill'} has v${latest.version} available`] : [];
  });
  const Container = compact ? Stack : TableContainer;
  const Collection = compact ? Stack : Table;
  const Rows = compact ? Stack : TableBody;
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Linked skills ({lock.length})</Typography>
    {availableUpdates.length > 0 && <Typography variant="s1">{`${availableUpdates.join('; ')}. Review the update before changing this lock.`}</Typography>}
    <Typography variant="s3" color="text.secondary">Each skill is locked to one version. Skills that follow production can be updated; pinned skills stay put.</Typography>
    <AuthoringError error={operation.error} />
    {session.mutationError && operation.error && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const result = await operation.retry(); if (result) { setLinks(result.contextSpec.links); setRefresh([]); invalidate(); } }}>Retry dependency update</Button>}
    {!links.length && <Typography variant="s3">No linked skills. AGENTS.md remains standing context.</Typography>}
    {proposed && !readOnly && <Alert severity="info">{links.length} skills from your migration are proposed, each pinned to its published version. Preview the dependency changes and accept them explicitly.</Alert>}
    {links.length > 0 && <Container sx={{ maxWidth: 1 }}><Collection {...(compact ? { spacing: 1.5, role: 'list' } : { size: 'small' })} aria-label="Linked skills">
      {!compact && <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{['Skill', 'Update rule', 'Version in this context', 'Status', 'Action'].map((heading) => <TableCell key={heading}>{heading}</TableCell>)}<TableCell padding="checkbox"><Box component="span" sx={visuallyHidden}>Details</Box></TableCell></TableRow></TableHead>}
      <Rows>{links.map((link) => {
        const included = lock.find((entry) => entry.skillId === link.skillId);
        const query = upstreamQueries[link.skillId];
        const skill = availableSkills.find((entry) => entry.id === link.skillId) || query?.data;
        const newer = query?.data?.labels?.find((label) => label.name === link.selector.label) || upstream[link.skillId];
        const pinned = Boolean(link.selector.version);
        const checking = !pinned && (query?.isLoading || query?.isFetching);
        const unavailable = !pinned && (query?.isError || !newer);
        const changed = !pinned && !unavailable && included && (newer.version !== included.version || (newer.versionId && included.versionId && newer.versionId !== included.versionId));
        const name = skill?.name || included?.mount?.replace(/^skills\//, '').replace(/\/$/, '') || 'Linked skill';
        const status = <StatusBadge status={pinned ? 'pinned' : checking ? 'checking' : unavailable ? 'unavailable' : changed ? 'update_available' : included ? 'up_to_date' : 'draft'} label={pinned ? 'Pinned on purpose' : checking ? 'Checking…' : unavailable ? "Couldn't check" : changed ? `v${newer.version} available` : included ? 'Up to date' : 'Preview required'} />;
        const retry = unavailable && !checking && query?.refetch && <Button variant="outlined" size="small" onClick={() => query.refetch()}>Retry</Button>;
        const identity = <Typography variant="s1" display="block">{name}</Typography>;
        const open = expanded.includes(link.skillId);
        const detailId = `link-details-${link.skillId}`;
        const toggle = <IconButton size="small" aria-label={`Show details for ${name}`} aria-expanded={open} aria-controls={open ? detailId : undefined} onClick={() => toggleExpanded(link.skillId)}><Iconify icon={open ? 'solar:alt-arrow-down-linear' : 'solar:alt-arrow-right-linear'} width={16} /></IconButton>;
        const details = open && <Stack id={detailId} spacing={0.5} sx={{ minWidth: 0 }}>{included?.versionId && <IdentityChip value={included.versionId} kind="id" label="Version ID" />}{included?.digest && <IdentityChip value={included.digest} label="Content fingerprint" />}{!included?.versionId && !included?.digest && <Typography variant="s3" color="text.secondary">No lock details yet. Preview the dependency changes first.</Typography>}</Stack>;
        const actions = readOnly ? <>{retry}{changed && <Button fullWidth={compact} variant="outlined" component={RouterLink} to={`${paths.dashboard.skills.detail(link.skillId)}?tab=dependents`} size="small">Review update</Button>}</> : <>{retry}
          {link.selector.label && <FormControlLabel control={<Checkbox checked={refresh.includes(link.skillId)} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => { setRefresh(event.target.checked ? [...refresh, link.skillId] : refresh.filter((id) => id !== link.skillId)); invalidate(); }} />} label={`Review update for ${skill?.name || link.skillId}`} />}
          <Button disabled={operation.pending || Boolean(session.mutationError)} onClick={() => { setLinks(links.filter((entry) => entry.skillId !== link.skillId)); setRefresh(refresh.filter((id) => id !== link.skillId)); invalidate(); }}>Remove from context</Button>
        </>;
        return compact ? <Stack key={link.skillId} role="listitem" spacing={1} sx={{ py: 1.5, borderBottom: 1, borderColor: 'divider' }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>{identity}{toggle}</Stack>
          <Typography variant="s3" color="text.secondary">{selectorLabel(link.selector)} · {included ? `locked v${included.version}` : 'Not yet accepted'}</Typography>
          {status}{actions}{details}
        </Stack> : [<TableRow hover key={link.skillId} sx={{ height: ROW_HEIGHT.default, ...(open ? { '& > td': { borderBottom: 0 } } : {}) }}>
          <TableCell>{identity}</TableCell>
          <TableCell>{selectorLabel(link.selector)}</TableCell>
          <TableCell>{included ? <VersionLabel number={included.version} digest={included.digest} label="Version" /> : 'Not yet accepted'}</TableCell>
          <TableCell>{status}</TableCell>
          <TableCell><Stack direction="row" useFlexGap spacing={1} alignItems="center" sx={{ flexWrap: 'wrap' }}>{actions}</Stack></TableCell>
          <TableCell padding="checkbox" align="right">{toggle}</TableCell>
        </TableRow>, open && <TableRow key={`${link.skillId}-details`}><TableCell colSpan={6} sx={{ pt: 0 }}>{details}</TableCell></TableRow>];
      })}</Rows>
    </Collection></Container>}
    {!readOnly && <>
      <TechnicalDetails title="Changing update policies"><Typography variant="s3">Remove from this context; keep the shared skill. To change a selector, remove and re-add the skill before previewing.</Typography></TechnicalDetails>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField select fullWidth size="small" label="Add a published skill" value={selected} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => setSelected(event.target.value)}>
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
        <FormControlLabel control={<Checkbox checked={confirmed} disabled={!current || operation.pending} onChange={(event) => setConfirmed(event.target.checked)} />} label="Accept these linked-skill versions and update rules for 1 context." />
        <Button color="primary" variant="contained" disabled={!onApply || !current || !confirmed || operation.pending || preview.checks?.summary?.errors > 0} onClick={apply}>Apply selected updates to draft</Button>
      </>}
    </>}
  </Stack>;
}
Links.propTypes = { proposedLinks: PropTypes.array, scopeKey: PropTypes.string.isRequired, session: PropTypes.object.isRequired, availableSkills: PropTypes.array, upstream: PropTypes.object, onPreview: PropTypes.func, onApply: PropTypes.func, readOnly: PropTypes.bool };
export default function ContextLinksPanel(props) { return <Links key={`${props.scopeKey}:${JSON.stringify(props.session.contextSpec?.links)}:${JSON.stringify(props.session.resolvedLock)}`} {...props} />; }
ContextLinksPanel.propTypes = Links.propTypes;
