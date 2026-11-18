import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useController, useWatch } from "react-hook-form";
import axios, { endpoints } from "src/utils/axios";
import { getNewTaskFilters } from "../schema";
import { samplingRequestError } from "./samplingRequestError";
import {
  samplingPolicyErrors,
  samplingPolicyForApi,
} from "./samplingPolicyForm";
import {
  SAMPLING_PREVIEW_SCOPE,
  samplingPreviewSummary,
  useSamplingPreview,
} from "./samplingPreviewSummary";
import {
  SamplingFieldPicker,
  SamplingValuePicker,
} from "./sampling_field_picker";
import { SamplingWindow } from "./sampling_selection_summary";
import SamplingPickIllustration from "./sampling_pick_illustration";
import {
  advancedSamplingSummary,
  compactSamplingPreview,
  isSimpleSamplingPolicy,
  pickLabel,
  pickLevels,
  pickNames,
  samplingWindowInvalid,
  simpleGroupsForValues,
  simpleGroupSelectionText,
  samplingGroupValueLabel,
} from "./simpleSamplingPolicy";

function AmountField({ label, rule, live, onChange, error }) {
  const key = live ? "rate" : "target_n";
  return (
    <TextField
      size="small"
      fullWidth
      type="number"
      label={label}
      value={rule[key] ?? ""}
      error={Boolean(error)}
      helperText={error}
      inputProps={{ min: 0, max: live ? 100 : 1000000, step: live ? "any" : 1 }}
      onChange={(event) =>
        onChange({
          ...rule,
          [key]: event.target.value === "" ? "" : Number(event.target.value),
        })
      }
    />
  );
}
AmountField.propTypes = {
  label: PropTypes.string.isRequired,
  rule: PropTypes.object.isRequired,
  live: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  error: PropTypes.string,
};

