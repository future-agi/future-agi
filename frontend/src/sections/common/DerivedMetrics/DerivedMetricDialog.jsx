// `New run metric` (W5-4 B2): Name, Formula (the one expression language),
// Format, Higher is better, and a live line per run in view
// `#2B · 0.71 = 0.7 × 0.82 + 0.3 × 0.45 − 10 × 0.0031` from the server's
// evaluation of the draft. Save is disabled while the formula has an error.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useDebounce } from "src/hooks/use-debounce";
import { apiErrorText } from "src/sections/common/Analysis/workbench/w5Api";
import {
  FORMULA_FUNCTIONS,
  METRIC_FORMATS,
  definitionErrors,
  evalFieldDocs,
  formulaCatalog,
  partsLine,
} from "./formulaModel";
import { useFormulaPreview, useSaveDerivedMetrics } from "./useDerivedMetrics";

const blank = {
  name: "",
  formula: "",
  format: "number",
  higher_is_better: true,
};

export default function DerivedMetricDialog({
  open,
  onClose,
  datasetId,
  runs,
  metricDefs,
  existing,
  editing,
}) {
  const [draft, setDraft] = useState(editing ?? blank);
  const formula = useDebounce(draft.formula, 250);
  const runIds = runs.map((r) => r.run_id);
  const preview = useFormulaPreview(datasetId, runIds, formula);
  const save = useSaveDerivedMetrics(datasetId);
  const others = existing.filter((m) => m.id !== editing?.id);
  const errors = useMemo(
    () => definitionErrors(draft, metricDefs, others),
    [draft, metricDefs, others],
  );
  const fields = useMemo(
    () => formulaCatalog(metricDefs).fields.map((f) => f.name),
    [metricDefs],
  );
  // L7c g6 (metric:new, RELABEL): what each eval field reads, in words.
  const evalDocs = useMemo(() => evalFieldDocs(metricDefs), [metricDefs]);
  const set = (k) => (e) =>
    setDraft((d) => ({
      ...d,
      [k]: k === "higher_is_better" ? e.target.checked : e.target.value,
    }));
  const canSave = !Object.keys(errors).length && !save.isPending;
  const submit = () => {
    const next = editing
      ? existing.map((m) => (m.id === editing.id ? { ...m, ...draft } : m))
      : [...existing, draft];
    save.mutate(next, { onSuccess: () => onClose(true) });
  };
  const previewOk =
    preview.data && !preview.data.errors?.draft && formula === draft.formula;

  return (
    <Dialog open={open} onClose={() => onClose(false)} maxWidth="md" fullWidth>
      <DialogTitle>
        {editing ? "Edit run metric" : "New run metric"}
      </DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }}>
          <TextField
            size="small"
            label="Name"
            placeholder="ship_score"
            value={draft.name}
            onChange={set("name")}
            error={Boolean(draft.name && errors.name)}
            helperText={draft.name ? errors.name : " "}
          />
          <TextField
            size="small"
            label="Formula"
            placeholder="0.7 * correctness + 0.3 * tone - 10 * cost_usd"
            value={draft.formula}
            onChange={set("formula")}
            error={Boolean(draft.formula && errors.formula)}
            helperText={
              draft.formula && errors.formula
                ? errors.formula
                : `Fields: ${fields.join(", ")} · functions: ${FORMULA_FUNCTIONS.join(", ")}`
            }
            inputProps={{
              "aria-label": "Formula",
              spellCheck: false,
              style: { fontFamily: "monospace" },
            }}
          />
          {evalDocs.length ? (
            <Stack gap={0.25} data-testid="formula-field-docs" sx={{ mt: -1 }}>
              {evalDocs.map((d) => (
                <Typography
                  key={d.name}
                  typography="s3"
                  color="text.secondary"
                  sx={{ wordBreak: "break-word" }}
                >
                  <Box
                    component="span"
                    sx={{ fontFamily: "monospace", color: "text.primary" }}
                  >
                    {d.name}
                  </Box>
                  {` — ${d.what}`}
                </Typography>
              ))}
            </Stack>
          ) : null}
          <Stack direction="row" gap={2} alignItems="center">
            <TextField
              select
              size="small"
              label="Format"
              value={draft.format}
              onChange={set("format")}
              sx={{ minWidth: 200 }}
              inputProps={{ "aria-label": "Format" }}
            >
              {METRIC_FORMATS.map((f) => (
                <MenuItem key={f.key} value={f.key}>
                  {f.label}
                </MenuItem>
              ))}
            </TextField>
            <FormControlLabel
              control={
                <Checkbox
                  checked={draft.higher_is_better}
                  onChange={set("higher_is_better")}
                />
              }
              label="Higher is better"
            />
          </Stack>
          <Stack gap={0.5} data-testid="derived-preview">
            <Typography typography="s2" color="text.secondary">
              On the runs in view
            </Typography>
            {previewOk && !errors.formula ? (
              runs.map((r) => {
                const v = preview.data.values?.[r.run_id]?.draft;
                return (
                  <Typography
                    key={r.run_id}
                    typography="s2"
                    data-run={r.label}
                    data-value={v?.value ?? ""}
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {v
                      ? `${r.label} · ${partsLine(draft.formula, v, draft.format)}`
                      : `${r.label} · no value`}
                  </Typography>
                );
              })
            ) : (
              <Typography typography="s2" color="text.disabled">
                {errors.formula && draft.formula
                  ? "Fix the formula to see each run's value."
                  : "Write a formula to see each run's value."}
              </Typography>
            )}
          </Stack>
          {save.isError && (
            <Typography typography="s2" color="error.main">
              {apiErrorText(save.error)}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)}>Cancel</Button>
        <Button variant="contained" disabled={!canSave} onClick={submit}>
          Save metric
        </Button>
      </DialogActions>
    </Dialog>
  );
}

DerivedMetricDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string.isRequired,
  runs: PropTypes.array.isRequired,
  metricDefs: PropTypes.array.isRequired,
  existing: PropTypes.array.isRequired,
  editing: PropTypes.object,
};
