import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Alert, Box, Button, MenuItem, Stack, Typography } from "@mui/material";

function Section({ title, description, children }) {
  return (
    <Stack spacing={2} component="section" sx={{ minWidth: 0 }}>
      <Box>
        <Typography component="h2" sx={{ fontSize: 13, fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {description}
        </Typography>
      </Box>
      {children}
    </Stack>
  );
}
Section.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  children: PropTypes.node,
};
import TextField from "./MetricField";
import MetricInputs from "./MetricInputs";
import MetricProjectSelect from "./MetricProjectSelect";
import useMetricEvaluatorNames from "./use_metric_evaluator_names";
import { weightedInputProblems } from "./metricForm";
import {
  applyCalculation,
  calculationFromForm,
  calculations,
  definitionSummary,
  plainDefinitionSummary,
} from "./metric_calculation";

export default function MetricDefinitionEditor({
  form,
  metric,
  initialDraft,
  onDraftChange,
  projects,
  projectSearch,
  projectPage,
  onProjectSearch,
  onProjectPage,
  onChange,
  onReplace,
  busy,
  readOnly,
  dirty,
  onValidate,
  onSave,
  onCancel,
  saveBlocked,
}) {
  const evaluatorNames = useMetricEvaluatorNames(form.project_id, form.inputs);
  const [step, setStep] = useState(0);
  const [calculation, setCalculation] = useState(() =>
    calculationFromForm(form),
  );
  const [advanced, setAdvanced] = useState(false);
  const [filtersText, setFiltersText] = useState(
    () =>
      initialDraft?.filtersText ?? JSON.stringify(form.filters || [], null, 2),
  );
  const [filtersError, setFiltersError] = useState(
    initialDraft?.filtersError || "",
  );
  const custom = calculation.kind === "custom";
  const autoAddedInput = useRef(null);
  const changeCalculation = (patch) => {
    if (readOnly || busy) return;
    const next = { ...calculation, ...patch };
    let source = form;
    // Only remove the untouched placeholder this editor itself created. Loaded
    // inputs and any configured denominator must survive calculation changes.
    if (!["ratio", "custom"].includes(next.kind) && autoAddedInput.current) {
      source = {
        ...form,
        inputs: form.inputs.filter(
          (input) =>
            JSON.stringify(input) !== JSON.stringify(autoAddedInput.current),
        ),
      };
      autoAddedInput.current = null;
    }
    const result = applyCalculation(source, next);
    if (next.kind === "ratio" && source.inputs.length === 1)
      autoAddedInput.current = result.inputs[1];
    setCalculation(next);
    onReplace(result);
  };
  const changeInputs = (inputs) => {
    if (
      autoAddedInput.current &&
      JSON.stringify(
        inputs.find((input) => input.alias === autoAddedInput.current.alias),
      ) !== JSON.stringify(autoAddedInput.current)
    )
      autoAddedInput.current = null;
    onChange("inputs", inputs);
  };
  const sourceReady = form.inputs.every((input) =>
    input.source === "eval"
      ? Boolean(input.config_id)
      : Boolean(input.field) &&
        (!input.field.startsWith("attribute_") || Boolean(input.attribute_key)),
  );
  const calculationReady =
    calculation.kind !== "count_if" ||
    (calculation.threshold.trim() !== "" &&
      Number.isFinite(Number(calculation.threshold)) &&
      Math.abs(Number(calculation.threshold)) <= 1e15);
  return (
    <Stack
      component="section"
      aria-label="Metric definition editor"
      sx={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        width: "100%",
        overflowWrap: "anywhere",
        "& .MuiFormControl-root": { minWidth: 0, maxWidth: "100%" },
        "& pre, & code": {
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          maxWidth: "100%",
        },
      }}
    >
      <Stack
        gap={2.5}
        sx={{ flex: 1, overflowY: "auto", minHeight: 0, p: 2.5 }}
      >
        <Stack direction="row" justifyContent="space-between" gap={1}>
          <Typography variant="body2">
            {metric
              ? `Saved version ${metric.current_version}`
              : "New definition"}
            {dirty ? " · Unsaved changes" : ""}
          </Typography>
        </Stack>
        <Typography
          variant="body2"
          aria-label="Definition steps"
          aria-live="polite"
          sx={{ fontWeight: 600 }}
        >
          Step {step + 1} of 3 · {["Data", "Calculation", "Review"][step]}
        </Typography>
        {step === 0 && (
          <Section
            title="Data"
            description="Summarize saved evaluation results or numeric span fields. No evaluator is created or run here."
          >
            <Stack gap={2}>
              <TextField
                label="Metric name"
                fullWidth
                value={form.name}
                readOnly={readOnly}
                disabled={busy}
                onChange={(e) => onChange("name", e.target.value)}
              />
              <MetricProjectSelect
                value={form.project_id}
                projects={projects}
                search={projectSearch}
                page={projectPage}
                onSearch={onProjectSearch}
                onPage={onProjectPage}
                readOnly={Boolean(metric) || readOnly}
                disabled={busy}
                onChange={(projectId) =>
                  onReplace({
                    ...form,
                    project_id: projectId,
                    inputs: form.inputs.map((input) =>
                      input.source === "eval"
                        ? { ...input, config_id: "", config_hash: "" }
                        : input,
                    ),
                  })
                }
              />
              {projects.isError && (
                <Alert severity="error">
                  Projects could not be loaded.{" "}
                  <Button onClick={() => projects.refetch()}>
                    Retry projects
                  </Button>
                </Alert>
              )}
              <TextField
                label="Description"
                fullWidth
                value={form.description}
                readOnly={readOnly}
                disabled={busy}
                onChange={(e) => onChange("description", e.target.value)}
              />
              <TextField
                select
                label="Summarize one value per"
                value={form.selection_unit}
                readOnly={readOnly}
                disabled={busy}
                onChange={(e) => onChange("selection_unit", e.target.value)}
                helperText="First reduce each input to one value per unit; then calculate across units."
              >
                {["session", "user", "span"].map((value) => (
                  <MenuItem key={value} value={value}>
                    {value}
                  </MenuItem>
                ))}
              </TextField>
              <MetricInputs
                key={form.project_id}
                inputs={form.inputs}
                projectId={form.project_id}
                selectionUnit={form.selection_unit}
                guided={!custom}
                disabled={busy}
                readOnly={readOnly}
                onChange={changeInputs}
              />
              <Typography color="text.secondary" variant="body2">
                If a unit has several values, each input uses its selected
                reduction: latest, mean, min or max.
              </Typography>
            </Stack>
          </Section>
        )}
        {step === 1 && (
          <Section
            title="Calculation"
            description="Calculate across the per-unit values, not individual evaluation calls."
          >
            <Stack gap={2}>
              <TextField
                select
                label="Calculation"
                value={calculation.kind}
                readOnly={readOnly}
                disabled={busy}
                onChange={(e) => changeCalculation({ kind: e.target.value })}
              >
                {calculations.map(({ value, label }) => (
                  <MenuItem key={value} value={value}>
                    {label}
                  </MenuItem>
                ))}
              </TextField>
              {!sourceReady && !custom && calculation.kind !== "ratio" && (
                <Alert severity="warning">
                  A retained input needs a source before you can continue.
                  Loaded or configured inputs are never removed automatically.{" "}
                  <Button onClick={() => setStep(0)}>Fix data inputs</Button>
                </Alert>
              )}
              {calculation.kind === "count_if" && (
                <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
                  <TextField
                    select
                    fullWidth
                    label="Match condition"
                    value={calculation.operator}
                    readOnly={readOnly}
                    disabled={busy}
                    onChange={(e) =>
                      changeCalculation({ operator: e.target.value })
                    }
                  >
                    {[
                      [">=", "At least"],
                      [">", "Above"],
                      ["<=", "At most"],
                      ["<", "Below"],
                      ["==", "Equal to"],
                      ["!=", "Not equal to"],
                    ].map(([value, label]) => (
                      <MenuItem key={value} value={value}>
                        {label}
                      </MenuItem>
                    ))}
                  </TextField>
                  <TextField
                    fullWidth
                    label="Matching value"
                    type="number"
                    value={calculation.threshold}
                    readOnly={readOnly}
                    disabled={busy}
                    onChange={(e) =>
                      changeCalculation({ threshold: e.target.value })
                    }
                    error={!calculationReady}
                    helperText={
                      !calculationReady
                        ? "Enter a finite number, at most 1e15 in magnitude."
                        : undefined
                    }
                  />
                </Stack>
              )}
              {calculation.kind === "ratio" && (
                <>
                  <Typography variant="body2">
                    Total of input 1 divided by total of input 2. This is not
                    automatically a percentage.
                  </Typography>
                  <MetricInputs
                    inputs={form.inputs}
                    projectId={form.project_id}
                    selectionUnit={form.selection_unit}
                    guided
                    readOnly={readOnly}
                    disabled={busy}
                    onChange={changeInputs}
                  />
                </>
              )}
              {custom ? (
                <>
                  <Alert severity="info">
                    Custom formulas and all existing inputs are preserved. Use
                    this mode for multiple conditions or expressions.
                  </Alert>
                  <MetricInputs
                    inputs={form.inputs}
                    projectId={form.project_id}
                    readOnly={readOnly}
                    disabled={busy}
                    onChange={changeInputs}
                  />
                  <TextField
                    label="Formula"
                    multiline
                    minRows={2}
                    value={form.formula}
                    readOnly={readOnly}
                    disabled={busy}
                    onChange={(e) => onChange("formula", e.target.value)}
                    inputProps={{
                      style: { fontFamily: "monospace" },
                      spellCheck: false,
                    }}
                    helperText="ratio(a, b), mean(value), sum(value), count_if(condition); arithmetic, comparisons and and/or/not."
                  />
                </>
              ) : (
                <Box component="details">
                  <Box component="summary" sx={{ cursor: "pointer" }}>
                    View formula
                  </Box>
                  <Typography
                    component="pre"
                    variant="body2"
                    sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {form.formula}
                  </Typography>
                </Box>
              )}
            </Stack>
          </Section>
        )}
        {step === 2 && (
          <Section
            title="Review"
            description="Saving creates a definition, not a result. Run it on a time window afterwards."
          >
            <Stack gap={1.5}>
              <Typography variant="h6">
                {form.name || "Untitled metric"}
              </Typography>
              <Typography variant="body2">
                Project:{" "}
                {projects.data?.rows.find((p) => p.id === form.project_id)
                  ?.name || "Saved project"}{" "}
                · One value per {form.selection_unit}
              </Typography>
              <Typography variant="body2">
                {
                  calculations.find((item) => item.value === calculation.kind)
                    ?.label
                }
              </Typography>
              <Typography component="code" sx={{ overflowWrap: "anywhere" }}>
                {form.formula}
              </Typography>
              <Typography variant="body2">
                Missing inputs are excluded, not zero. Zero denominators have no
                value. Invalid probabilities invalidate the rollup.
              </Typography>
            </Stack>
          </Section>
        )}
        <Box>
          <Typography variant="body2" sx={{ overflowWrap: "anywhere", mb: 1 }}>
            {plainDefinitionSummary(form, evaluatorNames)}
          </Typography>
          {filtersError && !advanced && (
            <Alert severity="warning">
              Definition filters need a valid JSON array. Open Advanced
              configuration to fix them before saving.
            </Alert>
          )}
          <Button
            aria-expanded={advanced}
            onClick={() => setAdvanced((value) => !value)}
          >
            Advanced configuration
          </Button>
          {advanced && (
            <Stack gap={2} sx={{ mt: 2 }}>
              <Typography variant="body2">
                {definitionSummary(form, evaluatorNames)}
              </Typography>
              <TextField
                select
                label="Weighting"
                value={form.weighting}
                readOnly={readOnly}
                disabled={busy}
                onChange={(e) => onChange("weighting", e.target.value)}
                helperText="Weighting requires compatible persisted sampling provenance and exact evaluator hashes. An unweighted sample is not a population estimate."
              >
                <MenuItem value="none">
                  Unweighted · explicit probability 1
                </MenuItem>
                <MenuItem value="inverse_probability">
                  Inverse inclusion probability
                </MenuItem>
              </TextField>
              <TextField
                label="Cohort attribute key"
                value={form.cohort_key || ""}
                readOnly={readOnly}
                disabled={busy}
                onChange={(e) => onChange("cohort_key", e.target.value)}
              />
              <TextField
                label="Definition filters (JSON)"
                multiline
                minRows={2}
                value={filtersText}
                readOnly={readOnly}
                disabled={busy}
                error={Boolean(filtersError)}
                helperText={
                  filtersError ||
                  "Existing exact field/value filters. Empty array means no definition filters."
                }
                onChange={(e) => {
                  setFiltersText(e.target.value);
                  try {
                    const filters = JSON.parse(e.target.value);
                    if (!Array.isArray(filters))
                      throw new Error("Expected an array.");
                    onChange("filters", filters);
                    setFiltersError("");
                    onDraftChange?.({
                      filtersText: e.target.value,
                      filtersError: "",
                    });
                  } catch {
                    const message =
                      "Enter a JSON array of filters before saving.";
                    setFiltersError(message);
                    onDraftChange?.({
                      filtersText: e.target.value,
                      filtersError: message,
                    });
                  }
                }}
              />
              {!custom && (
                <Button
                  onClick={() => {
                    setCalculation((c) => ({ ...c, kind: "custom" }));
                    setStep(1);
                  }}
                >
                  Edit inputs and custom formula
                </Button>
              )}
            </Stack>
          )}
        </Box>
        {weightedInputProblems(form).map((problem) => (
          <Alert key={problem} severity="warning">
            {problem}
          </Alert>
        ))}
      </Stack>
      <Stack
        direction="row"
        gap={1}
        justifyContent="flex-end"
        flexWrap="wrap"
        sx={{
          flexShrink: 0,
          p: "10px 16px",
          borderTop: 1,
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Button
          size="small"
          variant="outlined"
          disabled={busy}
          onClick={onCancel}
        >
          {readOnly ? "Back to results" : "Cancel editing"}
        </Button>
        <Button
          size="small"
          disabled={step === 0 || busy}
          onClick={() => setStep((value) => value - 1)}
        >
          Back
        </Button>
        {step < 2 ? (
          <Button
            variant="contained"
            color="inherit"
            size="small"
            disabled={
              busy ||
              !form.project_id ||
              !form.name.trim() ||
              !sourceReady ||
              !calculationReady
            }
            onClick={() => setStep((value) => value + 1)}
          >
            Continue
          </Button>
        ) : (
          <>
            <Button
              size="small"
              disabled={
                busy || readOnly || Boolean(filtersError) || !calculationReady
              }
              onClick={onValidate}
            >
              Validate formula
            </Button>
            <Button
              variant="contained"
              size="small"
              disabled={
                busy ||
                readOnly ||
                saveBlocked ||
                Boolean(filtersError) ||
                !sourceReady ||
                !calculationReady
              }
              onClick={onSave}
            >
              {metric ? "Save new version" : "Save metric"}
            </Button>
          </>
        )}
      </Stack>
    </Stack>
  );
}
MetricDefinitionEditor.propTypes = {
  form: PropTypes.object.isRequired,
  metric: PropTypes.object,
  initialDraft: PropTypes.object,
  onDraftChange: PropTypes.func,
  projects: PropTypes.object.isRequired,
  projectSearch: PropTypes.string,
  projectPage: PropTypes.number,
  onProjectSearch: PropTypes.func,
  onProjectPage: PropTypes.func,
  onChange: PropTypes.func.isRequired,
  onReplace: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  readOnly: PropTypes.bool,
  dirty: PropTypes.bool,
  onValidate: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  saveBlocked: PropTypes.bool,
};
