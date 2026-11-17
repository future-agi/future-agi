import { Fragment, useId, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import {
  Box, Button, Checkbox, Link, ListItemText, Menu, MenuItem, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Typography,
} from '@mui/material';
import { enqueueSnackbar } from 'notistack';
import Iconify from 'src/components/iconify';
import StatusBadge from 'src/components/status-badge';
import { paths } from 'src/routes/paths';
import { RowActionsCell } from 'src/sections/skills/components/RowActions';
import { rowLinkProps, stackedTableSx } from 'src/sections/skills/components/responsiveTable';
import { buildSkillTraceLink } from 'src/sections/skills/utils/traces';
import { NameIt } from './RuntimeDiagnosis';
import { TraceNumber, relativeFrom } from './RuntimeParts';
import {
  COLUMNS_LABEL, CONNECTED_APP_BADGE, COPY_FAILED, COPY_LINK, LINK_COPIED, LOADED_BY, METRIC,
  OPEN_SKILL_VERSION, PRODUCTION_ROW, VIEWING_ROW, askedForMix, evalCell, formatCost, formatCount,
  formatMs, formatPercent, lowSampleCount, plural, unnamedAgent, versionName, versionsWithNoLoads,
} from './runtimeCopy';

// The Loaded by table (SKILL-TRACES §7.3, UX-02, UX-03, UX-08, §15 D10).
// Version rows open to agent rows. Every number opens exactly the traces it
// counted. Rows are cohorts that overlap: there is no totals row and nothing
// is summed, so a version row shows its own server numbers or none.

const agentKey = (agent) => (Array.isArray(agent?.key) ? agent.key.join('\u0000') : JSON.stringify(agent?.key ?? agent?.name));
const evalKey = (config) => `eval:${config}`;

/** The eval the workspace runs most on these rows (by evaluated traces), or null. */
export function topEvalConfig(rows = []) {
  const totals = new Map();
  rows.forEach((row) => (row.evals || []).forEach((item) => {
    const current = totals.get(String(item.configId)) || { configId: String(item.configId), name: item.name, evaluated: 0 };
    current.evaluated += item.evaluated || 0;
    totals.set(current.configId, current);
  }));
  return [...totals.values()].sort((a, b) => b.evaluated - a.evaluated)[0] ?? null;
}
const allEvalConfigs = (rows = []) => {
  const seen = new Map();
  rows.forEach((row) => (row.evals || []).forEach((item) => seen.set(String(item.configId), item.name)));
  return [...seen.entries()].map(([configId, name]) => ({ configId, name }));
};

export const DEFAULT_COLUMNS = Object.freeze(['traces', 'failed', 'p95', 'cost', 'topEval', 'last']);
export const OPTIONAL_COLUMNS = Object.freeze([
  { key: 'p50', label: METRIC.p50 },
  { key: 'tokens', label: METRIC.tokens },
  { key: 'loadTime', label: METRIC.loadTime },
  { key: 'cached', label: METRIC.cachedShare },
  { key: 'scopeCost', label: METRIC.costAfterLoading },
  { key: 'askedFor', label: METRIC.askedFor },
]);

/** The column chooser: default columns stay; optional ones toggle. */
export function ColumnChooser({ extra, onChange, evalOptions = [] }) {
  const id = useId();
  const [anchor, setAnchor] = useState(null);
  const options = [...OPTIONAL_COLUMNS, ...evalOptions.map((item) => ({ key: evalKey(item.configId), label: item.name || 'Eval' }))];
  const toggle = (key) => onChange(extra.includes(key) ? extra.filter((value) => value !== key) : [...extra, key]);
  return (
    <>
      <Button size="small" color="inherit" startIcon={<Iconify icon="solar:settings-linear" width={16} />} aria-haspopup="menu" aria-controls={anchor ? id : undefined} onClick={(event) => setAnchor(event.currentTarget)}>
        {COLUMNS_LABEL}
      </Button>
      <Menu id={id} anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>
        {options.map((option) => (
          <MenuItem key={option.key} onClick={() => toggle(option.key)} role="menuitemcheckbox" aria-checked={extra.includes(option.key)}>
            <Checkbox size="small" checked={extra.includes(option.key)} tabIndex={-1} disableRipple sx={{ p: 0, mr: 1 }} />
            <ListItemText primary={option.label} primaryTypographyProps={{ variant: 's1' }} />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
ColumnChooser.propTypes = { extra: PropTypes.array.isRequired, onChange: PropTypes.func.isRequired, evalOptions: PropTypes.array };

/** Agent name, else service, else "Unnamed agent in <project>" with Name it (§3.4, §7.6). */
export function AgentName({ agent }) {
  if (!agent) return null;
  const unnamed = agent.nameSource === 'project' || !agent.name;
  return (
    <Stack spacing={0.25} sx={{ minWidth: 0 }}>
      <Stack direction="row" spacing={1} alignItems="baseline" useFlexGap sx={{ flexWrap: 'wrap' }}>
        <Typography variant="s1" component="span" sx={{ fontWeight: 500 }}>{unnamed ? unnamedAgent(agent.projectName) : agent.name}</Typography>
        {unnamed && <NameIt />}
        {agent.bindingId && <StatusBadge status="connected" role="info" variant="badge" label={CONNECTED_APP_BADGE} />}
      </Stack>
      <Typography variant="s3" color="text.secondary" component="span">
        {[!unnamed && agent.projectName, agent.nameSource === 'agent' && agent.service && !agent.service.startsWith('unknown_service') ? agent.service : null].filter(Boolean).join(' · ')}
        {agent.context?.id && (
          <>
            {agent.projectName ? ' · ' : ''}
            <Link component={RouterLink} to={paths.dashboard.skills.contextDetail(agent.context.id)} onClick={(event) => event.stopPropagation()} underline="hover">{agent.context.name}</Link>
          </>
        )}
      </Typography>
    </Stack>
  );
}
AgentName.propTypes = { agent: PropTypes.object };

/** One metric cell. Shared with the agent panel and the context table. */
export function MetricCell({ row, column, evalConfig, what }) {
  const links = row.links || {};
  const low = Boolean(row.lowSample);
  // The project may sit on each link, on the row's links, or on the agent (one project per agent row).
  const projectId = links.project_id ?? row.agent?.projectId ?? undefined;
  const common = { lowSample: low, traces: row.traces, projects: row.projects, projectId: projectId || undefined };
  switch (column) {
    case 'traces':
      return <TraceNumber {...common} lowSample={false} link={links.all} kind="all" what={`${what} traces`}>{formatCount(row.traces)}</TraceNumber>;
    case 'failed': {
      const failed = row.failedRequests || {};
      const denominator = failed.denominator ?? Math.max(0, (row.traces ?? 0) - (row.incomplete ?? 0));
      const text = low ? lowSampleCount(failed.count ?? 0, denominator) : formatPercent(failed.rate);
      return <TraceNumber {...common} link={links.failed} kind="failed" what={`${what} failed requests`}>{text}</TraceNumber>;
    }
    case 'p95':
      return <TraceNumber {...common} link={links.slowest} kind="slowest" what={`${what} p95`}>{formatMs(row.latencyMs?.p95)}</TraceNumber>;
    case 'p50':
      return <Box component="span" sx={{ color: low ? 'text.secondary' : undefined }}>{formatMs(row.latencyMs?.p50)}</Box>;
    case 'cost':
      return <TraceNumber {...common} link={links.all} kind="all" what={`${what} cost per trace`}>{formatCost(row.cost?.perTraceMean)}</TraceNumber>;
    case 'tokens':
      return <Box component="span">{formatCount(row.tokens?.perTraceMean == null ? null : Math.round(row.tokens.perTraceMean))}</Box>;
    case 'loadTime':
      return <Box component="span">{formatMs(row.loadOverheadMsP50)}</Box>;
    case 'cached':
      return <Box component="span">{formatPercent(row.cachedShare)}</Box>;
    case 'scopeCost':
      return <Box component="span">{row.inScopeCost ? formatCost(row.inScopeCost.total) : '—'}</Box>;
    case 'askedFor':
      return <Box component="span">{askedForMix(row.requestedLabels, row.requestedVersions)}</Box>;
    case 'last':
      return <Box component="span" title={row.lastSeen || ''}>{row.lastSeen ? relativeFrom(row.lastSeen) : '—'}</Box>;
    default: {
      if (!evalConfig) return null;
      const item = (row.evals || []).find((value) => String(value.configId) === String(evalConfig.configId));
      if (!item) return <Box component="span" sx={{ color: 'text.secondary' }}>—</Box>;
      const evalLink = links.eval_failed?.[evalConfig.configId];
      const lowEval = (item.evaluated ?? 0) < 30;
      return <TraceNumber {...common} lowSample={lowEval} traces={item.evaluated} link={evalLink} kind="eval_failed" what={`${what} ${evalConfig.name || 'eval'}`}>{evalCell(item.mean, item.coverage)}</TraceNumber>;
    }
  }
}
MetricCell.propTypes = { row: PropTypes.object.isRequired, column: PropTypes.string.isRequired, evalConfig: PropTypes.object, what: PropTypes.string };

export const columnLabel = (column, evalConfigs) => {
  const fixed = { traces: METRIC.traces, failed: METRIC.failedRequests, p95: METRIC.p95, cost: METRIC.costPerTrace, last: METRIC.lastLoaded };
  if (fixed[column]) return fixed[column];
  const optional = OPTIONAL_COLUMNS.find((item) => item.key === column);
  if (optional) return optional.label;
  return evalConfigs[column]?.name || 'Eval';
};

async function copyLink(href) {
  try {
    await navigator.clipboard.writeText(new URL(href, window.location.origin).href);
    enqueueSnackbar(LINK_COPIED);
  } catch {
    enqueueSnackbar(COPY_FAILED, { variant: 'warning' });
  }
}

export default function LoadedByTable({ skill, rows = [], allVersions = [], productionVersion, viewingVersion, onOpenAgent, extraColumns, onExtraColumnsChange }) {
  const [ownExtra, setOwnExtra] = useState([]);
  const extra = extraColumns ?? ownExtra;
  const setExtra = onExtraColumnsChange ?? setOwnExtra;
  const topEval = useMemo(() => topEvalConfig(rows), [rows]);
  const evalOptions = useMemo(() => allEvalConfigs(rows).filter((item) => item.configId !== topEval?.configId), [rows, topEval]);
  const evalConfigs = useMemo(() => Object.fromEntries([
    ...(topEval ? [['topEval', topEval]] : []),
    ...evalOptions.map((item) => [evalKey(item.configId), item]),
  ]), [topEval, evalOptions]);
  const columns = [
    ...DEFAULT_COLUMNS.filter((column) => column !== 'topEval' || topEval),
    ...extra.filter((column) => OPTIONAL_COLUMNS.some((item) => item.key === column) || evalConfigs[column]),
  ];

  const groups = useMemo(() => {
    const byVersion = new Map();
    rows.forEach((row) => {
      const id = String(row.versionId);
      const group = byVersion.get(id) || { versionId: id, version: row.version, labelsNow: row.labelsNow || [], versionRow: null, agents: [] };
      if (row.agent) group.agents.push(row);
      else group.versionRow = row;
      if (row.labelsNow?.length) group.labelsNow = row.labelsNow;
      byVersion.set(id, group);
    });
    const traces = (group) => group.versionRow?.traces ?? Math.max(0, ...group.agents.map((row) => row.traces || 0));
    return [...byVersion.values()]
      .map((group) => ({ ...group, agents: [...group.agents].sort((a, b) => (b.traces || 0) - (a.traces || 0)) }))
      .sort((a, b) => traces(b) - traces(a));
  }, [rows]);

  const production = groups.find((group) => group.labelsNow.includes('production') || group.version === productionVersion);
  const [expanded, setExpanded] = useState(() => new Set(production ? [production.versionId] : groups.slice(0, 1).map((group) => group.versionId)));
  const loaded = new Set(groups.map((group) => group.versionId));
  const noLoads = allVersions.filter((version) => !loaded.has(String(version.id))).length;
  const toggle = (id) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const versionHref = (version) => `${paths.dashboard.skills.detail(skill.id)}?version=${version}`;

  return (
    <Stack spacing={1}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
        <Typography variant="m3" component="h3">{LOADED_BY}</Typography>
        <ColumnChooser extra={extra} onChange={setExtra} evalOptions={evalOptions} />
      </Stack>
      <Table size="small" aria-label={LOADED_BY} sx={stackedTableSx}>
        <TableHead>
          <TableRow>
            <TableCell>{METRIC.agent}</TableCell>
            <TableCell>{METRIC.version}</TableCell>
            {columns.map((column) => <TableCell key={column} align={column === 'last' ? 'left' : 'right'}>{columnLabel(column, evalConfigs)}</TableCell>)}
            <TableCell aria-label="Actions" />
          </TableRow>
        </TableHead>
        <TableBody>
          {groups.map((group) => {
            const open = expanded.has(group.versionId);
            const name = versionName(group.version);
            const primary = { label: open ? `Hide agents on ${name}` : `Show agents on ${name}`, onClick: () => toggle(group.versionId) };
            const row = group.versionRow;
            const allHref = row?.links?.all ? buildSkillTraceLink({ link: row.links.all, kind: 'all', projectId: row.links.all.project_id ?? undefined }) : null;
            return (
              <Fragment key={group.versionId}>
                <TableRow {...rowLinkProps(primary)} aria-expanded={open} data-row="version" selected={group.version === viewingVersion}>
                  <TableCell>
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <Iconify icon={open ? 'solar:alt-arrow-down-linear' : 'solar:alt-arrow-right-linear'} width={14} aria-hidden />
                      {group.agents.length > 0 && <Typography variant="s1" component="span">{plural(group.agents.length, 'agent')}</Typography>}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.75} alignItems="center" useFlexGap sx={{ flexWrap: 'wrap' }}>
                      <Typography variant="s1" component="span" sx={{ fontWeight: 600 }}>{name}</Typography>
                      {group.labelsNow.includes('production') && <StatusBadge status="released" variant="badge" label={PRODUCTION_ROW} />}
                      {group.version === viewingVersion && <StatusBadge status="current_only" role="neutral" variant="badge" dot={false} label={VIEWING_ROW} />}
                    </Stack>
                  </TableCell>
                  {columns.map((column) => (
                    <TableCell key={column} align={column === 'last' ? 'left' : 'right'}>
                      {row ? <MetricCell row={row} column={column} evalConfig={evalConfigs[column]} what={name} /> : null}
                    </TableCell>
                  ))}
                  <RowActionsCell name={name} primary={primary} actions={[
                    { label: OPEN_SKILL_VERSION, to: versionHref(group.version) },
                    { label: COPY_LINK, onClick: () => copyLink(allHref || versionHref(group.version)) },
                  ]} />
                </TableRow>
                {open && group.agents.map((agentRow) => {
                  const agentName = agentRow.agent?.nameSource === 'project' ? unnamedAgent(agentRow.agent.projectName) : agentRow.agent?.name;
                  const agentPrimary = onOpenAgent ? { label: `Open ${agentName}`, onClick: () => onOpenAgent(agentRow) } : null;
                  const agentHref = agentRow.links?.all ? buildSkillTraceLink({ link: agentRow.links.all, kind: 'all', projectId: agentRow.links.all.project_id ?? agentRow.links.project_id ?? agentRow.agent?.projectId }) : null;
                  return (
                    <TableRow key={`${group.versionId}:${agentKey(agentRow.agent)}`} {...rowLinkProps(agentPrimary)} data-row="agent">
                      <TableCell sx={{ pl: { sm: 4 } }}><AgentName agent={agentRow.agent} /></TableCell>
                      <TableCell>{name}</TableCell>
                      {columns.map((column) => (
                        <TableCell key={column} align={column === 'last' ? 'left' : 'right'}>
                          <MetricCell row={agentRow} column={column} evalConfig={evalConfigs[column]} what={`${agentName} ${name}`} />
                        </TableCell>
                      ))}
                      <RowActionsCell name={`${agentName} ${name}`} primary={agentPrimary || undefined} actions={[
                        { label: OPEN_SKILL_VERSION, to: versionHref(group.version) },
                        ...(agentHref ? [{ label: COPY_LINK, onClick: () => copyLink(agentHref) }] : []),
                      ]} />
                    </TableRow>
                  );
                })}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
      {noLoads > 0 && <Typography variant="s3" color="text.secondary" data-line="versions-without-loads">{versionsWithNoLoads(noLoads)}</Typography>}
    </Stack>
  );
}

LoadedByTable.propTypes = {
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string }).isRequired,
  rows: PropTypes.array,
  allVersions: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]), number: PropTypes.number })),
  productionVersion: PropTypes.number,
  viewingVersion: PropTypes.number,
  onOpenAgent: PropTypes.func,
  extraColumns: PropTypes.array,
  onExtraColumnsChange: PropTypes.func,
};
