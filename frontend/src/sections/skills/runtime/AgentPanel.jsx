import { useId, useMemo } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Drawer, IconButton, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import EmptyState from 'src/components/empty-state';
import StatusBadge from 'src/components/status-badge';
import { paths } from 'src/routes/paths';
import { runtimeErrorKind, useRuntimeAgent } from 'src/api/skills/runtime';
import { stackedTableSx } from 'src/sections/skills/components/responsiveTable';
import { MetricCell, columnLabel, topEvalConfig } from './LoadedByTable';
import { NameIt } from './RuntimeDiagnosis';
import { RuntimeQueryState } from './RuntimeParts';
import {
  AGENT_PANEL_TITLE, CANT_OPEN_PROJECT, CLOSE_AGENT_PANEL, CONNECTED_APP_BADGE, CONTEXT, DIAGNOSIS, METRIC,
  NO_LOADS_RECORDED, PROJECT, RUNTIME_ERROR, SERVICE, agentLoadedLead, unnamedAgent, versionName, windowPhrase,
} from './runtimeCopy';

const COLUMNS = ['traces', 'failed', 'p95', 'cost', 'topEval', 'last'];

/**
 * The agent panel (SKILL-TRACES §7.5, UX-10), fed by endpoint 8. Every agent
 * name opens it: the Loaded by table, the context runtime table, the Observe
 * Agent column (lane ST9 mounts it there) and the trace detail rows. It needs
 * only a router, React Query and the signed-in workspace, not the Skills
 * layout.
 *
 * `agent` is the §3.4 agent key plus display fields:
 *   { projectId, projectName?, service, agentName, name?, nameSource?, context?, bindingId? }
 * `agentName` is the raw `gen_ai.agent.name` ('' for an unnamed agent).
 * The window is the caller's (`window` or `from`/`to`).
 */
export default function AgentPanel({ open, onClose, agent, window: windowValue = '7d', from, to }) {
  const titleId = useId();
  const query = useRuntimeAgent({
    projectId: agent?.projectId,
    service: agent?.service ?? '',
    agent: agent?.agentName ?? '',
    window: windowValue,
    from,
    to,
    enabled: Boolean(open && agent?.projectId),
  });
  const header = { ...agent, ...(query.data?.agent || {}) };
  const unnamed = header.nameSource === 'project' || !(header.name || header.agentName);
  const title = unnamed ? unnamedAgent(header.projectName) : header.name || header.agentName;
  const rows = query.data?.rows ?? [];
  const topEval = useMemo(() => topEvalConfig(rows), [rows]);
  const columns = COLUMNS.filter((column) => column !== 'topEval' || topEval);
  const errorKind = query.isError ? runtimeErrorKind(query.error) : null;

  return (
    <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{ role: 'dialog', 'aria-modal': true, 'aria-labelledby': titleId, sx: { width: { xs: '100%', sm: 640 }, maxWidth: '100%' } }}>
      <Stack spacing={2} sx={{ p: 3, minHeight: 0, height: '100%', overflowY: 'auto' }}>
        <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={1}>
          <Stack spacing={0.5} sx={{ minWidth: 0 }}>
            <Typography variant="s3" color="text.secondary">{AGENT_PANEL_TITLE}</Typography>
            <Stack direction="row" spacing={1} alignItems="baseline" useFlexGap sx={{ flexWrap: 'wrap' }}>
              <Typography id={titleId} variant="m1" component="h2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{title}</Typography>
              {unnamed && <NameIt />}
            </Stack>
          </Stack>
          <IconButton aria-label={CLOSE_AGENT_PANEL} onClick={onClose}><Iconify icon="solar:close-linear" /></IconButton>
        </Stack>
        <Stack component="dl" spacing={0.5} sx={{ m: 0, '& dt': { color: 'text.secondary', typography: 's3' }, '& dd': { m: 0, typography: 's1' } }}>
          {header.projectName && <Stack direction="row" spacing={1}><dt>{PROJECT}</dt><dd>{header.projectName}</dd></Stack>}
          {header.service && !header.service.startsWith('unknown_service') && <Stack direction="row" spacing={1}><dt>{SERVICE}</dt><dd>{header.service}</dd></Stack>}
          {header.context?.id && (
            <Stack direction="row" spacing={1}>
              <dt>{CONTEXT}</dt>
              <dd><Link component={RouterLink} to={paths.dashboard.skills.contextDetail(header.context.id)} underline="hover">{header.context.name}{header.context.version != null ? ` ${versionName(header.context.version)}` : ''}</Link></dd>
            </Stack>
          )}
          {header.bindingId && <StatusBadge status="connected" role="info" variant="badge" label={CONNECTED_APP_BADGE} />}
        </Stack>
        <RuntimeQueryState query={query} errorKind={errorKind} errorTitle={RUNTIME_ERROR} deniedText={CANT_OPEN_PROJECT} loadingLabel="Loading this agent's skills" rows={4} />
        {query.data && !rows.length && (
          <EmptyState variant="no-results" title={NO_LOADS_RECORDED} description={`${DIAGNOSIS.unknown}`} sx={{ flex: '0 0 auto', height: 'auto', py: 3 }} />
        )}
        {rows.length > 0 && (
          <Stack spacing={1}>
            <Typography variant="s1" color="text.secondary">{agentLoadedLead(rows.length)} · {windowPhrase(windowValue)}</Typography>
            <Table size="small" aria-label={`Skills loaded by ${title}`} sx={stackedTableSx}>
              <TableHead>
                <TableRow>
                  <TableCell>{METRIC.skill}</TableCell>
                  {columns.map((column) => <TableCell key={column} align={column === 'last' ? 'left' : 'right'}>{columnLabel(column, { topEval })}</TableCell>)}
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((row) => {
                  const name = `${row.skillName || row.name || 'Unknown skill'} ${versionName(row.version)}`;
                  return (
                    <TableRow key={`${row.skillId}:${row.versionId}`}>
                      <TableCell>
                        {row.skillId ? (
                          <Link component={RouterLink} to={`${paths.dashboard.skills.detail(row.skillId)}?tab=runtime`} underline="hover">{name}</Link>
                        ) : name}
                      </TableCell>
                      {columns.map((column) => (
                        <TableCell key={column} align={column === 'last' ? 'left' : 'right'}>
                          <MetricCell row={row} column={column} evalConfig={column === 'topEval' ? topEval : undefined} what={name} />
                        </TableCell>
                      ))}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Stack>
        )}
      </Stack>
    </Drawer>
  );
}

AgentPanel.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  agent: PropTypes.shape({
    projectId: PropTypes.string,
    projectName: PropTypes.string,
    service: PropTypes.string,
    agentName: PropTypes.string,
    name: PropTypes.string,
    nameSource: PropTypes.string,
    context: PropTypes.object,
    bindingId: PropTypes.string,
  }),
  window: PropTypes.string,
  from: PropTypes.string,
  to: PropTypes.string,
};

/** Agent-panel input from a §6.6 row's `agent` object (key = [project, service, agent]). */
export const agentPanelInput = (agent) => (agent ? {
  projectId: agent.projectId ?? agent.key?.[0],
  projectName: agent.projectName,
  service: agent.service ?? agent.key?.[1] ?? '',
  agentName: agent.key?.[2] ?? (agent.nameSource === 'agent' ? agent.name : ''),
  name: agent.name,
  nameSource: agent.nameSource,
  context: agent.context,
  bindingId: agent.bindingId,
} : null);
