import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { samplingStratumLabel } from "./samplingPolicyForm";

const number = (value, digits = 2) =>
  value === null || value === undefined
    ? "—"
    : Number(value.toFixed(digits)).toLocaleString();
const percent = (value, digits = 1) =>
  value === null || value === undefined
    ? "—"
    : `${Number((value * 100).toFixed(digits))}%`;

const categoryLabel = (attribute, row) => {
  if (!row.present) return "(missing)";
  if (attribute === "slice" && row.value === "default")
    return "All remaining contacts";
  return row.value;
};

function Stat({ label, value, hint }) {
  return (
    <Box sx={{ minWidth: 120 }}>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      <Typography variant="subtitle2">{value}</Typography>
      {hint && (
        <Typography variant="caption" color="text.secondary">
          {hint}
        </Typography>
      )}
    </Box>
  );
}
Stat.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
  hint: PropTypes.string,
};

function StratumCheck({ row, mode }) {
  if (mode === "live") {
    return row.srm_flagged ? (
      <Chip
        size="small"
        color="warning"
        label={`Mismatch p=${row.srm_p_value.toExponential(1)}`}
      />
    ) : (
      <Chip
        size="small"
        variant="outlined"
        label={`OK p=${number(row.srm_p_value, 3)}`}
      />
    );
  }
  if (row.population_check === "changed")
    return <Chip size="small" color="warning" label="Population changed" />;
  return row.exact ? (
    <Chip size="small" variant="outlined" label="Exact" />
  ) : (
    <Chip size="small" color="warning" label="Not exact" />
  );
}
StratumCheck.propTypes = {
  row: PropTypes.object.isRequired,
  mode: PropTypes.string.isRequired,
};

// Advisory, read-only design audit of a task's sampling cohort. It never
// changes the policy or the cohort.
export default function SamplingAuditPanel({ taskId }) {
  const query = useQuery({
    queryKey: ["samplingAudit", taskId],
    queryFn: () =>
      axios
        .get(endpoints.project.samplingAudit(), {
          params: { eval_task_id: taskId },
        })
        .then((response) => response.data.result),
    enabled: Boolean(taskId),
    retry: false,
  });
  if (query.isLoading)
    return (
      <Skeleton variant="rounded" height={160} aria-label="Loading audit" />
    );
  if (query.isError) {
    const status = query.error?.response?.status ?? query.error?.statusCode;
    const message = getSafeActionErrorMessage(
      query.error,
      "The sampling audit could not be loaded.",
    );
    return (
      <Alert
        severity={status === 404 || status === 409 ? "info" : "error"}
        action={
          <Button size="small" onClick={() => query.refetch()}>
            Retry
          </Button>
        }
      >
        {message}
      </Alert>
    );
  }
  const audit = query.data;
  const { summary } = audit;
  return (
    <Stack spacing={2} aria-label="Sampling audit">
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Typography variant="subtitle1">Sampling audit</Typography>
        <Chip size="small" label={`${audit.mode} window`} />
        <Chip size="small" label={`unit: ${audit.sampling_unit}`} />
        <Chip size="small" label={`coordination: ${audit.coordination}`} />
        {audit.frozen_at && (
          <Typography variant="caption" color="text.secondary">
            Frozen {new Date(audit.frozen_at).toLocaleString()}
          </Typography>
        )}
      </Stack>
      <Alert severity={summary.integrity === "ok" ? "success" : "warning"}>
        {summary.integrity === "ok"
          ? audit.mode === "live"
            ? "Realized selections match the configured rates in every stratum."
            : "Every stratum holds exactly its quota and the frozen population still reproduces."
          : `${summary.flagged_strata} stratum(s) need attention (see Check).`}
      </Alert>
      <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
        <Stat
          label="Population (N)"
          value={number(summary.population_size, 0)}
        />
        <Stat label="Sampled units" value={number(summary.achieved, 0)} />
        <Stat
          label="Kish effective n"
          value={number(summary.kish_effective_n, 1)}
          hint="Sample size an equal-weight sample would need for the same precision"
        />
        <Stat label="Design effect" value={number(summary.design_effect, 3)} />
        <Stat label="Weight CV" value={number(summary.weight_cv, 3)} />
        <Stat
          label="Unrepresented"
          value={`${number(summary.unrepresented_population, 0)} units in ${summary.unrepresented_strata} strata`}
        />
      </Stack>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" aria-label="Sampling audit by stratum">
          <TableHead>
            <TableRow>
              {[
                "Stratum",
                "Population (N)",
                "Target",
                "Achieved",
                "Shortage",
                "Inclusion probability",
                "Design weight",
                "Check",
              ].map((label) => (
                <TableCell key={label}>{label}</TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {audit.strata.map((row) => (
              <TableRow key={row.stratum}>
                <TableCell sx={{ wordBreak: "break-word" }}>
                  {samplingStratumLabel(row.stratum)}
                </TableCell>
                <TableCell>{number(row.population_size, 0)}</TableCell>
                <TableCell>
                  {audit.mode === "live"
                    ? number(row.expected_size, 1)
                    : number(row.target, 0)}
                </TableCell>
                <TableCell>{number(row.achieved, 0)}</TableCell>
                <TableCell>{number(row.shortage, 0)}</TableCell>
                <TableCell>{number(row.inclusion_probability, 4)}</TableCell>
                <TableCell>{number(row.design_weight, 3)}</TableCell>
                <TableCell>
                  <StratumCheck row={row} mode={audit.mode} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {audit.balance.map((item) => (
        <Box key={item.attribute}>
          <Typography variant="subtitle2">
            Balance on{" "}
            {item.attribute === "slice" ? "slice" : `“${item.attribute}”`}
          </Typography>
          <Typography variant="caption" color="text.secondary" display="block">
            Chi-square {number(item.chi_square, 2)}
            {item.degrees_of_freedom ? ` on ${item.degrees_of_freedom} df` : ""}
            , p = {item.p_value == null ? "—" : item.p_value.toPrecision(3)}{" "}
            (sampled versus population shares).{" "}
            {item.weighted_imbalance
              ? "Weighted shares do not restore the population mix."
              : "Weighted shares restore the population mix."}
          </Typography>
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small" aria-label={`Balance on ${item.attribute}`}>
              <TableHead>
                <TableRow>
                  {[
                    "Category",
                    "Population share",
                    "Sample share",
                    "Weighted share",
                    "Std. difference",
                    "Weighted std. difference",
                  ].map((label) => (
                    <TableCell key={label}>{label}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {item.categories.map((row) => (
                  <TableRow key={`${row.present}:${row.value}`}>
                    <TableCell>{categoryLabel(item.attribute, row)}</TableCell>
                    <TableCell>{percent(row.population_share)}</TableCell>
                    <TableCell>{percent(row.sample_share)}</TableCell>
                    <TableCell>{percent(row.weighted_share)}</TableCell>
                    <TableCell>
                      {number(row.standardized_difference, 3)}
                    </TableCell>
                    <TableCell>
                      {number(row.weighted_standardized_difference, 3)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        </Box>
      ))}
      {audit.notes.map((note) => (
        <Typography key={note} variant="caption" color="text.secondary">
          {note}
        </Typography>
      ))}
    </Stack>
  );
}
SamplingAuditPanel.propTypes = { taskId: PropTypes.string.isRequired };
