import { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Button, Checkbox, FormControlLabel, Stack, Typography } from '@mui/material';
import { useFollowerUpdates } from 'src/api/skills/context-follower-update';
import StatusBadge from 'src/components/status-badge';
import { paths } from 'src/routes/paths';
import TechnicalDetails from '../components/TechnicalDetails';
import IdentityChip from '../components/IdentityChip';

// Intent: review one child update across many follower contexts without editing each parent separately.
// Hierarchy: per-context exact changes first, one acknowledgement, then per-context outcomes.
// Palette/surfaces: shared neutral rows, divider and status roles. Typography m3/s1/s3; 12px spacing.
// Reject: hidden updates, applying to a context whose exact plan was not shown, and
// any shared success state; each context keeps its own fingerprint, receipt and failure.
const reasons = {
  different_origin: 'This context already has a draft started from another release. It was not replaced; open it to preserve or resume that work.',
  draft_conflict: 'The draft changed after it was previewed.',
  stale_plan: 'The upstream skill changed after this plan was previewed.',
  plan_stale: 'The upstream skill changed after this plan was previewed.',
  unknown: 'The operation was not confirmed for this context.',
};
const explain = (item) => reasons[item.code] || (item.httpStatus === 409 ? 'The plan no longer matches the current upstream or draft. Preview again.' : `Not confirmed (${item.code}).`);

const changeLabel = (change) => ({ version_changed: 'Version changes', updated: 'Version changes', unchanged: 'No change', added: 'Skill added', removed: 'Skill removed', policy_changed: 'Update policy changes' })[change] || 'Link changes';

