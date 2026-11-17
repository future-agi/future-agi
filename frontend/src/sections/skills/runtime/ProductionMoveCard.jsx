import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Button, Card, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { enqueueSnackbar } from 'notistack';
import ContentSkeleton from 'src/components/content-skeleton';
import StatusBadge from 'src/components/status-badge';
import { paths } from 'src/routes/paths';
import { runtimeErrorKind, useSkillComparison, useSkillRuntimeSeries } from 'src/api/skills/runtime';
import { stackedTableSx } from 'src/sections/skills/components/responsiveTable';
import { buildSkillTraceLink, runtimeTabUrl } from 'src/sections/skills/utils/traces';
import { AgentName } from './LoadedByTable';
import { moveTiming } from './MoveStateChip';
import { RuntimeQueryState, TraceNumber } from './RuntimeParts';
import {
  ADOPTION, AFTER, ALL_AGENTS, BEFORE, BY_AGENT, CANT_OPEN_PROJECT, COPY_FAILED, COPY_LINK, LINK_COPIED,
  MOVE_CARD_ERROR, MOVE_MARKER, OPEN_CONTEXT, SAME_AGENTS, SERIES_TITLE, VERDICT_TEXT, beforeCut, collecting,
  controlLine, deltaText, formatCost, formatCount, formatMs, formatPercent, lowSampleCount, metricPhrase,
  moveTitle, notMovedText, reviewFailed, rollBackTo, sameAgentsLead, soFar,
} from './runtimeCopy';

// The move card (SKILL-TRACES §7.3, §6.6 endpoint 3, UX-05): what happened
// after a production label move, on the same agents first. Every comparison
// is an association ("after the move"), never a cause.
//
// Expected response (camelCased by runtime.js; ST8 owns the final shape):
//   { eventId, label, fromVersion, toVersion, movedAt, endsAt, complete,
//     beforeCutAt, verdict, trigger: {metric, name?, agents, before: [..], after: [..]},
//     soFar, collecting: {hoursElapsed, hoursTotal, tracesSoFar} | null,
//     sameAgents: {agents, metrics: [Metric]}, control: {version, agents, metrics} | null,
//     allAgents: {metrics: [Metric]}, byAgent: [{agent, metrics: [Metric]}],
//     adoption: [{agent, share, notMovedReason, cached, context}],
//     links: {before: {<metric>: link}, after: {<metric>: link}}, primaryAction }
//   Metric = {metric, name?, before, after, delta, lowSample, beforeTraces,
//             afterTraces, beforeCount?, afterCount?}
// `trigger.before`/`after` are per-agent lists and are never averaged here.

const KIND = { failed_requests: 'failed', latency_p95: 'slowest', traces: 'all', cost_per_trace: 'all' };
const linkKind = (metric) => (String(metric).startsWith('eval:') ? 'eval_failed' : KIND[metric] || 'all');

export function formatMetric(metric, value) {
  if (value === null || value === undefined) return '—';
  if (metric === 'failed_requests' || String(metric).startsWith('eval:')) return formatPercent(value);
  if (metric === 'latency_p95') return formatMs(value);
  if (metric === 'cost_per_trace') return formatCost(value);
  return formatCount(value);
}

/** Metrics with the triggering (worse) metric first (UX-05 "the worse number first"). */
export function orderMetrics(metrics = [], trigger) {
  const rank = (item) => (item.metric === trigger?.metric ? 0 : item.metric === 'failed_requests' ? 1 : item.metric === 'latency_p95' ? 2 : 3);
  return [...metrics].sort((a, b) => rank(a) - rank(b));
}

async function copy(href) {
  try {
    await navigator.clipboard.writeText(href);
    enqueueSnackbar(LINK_COPIED);
  } catch {
    enqueueSnackbar(COPY_FAILED, { variant: 'warning' });
  }
}

function sideValue({ item, side, links, showLinks }) {
  const value = item[side];
  const traces = item[`${side}Traces`];
  const count = item[`${side}Count`];
  const low = Boolean(item.lowSample);
  const text = low && item.metric === 'failed_requests' && count != null ? lowSampleCount(count, traces ?? 0) : formatMetric(item.metric, value);
  const link = showLinks ? links?.[side]?.[item.metric] : null;
  return <TraceNumber link={link} kind={linkKind(item.metric)} lowSample={low} traces={traces} what={`${metricPhrase(item.metric, item.name)} ${side}`}>{text}</TraceNumber>;
}

