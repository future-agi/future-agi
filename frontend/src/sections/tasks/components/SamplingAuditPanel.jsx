import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
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
import MetricStrip from "src/components/metric-strip";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { samplingStratumLabel } from "./samplingPolicyForm";
import { samplingRequestError } from "./samplingRequestError";
import { SamplingDisclosure } from "./sampling_selection_summary";
import {
  isSimpleSamplingPolicy,
  pickNames,
  samplingGroupValueLabel,
  samplingLocalDate,
  unitCount,
} from "./simpleSamplingPolicy";

const NO_COHORT_MESSAGE =
  "No sampling cohort exists for this task's current policy yet. It is created when the task first runs.";

const number = (value, digits = 2) =>
  value === null || value === undefined
    ? "—"
    : Number(value.toFixed(digits)).toLocaleString();
const percent = (value, digits = 1) =>
  value === null || value === undefined
    ? "—"
    : `${Number((value * 100).toFixed(digits))}%`;

const categoryLabel = (attribute, row, defaultLabel, ruleLabels) => {
  if (!row.present) return "(missing)";
  if (attribute === "slice" && row.value === "default") return defaultLabel;
  if (attribute === "slice") return ruleLabels?.get(row.value) ?? row.value;
  return row.value;
};

// A simple policy names its groups "Group 1", "Group 2"; the user picked
// them by value, so the audit shows the value, as the editor does.
const simpleRuleLabels = (policy) =>
  isSimpleSamplingPolicy(policy)
    ? new Map(
        policy.strata.map((rule) => [
          rule.name,
          samplingGroupValueLabel(rule.filters[0].values[0]),
        ]),
      )
    : null;

const numericCell = { fontVariantNumeric: "tabular-nums" };
const isDefaultStratum = (identity) => {
  try {
    return JSON.parse(identity)[0] === "default";
  } catch {
    return false;
  }
};

// The requested historical total, only when the saved policy states one.
const requestedTotal = (policy) => {
  if (!isSimpleSamplingPolicy(policy) || policy.mode !== "closed") return null;
  const total = policy.strata.reduce(
    (sum, rule) => sum + rule.target_n,
    policy.default.target_n,
  );
  return Number.isSafeInteger(total) ? total : null;
};

