import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import EmptyState from "src/components/empty-state";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT } from "src/theme/sizes";
import { KIND_LABELS, LEVEL_LABELS, OPERATOR_LABELS } from "./builderSpec";
import { firstCalculationBadge } from "./metricCalculationState";
import {
  enumLabel,
  inputLabel,
  inputSourceKind,
  recipeSentence,
  REDUCER_LABELS,
  VALUE_FORMAT_LABELS,
  VALUE_TYPE_LABELS,
  WEIGHTING_LABELS,
} from "./metricPresentation";
import { formatMetricTime } from "./metricTime";

const literal = (value) =>
  value === undefined ? "Not specified" : JSON.stringify(value);

const line = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const stored = (value) => (value ? `Stored as ${value}` : undefined);
// An observation-stage input keeps a unit when its observations match.
const OBSERVATION_REDUCER_LABELS = {
  any: "Any observation matches",
  all: "Every observation matches",
};

// The source a person picked, then where it comes from. An evaluation input
// carries only its saved ID: its name shows when the project's saved
// evaluations return one, and the ID stays as the caption either way.
function InputSource({ input, label, evaluatorName }) {
  const kind = inputSourceKind(input);
  const name =
    input.source === "eval"
      ? evaluatorName || null
      : input.attribute_key ||
        (label !== input.alias ? label : input.field) ||
        null;
  return (
    <>
      <Typography variant="body2" component="div" sx={line}>
        {name || kind || "Not specified"}
      </Typography>
      {name && kind && (
        <Typography variant="caption" component="div" color="text.secondary">
          {kind}
        </Typography>
      )}
      {input.source === "eval" && input.config_id && (
        <Typography
          variant="code_sm"
          component="div"
          color="text.secondary"
          title={input.config_id}
          sx={line}
        >
          {input.config_id}
        </Typography>
      )}
    </>
  );
}
InputSource.propTypes = {
  input: PropTypes.object.isRequired,
  label: PropTypes.string,
  evaluatorName: PropTypes.string,
};

/** The active definition behind committed values. A metric still waiting for
 * its first calculation shows its saved version instead, read-only and
 * labelled as not active: it is never presented as the active one. */
