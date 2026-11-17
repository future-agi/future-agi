import {
  formatExactMoney,
  isMoneyValue,
} from "src/sections/common/format/money";
// W7 A2 (W7-3): Test a decision tree on a row — a dataset row (each
// {{variable}} mapped to a column) or typed values, and a sample output —
// through POST dag/execute-adhoc/ (the same scorer, key and judges a run
// uses: a saved tree sends its template id, whose model a question without
// its own asks). Shows the path step by step (answer, p, reason, cost,
// time), the score reached and the total; Test again says whether the path
// stayed the same. `{{expected}}` (an Equals expected check) starts on the
// dataset's expected column.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { validateDag } from "src/sections/common/EvalTypes/dagModel";
import useDagInputs from "../dag/useDagInputs";
import DagPathSteps from "src/sections/common/EvalTypes/DagPathSteps";

const fieldSx = { "& .MuiInputBase-root": { fontSize: "13px" } };
const cellText = (v) => {
  const x = v && typeof v === "object" && "cell_value" in v ? v.cell_value : v;
  return x == null ? "" : typeof x === "string" ? x : JSON.stringify(x);
};

export default function DagTestPanel({
  dag,
  templateId = null,
  threshold,
  onPath,
}) {
  const [source, setSource] = useState("row");
  const [datasetId, setDatasetId] = useState("");
  const [rowId, setRowId] = useState("");
  const [mapping, setMapping] = useState({});
  const [values, setValues] = useState({});
  const [output, setOutput] = useState("");
  const [result, setResult] = useState(null);
  const [previous, setPrevious] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const { vars, reasons: inputReasons } = useDagInputs(dag);
  const { data: datasets = [] } = useDevelopDatasetList();
  const { data: table } = useQuery({
    queryKey: ["evals", "dag-test-rows", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { page_size: 500, current_page_index: 0 },
      }),
    select: (res) => ({
      columns: res.data?.result?.column_config ?? [],
      rows: res.data?.result?.table ?? [],
    }),
    enabled: Boolean(datasetId),
  });
  const columns = useMemo(() => table?.columns ?? [], [table]);
  const mapped = useMemo(() => {
    const out = {};
    vars.forEach((v) => {
      out[v] =
        mapping[v] ??
        columns.find((c) => c.name === v)?.id ??
        (v === "expected"
          ? columns.find((c) => c.role === "expected")?.id
          : null) ??
        "";
    });
    return out;
  }, [vars, mapping, columns]);
  // Input requirements come from the live resolver, not saved child metadata.
  const errors = validateDag(dag);
  const reasons = [
    ...new Set([
      ...errors,
      ...inputReasons,
      ...vars
        .filter((v) => !(source === "row" ? mapped[v] : values[v]))
        .map((v) => `Map {{${v}}} to an input`),
    ]),
    ...(source === "row" && !rowId ? ["Pick a row"] : []),
    ...(!output.trim() ? ["Write a sample output"] : []),
  ];
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = {
        dag,
        output,
        pass_threshold: threshold,
        ...(templateId ? { template_id: templateId } : {}),
        ...(source === "row"
          ? { dataset_id: datasetId, row_id: rowId, mapping: mapped }
          : { values }),
      };
      const { data } = await axios.post(
        endpoints.develop.w7.dagExecuteAdhoc,
        body,
      );
      const res = data?.result ?? data;
      setPrevious(result);
      setResult(res);
      onPath?.({ steps: res.path ?? [], leafId: res.leaf?.id ?? null });
    } catch (e) {
      setError(
        e?.response?.data?.errors?.join(" · ") ?? e?.message ?? "Test failed",
      );
    } finally {
      setBusy(false);
    }
  };
  const same =
    previous && result ? previous.path_key === result.path_key : null;
  return (
    <Box
      data-testid="dag-test"
      sx={{
        p: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Typography typography="s1" fontWeight={600} sx={{ mb: 1 }}>
        Test on a row
      </Typography>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={source}
        onChange={(_, v) => v && setSource(v)}
        sx={{ mb: 1 }}
      >
        <ToggleButton
          value="row"
          sx={{ textTransform: "none", fontSize: "12px" }}
        >
          Dataset row
        </ToggleButton>
        <ToggleButton
          value="values"
          sx={{ textTransform: "none", fontSize: "12px" }}
        >
          Type values
        </ToggleButton>
      </ToggleButtonGroup>
      {source === "row" ? (
        <Stack gap={1}>
          <Stack direction="row" gap={1}>
            <TextField
              select
              size="small"
              label="Dataset"
              value={datasetId}
              sx={{ ...fieldSx, minWidth: 220 }}
              onChange={(e) => {
                setDatasetId(e.target.value);
                setRowId("");
                setMapping({});
              }}
            >
              {datasets.map((d) => (
                <MenuItem key={d.datasetId} value={d.datasetId}>
                  {d.name}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Row"
              value={rowId}
              disabled={!datasetId}
              sx={{ ...fieldSx, minWidth: 220 }}
              onChange={(e) => setRowId(e.target.value)}
              SelectProps={{
                MenuProps: { PaperProps: { sx: { maxHeight: 320 } } },
              }}
            >
              {(table?.rows ?? []).map((r, i) => (
                <MenuItem
                  key={r.row_id}
                  value={r.row_id}
                  sx={{ fontSize: "12px" }}
                >
                  {`Row ${Number.isFinite(r.order) ? r.order + 1 : i + 1} · ${cellText(r[columns[0]?.id]).slice(0, 40)}`}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          {vars.map((v) => (
            <TextField
              key={v}
              select
              size="small"
              label={`{{${v}}}`}
              value={mapped[v]}
              disabled={!datasetId}
              sx={{ ...fieldSx, maxWidth: 300 }}
              onChange={(e) => setMapping({ ...mapping, [v]: e.target.value })}
            >
              {columns.map((c) => (
                <MenuItem key={c.id} value={c.id}>
                  {c.name}
                </MenuItem>
              ))}
            </TextField>
          ))}
        </Stack>
      ) : (
        <Stack gap={1}>
          {vars.map((v) => (
            <TextField
              key={v}
              size="small"
              label={`{{${v}}}`}
              value={values[v] ?? ""}
              sx={fieldSx}
              onChange={(e) => setValues({ ...values, [v]: e.target.value })}
            />
          ))}
        </Stack>
      )}
      <TextField
        size="small"
        multiline
        minRows={2}
        label="Sample output"
        value={output}
        sx={{ ...fieldSx, mt: 1, width: "100%" }}
        onChange={(e) => setOutput(e.target.value)}
        placeholder="The reply the tree judges"
      />
      <Stack direction="row" alignItems="center" gap={1} sx={{ mt: 1 }}>
        <Button
          size="small"
          variant="contained"
          disabled={busy || reasons.length > 0}
          onClick={run}
          sx={{ textTransform: "none" }}
          title={reasons.join(" · ")}
        >
          {result ? "Test again" : "Test"}
        </Button>
        {reasons.length ? (
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="dag-test-reasons"
          >
            {reasons.join(" · ")}
          </Typography>
        ) : null}
        {same != null ? (
          <Typography
            typography="s3"
            fontWeight={600}
            data-testid="dag-test-same"
          >
            {same
              ? "Same path as the last Test"
              : "The path changed since the last Test"}
          </Typography>
        ) : null}
      </Stack>
      {error ? (
        <Typography typography="s3" color="error.main" sx={{ mt: 1 }}>
          {error}
        </Typography>
      ) : null}
      {result ? (
        <Box sx={{ mt: 1 }} data-testid="dag-test-result">
          <DagPathSteps path={result.path ?? []} leaf={result.leaf} />
          <Typography
            typography="s2"
            fontWeight={600}
            sx={{ mt: 0.5 }}
            data-testid="dag-test-total"
          >
            {result.leaf
              ? [
                  result.reason,
                  result.passed ? "Pass" : "Fail",
                  isMoneyValue(result.judge_cost_usd)
                    ? formatExactMoney(result.judge_cost_usd, {
                        minimumFractionDigits: 5,
                      })
                    : `${result.judge_calls ?? 0} judge call${result.judge_calls === 1 ? "" : "s"}`,
                  `${Math.round(result.latency_ms ?? 0)} ms`,
                ].join(" · ")
              : result.reason}
          </Typography>
        </Box>
      ) : null}
    </Box>
  );
}

DagTestPanel.propTypes = {
  dag: PropTypes.object.isRequired,
  templateId: PropTypes.string,
  threshold: PropTypes.number,
  onPath: PropTypes.func,
};
