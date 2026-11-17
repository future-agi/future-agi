import { Fragment, useId, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import { Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import StatusBadge from 'src/components/status-badge';
import { paths } from 'src/routes/paths';
import { runtimeErrorKind, useContextRuntime } from 'src/api/skills/runtime';
import { rowLinkProps, stackedTableSx } from 'src/sections/skills/components/responsiveTable';
import { RowActionsCell } from 'src/sections/skills/components/RowActions';
import { AuthorizedProjectPicker } from 'src/sections/skills/governance/AdoptionPanel';
import AgentPanel, { agentPanelInput } from './AgentPanel';
import ContextReleaseCard from './ContextReleaseCard';
import FailedLoadsLine, { ContentsWarningLine } from './FailedLoadsLine';
import { AgentName, MetricCell, columnLabel, topEvalConfig } from './LoadedByTable';
import RuntimeDiagnosis, { RuntimeNotes, diagnosisFor } from './RuntimeDiagnosis';
import { RuntimeQueryState, WindowSelect } from './RuntimeParts';
import { AboutRuntimeData, RuntimeStrip, runtimeLead } from './RuntimeTab';
import {
  CONTEXT_RUNTIME_ERROR, DEFAULT_WINDOW, DIAGNOSIS, LINKED_BADGE, LINKED_NO_LOADS, LOADING_RUNTIME, METRIC,
  NOT_LINKED_HERE, OPEN_SKILL_VERSION, PROJECTS_LABEL, SKILLS_LOADED_AT_RUNTIME, plural, unnamedAgent, versionName,
} from './runtimeCopy';

const COLUMNS = ['traces', 'failed', 'p95', 'cost', 'topEval'];

/**
 * The context Runtime tab (SKILL-TRACES §7.4, endpoint 4, UX-06, UX-09,
 * UX-11). The header's "Viewing vN" is the version selector (`versionId`);
 * this tab adds only a window and Projects. Order: failed loads, lead,
 * strip, the release card when a production release falls in the window,
 * Skills loaded at runtime, then Linked, no loads recorded. Numbers link to
 * Observe with `skill.context_id` + `skill.version_id` (the server's links).
 */
export default function ContextRuntimeView({ context, versionId, onRollback, onCheckTracing }) {
  const controlId = useId();
  const [params] = useSearchParams();
  const [windowValue, setWindowValue] = useState(DEFAULT_WINDOW);
  const [projectIds, setProjectIds] = useState([]);
  const [expanded, setExpanded] = useState(() => new Set());
  const [agent, setAgent] = useState(null);
  const query = useContextRuntime(context.id, { window: windowValue, projectIds, versionId });
  const errorKind = query.isError ? runtimeErrorKind(query.error) : null;
  const data = query.data;
  const rows = data?.rows ?? [];
  const notLinked = useMemo(() => new Set((data?.loadedNotLinked ?? []).map((row) => String(row.versionId))), [data]);
  const groups = useMemo(() => {
    const map = new Map();
    rows.forEach((row) => {
      const id = String(row.versionId);
      const group = map.get(id) || { versionId: id, skillId: row.skillId, name: row.skillName || row.name || 'Unknown skill', version: row.version, versionRow: null, agents: [] };
      if (row.agent) group.agents.push(row);
      else group.versionRow = row;
      map.set(id, group);
    });
    return [...map.values()].sort((a, b) => (b.versionRow?.traces ?? 0) - (a.versionRow?.traces ?? 0));
  }, [rows]);
  const topEval = useMemo(() => topEvalConfig(rows), [rows]);
  const columns = COLUMNS.filter((column) => column !== 'topEval' || topEval);
  const linkedNotLoaded = data?.linkedNotLoaded ?? [];
  const releaseId = params.get('move') || data?.move?.eventId || null;
  const loading = query.isPending && query.fetchStatus !== 'idle';
  const toggle = (id) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  return (
    <Stack spacing={2.5} data-tab="context-runtime">
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'flex-start' }}>
        <WindowSelect id={`${controlId}-window`} value={windowValue} onChange={setWindowValue} />
        <AuthorizedProjectPicker multiple label={PROJECTS_LABEL} value={projectIds} onChange={setProjectIds} sx={{ minWidth: 240 }} />
      </Stack>

      {loading ? (
        <Stack spacing={1.5}>
          <ContentSkeleton variant="rows" rows={1} label={LOADING_RUNTIME} />
          <ContentSkeleton variant="rows" rows={5} label="Loading skills loaded at runtime" />
        </Stack>
      ) : (
        <RuntimeQueryState query={query} errorKind={errorKind} errorTitle={CONTEXT_RUNTIME_ERROR} deniedText={DIAGNOSIS.noReadableProjects('agent context')} loadingLabel={LOADING_RUNTIME} />
      )}

      {data && (
        <>
          <FailedLoadsLine failedLoads={data.failedLoads} name={context.name} windowValue={windowValue} />
          <ContentsWarningLine mismatch={data.mismatch} />
          {rows.length === 0 ? (
            <RuntimeDiagnosis empty kind="agent_context" diagnosis={data.diagnosis} subjectId={context.id} onCheckTracing={onCheckTracing} />
          ) : (
            <>
              <Typography variant="s1" data-line="lead">{runtimeLead(context.name, data.summary)}</Typography>
              {data.diagnosis?.state === 'partially_recording' && (
                <RuntimeDiagnosis kind="agent_context" diagnosis={data.diagnosis} subjectId={context.id} loadsAgents={data.summary?.agents ?? 0} onCheckTracing={onCheckTracing} />
              )}
              <RuntimeStrip summary={data.summary} />
              {releaseId && <ContextReleaseCard contextId={context.id} eventId={releaseId} window={params.get('move') ? params.get('window') || '24h' : '24h'} onRollback={onRollback} />}
              <Stack spacing={1}>
                <Typography variant="m3" component="h3">{SKILLS_LOADED_AT_RUNTIME}</Typography>
                <Table size="small" aria-label={SKILLS_LOADED_AT_RUNTIME} sx={stackedTableSx}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{METRIC.skill}</TableCell>
                      <TableCell>{METRIC.version}</TableCell>
                      <TableCell>{METRIC.agents}</TableCell>
                      {columns.map((column) => <TableCell key={column} align="right">{columnLabel(column, { topEval })}</TableCell>)}
                      <TableCell aria-label="Actions" />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {groups.map((group) => {
                      const open = expanded.has(group.versionId);
                      const label = `${group.name} ${versionName(group.version)}`;
                      const primary = group.agents.length ? { label: open ? `Hide agents for ${label}` : `Show agents for ${label}`, onClick: () => toggle(group.versionId) } : null;
                      const linked = group.versionRow?.linked ?? !notLinked.has(group.versionId);
                      return (
                        <Fragment key={group.versionId}>
                          <TableRow {...rowLinkProps(primary)} aria-expanded={primary ? open : undefined} data-row="skill-version">
                            <TableCell><Typography variant="s1" component="span" sx={{ fontWeight: 500 }}>{group.name}</Typography></TableCell>
                            <TableCell>
                              <Stack direction="row" spacing={0.75} alignItems="center">
                                <span>{versionName(group.version)}</span>
                                <StatusBadge status={linked ? 'linked' : 'not_linked'} role={linked ? 'neutral' : 'warning'} variant="badge" dot={false} label={linked ? LINKED_BADGE : NOT_LINKED_HERE} />
                              </Stack>
                            </TableCell>
                            <TableCell>{group.agents.length > 0 ? plural(group.agents.length, 'agent') : null}</TableCell>
                            {columns.map((column) => (
                              <TableCell key={column} align="right">{group.versionRow ? <MetricCell row={group.versionRow} column={column} evalConfig={column === 'topEval' ? topEval : undefined} what={label} /> : null}</TableCell>
                            ))}
                            <RowActionsCell name={label} primary={primary || undefined} actions={group.skillId ? [{ label: OPEN_SKILL_VERSION, to: `${paths.dashboard.skills.detail(group.skillId)}?version=${group.version}` }] : []} />
                          </TableRow>
                          {open && group.agents.map((row) => {
                            const agentName = row.agent?.nameSource === 'project' ? unnamedAgent(row.agent.projectName) : row.agent?.name;
                            const agentPrimary = { label: `Open ${agentName}`, onClick: () => setAgent(agentPanelInput(row.agent)) };
                            return (
                              <TableRow key={`${group.versionId}:${JSON.stringify(row.agent?.key)}`} {...rowLinkProps(agentPrimary)} data-row="agent">
                                <TableCell sx={{ pl: { sm: 4 } }} colSpan={3}><AgentName agent={row.agent} /></TableCell>
                                {columns.map((column) => (
                                  <TableCell key={column} align="right"><MetricCell row={row} column={column} evalConfig={column === 'topEval' ? topEval : undefined} what={`${agentName} ${label}`} /></TableCell>
                                ))}
                                <RowActionsCell name={`${agentName} ${label}`} primary={agentPrimary} />
                              </TableRow>
                            );
                          })}
                        </Fragment>
                      );
                    })}
                  </TableBody>
                </Table>
              </Stack>
            </>
          )}

          {linkedNotLoaded.length > 0 && (
            <Stack spacing={1} data-section="linked-no-loads">
              <Typography variant="m3" component="h3">{LINKED_NO_LOADS}</Typography>
              <Stack component="ul" spacing={1} sx={{ m: 0, pl: 0, listStyle: 'none' }}>
                {linkedNotLoaded.map((item) => {
                  const state = item.diagnosis || data.diagnosis;
                  const reason = state?.state === 'recording' ? { text: DIAGNOSIS.linkedNotRead } : diagnosisFor({ diagnosis: state, kind: 'skill' });
                  return (
                    <Stack component="li" key={item.skillId} spacing={0.25}>
                      <Typography variant="s1">
                        {item.skillId ? <Link component={RouterLink} to={`${paths.dashboard.skills.detail(item.skillId)}?tab=runtime`} underline="hover">{item.name}</Link> : item.name}
                        {item.linkedVersion != null ? ` ${versionName(item.linkedVersion)}` : ''}
                      </Typography>
                      <Typography variant="s3" color="text.secondary">{reason?.text || DIAGNOSIS.unknown}</Typography>
                    </Stack>
                  );
                })}
              </Stack>
            </Stack>
          )}
          <RuntimeNotes coverage={data.coverage} truncated={data.truncated} />
          <AboutRuntimeData />
        </>
      )}
      <AgentPanel open={Boolean(agent)} onClose={() => setAgent(null)} agent={agent} window={windowValue} />
    </Stack>
  );
}

ContextRuntimeView.propTypes = {
  context: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired }).isRequired,
  versionId: PropTypes.string,
  onRollback: PropTypes.func,
  onCheckTracing: PropTypes.func,
};
