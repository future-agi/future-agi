import { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Button, Checkbox, FormControlLabel, Stack, Typography } from '@mui/material';
import { useFollowerUpdates } from 'src/api/skills/context-follower-update';
import ActionReason, { reasonProps } from '../components/ActionReason';
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
const mountName = (change) => change.mount?.replace(/^skills\//, '').replace(/\/$/, '');
const explain = (item) => reasons[item.code] || (item.httpStatus === 409 ? 'The plan no longer matches the current upstream or draft. Preview again.' : `Not confirmed (${item.code}).`);

// The card leads with the change itself, so the draft is never described as both started and about to change.
function ReadyCard({ item }) {
  const changed = item.plan.changes.filter((change) => change.change !== 'unchanged');
  const unchanged = item.plan.changes.filter((change) => change.change === 'unchanged');
  const summary = changed.map((change) => `${mountName(change)} ${change.before ? `v${change.before.version}` : 'not linked'} → ${change.after ? `v${change.after.version}` : 'removed'}`).join('; ') || 'the reviewed changes';
  const notes = [unchanged.length > 0 && `${unchanged.length} other linked ${unchanged.length === 1 ? 'skill' : 'skills'} unchanged`, item.reusedDraft === true && `Your existing draft for ${item.target.name} is kept`].filter(Boolean);
  return <>
    <Typography variant="m3" sx={{ overflowWrap: 'anywhere' }}>{item.target.name}: {summary} · {item.target.productionVersion != null ? `Production stays v${item.target.productionVersion}` : 'Production stays unchanged'}</Typography>
    {notes.length > 0 && <Typography variant="s3" color="text.secondary">{notes.join(' · ')}.</Typography>}
    <TechnicalDetails title="Technical details">
      <IdentityChip value={item.plan.candidateDigest} label="Candidate digest" />
      <IdentityChip value={item.plan.fingerprint} label="Plan fingerprint" />
      {unchanged.map((change) => <Typography key={change.skillId} variant="s3">{change.mount} · No change · v{change.after?.version ?? change.before?.version}</Typography>)}
    </TechnicalDetails>
    {item.plan.checks?.summary?.errors > 0 && <Alert severity="error">Blocking checks prevent this context from being updated. The others are not affected.</Alert>}
  </>;
}
ReadyCard.propTypes = { item: PropTypes.object.isRequired };

export default function ContextFollowerUpdate({ skillId, targets, sourceRelease, skillName, onApplied, impacts = [] }) {
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
      setItems([]); setOutcomes([]); setAccepted(false); setPreparedKey(null); setSelectionChanged(targets.length > 0);
      setPhase((current) => current === 'applying' ? current : 'idle');
    }
  }, [selectionKey, preparedKey, targets.length]);
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
    onApplied?.(results.filter((item) => item.status === 'applied').map((item) => item.target.contextId));
    setPhase('applied');
  };
  const failed = [...items.filter((item) => item.status === 'error'), ...outcomes];
  // After every selected context has been updated, preparing again would only restart the same drafts; it waits
  // for a changed selection. A failed or partial apply keeps Prepare available.
  const alreadyApplied = phase === 'applied' && failed.length === 0 && done.length > 0 && preparedKey === selectionKey;
  const prepareLabel = targets.length ? `Prepare draft updates for ${targets.length} ${targets.length === 1 ? 'context' : 'contexts'}` : 'Prepare draft updates';
  const nameOf = (target) => `${target.name} v${target.version}`;
  // Before Prepare the effect is already known from the dependents rows: show it per selected context so the click is informed.
  // The server plan card replaces these lines once it exists.
  const impactLines = alreadyApplied || phase === 'previewing' || items.length > 0 ? [] : targets.flatMap((target) => {
    const impact = impacts.find((item) => item.contextId === target.contextId);
    if (impact?.from == null || impact?.to == null) return [];
    return [{ key: target.contextId, text: `${target.name}: ${skillName || 'this skill'} v${impact.from} → v${impact.to} · ${target.productionVersion != null ? `Production stays v${target.productionVersion}` : 'Production stays unchanged'}` }];
  });
  return <Stack spacing={1.5} sx={{ pb: 8, minWidth: 0 }} aria-label="Update follower contexts" role="group">
    <Typography variant="m3" component="h3">Update contexts that follow production</Typography>
    <Typography variant="s1">Review the skill changes for each selected context, then accept them into drafts.</Typography>
    {!targets.length && <Typography variant="s3" color="text.secondary">Select at least one context to prepare updates.</Typography>}
    {selectionChanged && targets.length > 0 && <><Typography variant="s3" color="warning.main" role="status">Selection changed — prepare again.</Typography><Button variant="outlined" disabled sx={{ alignSelf: 'flex-start' }}>Apply selected updates</Button></>}
    {impactLines.length > 0 && <Stack component="ul" spacing={0.5} aria-label="What each update changes" sx={{ m: 0, p: 0, listStyle: 'none' }}>{impactLines.map((line) => <Typography key={line.key} component="li" variant="s1" sx={{ overflowWrap: 'anywhere' }}>{line.text}</Typography>)}</Stack>}
    {!items.length && !alreadyApplied && phase !== 'previewing' && <Typography variant="s3" color="text.secondary">Preparing starts a draft for each selected context. Production doesn&apos;t change.</Typography>}
    <Stack direction="row" useFlexGap spacing={1.5} alignItems="center" sx={{ flexWrap: 'wrap' }}>
      <Button color="primary" variant={ready.length || done.length ? 'outlined' : 'contained'} disabled={!targets.length || busy || alreadyApplied} {...reasonProps('follower-prepare-reason', alreadyApplied ? 'Already prepared for this selection.' : '')} onClick={() => preview(targets)}>{prepareLabel}</Button>
      <ActionReason id="follower-prepare-reason" reason={alreadyApplied ? 'Already prepared for this selection.' : ''} sx={{ flexBasis: 'auto' }} />
    </Stack>
    {phase === 'previewing' && <Typography variant="s3" role="status">Starting drafts and checking versions…</Typography>}
    {items.map((item) => <Stack key={item.target.contextId} sx={{ border: 1, borderRadius: 1, borderColor: 'divider', p: 1.5, minWidth: 0 }} spacing={0.5}>
      {item.status !== 'ready' && <Typography variant="m3">{item.target.name}</Typography>}
      {item.status === 'ready' ? <ReadyCard item={item} /> : <Alert severity="warning">{explain(item)}</Alert>}
    </Stack>)}
    {phase === 'previewed' && failed.length > 0 && ready.length === 0 && <Typography variant="s1">No context can be updated from this preview.</Typography>}
    {(phase === 'previewed' || phase === 'applying') && ready.length > 0 && <>
      <FormControlLabel control={<Checkbox checked={accepted} disabled={busy} onChange={(event) => setAccepted(event.target.checked)} />} label={`Accept these linked-skill versions and update rules for ${ready.length} ${ready.length === 1 ? 'context' : 'contexts'}.`} />
      <Button color="primary" sx={{ alignSelf: 'flex-start' }} variant="contained" disabled={!accepted || busy} onClick={apply}>{`Apply selected updates to ${ready.length} context ${ready.length === 1 ? 'draft' : 'drafts'}`}</Button>
    </>}
    {done.length > 0 && <Alert severity="success" role="status"><Stack spacing={1}>
      {done.map((item, index) => {
        const version = item.lock?.links?.find((link) => link.skillId === skillId)?.version;
        return <Stack key={item.target.contextId} direction="row" useFlexGap spacing={1} alignItems="center" sx={{ flexWrap: 'wrap' }}>
          <Typography variant="s1" sx={{ overflowWrap: 'anywhere' }}>{item.target.name} draft {skillName && version != null ? `now uses ${skillName} v${version}` : 'updated'}. Production unchanged.</Typography>
          <Button color="primary" variant={ready.length || index > 0 ? 'outlined' : 'contained'} component={RouterLink} to={paths.dashboard.skills.contextReview(item.target.contextId)}>Review {item.target.name} draft</Button>
        </Stack>;
      })}
    </Stack></Alert>}
    {phase === 'applied' && outcomes.map((item) => <Alert severity="warning" key={item.target.contextId}>{nameOf(item.target)}: {explain(item)}</Alert>)}
    {(phase === 'applied' || phase === 'previewed') && failed.length > 0 && <Button variant="outlined" disabled={busy} onClick={() => preview(failed.map((item) => item.target))}>{`Preview again for ${failed.length} ${failed.length === 1 ? 'context' : 'contexts'} that did not update`}</Button>}
  </Stack>;
}
ContextFollowerUpdate.propTypes = { impacts: PropTypes.arrayOf(PropTypes.shape({ contextId: PropTypes.string.isRequired, from: PropTypes.number, to: PropTypes.number })), skillName: PropTypes.string, onApplied: PropTypes.func, sourceRelease: PropTypes.string, skillId: PropTypes.string.isRequired, targets: PropTypes.arrayOf(PropTypes.shape({ contextId: PropTypes.string.isRequired, name: PropTypes.string.isRequired, version: PropTypes.number.isRequired })).isRequired };