// Why a group is flagged, by the badge its Check cell shows. The rule is the
// API's own (audit_report), so the alert can name what it counts.
const flagKind = (row, live) => {
  if (row.population_check === "changed") return "changed";
  if (live) return row.srm_flagged ? "mismatch" : null;
  return row.exact === true && row.population_check === "match"
    ? null
    : "inexact";
};
const FLAG_LABELS = {
  changed: "Population changed",
  inexact: "Not exact",
  mismatch: "Mismatch",
};
// What each badge means, in words, about `subject`: "it" (one named group),
// "the group" (a legend line) or "the task" (a sample with no groups).
const flagReason = (kind, { live, units, subject, alpha }) => {
  if (kind === "changed")
    return live
      ? `${subject === "it" ? "its" : `${subject}’s`} ${units} were picked at different shares`
      : `the ${units} that match ${subject} changed after the sample froze`;
  if (kind === "inexact")
    return "the number picked differs from the expected count";
  return `the share picked differs from the set share by more than chance${
    Number.isFinite(alpha) ? ` (p below ${alpha})` : ""
  }`;
};
// The integrity warning: which groups, why, and what to do about it.
function IntegrityAlert({ audit, defaultLabel, ruleLabels, live, action }) {
  const { summary } = audit;
  const units = pickNames(audit.sampling_unit);
  const flagged = audit.strata
    .map((row) => ({ row, kind: flagKind(row, live) }))
    .filter((item) => item.kind);
  const count = Number.isSafeInteger(summary.flagged_strata)
    ? summary.flagged_strata
    : null;
  const kinds = [...new Set(flagged.map((item) => item.kind))];
  const context = { live, units, alpha: summary.srm_alpha };
  // Name the groups only when they are the ones the API counted.
  const named = count != null && count > 0 && flagged.length === count;
  const onlySample =
    named &&
    audit.strata.length === 1 &&
    isDefaultStratum(flagged[0].row.stratum);
  const names = named
    ? flagged.map(({ row }) =>
        samplingStratumLabel(row.stratum, defaultLabel, ruleLabels),
      )
    : [];
  const shown =
    names.length > 3
      ? [
          ...names.slice(0, 3),
          `${names.length - 3} more group${names.length - 3 === 1 ? "" : "s"}`,
        ]
      : names;
  const one = onlySample || names.length === 1;
  const subject = onlySample
    ? "This sample"
    : names.length
      ? new Intl.ListFormat("en", { type: "conjunction" }).format(shown)
      : count != null && count > 0
        ? `${count.toLocaleString()} group${count === 1 ? "" : "s"}`
        : "Some groups";
  const verb = one || count === 1 ? "needs" : "need";
  // One reason reads as one sentence; several get a line per badge.
  const lead =
    one && kinds.length === 1
      ? `${subject} ${verb} attention: ${flagReason(kinds[0], { ...context, subject: onlySample ? "the task" : "it" })}.`
      : `${subject} ${verb} attention.`;
  const legend =
    one && kinds.length === 1
      ? []
      : kinds.map(
          (kind) =>
            `${FLAG_LABELS[kind]}: ${flagReason(kind, { ...context, subject: "the group" })}.`,
        );
  const next = live
    ? `Results from ${one ? "it" : "these groups"} may be skewed. Refresh to check again as new data arrives.`
    : "To pick a new sample from the current data, duplicate the task or change its window, filters or counts, then save.";
  return (
    <Alert severity="warning" action={action}>
      <Stack spacing={0.5}>
        <Typography variant="s1" component="div" sx={{ fontWeight: 500 }}>
          {lead}
        </Typography>
        {legend.map((line) => (
          <Typography key={line} variant="s1" component="div">
            {line}
          </Typography>
        ))}
        <Typography variant="s1" component="div">
          {next}
        </Typography>
      </Stack>
    </Alert>
  );
}
IntegrityAlert.propTypes = {
  audit: PropTypes.object.isRequired,
  defaultLabel: PropTypes.string.isRequired,
  ruleLabels: PropTypes.instanceOf(Map),
  live: PropTypes.bool,
  action: PropTypes.node,
};

// One status style for every check on the page: the DS status badge.
const CheckBadge = ({ role, label }) => (
  <StatusBadge status={role} role={role} label={label} />
);
CheckBadge.propTypes = {
  role: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
};

function StratumCheck({ row, mode }) {
  if (row.population_check === "changed")
    return <CheckBadge role="warning" label="Population changed" />;
  if (mode === "live") {
    return row.srm_flagged ? (
      <CheckBadge
        role="warning"
        label={`Mismatch p=${row.srm_p_value.toExponential(1)}`}
      />
    ) : (
      <CheckBadge role="success" label={`OK p=${number(row.srm_p_value, 3)}`} />
    );
  }
  return row.exact ? (
    <CheckBadge role="success" label="Exact" />
  ) : (
    <CheckBadge role="warning" label="Not exact" />
  );
}
StratumCheck.propTypes = {
  row: PropTypes.object.isRequired,
  mode: PropTypes.string.isRequired,
};

