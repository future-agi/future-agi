import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { useSearchParams } from 'react-router-dom';
import { Stack, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import MetricStrip from 'src/components/metric-strip';
import { runtimeErrorKind, useSkillRuntime } from 'src/api/skills/runtime';
import TechnicalDetails from 'src/sections/skills/components/TechnicalDetails';
import { AuthorizedProjectPicker } from 'src/sections/skills/governance/AdoptionPanel';
import AgentPanel, { agentPanelInput } from './AgentPanel';
import FailedLoadsLine, { ContentsWarningLine } from './FailedLoadsLine';
import LoadedByTable from './LoadedByTable';
import ProductionMoveCard, { formatMetric } from './ProductionMoveCard';
import RuntimeDiagnosis, { RuntimeNotes } from './RuntimeDiagnosis';
import { RuntimeQueryState, WindowSelect } from './RuntimeParts';
import {
  ABOUT_LINES, ABOUT_THIS_DATA, DEFAULT_WINDOW, DIAGNOSIS, LOADING_RUNTIME, METRIC, PROJECTS_LABEL,
  RUNTIME_ERROR, RUNTIME_TAB, formatCost, formatCount, formatMs, formatPercent, leadSentence,
} from './runtimeCopy';

/** The four headline numbers; the strip wraps to 2×2 on a phone (§7.7). */
export function RuntimeStrip({ summary }) {
  if (!summary) return null;
  return (
    <MetricStrip
      sx={{ gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', sm: 'repeat(4, minmax(0, 1fr))' } }}
      items={[
        { key: 'traces', label: METRIC.traces, value: formatCount(summary.traces) },
        { key: 'failed', label: METRIC.failedRequests, value: formatPercent(summary.failedRequests?.rate) },
        { key: 'p95', label: METRIC.p95, value: formatMs(summary.latencyMs?.p95) },
        { key: 'cost', label: METRIC.costPerTrace, value: formatCost(summary.cost?.perTraceMean) },
      ]}
    />
  );
}
RuntimeStrip.propTypes = { summary: PropTypes.object };

/** The lead sentence from endpoint 1 `summary` (§7.3 item 3). */
export function runtimeLead(subject, summary) {
  const compare = summary?.sameAgentCompare;
  return leadSentence({
    subject,
    agents: summary?.agents,
    projects: summary?.projects,
    compare: compare && {
      ...compare,
      aText: formatMetric(compare.metric, compare.a),
      bText: formatMetric(compare.metric, compare.b),
    },
  });
}

export function AboutRuntimeData() {
  return (
    <TechnicalDetails title={ABOUT_THIS_DATA}>
      <Stack spacing={0.5}>
        {ABOUT_LINES.map((line) => <Typography key={line} variant="s3" color="text.secondary">{line}</Typography>)}
      </Stack>
    </TechnicalDetails>
  );
}

/**
 * The skill Runtime tab (SKILL-TRACES §7.3, UX-08). UXF5 commit 2 places it
 * between Linked contexts and Versions (URL value `runtime`) when
 * `skillTraces` is on. One window control (default 7 days) drives the lead,
 * the strip and the table; the move card owns its own window. There is no
 * Version filter: `viewingVersion` (the header's "Viewing vN") marks a row.
 */
export default function RuntimeTab({ skill, versions = [], productionVersion, viewingVersion, onRollback, onCheckTracing }) {
  const controlId = useId();
  const [params] = useSearchParams();
  const [windowValue, setWindowValue] = useState(DEFAULT_WINDOW);
  const [projectIds, setProjectIds] = useState([]);
  const [agent, setAgent] = useState(null);
  const query = useSkillRuntime(skill.id, { window: windowValue, projectIds });
  const errorKind = query.isError ? runtimeErrorKind(query.error) : null;
  const data = query.data;
  const rows = data?.rows ?? [];
  const agentRows = rows.filter((row) => row.agent);
  const urlMove = params.get('move');
  const moveId = urlMove || data?.move?.eventId || null;
  const moveWindow = urlMove ? params.get('window') || '24h' : '24h';
  const noEvals = rows.length > 0 && rows.every((row) => !(row.evals || []).length);
  const incomplete = rows.filter((row) => !row.agent).reduce((max, row) => Math.max(max, row.incomplete || 0), 0);
  const loading = query.isPending && query.fetchStatus !== 'idle';

  return (
    <Stack spacing={2.5} aria-label={RUNTIME_TAB} data-tab="runtime">
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'flex-start' }}>
        <WindowSelect id={`${controlId}-window`} value={windowValue} onChange={setWindowValue} />
        <AuthorizedProjectPicker multiple label={PROJECTS_LABEL} value={projectIds} onChange={setProjectIds} sx={{ minWidth: 240 }} />
      </Stack>

      {loading ? (
        <Stack spacing={1.5}>
          <ContentSkeleton variant="rows" rows={1} label={LOADING_RUNTIME} />
          <ContentSkeleton variant="rows" rows={1} label="Loading summary" />
          <ContentSkeleton variant="rows" rows={5} label="Loading Loaded by" />
        </Stack>
      ) : (
        <RuntimeQueryState query={query} errorKind={errorKind} errorTitle={RUNTIME_ERROR} deniedText={DIAGNOSIS.noReadableProjects('skill')} loadingLabel={LOADING_RUNTIME} />
      )}

      {data && (
        <>
          <FailedLoadsLine failedLoads={data.failedLoads} name={skill.name} windowValue={windowValue} />
          <ContentsWarningLine mismatch={data.mismatch} />
          {rows.length === 0 ? (
            <RuntimeDiagnosis empty diagnosis={data.diagnosis} subjectId={skill.id} days={7} onCheckTracing={onCheckTracing} />
          ) : (
            <>
              <Typography variant="s1" data-line="lead">{runtimeLead(skill.name, data.summary)}</Typography>
              {data.diagnosis?.state === 'partially_recording' && (
                <RuntimeDiagnosis diagnosis={data.diagnosis} subjectId={skill.id} loadsAgents={data.summary?.agents ?? agentRows.length} onCheckTracing={onCheckTracing} />
              )}
              <RuntimeStrip summary={data.summary} />
              {moveId && <ProductionMoveCard skillId={skill.id} eventId={moveId} window={moveWindow} onRollback={onRollback} projectIds={projectIds} />}
              <LoadedByTable
                skill={skill}
                rows={rows}
                allVersions={versions}
                productionVersion={productionVersion}
                viewingVersion={viewingVersion}
                onOpenAgent={(row) => setAgent(agentPanelInput(row.agent))}
              />
            </>
          )}
          <RuntimeNotes coverage={data.coverage} truncated={data.truncated} incomplete={incomplete} noEvals={noEvals} />
          <AboutRuntimeData />
        </>
      )}
      <AgentPanel open={Boolean(agent)} onClose={() => setAgent(null)} agent={agent} window={windowValue} />
    </Stack>
  );
}

RuntimeTab.propTypes = {
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired }).isRequired,
  versions: PropTypes.array,
  productionVersion: PropTypes.number,
  viewingVersion: PropTypes.number,
  onRollback: PropTypes.func,
  onCheckTracing: PropTypes.func,
};
