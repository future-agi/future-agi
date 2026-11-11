import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  InputAdornment,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useController, useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
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
import {
  SamplingDisclosure,
  SamplingWindow,
} from "./sampling_selection_summary";
import SamplingPickIllustration from "./sampling_pick_illustration";
import {
  advancedSamplingSummary,
  compactSamplingPreview,
  isSimpleSamplingPolicy,
  pickLabel,
  pickLevels,
  pickNames,
  rowNames,
  samplingPreviewGroups,
  samplingPreviewShortfall,
  samplingPreviewWork,
  samplingShortfallText,
  samplingWindowInvalid,
  simpleGroupsForValues,
  simpleGroupSelectionText,
  samplingGroupValueLabel,
  unitCount,
} from "./simpleSamplingPolicy";

const numeric = { fontVariantNumeric: "tabular-nums" };

// Requested versus available per group: the reason a preview is short.
function PreviewGroupsTable({ groups, live, rowType }) {
  const rows = rowNames(rowType);
  return (
    <Table size="small" aria-label="Preview by group">
      <TableHead>
        <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
          <TableCell>Group</TableCell>
          <TableCell align="right">{live ? "Share" : "Requested"}</TableCell>
          <TableCell align="right">Available</TableCell>
          <TableCell align="right">Picked</TableCell>
          <TableCell align="right">
            {`${rows[0].toUpperCase()}${rows.slice(1)}`} evaluated
          </TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {groups.map((group) => (
          <TableRow key={group.key} sx={{ height: ROW_HEIGHT.default }}>
            <TableCell sx={{ overflowWrap: "anywhere" }}>
              {group.label}
            </TableCell>
            <TableCell align="right" sx={numeric}>
              {live ? `${group.requested}%` : group.requested.toLocaleString()}
            </TableCell>
            <TableCell align="right" sx={numeric}>
              {group.eligible.toLocaleString()}
            </TableCell>
            <TableCell align="right" sx={numeric}>
              {group.selected.toLocaleString()}
            </TableCell>
            <TableCell align="right" sx={numeric}>
              {group.rows.toLocaleString()}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
PreviewGroupsTable.propTypes = {
  groups: PropTypes.arrayOf(PropTypes.object).isRequired,
  live: PropTypes.bool,
  rowType: PropTypes.string,
};

// `label` is shown on the field; `inputLabel` names an unlabelled field in a
// quota row, whose visible label sits beside it. A share shows its "%" in
// the field, so a share never reads as a count.
function AmountField({ label, inputLabel, rule, live, onChange, error, sx }) {
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
      inputProps={{
        min: 0,
        max: live ? 100 : 1000000,
        step: live ? "any" : 1,
        ...(inputLabel && { "aria-label": inputLabel }),
      }}
      InputProps={
        live
          ? { endAdornment: <InputAdornment position="end">%</InputAdornment> }
          : undefined
      }
      onChange={(event) =>
        onChange({
          ...rule,
          [key]: event.target.value === "" ? "" : Number(event.target.value),
        })
      }
      sx={{ "& input": numeric, ...sx }}
    />
  );
}
AmountField.propTypes = {
  label: PropTypes.string,
  inputLabel: PropTypes.string,
  rule: PropTypes.object.isRequired,
  live: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  error: PropTypes.string,
  sx: PropTypes.object,
};

// One line of the per-group list: the group, then its count or share.
function QuotaRow({ label, inputLabel, ...field }) {
  return (
    <Stack direction="row" spacing={1.5} alignItems="flex-start">
      <Typography
        variant="s1"
        component="div"
        title={label}
        noWrap
        sx={{ flex: 1, minWidth: 0, pt: 0.75 }}
      >
        {label}
      </Typography>
      <AmountField
        inputLabel={inputLabel}
        {...field}
        sx={{ width: 160, flexShrink: 0 }}
      />
    </Stack>
  );
}
QuotaRow.propTypes = {
  label: PropTypes.string.isRequired,
  inputLabel: PropTypes.string.isRequired,
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
  const [remainderNotice, setRemainderNotice] = useState("");
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
  const previewGroups = invalid ? null : samplingPreviewGroups(preview, policy);
  const shortfall = samplingPreviewShortfall(previewGroups, policy);
  // The work line adds only what the headline lacks: the pool and the runs.
  const work = invalid ? null : samplingPreviewWork(preview, policy, rowType);
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
  // Null while any count is invalid, so no total is shown for a half-typed one.
  const groupSummary = simpleGroupSelectionText(policy);
  const groupTotal =
    groupSummary && !live
      ? policy.strata.reduce(
          (sum, rule) => sum + rule.target_n,
          policy.default.target_n || 0,
        )
      : null;
  const excludedValues = groupValues.length
    ? ` (not ${new Intl.ListFormat("en", { type: "disjunction" }).format(groupValues.map(samplingGroupValueLabel))})`
    : "";
  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      {!simple ? (
        <Typography
          role="status"
          variant="s1"
          component="div"
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
            // Indented like the group block, so the two field rows differ.
            <Stack
              sx={{ borderLeft: "1px solid", borderColor: "divider", pl: 1.5 }}
            >
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
            </Stack>
          )}
          {!unitInvalid && (
            <SamplingPickIllustration
              unit={policy.sampling_unit}
              rowType={rowType}
            />
          )}
          {!groupValues.length && (
            <AmountField
              label={
                live ? `What share of new ${units}? (%)` : `How many ${units}?`
              }
              rule={policy.default}
              live={live}
              error={errors[`default.${key}`]}
              onChange={(rule) => update({ ...policy, default: rule })}
              sx={{ maxWidth: 280 }}
            />
          )}
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
                  setRemainderNotice("");
                  update({ ...policy, strata: [] });
                }}
              />
              <SamplingValuePicker
                // Values typed for one field never carry over to another.
                key={`${groupField.source || "span"}:${groupField.key}`}
                project={project}
                distinctGroups
                predicate={{ ...groupField, op: "in", values: groupValues }}
                onChange={(values) => {
                  const strata = simpleGroupsForValues(
                    policy,
                    {
                      source: groupField.source || "span",
                      key: groupField.key,
                    },
                    values,
                  );
                  // The count set before any group silently changing meaning
                  // is what users miss; say it once, when it happens.
                  setRemainderNotice(
                    !policy.strata.length && strata.length
                      ? `${
                          typeof policy.default[key] === "number"
                            ? `The ${live ? `${policy.default.rate}% share` : `count of ${policy.default.target_n.toLocaleString()}`} you set`
                            : `The ${live ? "share" : "count"} you set`
                        } now applies only to ${units} outside these groups.`
                      : strata.length
                        ? remainderNotice
                        : "",
                  );
                  update({ ...policy, strata });
                }}
              />
              {groupValues.length > 0 && (
                <Stack
                  spacing={0.75}
                  role="group"
                  aria-label="Counts per group"
                >
                  {policy.strata.map((rule, index) => (
                    <QuotaRow
                      key={rule.name}
                      label={samplingGroupValueLabel(rule.filters[0].values[0])}
                      inputLabel={`${String(rule.filters[0].values[0])}${live ? " (%)" : ""}`}
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
                  <QuotaRow
                    label={`All other ${units}`}
                    inputLabel={`All other ${units}${excludedValues}${live ? " (%)" : ""}`}
                    rule={policy.default}
                    live={live}
                    error={errors[`default.${key}`]}
                    onChange={(rule) => update({ ...policy, default: rule })}
                  />
                  {groupSummary && (
                    <Typography
                      variant="s1"
                      component="div"
                      aria-label="Requested group selection"
                      sx={{
                        fontWeight: 500,
                        overflowWrap: "anywhere",
                        borderTop: "1px solid",
                        borderColor: "divider",
                        pt: 0.75,
                      }}
                    >
                      {live
                        ? `Each group keeps its own share of new ${units}; shares are not added together.`
                        : `Up to ${groupTotal.toLocaleString()} ${units} in total`}
                    </Typography>
                  )}
                  {remainderNotice && (
                    <Typography
                      role="status"
                      variant="s3"
                      component="div"
                      color="text.secondary"
                    >
                      {remainderNotice}
                    </Typography>
                  )}
                </Stack>
              )}
              <Button
                size="small"
                sx={{ alignSelf: "flex-start" }}
                onClick={() => {
                  setGroupDraft(null);
                  setRemainderNotice("");
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
      >
        {simple && (
          // The inputs are the exact UTC bounds, so no separate read-only copy.
          <SamplingDisclosure label="Change time window">
            <Stack direction="row" spacing={1} sx={{ pt: 0.5 }}>
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
                        [bound]: Number.isFinite(+date)
                          ? date.toISOString()
                          : "",
                      },
                    });
                  }}
                />
              ))}
            </Stack>
            <Typography
              variant="s3"
              component="div"
              color="text.secondary"
              sx={{ mt: 0.5 }}
            >
              {live
                ? "New groups are admitted from this start time."
                : "Choose a past window of at most 31 days. Counts are bounded by what exists."}
            </Typography>
          </SamplingDisclosure>
        )}
      </SamplingWindow>
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
        <Typography
          role="status"
          variant="s3"
          component="div"
          color="error.main"
        >
          Fix the highlighted sampling values before previewing or saving.
        </Typography>
      )}
      {error && <Alert severity="error">{error}</Alert>}
      {preview && (
        <Stack
          spacing={0.75}
          aria-label="Sampling population preview"
          sx={{ minWidth: 0 }}
        >
          <Typography
            role="status"
            aria-label="Sampling preview summary"
            variant="s1"
            component="div"
            sx={{ fontWeight: 500 }}
          >
            {compact || "Selected groups and evaluated rows: Unavailable."}
          </Typography>
          {shortfall && (
            <Stack direction="row" spacing={0.75} alignItems="flex-start">
              <Iconify
                icon="solar:danger-triangle-linear"
                width={16}
                sx={{ color: "status.warning.fg", flexShrink: 0, mt: 0.125 }}
              />
              <Typography
                variant="s1"
                component="div"
                aria-label="Sampling preview shortfall"
              >
                {policy.strata.length
                  ? samplingShortfallText(shortfall, policy.sampling_unit)
                  : `Only ${unitCount(shortfall.selected, policy.sampling_unit)} ${shortfall.selected === 1 ? "matches" : "match"} in this window, fewer than the ${shortfall.requested.toLocaleString()} requested. Widen the window or lower the count.`}
              </Typography>
            </Stack>
          )}
          <Typography variant="s1" component="div" color="text.secondary">
            {(detailSummary && work) || "Evaluation work is unavailable."}
          </Typography>
          {previewGroups && previewGroups.length > 1 && (
            <PreviewGroupsTable
              groups={previewGroups}
              live={live}
              rowType={rowType}
            />
          )}
          <Typography variant="s3" component="div" color="text.secondary">
            Preview only, not a reservation or executed results.{" "}
            {live
              ? "New groups continue to arrive; these counts cover the preview window only."
              : "Membership freezes at first execution, not when previewed or saved."}
          </Typography>
          {(preview.expected_evaluations != null ||
            preview.population_note ||
            preview.cost_note) && (
            <SamplingDisclosure label="How this is counted">
              <Stack spacing={0.5}>
                {preview.expected_evaluations != null && (
                  <Typography
                    variant="s3"
                    component="div"
                    color="text.secondary"
                  >
                    {`Expected evaluation runs: ${Number(preview.expected_evaluations.toFixed(2)).toLocaleString()} across ${preview.evaluator_count} evaluator(s).`}
                  </Typography>
                )}
                {[preview.population_note, preview.cost_note]
                  .filter(Boolean)
                  .map((note) => (
                    <Typography
                      key={note}
                      variant="s3"
                      component="div"
                      color="text.secondary"
                    >
                      {note}
                    </Typography>
                  ))}
              </Stack>
            </SamplingDisclosure>
          )}
        </Stack>
      )}
    </Stack>
  );
}
SamplingPolicyEditor.propTypes = {
  control: PropTypes.object.isRequired,
  getValues: PropTypes.func.isRequired,
};