// Diagnostics as a compact label/value list: they are details, not the
// page's headline numbers.
function Diagnostics({ items }) {
  return (
    <Box
      component="dl"
      aria-label="Weight diagnostics"
      sx={{ m: 0, display: "flex", flexWrap: "wrap", columnGap: 4, rowGap: 1 }}
    >
      {items.map((item) => (
        <Box key={item.label} sx={{ minWidth: 0 }}>
          <Typography component="dt" variant="s3" color="text.secondary">
            {item.label}
          </Typography>
          <Typography
            component="dd"
            variant="s1"
            sx={{ m: 0, fontWeight: 500, ...numericCell }}
          >
            {item.value}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
Diagnostics.propTypes = {
  items: PropTypes.arrayOf(PropTypes.object).isRequired,
};

// Advisory, read-only design audit of a task's sampling cohort. It never
// changes the policy or the cohort.
export default function SamplingAuditPanel({
  taskId,
  policy,
  onEditSelection,
}) {
  const query = useQuery({
    queryKey: ["samplingAudit", taskId],
    queryFn: () =>
      axios
        .get(endpoints.project.samplingAudit(), {
          params: { eval_task_id: taskId },
        })
        .then((response) => response.data.result)
        .catch((error) => {
          const status = error?.response?.status ?? error?.statusCode;
          // A not-yet-frozen cohort is an expected empty state. Only this exact
          // response is handled before QueryCache's global error notification.
          if (
            status === 404 &&
            getSafeActionErrorMessage(error, "") === NO_COHORT_MESSAGE
          )
            return { noCohortMessage: NO_COHORT_MESSAGE };
          throw error;
        }),
    enabled: Boolean(taskId),
    retry: false,
  });
  if (query.isLoading)
    return (
      <Skeleton variant="rounded" height={160} aria-label="Loading audit" />
    );
  const noCohortMessage = !query.isError && query.data?.noCohortMessage;
  if (query.isError || noCohortMessage) {
    const status = query.error?.response?.status ?? query.error?.statusCode;
    const message =
      noCohortMessage ||
      samplingRequestError(
        query.error,
        "audit",
        "The sampling audit could not be loaded.",
      );
    return (
      <Alert
        severity={
          noCohortMessage || status === 404 || status === 409 ? "info" : "error"
        }
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
  const live = audit.mode === "live";
  const units = pickNames(audit.sampling_unit);
  const hasGroups = audit.strata.some((row) => !isDefaultStratum(row.stratum));
  const defaultLabel = hasGroups ? `All other ${units}` : `All ${units}`;
  const empty = summary.achieved === 0;
  // Nothing to tabulate: no stratum had members. A population with no picks
  // keeps its counts, which say where the members were.
  const nothingMatched =
    empty && (summary.population_size === 0 || audit.strata.length === 0);
  const requested = requestedTotal(policy);
  const ruleLabels = simpleRuleLabels(policy);
  const multiple = audit.strata.length > 1;
  const single = audit.strata.length === 1 ? audit.strata[0] : null;
  // Diagnostics matter when groups or a flagged check exist; a plain random
  // pick keeps the statistics one click away.
  const statsOpen =
    multiple || summary.integrity !== "ok" || summary.flagged_strata > 0;
  // One name per number across the strip and the table.
  const matchingLabel = live ? "Decided" : "Matching";
  const meta = [
    live ? "New incoming data" : "Historical window",
    live
      ? `Picks a share of new ${units}`
      : `Picks ${units} at random${hasGroups ? ` across ${audit.strata.length} groups` : ""}`,
    audit.coordination === "project" && "Coordinated across the project",
    audit.frozen_at && `Frozen ${samplingLocalDate(audit.frozen_at)}`,
  ]
    .filter(Boolean)
    .join(" · ");
  const population = Number.isSafeInteger(summary.population_size)
    ? unitCount(summary.population_size, audit.sampling_unit)
    : `— ${units}`;
  const columns = [
    { label: "Group" },
    { label: matchingLabel, numeric: true },
    { label: live ? "Expected" : "Requested", numeric: true },
    { label: "Picked", numeric: true },
    { label: "Shortage", numeric: true },
    // Per-group weights only differ between groups; with one group they
    // sit with the other statistics.
    ...(multiple
      ? [
          { label: "Inclusion probability", numeric: true },
          { label: "Design weight", numeric: true },
        ]
      : []),
    { label: "Check" },
  ];
  return (
    <Stack spacing={2} aria-label="Sampling audit">
      <Stack
        direction="row"
        spacing={2}
        alignItems="flex-start"
        justifyContent="space-between"
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="m3" component="h2" sx={{ fontWeight: 600 }}>
            Sampling audit
          </Typography>
          <Typography variant="s1" component="div" color="text.secondary">
            {meta}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
          aria-label="Refresh sampling audit"
          sx={{ flexShrink: 0 }}
        >
          {query.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </Stack>
      {empty && (
        <Alert
          severity="warning"
          action={
            !live && onEditSelection ? (
              <Button color="inherit" size="small" onClick={onEditSelection}>
                Edit task
              </Button>
            ) : undefined
          }
        >
          {live
            ? `Nothing has been sampled yet: no new ${units} were picked since the start.`
            : summary.population_size === 0
              ? `Nothing was sampled: no ${units} matched this task’s filters and window when the sample froze.`
              : `Nothing was sampled from the ${population} that matched when the sample froze.`}
          {!live &&
            requested != null &&
            ` Requested: up to ${requested.toLocaleString()}.`}
          {!live &&
            (summary.population_size === 0
              ? " Widen the window or loosen the filters, then save to pick a new sample."
              : " Raise the counts, then save to pick a new sample.")}
        </Alert>
      )}
      {summary.integrity === "ok" ? (
        !empty && (
          <Stack direction="row" spacing={1} alignItems="center">
            <StatusBadge
              status="success"
              role="success"
              label={live ? "No mismatch" : "Counts match"}
            />
            <Typography variant="s1" component="div" color="text.secondary">
              {live
                ? "No sample-ratio mismatch detected at the configured threshold."
                : "Quota and population counts match in every group; identical membership is not verified."}
            </Typography>
          </Stack>
        )
      ) : (
        <IntegrityAlert
          audit={audit}
          defaultLabel={defaultLabel}
          ruleLabels={ruleLabels}
          live={live}
          action={
            // The empty-sample warning above already offers it.
            !live && !empty && onEditSelection ? (
              <Button color="inherit" size="small" onClick={onEditSelection}>
                Edit task
              </Button>
            ) : undefined
          }
        />
      )}
      {!nothingMatched && (
        <MetricStrip
          items={[
            {
              key: "population",
              label: `${matchingLabel} ${units}`,
              value: number(summary.population_size, 0),
              tooltip: live
                ? "Units decided since the policy start"
                : "Units in the population when the sample froze",
            },
            {
              key: "picked",
              label: `Picked ${units}`,
              value: number(summary.achieved, 0),
            },
            ...(multiple
              ? [
                  {
                    key: "unrepresented",
                    label: "Unrepresented",
                    value: number(summary.unrepresented_population, 0),
                    hint:
                      summary.unrepresented_strata > 0
                        ? `In ${summary.unrepresented_strata} group${summary.unrepresented_strata === 1 ? "" : "s"} with no pick`
                        : undefined,
                  },
                ]
              : []),
          ]}
        />
      )}
      {!nothingMatched && (
        <Box sx={{ overflowX: "auto" }}>
          <Table size="small" aria-label="Sampling audit by group">
            <TableHead>
              <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                {columns.map((column) => (
                  <TableCell
                    key={column.label}
                    align={column.numeric ? "right" : "left"}
                  >
                    {column.label}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {audit.strata.length === 0 && (
                <TableRow sx={{ height: ROW_HEIGHT.default }}>
                  <TableCell colSpan={columns.length}>
                    <Typography
                      variant="s1"
                      component="div"
                      color="text.secondary"
                    >
                      {live
                        ? `No ${units} have been decided yet.`
                        : "No groups had members when the sample froze."}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
              {audit.strata.map((row) => (
                <TableRow key={row.stratum} sx={{ height: ROW_HEIGHT.default }}>
                  <TableCell sx={{ wordBreak: "break-word" }}>
                    {samplingStratumLabel(
                      row.stratum,
                      defaultLabel,
                      ruleLabels,
                    )}
                  </TableCell>
                  <TableCell align="right" sx={numericCell}>
                    {number(row.population_size, 0)}
                  </TableCell>
                  <TableCell align="right" sx={numericCell}>
                    {live
                      ? number(row.expected_size, 1)
                      : number(row.target, 0)}
                  </TableCell>
                  <TableCell align="right" sx={numericCell}>
                    {number(row.achieved, 0)}
                  </TableCell>
                  <TableCell align="right" sx={numericCell}>
                    {number(row.shortage, 0)}
                  </TableCell>
                  {multiple && (
                    <TableCell align="right" sx={numericCell}>
                      {number(row.inclusion_probability, 4)}
                    </TableCell>
                  )}
                  {multiple && (
                    <TableCell align="right" sx={numericCell}>
                      {number(row.design_weight, 3)}
                    </TableCell>
                  )}
                  <TableCell>
                    <StratumCheck row={row} mode={audit.mode} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      <SamplingDisclosure label="Statistical details" defaultOpen={statsOpen}>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Stack spacing={0.5}>
            <Diagnostics
              items={[
                {
                  label: "Kish effective n",
                  value: number(summary.kish_effective_n, 1),
                },
                {
                  label: "Design effect",
                  value: number(summary.design_effect, 3),
                },
                { label: "Weight CV", value: number(summary.weight_cv, 3) },
                ...(single
                  ? [
                      {
                        label: "Inclusion probability",
                        value: number(single.inclusion_probability, 4),
                      },
                      {
                        label: "Design weight",
                        value: number(single.design_weight, 3),
                      },
                    ]
                  : []),
              ]}
            />
            {!audit.notes.some((note) => /kish/i.test(note)) && (
              <Typography variant="s3" component="div" color="text.secondary">
                Kish effective n is an unequal-weight diagnostic, not
                outcome-specific precision.
              </Typography>
            )}
          </Stack>
          {audit.balance.map((item) => (
            <BalanceTable
              key={item.attribute}
              item={item}
              defaultLabel={defaultLabel}
              ruleLabels={ruleLabels}
            />
          ))}
          {audit.notes.length > 0 && (
            <Stack spacing={0.5}>
              {audit.notes.map((note) => (
                <Typography
                  key={note}
                  variant="s1"
                  component="p"
                  color="text.secondary"
                  sx={{ m: 0 }}
                >
                  {note}
                </Typography>
              ))}
            </Stack>
          )}
        </Stack>
      </SamplingDisclosure>
    </Stack>
  );
}
SamplingAuditPanel.propTypes = {
  taskId: PropTypes.string.isRequired,
  // The saved policy, for the requested count; optional.
  policy: PropTypes.object,
  // Opens where the task's data selection is edited; offered as the next
  // step when a historical sample came out empty.
  onEditSelection: PropTypes.func,
};

function BalanceTable({ item, defaultLabel, ruleLabels }) {
  const name = item.attribute === "slice" ? "group rule" : item.attribute;
  return (
    <Box>
      <Typography variant="m3" component="h3" sx={{ fontWeight: 500 }}>
        Balance on{" "}
        {item.attribute === "slice" ? "group rule" : `“${item.attribute}”`}
      </Typography>
      <Typography variant="s1" component="div" color="text.secondary">
        Chi-square {number(item.chi_square, 2)}
        {item.degrees_of_freedom ? ` on ${item.degrees_of_freedom} df` : ""}, p
        = {item.p_value == null ? "—" : item.p_value.toPrecision(3)} (sampled
        versus population shares).{" "}
        {item.weighted_imbalance
          ? "Weighted shares exceed the imbalance threshold."
          : item.categories.some((row) => row.weighted_share != null)
            ? "No weighted imbalance above the threshold."
            : "Weighted balance is not estimable without sampled units."}
      </Typography>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" aria-label={`Balance on ${name}`}>
          <TableHead>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              {[
                "Category",
                "Population share",
                "Sample share",
                "Weighted share",
                "Std. difference",
                "Weighted std. difference",
              ].map((label, index) => (
                <TableCell key={label} align={index ? "right" : "left"}>
                  {label}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {item.categories.map((row) => (
              <TableRow
                key={`${row.present}:${row.value}`}
                sx={{ height: ROW_HEIGHT.default }}
              >
                <TableCell>
                  {categoryLabel(item.attribute, row, defaultLabel, ruleLabels)}
                </TableCell>
                <TableCell align="right" sx={numericCell}>
                  {percent(row.population_share)}
                </TableCell>
                <TableCell align="right" sx={numericCell}>
                  {percent(row.sample_share)}
                </TableCell>
                <TableCell align="right" sx={numericCell}>
                  {percent(row.weighted_share)}
                </TableCell>
                <TableCell align="right" sx={numericCell}>
                  {number(row.standardized_difference, 3)}
                </TableCell>
                <TableCell align="right" sx={numericCell}>
                  {number(row.weighted_standardized_difference, 3)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}
BalanceTable.propTypes = {
  item: PropTypes.object.isRequired,
  defaultLabel: PropTypes.string.isRequired,
  ruleLabels: PropTypes.instanceOf(Map),
};