export default function SamplingPolicyEditor({ control, getValues }) {
  const { field } = useController({
    control,
    name: "samplingPolicy",
    defaultValue: null,
  });
  const [project, rowType] = useWatch({
    control,
    name: ["project", "rowType"],
  });
  const scope = useWatch({ control, name: SAMPLING_PREVIEW_SCOPE });
  const scopeKey = JSON.stringify(scope);
  const [, publishPreview] = useSamplingPreview(control);
  const request = useRef(0);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [groupDraft, setGroupDraft] = useState(null);
  const policy = field.value;
  const simple = policy && isSimpleSamplingPolicy(policy);
  const live = policy?.mode === "live";
  const units = pickNames(policy?.sampling_unit);
  const errors = samplingPolicyErrors(policy);
  const unitInvalid =
    policy && !pickLevels(rowType).includes(policy.sampling_unit);
  const invalid =
    unitInvalid ||
    samplingWindowInvalid(policy) ||
    Object.keys(errors).length > 0;
  const preview = result?.scopeKey === scopeKey ? result.data : null;
  const detailSummary = invalid
    ? null
    : samplingPreviewSummary(preview, policy, rowType);
  const compact = invalid
    ? null
    : compactSamplingPreview(preview, policy, rowType);
  useEffect(() => {
    request.current += 1;
    setResult(null);
    setBusy(false);
    setError("");
  }, [scopeKey]);
  useEffect(
    () => () => {
      request.current += 1;
    },
    [],
  );
  useEffect(() => {
    publishPreview(detailSummary ? { scopeKey, text: detailSummary } : null);
    return () => publishPreview(null);
  }, [detailSummary, publishPreview, scopeKey]);
  const update = (next) => {
    // This editor never drops opaque API fields. Its controls are unavailable
    // unless the entire policy can be represented, including every group rule.
    if (!simple) return;
    request.current += 1;
    field.onChange(next);
    setResult(null);
    setBusy(false);
    setError("");
  };
  const previewSample = async () => {
    if (!policy || invalid) return;
    const id = ++request.current;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const values = getValues();
      const { filters, attributeFilters } = getNewTaskFilters(values, project);
      const response = await axios.post(endpoints.project.samplingPreview(), {
        project,
        row_type: rowType,
        sampling_policy: samplingPolicyForApi(policy),
        evals: (values.evalsDetails || []).map((item) =>
          typeof item === "string" ? item : item.id,
        ),
        filters: {
          ...filters,
          ...(attributeFilters.length ? { filters: attributeFilters } : {}),
        },
      });
      if (id === request.current)
        setResult({ scopeKey, data: response.data.result });
    } catch (err) {
      if (id === request.current)
        setError(
          samplingRequestError(
            err,
            "preview",
            "Sampling preview could not be loaded. Please try again.",
          ),
        );
    } finally {
      if (id === request.current) setBusy(false);
    }
  };
  if (!policy) return null;
  const groupField = policy.strata?.[0]?.filters?.[0] || groupDraft;
  const groupValues = simple
    ? policy.strata.map((rule) => rule.filters[0].values[0])
    : [];
  const key = live ? "rate" : "target_n";
  const groupSummary = simpleGroupSelectionText(policy);
  const excludedValues = groupValues.length
    ? ` (not ${new Intl.ListFormat("en", { type: "disjunction" }).format(groupValues.map(samplingGroupValueLabel))})`
    : "";
  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      {!simple ? (
        <Typography
          role="status"
          variant="body2"
          aria-label="Advanced sampling settings"
        >
          {advancedSamplingSummary(policy)}
        </Typography>
      ) : (
        <>
          <TextField
            select
            size="small"
            label="Pick:"
            value={policy.sampling_unit}
            onChange={(event) => {
              const next = { ...policy, sampling_unit: event.target.value };
              if (next.sampling_unit !== "attribute")
                delete next.unit_attribute;
              update(next);
            }}
            fullWidth
          >
            {pickLevels(rowType).map((unit) => (
              <MenuItem key={unit} value={unit}>
                {pickLabel(unit, rowType)}
              </MenuItem>
            ))}
          </TextField>
          {policy.sampling_unit === "attribute" && (
            // Shown in place, not behind a disclosure: the attribute is
            // required whenever the project has no contact attribute set.
            <>
              <SamplingFieldPicker
                project={project}
                label="Custom ID attribute"
                value={policy.unit_attribute}
                emptyLabel="Project’s contact attribute (if set)"
                helperText="Leave empty to use the project’s contact attribute (if one is set in project settings); otherwise choose an attribute."
                onChange={(attribute) => {
                  const next = { ...policy };
                  if (attribute.key) next.unit_attribute = attribute;
                  else delete next.unit_attribute;
                  update(next);
                }}
              />
              {live && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  data-testid="live-custom-id-limit"
                >
                  Each new contact is checked against every span since this
                  policy started. Once that passes about 20 million spans, new
                  contacts can&apos;t be added and the task stops with an error;
                  start a new live policy with a later start date to continue.
                </Typography>
              )}
            </>
          )}
          {!unitInvalid && (
            <SamplingPickIllustration
              unit={policy.sampling_unit}
              rowType={rowType}
            />
          )}
          {groupSummary && (
            <Typography
              variant="body2"
              aria-label="Requested group selection"
              sx={{ fontWeight: 500, overflowWrap: "anywhere" }}
            >
              {groupSummary}
            </Typography>
          )}
          <AmountField
            label={
              groupValues.length
                ? `All other ${units}${excludedValues}${live ? " (%)" : ""}`
                : live
                  ? `What share of new ${units}? (%)`
                  : `How many ${units}?`
            }
            rule={policy.default}
            live={live}
            error={errors[`default.${key}`]}
            onChange={(rule) => update({ ...policy, default: rule })}
          />
          {!groupField ? (
            <Button
              size="small"
              variant="outlined"
              sx={{ alignSelf: "flex-start" }}
              onClick={() => setGroupDraft({ source: "span", key: "" })}
            >
              {live ? "Set a share per group" : "Set a count per group"}
            </Button>
          ) : (
            <Stack
              spacing={1.5}
              sx={{ borderLeft: "1px solid", borderColor: "divider", pl: 1.5 }}
            >
              <SamplingFieldPicker
                project={project}
                label="Group field"
                value={groupField}
                onChange={(next) => {
                  setGroupDraft(next);
                  update({ ...policy, strata: [] });
                }}
              />
              <SamplingValuePicker
                // Values typed for one field never carry over to another.
                key={`${groupField.source || "span"}:${groupField.key}`}
                project={project}
                distinctGroups
                predicate={{ ...groupField, op: "in", values: groupValues }}
                onChange={(values) =>
                  update({
                    ...policy,
                    strata: simpleGroupsForValues(
                      policy,
                      {
                        source: groupField.source || "span",
                        key: groupField.key,
                      },
                      values,
                    ),
                  })
                }
              />
              {policy.strata.map((rule, index) => (
                <AmountField
                  key={rule.name}
                  label={`${String(rule.filters[0].values[0])}${live ? " (%)" : ""}`}
                  rule={rule}
                  live={live}
                  error={errors[`strata.${index}.${key}`]}
                  onChange={(next) =>
                    update({
                      ...policy,
                      strata: policy.strata.map((item, i) =>
                        i === index ? next : item,
                      ),
                    })
                  }
                />
              ))}
              <Button
                size="small"
                sx={{ alignSelf: "flex-start" }}
                onClick={() => {
                  setGroupDraft(null);
                  update({ ...policy, strata: [] });
                }}
              >
                Remove per-group {live ? "shares" : "counts"}
              </Button>
            </Stack>
          )}
        </>
      )}
      <SamplingWindow
        start={policy.window?.start}
        end={policy.window?.end}
        ongoing={live}
      />
      {simple && (
        <Box
          component="details"
          sx={{ "& > summary": { cursor: "pointer", fontSize: 12 } }}
        >
          <summary>Change time window</summary>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1}
            sx={{ mt: 1 }}
          >
            {(live ? ["start"] : ["start", "end"]).map((bound) => (
              <TextField
                key={bound}
                fullWidth
                size="small"
                type="datetime-local"
                label={`UTC window ${bound}`}
                InputLabelProps={{ shrink: true }}
                value={(policy.window?.[bound] || "").slice(0, 16)}
                onChange={(event) => {
                  const date = new Date(`${event.target.value}Z`);
                  update({
                    ...policy,
                    window: {
                      ...policy.window,
                      [bound]: Number.isFinite(+date) ? date.toISOString() : "",
                    },
                  });
                }}
              />
            ))}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {live
              ? "New groups are admitted from this start time."
              : "Choose a past window of at most 31 days. Counts are bounded by what exists."}
          </Typography>
        </Box>
      )}
      {unitInvalid && (
        <Alert severity="warning">
          This pick level is finer than the rows being evaluated. Replace the
          advanced settings or restore the original row type before saving.
        </Alert>
      )}
      {samplingWindowInvalid(policy) && (
        <Alert severity="warning">
          Choose a valid past UTC window
          {live ? "." : " of no more than 31 days."}
        </Alert>
      )}
      <Button
        size="small"
        variant="outlined"
        sx={{ alignSelf: "flex-start" }}
        onClick={previewSample}
        disabled={busy || !project || invalid}
      >
        {busy ? "Previewing sample…" : "Preview sample"}
      </Button>
      {Object.keys(errors).length > 0 && (
        <Typography role="status" variant="caption" color="error.main">
          Fix the highlighted sampling values before previewing or saving.
        </Typography>
      )}
      {error && <Alert severity="error">{error}</Alert>}
      {preview && (
        <Box aria-label="Sampling population preview">
          <Typography
            role="status"
            aria-label="Sampling preview summary"
            variant="body2"
          >
            {compact || "Selected groups and evaluated rows: Unavailable."}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Preview only, not a reservation or executed results.{" "}
            {live
              ? "New groups continue to arrive; these counts cover the preview window only."
              : "Membership freezes at first execution, not when previewed or saved."}
          </Typography>
          <Box
            component="details"
            sx={{ mt: 1, "& > summary": { cursor: "pointer", fontSize: 12 } }}
          >
            <summary>Evaluation work</summary>
            <Typography variant="caption" component="div">
              {detailSummary || "Evaluation work is unavailable."}
            </Typography>
            <Typography variant="caption" component="div">
              {preview.expected_evaluations == null
                ? "Select evaluators to estimate evaluation runs."
                : `Expected evaluation runs: ${Number(preview.expected_evaluations.toFixed(2)).toLocaleString()} across ${preview.evaluator_count} evaluator(s).`}
            </Typography>
            {preview.population_note && (
              <Typography variant="caption" component="div">
                {preview.population_note}
              </Typography>
            )}
            {preview.cost_note && (
              <Typography variant="caption" component="div">
                {preview.cost_note}
              </Typography>
            )}
          </Box>
        </Box>
      )}
    </Stack>
  );
}
SamplingPolicyEditor.propTypes = {
  control: PropTypes.object.isRequired,
  getValues: PropTypes.func.isRequired,
};
