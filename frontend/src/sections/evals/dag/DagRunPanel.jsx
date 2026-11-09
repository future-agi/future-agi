// Run a saved decision tree on a dataset's first rows. Each {{variable}} and
// the output are read from mapped columns; the server stores one result per
// row, and the paths table below reads that run.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import useDagInputs from "./useDagInputs";

// The server runs at most this many rows a run.
export const RUN_ROWS = 20;
const fieldSx = { "& .MuiInputBase-root": { fontSize: "13px" } };

/** The run request body, or the reasons it cannot be sent yet. Pure. */
export function runRequestOf({ datasetId, vars, mapping }) {
  const reasons = [];
  if (!datasetId) reasons.push("Pick a dataset");
  ["output", ...vars].forEach((v) => {
    if (!mapping[v]) reasons.push(`Map {{${v}}} to a column`);
  });
  if (reasons.length) return { reasons };
  const picked = Object.fromEntries(
    ["output", ...vars].map((v) => [v, mapping[v]]),
  );
  return { reasons, body: { dataset_id: datasetId, mapping: picked } };
}

export default function DagRunPanel({ templateId, dag, dirty = false }) {
  const queryClient = useQueryClient();
  const [datasetId, setDatasetId] = useState("");
  const [mapping, setMapping] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const { vars, reasons: inputReasons } = useDagInputs(dag);
  const { data: datasets = [] } = useDevelopDatasetList();
  const { data: columns = [] } = useQuery({
    queryKey: ["evals", "dag-run-columns", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { page_size: 1, current_page_index: 0 },
      }),
    select: (res) => res.data?.result?.column_config ?? [],
    enabled: Boolean(datasetId),
  });
  const { reasons, body } = runRequestOf({ datasetId, vars, mapping });
  const blocked = [
    ...inputReasons,
    ...reasons,
    ...(dirty ? ["Save the version before running it"] : []),
  ];
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.post(
        apiPath("/model-hub/eval-templates/{template_id}/dag/runs/", {
          template_id: templateId,
        }),
        body,
      );
      const n = data?.result?.n ?? 0;
      enqueueSnackbar(`Scored ${n} row${n === 1 ? "" : "s"}`, {
        variant: "success",
      });
      await queryClient.invalidateQueries({
        queryKey: ["w7-dag-paths", templateId],
      });
    } catch (e) {
      setError(
        e?.response?.data?.errors?.join(" · ") ?? e?.message ?? "Run failed",
      );
    } finally {
      setBusy(false);
    }
  };
  const columnSelect = (v) => (
    <TextField
      key={v}
      select
      size="small"
      label={`{{${v}}}`}
      value={mapping[v] ?? ""}
      disabled={!datasetId}
      sx={{ ...fieldSx, minWidth: 220 }}
      onChange={(e) => setMapping({ ...mapping, [v]: e.target.value })}
    >
      {columns.map((c) => (
        <MenuItem key={c.id} value={c.id}>
          {c.name}
        </MenuItem>
      ))}
    </TextField>
  );
  return (
    <Box
      component="section"
      aria-label="Run on a dataset"
      data-testid="dag-run"
      sx={{
        p: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Typography typography="s1" fontWeight={600}>
        Run on a dataset
      </Typography>
      <Typography typography="s3" color="text.secondary" sx={{ mb: 1 }}>
        {`Scores the first ${RUN_ROWS} rows with the saved version and keeps every path.`}
      </Typography>
      <Stack direction="row" gap={1} flexWrap="wrap">
        <TextField
          select
          size="small"
          label="Dataset"
          value={datasetId}
          sx={{ ...fieldSx, minWidth: 220 }}
          onChange={(e) => {
            setDatasetId(e.target.value);
            setMapping({});
          }}
        >
          {datasets.map((d) => (
            <MenuItem key={d.datasetId} value={d.datasetId}>
              {d.name}
            </MenuItem>
          ))}
        </TextField>
        {["output", ...vars].map(columnSelect)}
      </Stack>
      <Stack direction="row" alignItems="center" gap={1} sx={{ mt: 1 }}>
        <Button
          size="small"
          variant="contained"
          disabled={busy || blocked.length > 0}
          onClick={run}
          sx={{ textTransform: "none" }}
        >
          {busy ? "Running..." : "Run on dataset"}
        </Button>
        {blocked.length ? (
          <Typography typography="s3" color="text.secondary">
            {blocked.join(" · ")}
          </Typography>
        ) : null}
      </Stack>
      {error ? (
        <Typography
          typography="s3"
          color="error.main"
          role="alert"
          sx={{ mt: 1 }}
        >
          {error}
        </Typography>
      ) : null}
    </Box>
  );
}

DagRunPanel.propTypes = {
  templateId: PropTypes.string.isRequired,
  dag: PropTypes.object.isRequired,
  dirty: PropTypes.bool,
};