export default function MetricDefinition({ metric, evaluatorNames = {} }) {
  const waiting = !metric.definition;
  const definition = metric.definition || metric.draft_definition;
  if (!definition)
    return (
      <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
        <EmptyState
          variant="no-results"
          icon="solar:document-text-linear"
          title="No active definition yet"
          description="The saved definition is unavailable. It appears here once it can be read."
        />
      </Box>
    );
  const inputs = definition.field_spec?.inputs?.length
    ? definition.field_spec.inputs
    : definition.inputs || [];
  const conditions = definition.field_spec?.conditions || [];
  const isPercentile = definition.kind === "percentile";
  const customFormula = (definition.kind || "custom") === "custom";
  const percentile = definition.field_spec?.percentile;
  const percentileLabel =
    Number.isFinite(percentile) && percentile > 0 && percentile < 1
      ? `P${new Intl.NumberFormat("en-US", { maximumFractionDigits: 10 }).format(percentile * 100)}`
      : null;
  const labelOf = (alias) => inputLabel(alias, { inputs });
  const valueAlias = definition.field_spec?.value_alias || inputs[0]?.alias;
  const sentence = isPercentile
    ? `${percentileLabel || "Percentile"} of ${valueAlias ? labelOf(valueAlias) : "the input"} across ${LEVEL_LABELS[definition.level] || "units"}`
    : recipeSentence({ ...definition, inputs }, labelOf);
  return (
    <Stack spacing={3}>
      {waiting ? (
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography component="h2" variant="subtitle1" fontWeight={600}>
              Definition v{definition.version}
            </Typography>
            <StatusBadge
              {...firstCalculationBadge({
                backfill_status: definition.backfill_status,
              })}
              variant="dot"
            />
          </Stack>
          <Typography variant="body2" color="text.secondary">
            No active definition yet. This saved version becomes the active
            definition when its first calculation finishes.
          </Typography>
        </Box>
      ) : (
        <Box>
          <Typography component="h2" variant="subtitle1" fontWeight={600}>
            Active definition · v{definition.version}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            This is the definition used for committed values, not an unactivated
            draft.
          </Typography>
        </Box>
      )}
      {!waiting &&
        metric.draft_definition?.version !== definition.version &&
        metric.draft_definition && (
          <Alert severity="info">
            Draft v{metric.draft_definition.version} is not active. Its changes
            are not reflected in these values.
          </Alert>
        )}
      {definition.level === "user" && (
        <Alert severity="info">
          User-level metrics are not available yet. This definition is saved,
          but user-level values are unavailable.
        </Alert>
      )}
      {definition.needs_rebuild && (
        <Alert severity="warning">
          This legacy definition needs a rebuild. Its saved formula has not been
          converted.
        </Alert>
      )}
      <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
        <Box
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Typography variant="caption" color="text.secondary">
            Calculates
          </Typography>
          <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
            {sentence}
          </Typography>
        </Box>
        <Box
          component="dl"
          sx={{
            m: 0,
            px: 2,
            py: 1,
            display: "grid",
            gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
            columnGap: 4,
            "& dt": { color: "text.secondary", width: 160, flexShrink: 0 },
            "& dd": { m: 0, overflowWrap: "anywhere", minWidth: 0 },
          }}
        >
          {[
            [
              "Calculation",
              isPercentile
                ? "Percentile"
                : KIND_LABELS[definition.kind] || definition.kind,
            ],
            ...(isPercentile ? [["Selected percentile", percentileLabel]] : []),
            [
              "Measured across",
              enumLabel({}, LEVEL_LABELS[definition.level] || definition.level),
            ],
            [
              "Per-unit result",
              enumLabel(VALUE_TYPE_LABELS, definition.value_type),
              definition.value_type,
            ],
            ["Unit", definition.unit || "None"],
            [
              "Format",
              enumLabel(VALUE_FORMAT_LABELS, definition.value_format) ||
                "Number",
              definition.value_format,
            ],
            [
              "Weighting",
              enumLabel(WEIGHTING_LABELS, definition.weighting),
              definition.weighting,
            ],
            [
              "Sampling",
              definition.sampling == null
                ? "Not specified"
                : literal(definition.sampling),
            ],
            [
              "Group dimensions",
              definition.group_dims?.join(", ") ||
                definition.cohort_key ||
                "None",
            ],
            waiting
              ? [
                  "Saved",
                  definition.created_at
                    ? formatMetricTime(definition.created_at)
                    : "Not available",
                ]
              : [
                  "Activated",
                  definition.activated_at
                    ? formatMetricTime(definition.activated_at)
                    : "Not available",
                ],
          ].map(([label, value, raw]) => (
            <Stack direction="row" key={label} sx={{ py: 0.75 }}>
              <Typography component="dt" variant="body2">
                {label}
              </Typography>
              <Typography
                component="dd"
                variant="body2"
                title={raw !== value ? stored(raw) : undefined}
              >
                {value ?? "Not specified"}
              </Typography>
            </Stack>
          ))}
        </Box>
      </Box>
      {isPercentile && (
        <Box>
          <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>
            Percentile calculation
          </Typography>
          <Typography variant="body2">
            Estimates {percentileLabel || "the selected percentile"} of{" "}
            {definition.field_spec?.value_alias ||
              inputs[0]?.alias ||
              "the input"}{" "}
            from the combined distribution of per-unit values using a t-digest.
            This is a percentile, not an average.
          </Typography>
        </Box>
      )}
      {!isPercentile && definition.formula && (
        <Box>
          <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>
            Formula
          </Typography>
          <Box
            component="pre"
            sx={{
              m: 0,
              px: 2,
              py: 1.5,
              bgcolor: "background.neutral",
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              fontFamily: "monospace",
              typography: "s1",
            }}
          >
            {definition.formula}
          </Box>
        </Box>
      )}
      <Box>
        <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>
          Inputs
        </Typography>
        {inputs.length ? (
          <TableContainer>
            <Table
              size="small"
              aria-label="Definition inputs"
              sx={{ tableLayout: "fixed" }}
            >
              <TableHead>
                <TableRow>
                  {[
                    // A formula names its inputs by the alias the person
                    // typed; sentence recipes generate theirs, so hide it.
                    ...(customFormula ? [["Alias", "16%"]] : []),
                    ["Source", "28%"],
                    ["Source type", "16%"],
                    ["Per-unit reduction", "18%"],
                    ["Observation condition"],
                  ].map(([label, width]) => (
                    <TableCell key={label} sx={{ width }}>
                      {label}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {inputs.map((input, index) => (
                  <TableRow
                    key={`${input.alias}:${index}`}
                    sx={{ height: ROW_HEIGHT.twoLine }}
                  >
                    {customFormula && (
                      <TableCell>
                        <Typography
                          variant="code_md"
                          component="div"
                          title={input.alias}
                          sx={line}
                        >
                          {input.alias}
                        </Typography>
                      </TableCell>
                    )}
                    <TableCell>
                      <InputSource
                        input={input}
                        label={labelOf(input.alias)}
                        evaluatorName={evaluatorNames[input.config_id]}
                      />
                    </TableCell>
                    <TableCell title={stored(input.value_type)}>
                      {enumLabel(VALUE_TYPE_LABELS, input.value_type) ||
                        "Not specified"}
                    </TableCell>
                    <TableCell title={stored(input.reducer)}>
                      {(input.condition_stage === "observation"
                        ? enumLabel(OBSERVATION_REDUCER_LABELS, input.reducer)
                        : enumLabel(REDUCER_LABELS, input.reducer)) ||
                        "Not specified"}
                    </TableCell>
                    <TableCell sx={{ overflowWrap: "anywhere" }}>
                      {input.condition
                        ? `${OPERATOR_LABELS[input.condition.operator] || input.condition.operator} ${literal(input.condition.value)}`
                        : "None"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        ) : (
          <Typography variant="body2" color="text.secondary">
            No inputs in this definition.
          </Typography>
        )}
      </Box>
      {conditions.length > 0 && (
        <Box>
          <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>
            Conditions
          </Typography>
          <Stack spacing={0.5}>
            {conditions.map((condition, index) => (
              <Typography variant="body2" key={index}>
                {labelOf(condition.alias)}{" "}
                {OPERATOR_LABELS[condition.operator] || condition.operator}{" "}
                {condition.right_alias
                  ? labelOf(condition.right_alias)
                  : literal(condition.value)}
              </Typography>
            ))}
          </Stack>
        </Box>
      )}
      {Object.keys(definition.value_labels || {}).length > 0 && (
        <Box>
          <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>
            Value labels
          </Typography>
          <Stack spacing={0.5}>
            {Object.entries(definition.value_labels).map(([key, value]) => (
              <Stack direction="row" key={key}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ width: 160, flexShrink: 0 }}
                >
                  {{ match: "Matches show as", other: "The rest show as" }[
                    key
                  ] || key}
                </Typography>
                <Typography variant="body2">{value}</Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}
      {definition.user_rule && (
        <Box>
          <Typography component="h3" variant="subtitle2">
            User rule
          </Typography>
          <Typography variant="body2">
            {literal(definition.user_rule)}
          </Typography>
        </Box>
      )}
      <Box>
        <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>
          Filters
        </Typography>
        {definition.filters?.length ? (
          <Box
            component="pre"
            sx={{
              m: 0,
              px: 2,
              py: 1.5,
              bgcolor: "background.neutral",
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              fontFamily: "monospace",
              typography: "s1",
            }}
          >
            {JSON.stringify(definition.filters, null, 2)}
          </Box>
        ) : (
          <Typography variant="body2" color="text.secondary">
            No additional filters
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
MetricDefinition.propTypes = {
  metric: PropTypes.object.isRequired,
  // Saved evaluation names by config ID; display only, never the binding.
  evaluatorNames: PropTypes.objectOf(PropTypes.string),
};
