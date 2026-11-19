import { useState } from 'react';
import PropTypes from 'prop-types';
import { Button, Stack, Typography } from '@mui/material';
import { loadAcceptedDependencyComparison, useContextInspection, useContextInspectionFile } from 'src/api/skills/context-production';
import { contextParts } from 'src/api/skills/context-authoring';
import { useSkillV11Query } from 'src/api/skills/testing';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
import TechnicalDetails from '../components/TechnicalDetails';
import TextDiff from '../components/TextDiff';
import { mountName } from '../utils/conclusions';

// Intent: see exactly what one published version changes compared with another, in place on Versions.
// Hierarchy: one lead sentence, linked-skill moves, then files as Added, Removed and Changed
// (unchanged files fold away); a file opens as a side-by-side diff of both versions' exact bytes.
// Palette/depth/surfaces: one panel section with flat dividers; no status colors, the group names carry the meaning.
// Typography/spacing: m3/s1/s3 and 12px groups.
const GENERATED = '.futureagi/context.json';
const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
const fileLabel = (path) => (path === GENERATED ? 'Context settings (generated)' : path);

// Compare the two manifests by the full sha256 of each path.
function compareManifests(before = [], after = []) {
  const old = new Map(before.map((file) => [file.path, file]));
  const next = new Map(after.map((file) => [file.path, file]));
  const byPath = (a, b) => a.path.localeCompare(b.path);
  return {
    added: after.filter((file) => !old.has(file.path)).sort(byPath),
    removed: before.filter((file) => !next.has(file.path)).sort(byPath),
    changed: after.filter((file) => old.has(file.path) && old.get(file.path).sha256 !== file.sha256).sort(byPath),
    same: after.filter((file) => old.has(file.path) && old.get(file.path).sha256 === file.sha256).sort(byPath),
  };
}

function FileDiff({ contextId, path, from, to, hasBefore, hasAfter }) {
  // A side that doesn't have the file never loads it (the hook stays disabled without a path).
  const before = useContextInspectionFile(contextId, from.number, hasBefore ? path : null, hasBefore ? from.id : null, from.digest);
  const after = useContextInspectionFile(contextId, to.number, hasAfter ? path : null, hasAfter ? to.id : null, to.digest);
  const loading = (hasBefore && before.isLoading) || (hasAfter && after.isLoading);
  const failed = (hasBefore && before.isError) || (hasAfter && after.isError);
  if (loading) return <ContentSkeleton variant="panel" />;
  if (failed) return <ErrorState title="Couldn't load this file." detail="Check your connection and try again." onRetry={() => { if (before.isError) before.refetch(); if (after.isError) after.refetch(); }} />;
  return <TextDiff fromText={before.data?.text ?? ''} toText={after.data?.text ?? ''} fromTitle={`v${from.number}`} toTitle={`v${to.number}`} fromMissing={!hasBefore} toMissing={!hasAfter} />;
}
FileDiff.propTypes = { contextId: PropTypes.string.isRequired, path: PropTypes.string.isRequired, from: PropTypes.object.isRequired, to: PropTypes.object.isRequired, hasBefore: PropTypes.bool, hasAfter: PropTypes.bool };

function Group({ title, files, selected, onSelect }) {
  if (!files.length) return null;
  return <Stack spacing={0.5} role="group" aria-label={`${title} files`}>
    <Typography variant="s3" color="text.secondary">{title} ({files.length})</Typography>
    {files.map((file) => <Button key={file.path} variant="text" color="inherit" size="small" aria-pressed={selected === file.path} title={file.path} onClick={() => onSelect(selected === file.path ? null : file.path)} sx={{ justifyContent: 'flex-start', textAlign: 'left', overflowWrap: 'anywhere', ...(selected === file.path ? { bgcolor: 'action.selected' } : {}) }}>{fileLabel(file.path)}</Button>)}
  </Stack>;
}
Group.propTypes = { title: PropTypes.string.isRequired, files: PropTypes.array.isRequired, selected: PropTypes.string, onSelect: PropTypes.func.isRequired };

