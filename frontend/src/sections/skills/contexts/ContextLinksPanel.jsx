import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useInRouterContext, useNavigate } from 'react-router-dom';
import { visuallyHidden } from '@mui/utils';
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from '@mui/material/styles';
import { paths } from 'src/routes/paths';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import StatusBadge from 'src/components/status-badge';
import { useLinkedSkillUpstream } from 'src/api/skills/context-skill-inventory';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import VersionLabel from '../components/VersionLabel';
import RowActions from '../components/RowActions';
import { rowLinkProps } from '../components/responsiveTable';
import { ADD_SKILL, CHECK_FOR_UPDATE, FOLLOWS_PRODUCTION, PINNED, UPDATE_RULE, followsLabel, pinnedTo } from '../utils/terms';
import { AuthoringError, useAuthoringOperation } from "../editor/HotfixHeadReview";

// Intent: accept a specific dependency plan without following moving upstream bytes.
// Hierarchy: included version versus upstream, explicit desired set, reviewed apply.
// Palette/depth/surfaces: existing neutral/divider and uncertainty roles, flat groups.
// Typography/spacing: m3/s1/s3 and compact 12px spacing; no dependency graph.
// Names match the read views everywhere (SKILL-TRACES §16 UXF-16): "Update rule", with
// "Follows production", "Follows <label>" and "Pinned"; "Check for update" only where one exists.
// Row actions follow the shared pattern (§16 UXF-12): the whole row opens its primary target
// (the skill, only where the caller asks for it with `rowOpensSkill`, as the context page does;
// otherwise the row's technical details, so a click never leaves the draft editor, locked or not),
// every other action is in the trailing ⋯, and "Check for update" is the one inline ghost action.
const MAX_LINKS = 20;
const LABEL_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
const VERSION_PATTERN = /^[1-9]\d*$/;
const selectorLabel = (selector) => selector?.version ? pinnedTo(selector.version) : followsLabel(selector?.label);
const sameIds = (left = [], right = []) => left.length === right.length && [...left].sort().every((id, index) => id === [...right].sort()[index]);
// Proposals only seed an empty draft's unaccepted set; nothing is applied until
// the owner previews the exact resolution and explicitly accepts it.
const seedLinks = (accepted, proposed) => (accepted.length || !Array.isArray(proposed) ? accepted
  : proposed.filter((link, index) => typeof link?.skillId === "string" && Number.isSafeInteger(link.version) && link.version > 0 && proposed.findIndex((other) => other.skillId === link.skillId) === index)
    .slice(0, 20).map((link) => ({ skillId: link.skillId, selector: { version: link.version } })));
