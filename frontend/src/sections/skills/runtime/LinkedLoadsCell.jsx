import { useMemo } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Link, Skeleton, Stack, Tooltip, Typography } from '@mui/material';
import { paths } from 'src/routes/paths';
import { useContextLinkLoads } from 'src/api/skills/runtime';
import { runtimeTabUrl } from 'src/sections/skills/utils/traces';
import {
  CANT_RECORD_YET, COULDNT_LOAD, LOADED_7_DAYS, LOADED_NOT_LINKED, NO_LOADS_RECORDED, formatCount,
  libraryMoveText, loadedAsOther, tracesCount, versionName,
} from './runtimeCopy';

/**
 * The Linked skills "Loaded (7 days)" cell (SKILL-TRACES §7.4, UX-09), from
 * endpoint 4 `links_summary` (always the last 7 days): "1,204 traces", "No
 * loads recorded" linking to the Runtime tab where the diagnosis is, or
 * "Loaded as v1; this context links v2".
 */
export default function LinkedLoadsCell({ entry, contextId, loading = false, error = false }) {
  if (loading) return <Skeleton variant="text" width={64} aria-label="Loading loads" />;
  if (error) return <Tooltip title={COULDNT_LOAD}><Box component="span" sx={{ color: 'text.secondary' }}>—</Box></Tooltip>;
  if (!entry) return <Box component="span" sx={{ color: 'text.secondary' }}>—</Box>;
  const loaded = (entry.loadedVersions || []).filter((version) => version !== entry.linkedVersion);
  if (!entry.traces7d) {
    return (
      <Link component={RouterLink} to={runtimeTabUrl({ kind: 'agent_context', id: contextId }) || '#'} variant="s3" color="text.secondary" underline="hover" onClick={(event) => event.stopPropagation()}>
        {NO_LOADS_RECORDED}
      </Link>
    );
  }
  if (loaded.length && !(entry.loadedVersions || []).includes(entry.linkedVersion)) {
    return <Typography component="span" variant="s3">{loadedAsOther({ loaded, linked: entry.linkedVersion })}</Typography>;
  }
  return <Typography component="span" variant="s3" sx={{ fontVariantNumeric: 'tabular-nums' }}>{tracesCount(entry.traces7d)}</Typography>;
}
LinkedLoadsCell.propTypes = {
  entry: PropTypes.shape({ skillId: PropTypes.string, linkedVersion: PropTypes.number, traces7d: PropTypes.number, loadedVersions: PropTypes.array }),
  contextId: PropTypes.string.isRequired,
  loading: PropTypes.bool,
  error: PropTypes.bool,
};

/**
 * The `extraColumn` for ContextLinksPanel (UXF5 commit 2, read-only mode):
 *   const loads = useLinkedLoadsColumn(context.id, viewedVersionId);
 *   <ContextLinksPanel extraColumn={loads.column} … />
 *   <LoadedNotLinkedList items={loads.loadedNotLinked} />
 * Pass `enabled = skillTraces flag` so nothing is read with the flag off.
 */
export function useLinkedLoadsColumn(contextId, versionId, { enabled = true } = {}) {
  const loads = useContextLinkLoads(contextId, versionId, { enabled: Boolean(contextId) && enabled });
  const { isPending, isError, bySkillId, loadedNotLinked } = loads;
  const column = useMemo(() => ({
    header: LOADED_7_DAYS,
    cell: ({ link }) => (
      <LinkedLoadsCell entry={bySkillId[String(link?.skillId)]} contextId={contextId} loading={isPending} error={isError} />
    ),
  }), [bySkillId, contextId, isPending, isError]);
  return { column, loadedNotLinked, isPending, isError };
}

/** "Loaded but not linked here": loads of skills outside the context's links (§7.4). */
export function LoadedNotLinkedList({ items = [] }) {
  if (!items.length) return null;
  return (
    <Stack spacing={0.5} data-section="loaded-not-linked">
      <Typography variant="s1" sx={{ fontWeight: 500 }}>{LOADED_NOT_LINKED}</Typography>
      <Stack component="ul" spacing={0.25} sx={{ m: 0, pl: 2.5 }}>
        {items.map((item) => (
          <Typography component="li" variant="s3" key={`${item.skillId}:${item.versionId ?? item.version}`}>
            {item.skillId ? <Link component={RouterLink} to={`${paths.dashboard.skills.detail(item.skillId)}?tab=runtime`} underline="hover">{item.name}</Link> : item.name}
            {item.version != null ? ` ${versionName(item.version)}` : ''}
            {item.traces != null ? ` · ${tracesCount(item.traces)}` : ''}
          </Typography>
        ))}
      </Stack>
    </Stack>
  );
}
LoadedNotLinkedList.propTypes = { items: PropTypes.array };

/**
 * The library "Loaded (7 days)" cell (§7.3, UX-01, UX-16), from the list
 * serializer's `runtime` field: the trace count; "—" with "Can't record
 * loads yet" when `loaded_7d` is null; "0" when consumers can record but
 * none arrived; plus the short move state when production moved in the last
 * 7 days. UXF5 commit 2 renders it in the library column.
 */
export function LibraryLoadsCell({ runtime, loading = false, error = false }) {
  if (loading) return <Box component="span" sx={{ color: 'text.secondary' }}>…</Box>;
  if (error) return <Tooltip title={COULDNT_LOAD}><Box component="span" sx={{ color: 'text.secondary' }}>—</Box></Tooltip>;
  if (!runtime) return <Box component="span" sx={{ color: 'text.secondary' }}>—</Box>;
  const loaded = runtime.loaded7d ?? runtime.loaded_7d;
  const move = runtime.move ? libraryMoveText({
    verdict: runtime.move.verdict,
    toVersion: runtime.move.toVersion ?? runtime.move.to_version,
    metric: runtime.move.metric,
    evalName: runtime.move.name,
  }) : null;
  return (
    <Stack component="span" direction="row" spacing={0.75} alignItems="baseline" useFlexGap sx={{ flexWrap: 'wrap' }}>
      {loaded === null || loaded === undefined
        ? <Tooltip title={CANT_RECORD_YET}><Box component="span" sx={{ color: 'text.secondary' }} aria-label={CANT_RECORD_YET}>—</Box></Tooltip>
        : <Box component="span" sx={{ fontVariantNumeric: 'tabular-nums' }}>{formatCount(loaded)}</Box>}
      {move && (
        <Typography component="span" variant="s3" color={runtime.move.verdict === 'watching' ? 'text.secondary' : 'warning.main'}>{move}</Typography>
      )}
    </Stack>
  );
}
LibraryLoadsCell.propTypes = { runtime: PropTypes.object, loading: PropTypes.bool, error: PropTypes.bool };