/** before → after, with a delta only when the sample rule is met (UX-16). */
function MetricRows({ metrics, trigger, links, showDeltas, soFarText, showLinks = true, label }) {
  if (!metrics?.length) return null;
  return (
    <Table size="small" aria-label={label} sx={stackedTableSx}>
      <TableHead>
        <TableRow>
          <TableCell>{label}</TableCell>
          <TableCell align="right">{BEFORE}</TableCell>
          <TableCell align="right">{AFTER}</TableCell>
          <TableCell align="right">Change</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {orderMetrics(metrics, trigger).map((item) => {
          const delta = showDeltas && !item.lowSample ? deltaText(item.delta, item.metric) : null;
          return (
            <TableRow key={item.metric} data-metric={item.metric}>
              <TableCell>{metricPhrase(item.metric, item.name).replace(/^./, (c) => c.toUpperCase())}</TableCell>
              <TableCell align="right">{sideValue({ item, side: 'before', links, showLinks })}</TableCell>
              <TableCell align="right">{sideValue({ item, side: 'after', links, showLinks })}</TableCell>
              <TableCell align="right">
                {delta ? <Typography component="span" variant="s3" color="text.secondary">{delta}{soFarText ? ` · ${soFarText}` : ''}</Typography> : <Typography component="span" variant="s3" color="text.secondary">—</Typography>}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
MetricRows.propTypes = { metrics: PropTypes.array, trigger: PropTypes.object, links: PropTypes.object, showDeltas: PropTypes.bool, soFarText: PropTypes.string, showLinks: PropTypes.bool, label: PropTypes.string.isRequired };

/** A small per-version series with the move marker (endpoint 2). Theme tokens only. */
export function MoveSeries({ series, movedAt, metric }) {
  const lines = (series?.series || []).filter((line) => line.points?.length);
  if (!lines.length) return null;
  const points = lines.flatMap((line) => line.points);
  const times = points.map((point) => new Date(point.t).getTime());
  const values = points.map((point) => Number(point.value) || 0);
  const [t0, t1] = [Math.min(...times), Math.max(...times)];
  const vMax = Math.max(...values, 0) || 1;
  const x = (t) => (t1 === t0 ? 0 : ((new Date(t).getTime() - t0) / (t1 - t0)) * 300);
  const y = (v) => 60 - (Number(v) / vMax) * 56;
  const move = movedAt ? x(movedAt) : null;
  return (
    <Stack spacing={0.5}>
      <Typography variant="s3" color="text.secondary">{SERIES_TITLE(metric)}</Typography>
      <Box component="svg" viewBox="0 0 300 64" role="img" aria-label={SERIES_TITLE(metric)} sx={{ width: 1, maxWidth: 480, height: 64 }}>
        {lines.map((line, index) => (
          <Box component="polyline" key={line.versionId} fill="none" strokeWidth={1.5}
            sx={{ stroke: (theme) => (index === 0 ? theme.palette.primary.main : theme.palette.text.secondary) }}
            points={line.points.map((point) => `${x(point.t).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ')} />
        ))}
        {move !== null && move >= 0 && move <= 300 && (
          <Box component="line" x1={move} x2={move} y1={0} y2={64} strokeDasharray="3 3" sx={{ stroke: (theme) => theme.palette.text.primary }}>
            <title>{MOVE_MARKER}</title>
          </Box>
        )}
      </Box>
      <Stack direction="row" spacing={1.5} useFlexGap sx={{ flexWrap: 'wrap' }}>
        {lines.map((line, index) => <Typography key={line.versionId} variant="s3" color={index === 0 ? 'primary' : 'text.secondary'}>v{line.version}</Typography>)}
      </Stack>
    </Stack>
  );
}
MoveSeries.propTypes = { series: PropTypes.object, movedAt: PropTypes.string, metric: PropTypes.string };

/**
 * The card body, shared by the skill move card and the context release card
 * (`ContextReleaseCard`). `title` overrides the skill wording.
 */
export function MoveCardBody({ data, subjectKind = 'skill', subjectId, title, onRollback, windowHours, children, now = Date.now() }) {
  const timing = moveTiming(data, now);
  const hours = windowHours ?? Math.round(timing.hoursTotal);
  const isCollecting = Boolean(data.collecting) && !data.complete && data.soFar !== true;
  const soFarText = !data.complete && data.soFar ? soFar(timing) : null;
  const showDeltas = !isCollecting;
  const worse = data.verdict === 'worse' || data.verdict === 'worse_so_far' || data.primaryAction === 'review_failed';
  const failedAfter = [...(data.allAgents?.metrics || []), ...(data.sameAgents?.metrics || [])].find((item) => item.metric === 'failed_requests');
  const reviewCount = failedAfter?.afterCount ?? 0;
  const reviewLink = data.links?.after?.failed_requests;
  const reviewHref = reviewLink ? buildSkillTraceLink({ link: reviewLink, kind: 'failed' }) : null;
  const cardUrl = runtimeTabUrl({ kind: subjectKind, id: subjectId, eventId: data.eventId, window: data.window || `${hours}h`, absolute: true });
  const detail = subjectKind === 'agent_context' ? paths.dashboard.skills.contextDetail(subjectId) : paths.dashboard.skills.detail(subjectId);

  return (
    <Stack spacing={2}>
      <Stack spacing={0.5}>
        <Typography variant="m3" component="h3">{title || moveTitle({ label: data.label || 'production', fromVersion: data.fromVersion, toVersion: data.toVersion, windowHours: hours })}</Typography>
        {data.beforeCutAt && <Typography variant="s3" color="text.secondary">{beforeCut({ toVersion: data.fromVersion, date: new Date(data.beforeCutAt).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) })}</Typography>}
        <Stack direction="row" spacing={1} alignItems="center" useFlexGap sx={{ flexWrap: 'wrap' }}>
          <StatusBadge status={data.verdict} role={worse ? 'warning' : data.verdict === 'better' ? 'success' : 'neutral'} variant="badge" label={VERDICT_TEXT[data.verdict] || VERDICT_TEXT.watching} />
          {isCollecting && <Typography variant="s3" color="text.secondary" data-line="collecting">{collecting({ ...timing, traces: data.collecting?.tracesSoFar })}</Typography>}
          {soFarText && <Typography variant="s3" color="text.secondary" data-line="so-far">{soFarText}</Typography>}
        </Stack>
      </Stack>

      {data.sameAgents && (
        <Stack spacing={0.5} data-section="same-agents">
          <Typography variant="s1" sx={{ fontWeight: 500 }}>{SAME_AGENTS}</Typography>
          {data.sameAgents.agents > 0 && <Typography variant="s3" color="text.secondary">{sameAgentsLead(data.sameAgents.agents)}</Typography>}
          <MetricRows label={SAME_AGENTS} metrics={data.sameAgents.metrics} trigger={data.trigger} links={data.links} showDeltas={showDeltas} soFarText={soFarText} />
        </Stack>
      )}

      {data.control && (
        <Stack spacing={0.5} data-section="control">
          <Typography variant="s1" sx={{ fontWeight: 500 }}>{controlLine({ version: data.control.version ?? data.fromVersion, agents: data.control.agents })}</Typography>
          <MetricRows label={controlLine({ version: data.control.version ?? data.fromVersion, agents: data.control.agents })} metrics={data.control.metrics} trigger={data.trigger} links={data.control.links} showDeltas={false} />
        </Stack>
      )}

      {data.allAgents && (
        <Stack spacing={0.5} data-section="all-agents">
          <Typography variant="s1" sx={{ fontWeight: 500 }}>{ALL_AGENTS}</Typography>
          <MetricRows label={ALL_AGENTS} metrics={data.allAgents.metrics} trigger={data.trigger} links={data.allAgents.links} showDeltas={showDeltas} soFarText={soFarText} />
        </Stack>
      )}

      {data.byAgent?.length > 0 && (
        <Stack spacing={1} data-section="by-agent">
          <Typography variant="s1" sx={{ fontWeight: 500 }}>{BY_AGENT}</Typography>
          {data.byAgent.map((row) => (
            <Stack key={JSON.stringify(row.agent?.key ?? row.agent?.name)} spacing={0.5}>
              <AgentName agent={row.agent} />
              <MetricRows label={row.agent?.name || 'Agent'} metrics={row.metrics} trigger={data.trigger} links={row.links} showDeltas={showDeltas} soFarText={soFarText} />
            </Stack>
          ))}
        </Stack>
      )}

      {data.adoption?.length > 0 && (
        <Stack spacing={0.5} data-section="adoption">
          <Typography variant="s1" sx={{ fontWeight: 500 }}>{ADOPTION}</Typography>
          <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 0, listStyle: 'none' }}>
            {data.adoption.map((row) => {
              const contextAction = ['pinned_in_context', 'waiting_for_context_release'].includes(row.notMovedReason) && row.context?.id;
              return (
                <Stack component="li" key={JSON.stringify(row.agent?.key ?? row.agent?.name)} direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 0.25, sm: 1.5 }} alignItems={{ sm: 'center' }} data-reason={row.notMovedReason}>
                  <Box sx={{ minWidth: 0, flex: '0 1 40%' }}><AgentName agent={row.agent} /></Box>
                  <Typography variant="s1">{notMovedText({ reason: row.notMovedReason, fromVersion: data.fromVersion, toVersion: data.toVersion, contextName: row.context?.name, cached: row.cached })}</Typography>
                  {contextAction && <Link component={RouterLink} to={paths.dashboard.skills.contextDetail(row.context.id)} variant="s3">{OPEN_CONTEXT}</Link>}
                </Stack>
              );
            })}
          </Stack>
        </Stack>
      )}

      {children}

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'center' }}>
        {worse && reviewHref && (
          <Button variant="contained" component={RouterLink} to={reviewHref} sx={{ width: { xs: 1, sm: 'auto' } }}>{reviewFailed(reviewCount)}</Button>
        )}
        {worse && data.fromVersion != null && (
          onRollback
            ? <Button variant="outlined" onClick={() => onRollback(data.fromVersion)} sx={{ width: { xs: 1, sm: 'auto' } }}>{rollBackTo(data.fromVersion)}</Button>
            : <Button variant="outlined" component={RouterLink} to={`${detail}?tab=${subjectKind === 'agent_context' ? 'recovery' : 'versions'}`} sx={{ width: { xs: 1, sm: 'auto' } }}>{rollBackTo(data.fromVersion)}</Button>
        )}
        {cardUrl && <Button color="inherit" onClick={() => copy(cardUrl)}>{COPY_LINK}</Button>}
      </Stack>
    </Stack>
  );
}
MoveCardBody.propTypes = {
  data: PropTypes.object.isRequired,
  subjectKind: PropTypes.oneOf(['skill', 'agent_context']),
  subjectId: PropTypes.string.isRequired,
  title: PropTypes.string,
  onRollback: PropTypes.func,
  windowHours: PropTypes.number,
  children: PropTypes.node,
  now: PropTypes.number,
};

/**
 * The production move card for a skill. `eventId` comes from the URL
 * (`?tab=runtime&move=<event_id>&window=24h`) or from the tab's latest move.
 * `onRollback(version)` opens the existing rollback flow; without it the
 * secondary action goes to the Versions tab.
 */
export default function ProductionMoveCard({ skillId, eventId, window: moveWindow = '24h', onRollback, projectIds = [] }) {
  const query = useSkillComparison(skillId, { eventId, window: moveWindow });
  const versions = query.data ? [query.data.fromVersionId, query.data.toVersionId].filter(Boolean) : [];
  const series = useSkillRuntimeSeries(skillId, {
    window: '7d',
    projectIds,
    versionIds: versions,
    metric: query.data?.trigger?.metric || 'failed_requests',
    enabled: Boolean(query.data && versions.length),
  });
  const errorKind = query.isError ? runtimeErrorKind(query.error) : null;
  return (
    <Card variant="outlined" sx={{ p: { xs: 2, sm: 2.5 } }} aria-label="Production move" data-card="move">
      {query.isPending && query.fetchStatus !== 'idle' ? (
        <ContentSkeleton variant="panel" label="Loading this move" />
      ) : query.isError ? (
        <RuntimeQueryState query={query} errorKind={errorKind} errorTitle={MOVE_CARD_ERROR} deniedText={CANT_OPEN_PROJECT} loadingLabel="Loading this move" compact />
      ) : query.data ? (
        <MoveCardBody data={query.data} subjectId={skillId} onRollback={onRollback}>
          {series.data && <MoveSeries series={series.data} movedAt={query.data.movedAt} metric={query.data.trigger?.metric || 'failed_requests'} />}
        </MoveCardBody>
      ) : null}
    </Card>
  );
}
ProductionMoveCard.propTypes = {
  skillId: PropTypes.string.isRequired,
  eventId: PropTypes.string,
  window: PropTypes.string,
  onRollback: PropTypes.func,
  projectIds: PropTypes.array,
};
