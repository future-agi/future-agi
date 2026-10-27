import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Typography } from "@mui/material";
import TrendCard from "src/sections/projects/Insights/TrendCard";
import Findings from "src/sections/projects/Insights/Findings";
import ChangeCards from "src/sections/projects/Insights/ChangeCards";
import Highlights from "src/sections/projects/Insights/Highlights";
import MethodNote from "src/sections/projects/Insights/MethodNote";
import {
  ReportProvider,
  useReportModel,
} from "src/sections/projects/Insights/reportContext";
import {
  buildSegments,
  resultTops,
} from "src/sections/projects/Insights/insightsDistribution";
import { runAnalysisOf } from "../_mock/runAnalysis";

/*
  The run's Insights tab: Observe's Insights report, read over this run's
  scenarios. Categories are the run's failure causes; a category opens the
  Scenarios tab filtered to it, a "#n" opens that scenario's trace, and the
  chart's runs are this environment's earlier runs.
*/
export default function RunAnalysis({
  env,
  envState,
  tasks,
  history,
  runId,
  goalOf,
  onFilter,
  onOpenTrace,
}) {
  const navigate = useNavigate();
  const [methodOpen, setMethodOpen] = useState(false);
  const report = useMemo(
    () => runAnalysisOf({ env, envState, tasks, history, runId, goalOf }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState, tasks, runId],
  );
  const { config, run, counts } = report;
  const model = useReportModel(config, run);

  const openCategory = (id) => {
    const cat = model.categoriesById.get(id);
    onFilter?.(cat?.name || "Issue", report.membersOf(id));
  };
  const openExample = (unitId) => {
    const t = report.taskOf(unitId);
    if (t) onOpenTrace?.(t);
  };
  const context = useMemo(
    () => ({
      ...model,
      previews: new Map([...model.previews, ...report.previews]),
      projectId: null,
      runId,
      unit: "scenarios",
      openCategory,
      openExample,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, report, runId],
  );
  const segments = useMemo(
    () =>
      buildSegments(resultTops(model.categories), {
        order: model.order,
        mode: model.mode,
      }),
    [model],
  );
  const onSelectRun = (id) =>
    navigate(
      `/dashboard/simulate/environments/${env?.id}/runs/${id}?tab=insights`,
    );

  const meta = [
    `${counts.total} scenarios`,
    `${counts.measured} measured`,
    `${counts.failed} failed`,
    counts.mixed > 0 && `${counts.mixed} mixed`,
    counts.notMeasured > 0 && `${counts.notMeasured} not counted`,
  ].filter(Boolean);
  const hasFailures = model.categories.length > 0;

  return (
    <ReportProvider value={context}>
      <Box sx={{ maxWidth: 960, width: "100%", mx: "auto", pb: 4 }}>
        {hasFailures && (
          <TrendCard
            runs={config.runs}
            runId={runId}
            order={model.order}
            currentSegments={segments}
            sharedTaxonomy
            onSelectRun={onSelectRun}
            onCategory={openCategory}
          />
        )}
        <Stack spacing={3.5} sx={{ mt: hasFailures ? 3 : 0 }}>
          <Box>
            <Typography
              component="h2"
              sx={{ fontSize: 22, lineHeight: "30px", fontWeight: 600 }}
            >
              {`Why scenarios failed in Run ${report.ordinal ?? ""}`.trim()}
            </Typography>
            <Typography
              color="text.secondary"
              sx={{ mt: 0.5, fontSize: 13, fontVariantNumeric: "tabular-nums" }}
            >
              {meta.join(" · ")}
            </Typography>
            <Box sx={{ mt: 1.5 }}>
              {hasFailures ? (
                <Findings summary={run.result.summary} />
              ) : (
                <Box
                  sx={{
                    p: 2.5,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                  }}
                >
                  <Typography variant="subtitle2">
                    No failures in this run
                  </Typography>
                  <Typography fontSize={13} color="text.secondary">
                    {`All ${counts.measured} measured scenarios passed.`}
                  </Typography>
                </Box>
              )}
            </Box>
          </Box>
          {hasFailures && (
            <>
              <ChangeCards changes={run.result.changes} />
              <Highlights highlights={run.result.summary.highlights} />
            </>
          )}
          <MethodNote
            run={run}
            config={config}
            open={methodOpen}
            onToggle={() => setMethodOpen((v) => !v)}
          />
        </Stack>
      </Box>
    </ReportProvider>
  );
}

RunAnalysis.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  tasks: PropTypes.array.isRequired,
  history: PropTypes.array,
  runId: PropTypes.string,
  goalOf: PropTypes.func,
  onFilter: PropTypes.func,
  onOpenTrace: PropTypes.func,
};