function Links({ scopeKey, session, availableSkills = [], upstream = {}, onPreview, onApply, proposedLinks, readOnly = false, rowOpensSkill = false, extraColumn, navigate }) {
  const theme = useTheme();
  const compact = useMediaQuery(theme.breakpoints.down('md'));
  const phone = useMediaQuery(theme.breakpoints.down('sm'));
  const accepted = session.contextSpec?.links || [];
  const [links, setLinks] = useState(() => seedLinks(accepted, proposedLinks));
  const upstreamQueries = useLinkedSkillUpstream(links);
  const proposed = !accepted.length && links.length > 0 && Array.isArray(proposedLinks);
  const autoPreviewed = useRef(false);
  const [refresh, setRefresh] = useState([]);
  const [expanded, setExpanded] = useState([]);
  const toggleExpanded = (skillId) => setExpanded((open) => (open.includes(skillId) ? open.filter((id) => id !== skillId) : [...open, skillId]));
  const [selected, setSelected] = useState("");
  // The update rule for the next skill added: 'production' (follows production), 'label' (follows the typed label) or 'version' (pinned).
  const [rule, setRule] = useState("production");
  const [selector, setSelector] = useState("production");
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const operation = useAuthoringOperation(scopeKey);
  const lock = session.resolvedLock?.links || [];
  const remaining = availableSkills.filter((skill) => skill.kind === "skill" && skill.latestVersion && !links.some((link) => link.skillId === skill.id));
  const invalidate = () => { setPreview(null); setConfirmed(false); };
  const validSelector = rule === "production" || (rule === "version" ? VERSION_PATTERN.test(selector) && Number.isSafeInteger(Number(selector)) : LABEL_PATTERN.test(selector));
  const chosenSelector = () => (rule === "version" ? { version: Number(selector) } : { label: rule === "production" ? "production" : selector });
  const resetRule = () => { setRule("production"); setSelector("production"); };
  // One row model for the table, the phone list and the refresh set.
  const rows = links.map((link) => {
    const included = lock.find((entry) => entry.skillId === link.skillId);
    const query = upstreamQueries[link.skillId];
    const skill = availableSkills.find((entry) => entry.id === link.skillId) || query?.data;
    const newer = query?.data?.labels?.find((label) => label.name === link.selector.label) || upstream[link.skillId];
    const pinned = Boolean(link.selector.version);
    const checking = !pinned && (query?.isLoading || query?.isFetching);
    const unavailable = !pinned && (query?.isError || !newer);
    const changed = Boolean(!pinned && !unavailable && included && (newer.version !== included.version || (newer.versionId && included.versionId && newer.versionId !== included.versionId)));
    const name = skill?.name || included?.mount?.replace(/^skills\//, '').replace(/\/$/, '') || 'Linked skill';
    return { link, included, query, newer, pinned, checking, unavailable, changed, name };
  });
  // "Check for update" exists only on rows with an update, so a tick on a row that no longer
  // offers it is never sent: the preview and the apply use only the ticks still on screen.
  const updatable = rows.filter((row) => row.changed && row.link.selector.label).map((row) => row.link.skillId);
  const refreshIds = refresh.filter((id) => updatable.includes(id));
  const current = preview && Array.isArray(preview.changes) && Boolean(preview.fingerprint) && Boolean(preview.candidateDigest) && Number.isInteger(preview.checks?.summary?.errors) && !session.dirty && !session.mutationError && !session.pendingMutations && preview.revision === session.revision && sameIds(preview.refreshSkillIds, refreshIds);
  const prepare = async () => {
    invalidate();
    const refreshSkillIds = refreshIds;
    const result = await operation.run(async () => {
      const saved = await session.flush();
      if (!saved) throw new Error("Save blocked");
      return onPreview({ revision: saved.revision, links, refreshSkillIds });
    }, { action: "preview-links", links, refresh: refreshSkillIds, editSequence: session.editSequence }, { retainUnknown: false });
    if (result) setPreview({ ...result, refreshSkillIds });
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
    }, { revision: preview.revision, links, refreshSkillIds: preview.refreshSkillIds, expectedFingerprint: preview.fingerprint });
    if (result) { setLinks(result.contextSpec.links); setRefresh([]); invalidate(); }
  };
  // Previewing is read-only, so seeded migration proposals are resolved once on
  // arrival. Applying them still requires the explicit acceptance below.
  useEffect(() => {
    if (!proposed || readOnly || !onPreview || autoPreviewed.current) return;
    autoPreviewed.current = true;
    prepare();
  });
  const blocked = operation.pending || Boolean(session.mutationError);
  const Container = compact ? Stack : TableContainer;
  const Collection = compact ? Stack : Table;
  const Rows = compact ? Stack : TableBody;
  const extra = extraColumn && typeof extraColumn.cell === 'function' ? extraColumn : null;
  const columnCount = 5 + (extra ? 1 : 0);
  const ruleOption = rule === "label" && LABEL_PATTERN.test(selector) && selector !== "production" ? followsLabel(selector) : "Follows another label";
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Linked skills ({lock.length})</Typography>
    <Typography variant="s3" color="text.secondary">Each skill is locked to one version. Skills that follow production can be updated; pinned skills stay put.</Typography>
    <AuthoringError error={operation.error} />
    {session.mutationError && operation.error && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const result = await operation.retry(); if (result) { setLinks(result.contextSpec.links); setRefresh([]); invalidate(); } }}>Retry dependency update</Button>}
    {!links.length && <Typography variant="s3">No linked skills. AGENTS.md remains standing context.</Typography>}
    {proposed && !readOnly && <Alert severity="info">{links.length} {links.length === 1 ? 'skill' : 'skills'} from your migration {links.length === 1 ? 'is' : 'are'} proposed, each pinned to its published version. Preview the dependency changes and accept them explicitly.</Alert>}
    {links.length > 0 && <Container sx={{ maxWidth: 1 }}><Collection {...(compact ? { spacing: 1.5, role: 'list' } : { size: 'small' })} aria-label="Linked skills">
      {!compact && <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{['Skill', UPDATE_RULE, 'Version in this context', 'Status', ...(extra ? [extra.header] : [])].map((heading) => <TableCell key={heading}>{heading}</TableCell>)}<TableCell align="right"><Box component="span" sx={visuallyHidden}>Actions</Box></TableCell></TableRow></TableHead>}
      <Rows>{rows.map((row) => {
        const { link, included, query, newer, pinned, checking, unavailable, changed, name } = row;
        const status = <StatusBadge status={pinned ? 'pinned' : checking ? 'checking' : unavailable ? 'unavailable' : changed ? 'update_available' : included ? 'up_to_date' : 'draft'} label={pinned ? 'Pinned on purpose' : checking ? 'Checking…' : unavailable ? "Couldn't check" : changed ? `v${newer.version} available` : included ? 'Up to date' : 'Preview required'} />;
        const identity = <Typography variant="s1" display="block">{name}</Typography>;
        const open = expanded.includes(link.skillId);
        const detailId = `link-details-${link.skillId}`;
        const details = open && <Stack id={detailId} spacing={0.5} sx={{ minWidth: 0 }}><Typography variant="s3" color="text.secondary">Technical details</Typography>{included?.versionId && <IdentityChip value={included.versionId} kind="id" label="Source version" />}{included?.digest && <IdentityChip value={included.digest} label="Digest" />}{!included?.versionId && !included?.digest && <Typography variant="s3" color="text.secondary">No lock details yet. Preview the dependency changes first.</Typography>}</Stack>;
        const toggleDetails = { label: open ? 'Hide technical details' : 'Show technical details', onClick: () => toggleExpanded(link.skillId) };
        const retry = unavailable && !checking && query?.refetch ? [{ label: 'Retry check', onClick: () => query.refetch() }] : [];
        // Read view: the row opens the skill at the version this context locks; "Check for update"
        // (rows with an update only) opens the skill's Linked contexts, where followers are updated.
        const openSkill = readOnly && rowOpensSkill && navigate ? { label: 'Open skill', onClick: () => navigate(`${paths.dashboard.skills.detail(link.skillId)}${included?.version ? `?version=${included.version}` : ''}`) } : null;
        const checkUpdate = readOnly && changed && navigate ? { label: CHECK_FOR_UPDATE, onClick: () => navigate(`${paths.dashboard.skills.detail(link.skillId)}?tab=dependents`) } : null;
        const remove = { label: 'Remove from context', danger: true, disabled: blocked, reason: blocked ? 'Wait for the current dependency update to finish.' : undefined, onClick: () => { setLinks(links.filter((entry) => entry.skillId !== link.skillId)); setRefresh(refresh.filter((id) => id !== link.skillId)); invalidate(); } };
        const primary = openSkill || toggleDetails;
        // A phone keeps the ⋯ (the details and retry live there), and the inline action moves into it.
        const menu = readOnly ? [...(openSkill ? [toggleDetails] : []), ...retry, ...(phone && checkUpdate ? [checkUpdate] : [])] : [...retry, remove];
        const actions = <RowActions name={name} primary={primary} actions={menu} inline={checkUpdate || undefined} phoneMenu phone={phone} />;
        // Draft editor: ask for the newer version only where one exists (UXF-16). The tick feeds the preview; acceptance below is unchanged.
        const refreshControl = !readOnly && updatable.includes(link.skillId) && <FormControlLabel control={<Checkbox size="small" checked={refresh.includes(link.skillId)} disabled={blocked} inputProps={{ 'aria-label': `${CHECK_FOR_UPDATE}: ${name}` }} onChange={(event) => { setRefresh(event.target.checked ? [...refresh, link.skillId] : refresh.filter((id) => id !== link.skillId)); invalidate(); }} />} label={CHECK_FOR_UPDATE} />;
        const extraCell = extra ? extra.cell({ link, included, name, readOnly }) : null;
        const rowLink = rowLinkProps(primary);
        if (compact) {
          const { hover: _hover, sx: rowSx, ...cardLink } = rowLink;
          return <Stack key={link.skillId} role="listitem" spacing={1} {...cardLink} sx={[{ py: 1.5, borderBottom: 1, borderColor: 'divider' }, rowSx || {}]}>
            <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>{identity}{actions}</Stack>
            <Typography variant="s3" color="text.secondary">{selectorLabel(link.selector)} · {included ? `locked v${included.version}` : 'Not yet accepted'}</Typography>
            <Stack direction="row" useFlexGap spacing={1} alignItems="center" sx={{ flexWrap: 'wrap' }}>{status}{refreshControl}</Stack>
            {extra && <Stack direction="row" useFlexGap spacing={1} alignItems="baseline" sx={{ flexWrap: 'wrap', minWidth: 0 }} data-extra-column="true"><Typography variant="s3" color="text.secondary">{extra.header}</Typography>{extraCell}</Stack>}
            {details}
          </Stack>;
        }
        return [<TableRow key={link.skillId} {...rowLink} sx={[{ height: ROW_HEIGHT.default, ...(open ? { '& > td': { borderBottom: 0 } } : {}) }, rowLink.sx || {}]}>
          <TableCell>{identity}</TableCell>
          <TableCell>{selectorLabel(link.selector)}</TableCell>
          <TableCell>{included ? <VersionLabel number={included.version} digest={included.digest} /> : 'Not yet accepted'}</TableCell>
          <TableCell><Stack direction="row" useFlexGap spacing={1} alignItems="center" sx={{ flexWrap: 'wrap' }}>{status}{refreshControl}</Stack></TableCell>
          {extra && <TableCell data-extra-column="true">{extraCell}</TableCell>}
          <TableCell align="right" sx={{ width: '1%', whiteSpace: 'nowrap' }}>{actions}</TableCell>
        </TableRow>, open && <TableRow key={`${link.skillId}-details`}><TableCell colSpan={columnCount} sx={{ pt: 0 }}>{details}</TableCell></TableRow>];
      })}</Rows>
    </Collection></Container>}
    {!readOnly && <>
      <TechnicalDetails title="Changing update rules"><Typography variant="s3">Remove from this context; keep the shared skill. To change an update rule, remove the skill and add it again before previewing.</Typography></TechnicalDetails>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField select fullWidth size="small" label="Add a published skill" value={selected} disabled={blocked} onChange={(event) => setSelected(event.target.value)}>
          <MenuItem value="">Choose a skill</MenuItem>
          {remaining.map((skill) => <MenuItem key={skill.id} value={skill.id}>{skill.name}</MenuItem>)}
        </TextField>
        <TextField select size="small" label={UPDATE_RULE} value={rule} disabled={blocked} onChange={(event) => { const next = event.target.value; setRule(next); setSelector(next === "version" ? "1" : next === "label" ? "" : "production"); }} sx={{ minWidth: 180 }}>
          <MenuItem value="production">{FOLLOWS_PRODUCTION}</MenuItem>
          <MenuItem value="label">{ruleOption}</MenuItem>
          <MenuItem value="version">{PINNED}</MenuItem>
        </TextField>
        {rule !== "production" && <TextField size="small" label={rule === "version" ? "Version" : "Label"} value={selector} disabled={blocked} onChange={(event) => setSelector(event.target.value)} error={selector !== "" && !validSelector} />}
      </Stack>
      <Button variant="outlined" disabled={!selected || !validSelector || blocked || links.length >= MAX_LINKS} onClick={() => { setLinks([...links, { skillId: selected, selector: chosenSelector() }]); setSelected(""); resetRule(); invalidate(); }}>{ADD_SKILL}</Button>
      {/* Batch add: one decision over every remaining published skill, using the update rule shown above. */}
      {remaining.length > 1 && <Button variant="outlined" disabled={!validSelector || blocked || links.length + remaining.length > MAX_LINKS} onClick={() => { const next = chosenSelector(); setLinks([...links, ...remaining.map((skill) => ({ skillId: skill.id, selector: { ...next } }))]); setSelected(""); invalidate(); }}>{`Add all ${remaining.length} remaining published skills`}</Button>}
      {remaining.length > 1 && links.length + remaining.length > MAX_LINKS && <Typography variant="s3">A context holds at most {MAX_LINKS} linked skills; add fewer at a time.</Typography>}
      <Button variant="outlined" disabled={!onPreview || blocked} onClick={prepare}>Preview changes to linked skills</Button>
      {preview && <>
        <IdentityChip value={preview.candidateDigest} label="Compiled candidate" />
        {(preview.changes || []).map((change) => <Typography key={change.skillId} variant="s1">{change.mount} · {change.change} · {change.before ? `v${change.before.version}` : "absent"} → {change.after ? `v${change.after.version}` : "removed"}</Typography>)}
        {preview.checks?.summary?.errors > 0 && <Alert severity="error">The compiled context has blocking checks. No partial dependency set can be applied.</Alert>}
        {!current && <Alert severity="warning">{session.mutationError ? "Recover the original dependency update with Retry dependency update before previewing or publishing." : "The draft changed. Preview again before applying."}</Alert>}
        <FormControlLabel control={<Checkbox checked={confirmed} disabled={!current || operation.pending} onChange={(event) => setConfirmed(event.target.checked)} />} label="Accept these skill versions and update rules into 1 context draft." />
        <Button color="primary" variant="contained" disabled={!onApply || !current || !confirmed || operation.pending || preview.checks?.summary?.errors > 0} onClick={apply}>Apply selected updates to draft</Button>
      </>}
    </>}
  </Stack>;
}
Links.propTypes = {
  proposedLinks: PropTypes.array, scopeKey: PropTypes.string.isRequired, session: PropTypes.object.isRequired, availableSkills: PropTypes.array, upstream: PropTypes.object, onPreview: PropTypes.func, onApply: PropTypes.func, readOnly: PropTypes.bool,
  // One extra column, before the row actions (commit 2: "Loaded (7 days)"). `cell` receives
  // { link, included, name, readOnly } and returns the cell content; below md it becomes a
  // "<header> <cell>" line in the stacked card. Absent: the table is unchanged.
  extraColumn: PropTypes.shape({ header: PropTypes.string.isRequired, cell: PropTypes.func.isRequired }),
  // Read view only: a row click opens the linked skill (the context page). Never set in the editor.
  rowOpensSkill: PropTypes.bool,
  navigate: PropTypes.func,
};
// Only the read view navigates (to the linked skill), and only inside a router; the draft editor,
// locked or not, keeps working wherever it is rendered.
function ReadOnlyLinks(props) {
  const navigate = useNavigate();
  return <Links {...props} navigate={navigate} />;
}
ReadOnlyLinks.propTypes = Links.propTypes;
export default function ContextLinksPanel(props) {
  const inRouter = useInRouterContext();
  const key = `${props.scopeKey}:${JSON.stringify(props.session.contextSpec?.links)}:${JSON.stringify(props.session.resolvedLock)}`;
  return props.readOnly && inRouter ? <ReadOnlyLinks key={key} {...props} /> : <Links key={key} {...props} />;
}
ContextLinksPanel.propTypes = Links.propTypes;