export default function ContextVersionCompare({ contextId, from, to, onClose }) {
  const [selected, setSelected] = useState(null);
  const older = useContextInspection(contextId, from);
  const newer = useContextInspection(contextId, to);
  // The loader fences the newer version by digest, taken from that version's own inspection.
  const digest = newer.data?.version.digest;
  const links = useSkillV11Query({ parts: contextParts(contextId, 'compare-links', from, to, digest), load: (config) => loadAcceptedDependencyComparison(contextId, from, to, digest, config), enabled: Boolean(digest && older.data) });
  const close = <Button variant="text" size="small" onClick={onClose}>Close compare</Button>;
  if ((!older.data && older.isLoading) || (!newer.data && newer.isLoading)) return <Section variant="panel" title={`Compare v${from} and v${to}`} actions={close}><ContentSkeleton variant="panel" /></Section>;
  if (older.isError || newer.isError || !older.data || !newer.data) return <Section variant="panel" title={`Compare v${from} and v${to}`} actions={close}><ErrorState title="Couldn't load the comparison." detail="One of the two versions didn't load. Check your connection and try again." onRetry={() => { older.refetch(); newer.refetch(); }} /></Section>;
  const files = compareManifests(older.data.files, newer.data.files);
  const changedFiles = files.added.length + files.removed.length + files.changed.length;
  const linkRows = links.data || [];
  const linksSettled = Boolean(links.data) && !links.isError;
  const lead = linksSettled
    ? (linkRows.length || changedFiles ? `v${to} changes ${plural(linkRows.length, 'linked skill', 'linked skills')} and ${plural(changedFiles, 'file', 'files')} compared with v${from}.` : `v${to} has no changes compared with v${from}.`)
    : links.isError ? `v${to} changes ${plural(changedFiles, 'file', 'files')} compared with v${from}.` : null;
  const status = (path) => ({ hasBefore: !files.added.some((file) => file.path === path), hasAfter: !files.removed.some((file) => file.path === path) });
  const open = selected && [...files.added, ...files.removed, ...files.changed].some((file) => file.path === selected) ? selected : null;
  return <Section variant="panel" title={`Compare v${from} and v${to}`} actions={close}>
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      {lead ? <Typography variant="m3" role="status">{lead}</Typography> : <ContentSkeleton variant="panel" />}
      <Stack spacing={1} aria-label="Linked skill changes" role="group">
        <Typography variant="m3">Linked skills</Typography>
        {links.isError ? <ErrorState title="Couldn't load the linked-skill comparison." onRetry={links.refetch} />
          : !links.data ? <ContentSkeleton variant="panel" />
            : !linkRows.length ? <Typography variant="s1" color="text.secondary">No linked-skill changes.</Typography>
              : linkRows.map((row) => <Typography key={row.mount} variant="s1" sx={{ overflowWrap: 'anywhere' }}>
                {row.before && row.after ? `${mountName(row.mount)} v${row.before.version} → v${row.after.version}` : row.after ? `${mountName(row.mount)} v${row.after.version} added` : `${mountName(row.mount)} v${row.before.version} removed`}
              </Typography>)}
      </Stack>
      <Stack spacing={1} aria-label="File comparison" role="group">
        <Typography variant="m3">Files</Typography>
        {changedFiles === 0 && <Typography variant="s1" color="text.secondary">No file changes.</Typography>}
        <Group title="Added" files={files.added} selected={open} onSelect={setSelected} />
        <Group title="Removed" files={files.removed} selected={open} onSelect={setSelected} />
        <Group title="Changed" files={files.changed} selected={open} onSelect={setSelected} />
        {files.same.length > 0 && <TechnicalDetails title={`Show ${plural(files.same.length, 'unchanged file', 'unchanged files')}`}>
          <Stack spacing={0.5}>{files.same.map((file) => <Typography key={file.path} variant="s3" color="text.secondary" title={file.path} sx={{ overflowWrap: 'anywhere' }}>{fileLabel(file.path)}</Typography>)}</Stack>
        </TechnicalDetails>}
      </Stack>
      {open && <Stack spacing={1} sx={{ minWidth: 0 }}>
        <Typography variant="m3" sx={{ overflowWrap: 'anywhere' }}>{fileLabel(open)}</Typography>
        <FileDiff key={open} contextId={contextId} path={open} from={older.data.version} to={newer.data.version} {...status(open)} />
      </Stack>}
    </Stack>
  </Section>;
}
ContextVersionCompare.propTypes = { contextId: PropTypes.string.isRequired, from: PropTypes.number.isRequired, to: PropTypes.number.isRequired, onClose: PropTypes.func.isRequired };
