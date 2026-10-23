import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useController, useWatch } from "react-hook-form";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { getNewTaskFilters } from "../schema";

import {
  defaultSamplingPolicy,
  samplingPolicyErrors,
  samplingStratumLabel,
  selectionMode,
  withCoordination,
} from "./samplingPolicyForm";

const SOURCES = ["span", "resource", "metadata"];
// The active selection key (rate or target_n) is required: clearing it keeps
// the key with an explicit empty value so the mode cannot change and the
// policy cannot be previewed or saved until a value is entered. Optional
// floors, caps and census thresholds are removed when cleared.
const numberUpdate = (rule, key, text) => {
  const next = { ...rule };
  if (text !== "") next[key] = Number(text);
  else if (key === selectionMode(rule)) next[key] = "";
  else delete next[key];
  return next;
};

function RuleControls({ rule, onChange, live, errors = {} }) {
  const mode = selectionMode(rule);
  const target = mode === "target_n";
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1}>
        <TextField
          select
          size="small"
          label="Selection"
          value={mode}
          onChange={(event) => {
            const next = { ...rule };
            delete next.rate;
            delete next.target_n;
            next[event.target.value] = event.target.value === "rate" ? 10 : 100;
            onChange(next);
          }}
          sx={{ minWidth: 150 }}
        >
          <MenuItem value="rate">Rate (%)</MenuItem>
          <MenuItem value="target_n" disabled={live}>
            Target units
          </MenuItem>
        </TextField>
        <TextField
          size="small"
          type="number"
          label={target ? "Target units" : "Rate (%)"}
          value={rule[mode] ?? ""}
          error={Boolean(errors[mode])}
          helperText={errors[mode] || undefined}
          inputProps={{ min: 0, max: target ? 1000000 : 100 }}
          onChange={(event) =>
            onChange(numberUpdate(rule, mode, event.target.value))
          }
        />
      </Stack>
      {!live && (
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          {[
            ["min_n", "Minimum units"],
            ["max_n", "Maximum units"],
            ["rare_below", "Census if cell size ≤"],
          ].map(([key, label]) => (
            <TextField
              key={key}
              size="small"
              type="number"
              label={label}
              value={rule[key] ?? ""}
              error={Boolean(errors[key])}
              helperText={errors[key] || undefined}
              inputProps={{ min: 0, max: 1000000 }}
              onChange={(event) =>
                onChange(numberUpdate(rule, key, event.target.value))
              }
            />
          ))}
        </Stack>
      )}
    </Stack>
  );
}
RuleControls.propTypes = {
  rule: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  live: PropTypes.bool,
  errors: PropTypes.object,
};

const ruleErrors = (errors, prefix) =>
  Object.fromEntries(
    Object.entries(errors)
      .filter(([path]) => path.startsWith(`${prefix}.`))
      .map(([path, message]) => [path.slice(prefix.length + 1), message]),
  );

