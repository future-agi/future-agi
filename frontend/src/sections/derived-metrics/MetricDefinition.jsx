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
import { KIND_LABELS, LEVEL_LABELS, OPERATOR_LABELS } from "./builderSpec";
import { formatMetricTime } from "./metricTime";

const literal = (value) =>
  value === undefined ? "Not specified" : JSON.stringify(value);

export default function MetricDefinition({ metric }) {
  const definition = metric.definition;
  if (!definition)
    return (
      <Alert severity="info">
        No active definition yet. The saved draft has not completed
        recalculation.
      </Alert>
    );
  const inputs = definition.field_spec?.inputs?.length
    ? definition.field_spec.inputs
    : definition.inputs || [];
  const conditions = definition.field_spec?.conditions || [];
  return (
    <Stack spacing={5}>
      <Box>
        <Typography component="h2" variant="subtitle1" fontWeight={600}>
          Active definition · v{definition.version}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          This is the definition used for committed values, not an unactivated
          draft.
        </Typography>
      </Box>
      {metric.draft_definition?.version !== definition.version &&
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
      <Stack
        component="dl"
        spacing={2}
        sx={{
          m: 0,
          "& dt": { color: "text.secondary", width: 180, flexShrink: 0 },
          "& dd": { m: 0, overflowWrap: "anywhere" },
        }}
      >
        {[
          ["Calculation", KIND_LABELS[definition.kind] || definition.kind],
          [
            "Measured across",
            LEVEL_LABELS[definition.level] || definition.level,
          ],
          ["Value type", definition.value_type],
          ["Unit", definition.unit || "Unitless"],
          ["Format", definition.value_format || "Number"],
          ["Weighting", definition.weighting],
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
          [
            "Activated",
            definition.activated_at
              ? formatMetricTime(definition.activated_at)
              : "Not available",
          ],
        ].map(([label, value]) => (
          <Stack direction="row" key={label}>
            <Typography component="dt" variant="body2">
              {label}
            </Typography>
            <Typography component="dd" variant="body2">
              {value ?? "Not specified"}
            </Typography>
          </Stack>
        ))}
      </Stack>
      {definition.formula && (
        <Box>
          <Typography component="h3" variant="subtitle2" sx={{ mb: 2 }}>
            Formula
          </Typography>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 3,
              bgcolor: "background.neutral",
              borderRadius: 1,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              fontFamily: "monospace",
              fontSize: 13,
            }}
          >
            {definition.formula}
          </Box>
        </Box>
      )}
      <Box>
        <Typography component="h3" variant="subtitle2" sx={{ mb: 2 }}>
          Inputs
        </Typography>
        {inputs.length ? (
          <TableContainer>
            <Table size="small" aria-label="Definition inputs">
              <TableHead>
                <TableRow>
                  {[
                    "Alias",
                    "Source",
                    "Type",
                    "Reducer",
                    "Observation condition",
                  ].map((label) => (
                    <TableCell key={label}>{label}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {inputs.map((input, index) => (
                  <TableRow key={`${input.alias}:${index}`}>
                    <TableCell>{input.alias}</TableCell>
                    <TableCell sx={{ overflowWrap: "anywhere" }}>
                      {input.config_id ||
                        input.attribute_key ||
                        input.field ||
                        input.source}
                      <Typography
                        variant="caption"
                        component="div"
                        color="text.secondary"
                      >
                        {input.source}
                      </Typography>
                    </TableCell>
                    <TableCell>{input.value_type}</TableCell>
                    <TableCell>{input.reducer}</TableCell>
                    <TableCell>
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
          <Typography component="h3" variant="subtitle2" sx={{ mb: 2 }}>
            Conditions
          </Typography>
          {conditions.map((condition, index) => (
            <Typography variant="body2" key={index}>
              {condition.alias}{" "}
              {OPERATOR_LABELS[condition.operator] || condition.operator}{" "}
              {condition.right_alias || literal(condition.value)}
            </Typography>
          ))}
        </Box>
      )}
      {Object.keys(definition.value_labels || {}).length > 0 && (
        <Box>
          <Typography component="h3" variant="subtitle2" sx={{ mb: 2 }}>
            Value labels
          </Typography>
          {Object.entries(definition.value_labels).map(([key, value]) => (
            <Typography variant="body2" key={key}>
              {key}: {value}
            </Typography>
          ))}
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
        <Typography component="h3" variant="subtitle2" sx={{ mb: 2 }}>
          Filters
        </Typography>
        {definition.filters?.length ? (
          <Box
            component="pre"
            sx={{
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              fontSize: 13,
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
MetricDefinition.propTypes = { metric: PropTypes.object.isRequired };