export default function ContextFollowerUpdate({ skillId, targets, sourceRelease }) {
  const updates = useFollowerUpdates(skillId);
  const [phase, setPhase] = useState('idle');
  const [items, setItems] = useState([]);
  const [outcomes, setOutcomes] = useState([]);
  const [done, setDone] = useState([]);
  const [accepted, setAccepted] = useState(false);
  const selectionKey = JSON.stringify([skillId, sourceRelease, targets.map((target) => [target.contextId, target.sourceVersionId || target.version]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))]);
  const liveSelection = useRef(selectionKey);
  liveSelection.current = selectionKey;
  const sequence = useRef(0);
  const [preparedKey, setPreparedKey] = useState(null);
  const [selectionChanged, setSelectionChanged] = useState(false);
  useEffect(() => {
    if (preparedKey !== null && preparedKey !== selectionKey) {
      sequence.current += 1;
      setItems([]); setOutcomes([]); setAccepted(false); setPreparedKey(null); setSelectionChanged(true);
      setPhase((current) => current === 'applying' ? current : 'idle');
    }
  }, [selectionKey, preparedKey]);
  const busy = phase === 'previewing' || phase === 'applying';
  const preview = async (list) => {
    const key = selectionKey;
    const request = ++sequence.current;
    setPreparedKey(key); setSelectionChanged(false);
    setPhase('previewing'); setAccepted(false); setOutcomes([]);
    const result = await updates.prepare(list);
    if (liveSelection.current !== key || sequence.current !== request) return;
    setItems(result); setPhase('previewed');
  };
  const ready = preparedKey === selectionKey ? items.filter((item) => item.status === 'ready' && !(item.plan.checks?.summary?.errors > 0)) : [];
  const apply = async () => {
    if (!accepted || busy || preparedKey !== liveSelection.current || !ready.length) return;
    setPhase('applying');
    const results = await updates.apply(ready);
    setDone((previous) => [...previous.filter((old) => !results.some((item) => item.target.contextId === old.target.contextId)), ...results.filter((item) => item.status === 'applied')]);
    setOutcomes(results.filter((item) => item.status === 'error'));
    setItems((previous) => previous.filter((item) => item.status === 'error'));
    setAccepted(false);
    setPhase('applied');
  };
  const failed = [...items.filter((item) => item.status === 'error'), ...outcomes];
  const nameOf = (target) => `${target.name} v${target.version}`;
  return <Stack spacing={1.5} aria-label="Update follower contexts" role="group">
    <Typography variant="m3" component="h3">Update follower contexts</Typography>
    <Typography variant="s1">Review the skill changes for each selected context, then accept them into drafts. Each context is published and reviewed separately.</Typography>
    <Typography variant="s3" color="text.secondary">Creates a draft for each context that doesn&apos;t have one. Production doesn&apos;t change.</Typography>
    {selectionChanged && <Alert severity="warning">Selection changed — prepare again.</Alert>}
    <Button sx={{ alignSelf: 'flex-start' }} variant={ready.length ? 'outlined' : 'contained'} disabled={!targets.length || busy} onClick={() => preview(targets)}>{`Prepare draft updates for ${targets.length} ${targets.length === 1 ? 'context' : 'contexts'}`}</Button>
    {phase === 'previewing' && <Typography variant="s3" role="status">Starting drafts and resolving exact versions…</Typography>}
    {items.map((item) => <Stack key={item.target.contextId} sx={{ borderBottom: 1, borderColor: 'divider', py: 1, minWidth: 0 }} spacing={0.5}>
      <Typography variant="m3" sx={{ overflowWrap: 'anywhere' }}>{nameOf(item.target)}</Typography>
      {item.status === 'ready' ? <>
        <Typography variant="s1">{item.plan.changes.filter((change) => change.change !== 'unchanged').length} skill changes · {item.plan.changes.filter((change) => change.change === 'unchanged').length} unchanged · production unchanged</Typography>
        {item.plan.changes.filter((change) => change.change !== 'unchanged').map((change) => <Typography key={change.skillId} variant="s1">{change.mount} · {changeLabel(change.change)} · {change.before ? `v${change.before.version}` : 'absent'} → {change.after ? `v${change.after.version}` : 'removed'}</Typography>)}
        {!item.plan.changes.length && <Typography variant="s1">No linked-skill changes.</Typography>}
        <Typography variant="s3" color="text.secondary">{item.reusedDraft === true ? 'Reused draft' : item.reusedDraft === false ? 'New draft' : 'Draft origin not reported'}</Typography>
        <TechnicalDetails title="Compiled candidate">
          <IdentityChip value={item.plan.candidateDigest} label="Candidate digest" />
          {item.plan.changes.filter((change) => change.change === 'unchanged').map((change) => <Typography key={change.skillId} variant="s3">{change.mount} · No change · v{change.after?.version ?? change.before?.version}</Typography>)}
        </TechnicalDetails>
        {item.plan.checks?.summary?.errors > 0 && <Alert severity="error">Blocking checks prevent this context from being updated. The others are not affected.</Alert>}
      </> : <Alert severity="warning">{explain(item)}</Alert>}
    </Stack>)}
    {phase === 'previewed' && failed.length > 0 && ready.length === 0 && <Typography variant="s1">No context can be updated from this preview.</Typography>}
    {(phase === 'previewed' || phase === 'applying') && ready.length > 0 && <>
      <FormControlLabel control={<Checkbox checked={accepted} disabled={busy} onChange={(event) => setAccepted(event.target.checked)} />} label={`Accept these exact resolved versions and selector policies for all ${ready.length} ${ready.length === 1 ? 'context' : 'contexts'}.`} />
      <Button sx={{ alignSelf: 'flex-start' }} variant="contained" disabled={!accepted || busy} onClick={apply}>{`Apply selected updates to ${ready.length} context ${ready.length === 1 ? 'draft' : 'drafts'}`}</Button>
    </>}
    {done.length > 0 && <Stack spacing={0.5}>
      {done.map((item) => <Stack key={item.target.contextId} direction="row" spacing={1} alignItems="center">
        <StatusBadge status="completed" label="Draft updated — production unchanged" />
        <Typography variant="s1" sx={{ overflowWrap: 'anywhere' }}>{nameOf(item.target)} · draft revision {item.revision}{item.reusedDraft === true ? ' · Reused draft' : item.reusedDraft === false ? ' · New draft' : ''}</Typography>
        <Button component={RouterLink} to={paths.dashboard.skills.contextEdit(item.target.contextId)} aria-label={`Open draft for ${item.target.name}`}>Open draft</Button>
      </Stack>)}
    </Stack>}
    {phase === 'applied' && outcomes.map((item) => <Alert severity="warning" key={item.target.contextId}>{nameOf(item.target)}: {explain(item)}</Alert>)}
    {(phase === 'applied' || phase === 'previewed') && failed.length > 0 && <Button variant="outlined" disabled={busy} onClick={() => preview(failed.map((item) => item.target))}>{`Preview again for ${failed.length} ${failed.length === 1 ? 'context' : 'contexts'} that did not update`}</Button>}
  </Stack>;
}
ContextFollowerUpdate.propTypes = { sourceRelease: PropTypes.string, skillId: PropTypes.string.isRequired, targets: PropTypes.arrayOf(PropTypes.shape({ contextId: PropTypes.string.isRequired, name: PropTypes.string.isRequired, version: PropTypes.number.isRequired })).isRequired };