function PredicateEditor({ predicate, onChange, onRemove }) {
  return (
    <Stack
      spacing={1}
      sx={{ pl: 1, borderLeft: "2px solid", borderColor: "divider" }}
    >
      <Stack direction="row" spacing={1}>
        <TextField
          select
          label="Source"
          size="small"
          value={predicate.source}
          onChange={(e) => onChange({ ...predicate, source: e.target.value })}
          sx={{ minWidth: 105 }}
        >
          {SOURCES.map((s) => (
            <MenuItem key={s} value={s}>
              {s}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          label="Slice attribute"
          size="small"
          value={predicate.key}
          onChange={(e) => onChange({ ...predicate, key: e.target.value })}
          fullWidth
        />
        <Button aria-label="Remove slice predicate" onClick={onRemove}>
          Remove
        </Button>
      </Stack>
      <Stack direction="row" spacing={1}>
        <TextField
          select
          size="small"
          label="Operator"
          value={predicate.op}
          onChange={(e) =>
            onChange({
              ...predicate,
              op: e.target.value,
              values: ["gt", "gte", "lt", "lte"].includes(e.target.value)
                ? [0]
                : [],
            })
          }
          sx={{ minWidth: 130 }}
        >
          {[
            ["in", "is one of"],
            ["not_in", "is not one of"],
            ["exists", "exists"],
            ["missing", "is missing"],
            ["gt", ">"],
            ["gte", "≥"],
            ["lt", "<"],
            ["lte", "≤"],
          ].map(([v, l]) => (
            <MenuItem key={v} value={v}>
              {l}
            </MenuItem>
          ))}
        </TextField>
        {!["exists", "missing"].includes(predicate.op) && (
          <TextField
            label="Values (comma separated)"
            size="small"
            value={predicate.values.join(",")}
            onChange={(e) =>
              onChange({
                ...predicate,
                values: ["gt", "gte", "lt", "lte"].includes(predicate.op)
                  ? [Number(e.target.value)]
                  : e.target.value.split(",").map((s) => s.trim()),
              })
            }
            fullWidth
          />
        )}
      </Stack>
    </Stack>
  );
}
PredicateEditor.propTypes = {
  predicate: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

export default function SamplingPolicyEditor({ control, getValues }) {
  const { field } = useController({
    control,
    name: "samplingPolicy",
    defaultValue: null,
  });
  const { field: runTypeField } = useController({ control, name: "runType" });
  const project = useWatch({ control, name: "project" });
  const scope = useWatch({
    control,
    name: [
      "project",
      "rowType",
      "filters",
      "runType",
      "startDate",
      "endDate",
      "datePreset",
      "evalsDetails",
    ],
  });
  const scopeKey = JSON.stringify(scope);
  const requestRef = useRef(0);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    requestRef.current += 1;
    setPreview(null);
    setBusy(false);
  }, [scopeKey]);
  const policy = field.value;
  const policyErrors = samplingPolicyErrors(policy);
  const invalid = Object.keys(policyErrors).length > 0;
  const update = (next) => {
    requestRef.current += 1;
    setBusy(false);
    field.onChange(next);
    setPreview(null);
    setError("");
  };
  const setRule = (index, rule) =>
    update({
      ...policy,
      strata: policy.strata.map((r, i) => (i === index ? rule : r)),
    });
  const runPreview = async () => {
    if (invalid) return;
    const requestId = ++requestRef.current;
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const values = getValues();
      const { filters, attributeFilters } = getNewTaskFilters(values, project);
      const response = await axios.post(endpoints.project.samplingPreview(), {
        project,
        row_type: values.rowType,
        sampling_policy: policy,
        evals: (values.evalsDetails || []).map((item) =>
          typeof item === "string" ? item : item.id,
        ),
        filters: {
          ...filters,
          ...(attributeFilters.length ? { filters: attributeFilters } : {}),
        },
      });
      if (requestId === requestRef.current) setPreview(response.data.result);
    } catch (err) {
      if (requestId !== requestRef.current) return;
      setError(
        getSafeActionErrorMessage(
          err,
          "Preview failed. Check the policy, close quota windows and retry.",
        ),
      );
    } finally {
      if (requestId === requestRef.current) setBusy(false);
    }
  };
  return (
    <Stack
      spacing={1.5}
      sx={{ borderTop: "1px solid", borderColor: "divider", pt: 2 }}
    >
      <FormControlLabel
        control={
          <Switch
            checked={Boolean(policy)}
            onChange={(_, checked) =>
              update(checked ? defaultSamplingPolicy() : null)
            }
          />
        }
        label="Contact-aware sampling policy"
      />
      {policy && (
        <>
          <Typography variant="caption" color="text.secondary">
            Select contacts once, then evaluate every matching row in each
            selected contact. Task filters choose evaluation rows; the first
            matching slice assigns each contact exactly once.
          </Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <TextField
              select
              size="small"
              label="Sampling unit"
              value={policy.sampling_unit}
              onChange={(e) => {
                const next = { ...policy, sampling_unit: e.target.value };
                if (e.target.value !== "attribute") delete next.unit_attribute;
                update(next);
              }}
              fullWidth
            >
              {["span", "trace", "session", "user", "attribute"].map((u) => (
                <MenuItem key={u} value={u}>
                  {u === "attribute" ? "Custom contact attribute" : u}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Missing contact ID"
              value={policy.missing_id}
              onChange={(e) =>
                update({ ...policy, missing_id: e.target.value })
              }
              fullWidth
            >
              <MenuItem value="exclude">Exclude row</MenuItem>
              <MenuItem value="row">Sample row independently</MenuItem>
            </TextField>
          </Stack>
          {policy.sampling_unit === "attribute" && (
            <TextField
              label="Contact attribute"
              helperText="Span attribute. Leave empty to use the project’s configured contact attribute."
              size="small"
              value={policy.unit_attribute?.key || ""}
              onChange={(e) => {
                const next = { ...policy };
                if (e.target.value)
                  next.unit_attribute = { source: "span", key: e.target.value };
                else delete next.unit_attribute;
                update(next);
              }}
            />
          )}
          <TextField
            select
            size="small"
            label="Population mode"
            value={policy.mode}
            onChange={(e) => {
              const live = e.target.value === "live";
              const clean = (rule) => {
                if (!live) return rule;
                const { target_n, min_n, max_n, rare_below, ...rest } = rule;
                return { ...rest, rate: rule.rate ?? 10 };
              };
              if (live) runTypeField.onChange("continuous");
              update({
                ...policy,
                mode: e.target.value,
                window: {
                  ...policy.window,
                  end: live ? null : new Date(Date.now() - 1000).toISOString(),
                },
                strata: policy.strata.map(clean),
                default: clean(policy.default),
              });
            }}
          >
            <MenuItem value="closed">Closed window · exact quotas</MenuItem>
            <MenuItem value="live">Live window · percentage only</MenuItem>
          </TextField>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            {(policy.mode === "live" ? ["start"] : ["start", "end"]).map(
              (key) => (
                <TextField
                  key={key}
                  type="datetime-local"
                  label={`UTC window ${key}`}
                  size="small"
                  InputLabelProps={{ shrink: true }}
                  value={policy.window[key].slice(0, 16)}
                  onChange={(e) => {
                    if (e.target.value)
                      update({
                        ...policy,
                        window: {
                          ...policy.window,
                          [key]: new Date(`${e.target.value}Z`).toISOString(),
                        },
                      });
                  }}
                  fullWidth
                />
              ),
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {policy.mode === "live"
              ? "No end date: new contacts continue to be admitted. Preview covers the last 24 hours; first-admission decisions persist across future windows."
              : "Maximum 31 days. Closed cohorts freeze at first execution; new contacts arriving afterward are excluded, but late rows of selected contacts remain selected."}
            Neither mode truncates a contact to the legacy row limit. Trace rows
            use root-span attributes; session rows use the earliest eligible
            root for contact identity.
          </Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <TextField
              select
              size="small"
              label="Coordination"
              value={policy.coordination || "independent"}
              onChange={(e) => update(withCoordination(policy, e.target.value))}
              fullWidth
            >
              <MenuItem value="independent">
                Independent · this policy draws its own sample
              </MenuItem>
              <MenuItem value="project">
                Coordinated · nested across this project
              </MenuItem>
            </TextField>
            {policy.coordination === "project" && (
              <>
                <TextField
                  size="small"
                  label="PRN panel"
                  value={policy.panel ?? ""}
                  error={Boolean(policyErrors.panel)}
                  helperText={
                    policyErrors.panel ||
                    "Optional. Different panels draw independent numbers."
                  }
                  onChange={(e) => {
                    const next = { ...policy };
                    if (e.target.value) next.panel = e.target.value;
                    else delete next.panel;
                    update(next);
                  }}
                  fullWidth
                />
                <TextField
                  size="small"
                  type="number"
                  label="PRN start (%)"
                  value={policy.prn_start ?? ""}
                  error={Boolean(policyErrors.prn_start)}
                  helperText={
                    policyErrors.prn_start ||
                    "Where the selection interval begins on the hash circle."
                  }
                  inputProps={{ min: 0, max: 100 }}
                  onChange={(e) => {
                    const next = { ...policy };
                    if (e.target.value !== "")
                      next.prn_start = Number(e.target.value);
                    else delete next.prn_start;
                    update(next);
                  }}
                  fullWidth
                />
              </>
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {policy.coordination === "project"
              ? "Every coordinated policy in this project (and panel) gives each contact the same permanent random number: raising a rate or quota keeps every contact already selected, and tasks with equal rates select the same contacts. Disjoint start intervals give disjoint live samples."
              : "This policy draws its own sample: editing a rate or quota re-draws every contact."}
          </Typography>
          <TextField
            label="Group by attributes (comma separated)"
            size="small"
            helperText="Example: locale, category. Each combination becomes its own cell."
            value={policy.group_by
              .map(
                (a) => `${a.source === "span" ? "" : `${a.source}:`}${a.key}`,
              )
              .join(",")}
            onChange={(e) =>
              update({
                ...policy,
                group_by: (e.target.value.trim()
                  ? e.target.value.split(",")
                  : []
                )
                  .map((s) => s.trim())
                  .map((key) => {
                    const match = key.match(/^(metadata|resource):(.*)$/);
                    return match
                      ? { source: match[1], key: match[2] }
                      : { source: "span", key };
                  }),
              })
            }
          />
          {policy.strata.map((rule, index) => (
            <Stack
              key={index}
              spacing={1.5}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                p: 1.5,
              }}
            >
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="caption">Priority {index + 1}</Typography>
                <TextField
                  label="Slice name"
                  size="small"
                  value={rule.name}
                  onChange={(e) =>
                    setRule(index, { ...rule, name: e.target.value })
                  }
                  fullWidth
                />
                <Button
                  aria-label={`Move slice ${index + 1} up`}
                  disabled={index === 0}
                  onClick={() => {
                    const strata = [...policy.strata];
                    [strata[index - 1], strata[index]] = [
                      strata[index],
                      strata[index - 1],
                    ];
                    update({ ...policy, strata });
                  }}
                >
                  Up
                </Button>
                <Button
                  aria-label={`Remove slice ${index + 1}`}
                  onClick={() =>
                    update({
                      ...policy,
                      strata: policy.strata.filter((_, i) => i !== index),
                    })
                  }
                >
                  Remove
                </Button>
              </Stack>
              {rule.filters.map((predicate, pi) => (
                <PredicateEditor
                  key={pi}
                  predicate={predicate}
                  onChange={(next) =>
                    setRule(index, {
                      ...rule,
                      filters: rule.filters.map((p, i) =>
                        i === pi ? next : p,
                      ),
                    })
                  }
                  onRemove={() =>
                    setRule(index, {
                      ...rule,
                      filters: rule.filters.filter((_, i) => i !== pi),
                    })
                  }
                />
              ))}
              <Button
                size="small"
                onClick={() =>
                  setRule(index, {
                    ...rule,
                    filters: [
                      ...rule.filters,
                      { source: "span", key: "", op: "in", values: [] },
                    ],
                  })
                }
              >
                Add slice predicate
              </Button>
              <RuleControls
                rule={rule}
                onChange={(next) => setRule(index, next)}
                live={policy.mode === "live"}
                errors={ruleErrors(policyErrors, `strata.${index}`)}
              />
            </Stack>
          ))}
          <Button
            variant="outlined"
            onClick={() =>
              update({
                ...policy,
                strata: [
                  ...policy.strata,
                  {
                    name: `Slice ${policy.strata.length + 1}`,
                    filters: [],
                    rate: 100,
                  },
                ],
              })
            }
          >
            Add priority slice
          </Button>
          <Typography variant="subtitle2">All remaining contacts</Typography>
          <RuleControls
            rule={policy.default}
            onChange={(rule) => update({ ...policy, default: rule })}
            live={policy.mode === "live"}
            errors={ruleErrors(policyErrors, "default")}
          />
          <Typography variant="caption" color="text.secondary">
            Rare-cell census includes every eligible unit below the threshold,
            subject to the maximum. A minimum cannot create units that are not
            present. Inclusion probabilities are saved with results for weighted
            metrics.
          </Typography>
          <Button
            variant="outlined"
            onClick={runPreview}
            disabled={busy || !project || invalid}
          >
            {busy ? "Previewing population…" : "Preview sampling policy"}
          </Button>
          {invalid && (
            <Typography variant="caption" color="error" role="status">
              Fix the highlighted sampling values before previewing or saving.
            </Typography>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          {preview && (
            <Box aria-label="Sampling population preview">
              <Typography variant="caption">
                {preview.population_note}
              </Typography>
              <Box sx={{ overflowX: "auto" }}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      {[
                        "Stratum",
                        "Eligible units",
                        "Expected units",
                        "Selected rows",
                      ].map((label) => (
                        <TableCell key={label}>{label}</TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {preview.strata.map((cell) => (
                      <TableRow key={samplingStratumLabel(cell.stratum)}>
                        <TableCell sx={{ wordBreak: "break-word" }}>
                          {samplingStratumLabel(cell.stratum)}
                        </TableCell>
                        <TableCell>{cell.eligible_units}</TableCell>
                        <TableCell>
                          {Number(cell.expected_units.toFixed(2))}
                        </TableCell>
                        <TableCell>{cell.selected_rows}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
              {!preview.strata.length && (
                <Typography variant="body2">
                  No eligible contacts in this window.
                </Typography>
              )}
              <Typography variant="caption" display="block">
                {preview.expected_evaluations == null
                  ? "Select evaluators to estimate the number of evaluation runs."
                  : `Expected evaluation runs: ${Number(preview.expected_evaluations.toFixed(2)).toLocaleString()} across ${preview.evaluator_count} evaluator(s).`}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {preview.cost_note}
              </Typography>
            </Box>
          )}
        </>
      )}
    </Stack>
  );
}
SamplingPolicyEditor.propTypes = {
  control: PropTypes.object.isRequired,
  getValues: PropTypes.func.isRequired,
};
