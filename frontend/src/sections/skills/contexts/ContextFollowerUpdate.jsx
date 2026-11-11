import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Button, Checkbox, FormControlLabel, Stack, Typography } from '@mui/material';
import { useFollowerUpdates } from 'src/api/skills/context-follower-update';
import StatusBadge from 'src/components/status-badge';
import { paths } from 'src/routes/paths';

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

export default function ContextFollowerUpdate({ skillId, targets }) {
  const updates = useFollowerUpdates(skillId);
  const [phase, setPhase] = useState('idle');
  const [items, setItems] = useState([]);
  const [outcomes, setOutcomes] = useState([]);
  const [done, setDone] = useState([]);
  const [accepted, setAccepted] = useState(false);
  const busy = phase === 'previewing' || phase === 'applying';
  const preview = async (list) => {
    setPhase('previewing'); setAccepted(false); setOutcomes([]);
    setItems(await updates.prepare(list));
    setPhase('previewed');
  };
  const ready = items.filter((item) => item.status === 'ready' && !(item.plan.checks?.summary?.errors > 0));
  const apply = async () => {
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
    <Typography variant="s1">Preview the new shared version for every selected context. You accept all exact results together; each context still gets its own draft and is published and reviewed separately.</Typography>
    <Button variant="contained" disabled={!targets.length || busy} onClick={() => preview(targets)}>{`Preview updates for ${targets.length} selected ${targets.length === 1 ? 'context' : 'contexts'}`}</Button>
    {phase === 'previewing' && <Typography variant="s3" role="status">Starting drafts and resolving exact versions…</Typography>}
    {items.map((item) => <Stack key={item.target.contextId} sx={{ borderBottom: 1, borderColor: 'divider', py: 1, minWidth: 0 }} spacing={0.5}>
      <Typography variant="m3" sx={{ overflowWrap: 'anywhere' }}>{nameOf(item.target)}</Typography>
      {item.status === 'ready' ? <>
        {item.plan.changes.map((change) => <Typography key={change.skillId} variant="s1">{change.mount} · {change.change} · {change.before ? `v${change.before.version}` : 'absent'} → {change.after ? `v${change.after.version}` : 'removed'}</Typography>)}
        {!item.plan.changes.length && <Typography variant="s1">No dependency change.</Typography>}
        <Typography variant="s3" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>Compiled candidate {item.plan.candidateDigest}</Typography>
        {item.plan.checks?.summary?.errors > 0 && <Alert severity="error">Blocking checks prevent this context from being updated. The others are not affected.</Alert>}
      </> : <Alert severity="warning">{explain(item)}</Alert>}
    </Stack>)}
    {phase === 'previewed' && failed.length > 0 && ready.length === 0 && <Typography variant="s1">No context can be updated from this preview.</Typography>}
    {(phase === 'previewed' || phase === 'applying') && ready.length > 0 && <>
      <FormControlLabel control={<Checkbox checked={accepted} disabled={busy} onChange={(event) => setAccepted(event.target.checked)} />} label={`Accept these exact resolved versions and selector policies for all ${ready.length} ${ready.length === 1 ? 'context' : 'contexts'}.`} />
      <Button variant="contained" disabled={!accepted || busy} onClick={apply}>{`Apply selected updates to ${ready.length} context ${ready.length === 1 ? 'draft' : 'drafts'}`}</Button>
    </>}
    {done.length > 0 && <Stack spacing={0.5}>
      {done.map((item) => <Stack key={item.target.contextId} direction="row" spacing={1} alignItems="center">
        <StatusBadge status="completed" label="Draft updated" />
        <Typography variant="s1" sx={{ overflowWrap: 'anywhere' }}>{nameOf(item.target)} · draft revision {item.revision}</Typography>
        <Button component={RouterLink} to={paths.dashboard.skills.contextEdit(item.target.contextId)} aria-label={`Open draft for ${item.target.name}`}>Open draft</Button>
      </Stack>)}
    </Stack>}
    {phase === 'applied' && outcomes.map((item) => <Alert severity="warning" key={item.target.contextId}>{nameOf(item.target)}: {explain(item)}</Alert>)}
    {(phase === 'applied' || phase === 'previewed') && failed.length > 0 && <Button variant="outlined" disabled={busy} onClick={() => preview(failed.map((item) => item.target))}>{`Preview again for ${failed.length} ${failed.length === 1 ? 'context' : 'contexts'} that did not update`}</Button>}
  </Stack>;
}
ContextFollowerUpdate.propTypes = { skillId: PropTypes.string.isRequired, targets: PropTypes.arrayOf(PropTypes.shape({ contextId: PropTypes.string.isRequired, name: PropTypes.string.isRequired, version: PropTypes.number.isRequired })).isRequired };
