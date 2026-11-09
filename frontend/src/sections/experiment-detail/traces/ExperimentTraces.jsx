// W5-8 (RV-14): the experiment page's Traces tab
// (`/dashboard/develop/experiment/:experimentId/traces`, integration C-H5):
// pick one of the experiment's runs, see its traces. `?run=` keeps the pick.
import React, { useEffect, useRef } from "react";
import { Helmet } from "react-helmet-async";
import { useParams, useSearchParams } from "react-router-dom";
import { Box, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { useExperimentDetail } from "src/api/develop/runs";
import TracesView from "./TracesView";

const runName = (r) =>
  [r.label ?? r.run_label, r.name ?? r.run_name].filter(Boolean).join(" ") ||
  r.run_id;

export default function ExperimentTraces() {
  const { experimentId } = useParams();
  const [params, setParams] = useSearchParams();
  // Read the experiment again on every visit: a run started a moment ago
  // (a rerun, Test a fix) is listed, and `?run=` can name it.
  const {
    data: experiment,
    isFetching,
    refetch,
  } = useExperimentDetail(experimentId, { refetchOnMount: "always" });
  const runs = experiment?.runs ?? [];
  const requested = params.get("run");
  const listed = runs.some((r) => r.run_id === requested);
  // `?run=` names a run the cached list does not have (started while this
  // tab was open): read the experiment again, once per requested id.
  const asked = useRef(null);
  useEffect(() => {
    if (!requested || listed || isFetching || asked.current === requested)
      return;
    asked.current = requested;
    refetch();
  }, [requested, listed, isFetching, refetch]);
  const waiting = requested && !listed && asked.current !== requested;
  // A requested run the cached list does not have yet waits for the fresh
  // read; the traces endpoint answers 404 for a run of another experiment.
  const runId = listed
    ? requested
    : requested && (isFetching || waiting)
      ? null
      : runs[0]?.run_id ?? null;

  return (
    <Box
      sx={{
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minHeight: 0,
      }}
    >
      <Helmet>
        <title>Experiment Traces</title>
      </Helmet>
      <Stack direction="row" alignItems="center" gap={1.5}>
        <Typography variant="subtitle1">Traces</Typography>
        {runs.length ? (
          <TextField
            select
            size="small"
            label="Run"
            value={runId ?? ""}
            onChange={(e) => {
              const next = new URLSearchParams(params);
              next.set("run", e.target.value);
              setParams(next, { replace: true });
            }}
            sx={{ minWidth: 260 }}
            inputProps={{ "data-testid": "traces-run-select" }}
          >
            {runs.map((r) => (
              <MenuItem key={r.run_id} value={r.run_id}>
                {runName(r)}
              </MenuItem>
            ))}
          </TextField>
        ) : null}
      </Stack>
      {runId ? <TracesView experimentId={experimentId} runId={runId} /> : null}
    </Box>
  );
}
