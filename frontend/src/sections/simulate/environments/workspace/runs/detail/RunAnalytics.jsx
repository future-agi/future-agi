import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";

import { useRunAnalytics } from "src/api/simulate-environments/runAnalytics";

import EmptyState from "../../../components/EmptyState";
import SectionCard from "../../../components/SectionCard";
import { BUILD_TONES } from "../../../buildEnvironment/buildTones";

const formatNumber = (value, maximumFractionDigits = 1) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(value);

const percent = (value) => (value == null ? "—" : `${formatNumber(value)}%`);
const money = (cents) =>
  cents == null ? "—" : `$${formatNumber(cents / 100, 2)}`;

function MetricCard({ label, value, detail, tone }) {
  return (
    <Box
      sx={{
        p: 2,
        minWidth: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.25,
        bgcolor: "background.paper",
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.subtitle",
          textTransform: "uppercase",
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{ typography: "h5", color: tone || "text.primary", mt: 0.5 }}
      >
        {value}
      </Typography>
      {detail && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {detail}
        </Typography>
      )}
    </Box>
  );
}

MetricCard.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
  detail: PropTypes.node,
  tone: PropTypes.string,
};

function OutcomeBar({ row }) {
  const total = row.passed + row.failed + row.error + row.inconclusive;
  if (!total) return <Box sx={{ color: "text.disabled" }}>—</Box>;
  const segments = [
    ["passed", BUILD_TONES.green],
    ["failed", BUILD_TONES.red],
    ["error", BUILD_TONES.orange],
    ["inconclusive", BUILD_TONES.grey],
  ];
  return (
    <Box
      sx={{
        display: "flex",
        height: 18,
        minWidth: 32,
        borderRadius: 0.75,
        overflow: "hidden",
      }}
    >
      {segments.map(
        ([key, color]) =>
          row[key] > 0 && (
            <Box
              key={key}
              title={`${key}: ${row[key]}`}
              sx={{ width: `${(row[key] / total) * 100}%`, bgcolor: color }}
            />
          ),
      )}
    </Box>
  );
}

OutcomeBar.propTypes = { row: PropTypes.object.isRequired };

const tableSx = {
  "& th": {
    typography: "s3",
    color: "text.subtitle",
    fontWeight: 600,
    whiteSpace: "nowrap",
  },
  "& td": { typography: "s2", color: "text.secondary" },
};

function BreakdownTable({ rows, labelKey, label, empty = "No measured data" }) {
  return rows.length ? (
    <Table size="small" sx={tableSx}>
      <TableHead>
        <TableRow>
          <TableCell>{label}</TableCell>
          <TableCell align="right">Tasks</TableCell>
          <TableCell align="right">Failed</TableCell>
          <TableCell align="right">Pass rate</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row[labelKey]}>
            <TableCell>{row[labelKey]}</TableCell>
            <TableCell align="right">{row.total}</TableCell>
            <TableCell align="right">
              {(row.outcomes?.failed || 0) + (row.outcomes?.error || 0)}
            </TableCell>
            <TableCell align="right">{percent(row.pass_rate)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ) : (
    <EmptyState icon="solar:chart-2-linear" title={empty} />
  );
}

BreakdownTable.propTypes = {
  rows: PropTypes.array.isRequired,
  labelKey: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  empty: PropTypes.string,
};

export default function RunAnalytics({ executionId }) {
  const { data, isPending, isError } = useRunAnalytics(executionId);

  if (isPending) {
    return (
      <Stack alignItems="center" sx={{ py: 8 }}>
        <CircularProgress size={26} />
      </Stack>
    );
  }
  if (isError) {
    return (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title="Analytics could not be loaded"
      />
    );
  }
  if (!data?.summary?.total) {
    return (
      <EmptyState
        icon="solar:chart-2-linear"
        title="No completed calls to analyze"
      />
    );
  }

  const { summary } = data;
  const failed =
    (summary.outcomes?.failed || 0) + (summary.outcomes?.error || 0);
  const maxTurns = Math.max(
    1,
    ...data.turn_distribution.map(
      (row) => row.passed + row.failed + row.error + row.inconclusive,
    ),
  );

  return (
    <Stack spacing={2}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 1,
        }}
      >
        <MetricCard
          label="Pass rate"
          value={percent(summary.pass_rate)}
          detail={`${summary.measured}/${summary.total} measured`}
          tone={BUILD_TONES.amber}
        />
        <MetricCard
          label="Failed"
          value={failed}
          detail={`${summary.outcomes?.error || 0} execution errors`}
          tone={failed ? BUILD_TONES.red : BUILD_TONES.green}
        />
        <MetricCard
          label="Median duration"
          value={
            summary.duration?.p50 == null
              ? "—"
              : `${formatNumber(summary.duration.p50)}s`
          }
          detail={`p95 ${formatNumber(summary.duration?.p95)}s`}
        />
        <MetricCard
          label="Median latency"
          value={
            summary.latency?.p50 == null
              ? "—"
              : `${formatNumber(summary.latency.p50)}ms`
          }
          detail={`p95 call-average ${formatNumber(summary.latency?.p95)}ms`}
        />
        <MetricCard
          label="Tokens"
          value={formatNumber(summary.tokens?.total_value, 0)}
          detail={`${summary.tokens?.measured || 0} measured calls`}
        />
        <MetricCard
          label="Cost"
          value={money(summary.cost_cents?.total_value)}
          detail={`${money(summary.cost_cents?.average)} average`}
        />
        <MetricCard
          label="Evaluators"
          value={summary.evaluators}
          detail="graders applied"
        />
      </Box>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", lg: "1.2fr 1fr" },
          gap: 2,
        }}
      >
        <SectionCard
          title="Use-case risk"
          subtitle="Weakest measured goals first"
          dense
        >
          <BreakdownTable
            rows={data.scenario_risk}
            labelKey="goal"
            label="Goal"
          />
        </SectionCard>

        <SectionCard
          title="Tasks by turn count"
          subtitle="Stacked by outcome"
          dense
        >
          <Stack spacing={1.25} sx={{ p: 2 }}>
            {data.turn_distribution.length ? (
              data.turn_distribution.map((row) => {
                const count =
                  row.passed + row.failed + row.error + row.inconclusive;
                return (
                  <Stack
                    key={row.turn_count}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                  >
                    <Typography
                      sx={{ typography: "s3", width: 26, textAlign: "right" }}
                    >
                      {row.turn_count}
                    </Typography>
                    <Box
                      sx={{
                        width: `${Math.max(8, (count / maxTurns) * 100)}%`,
                      }}
                    >
                      <OutcomeBar row={row} />
                    </Box>
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {count}
                    </Typography>
                  </Stack>
                );
              })
            ) : (
              <EmptyState
                icon="solar:chart-square-linear"
                title="Turn count is unavailable"
              />
            )}
          </Stack>
        </SectionCard>
      </Box>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" },
          gap: 2,
        }}
      >
        <SectionCard
          title="Evaluations"
          subtitle="Pass rates and measurement coverage"
          dense
        >
          {data.evaluations.length ? (
            <Table size="small" sx={tableSx}>
              <TableHead>
                <TableRow>
                  <TableCell>Evaluation</TableCell>
                  <TableCell align="right">Passed</TableCell>
                  <TableCell align="right">Measured</TableCell>
                  <TableCell align="right">Pass rate</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.evaluations.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell>{row.name}</TableCell>
                    <TableCell align="right">{row.passed}</TableCell>
                    <TableCell align="right">{row.measured}</TableCell>
                    <TableCell align="right">
                      {percent(row.pass_rate)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState
              icon="solar:checklist-minimalistic-linear"
              title="No evaluations were recorded"
            />
          )}
        </SectionCard>

        <SectionCard
          title="Failure reasons"
          subtitle="Native execution and evaluator evidence"
          dense
        >
          {data.failure_breakdown.length ? (
            <Table size="small" sx={tableSx}>
              <TableHead>
                <TableRow>
                  <TableCell>Reason</TableCell>
                  <TableCell align="right">Failures</TableCell>
                  <TableCell align="right">Share</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.failure_breakdown.map((row) => (
                  <TableRow key={row.reason}>
                    <TableCell>{row.reason}</TableCell>
                    <TableCell align="right">{row.failures}</TableCell>
                    <TableCell align="right">{percent(row.share)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState
              icon="solar:shield-check-linear"
              title="No failures recorded"
            />
          )}
        </SectionCard>
      </Box>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" },
          gap: 2,
        }}
      >
        <SectionCard title="Provider performance" dense>
          <BreakdownTable
            rows={data.provider_breakdown}
            labelKey="provider"
            label="Provider"
          />
        </SectionCard>
        <SectionCard title="Modality performance" dense>
          <BreakdownTable
            rows={data.modality_breakdown}
            labelKey="modality"
            label="Modality"
          />
        </SectionCard>
      </Box>

      <SectionCard
        title="Cost breakdown"
        subtitle="Stored components; unavailable values are not treated as zero"
        dense
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
            gap: 1,
            p: 2,
          }}
        >
          {Object.entries(data.cost_breakdown_cents).map(([key, value]) => (
            <Box
              key={key}
              sx={{
                p: 1.5,
                borderRadius: 1,
                bgcolor: (theme) => alpha(theme.palette.text.primary, 0.035),
              }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  textTransform: "uppercase",
                }}
              >
                {key}
              </Typography>
              <Typography sx={{ typography: "s1", mt: 0.25 }}>
                {money(value.total)}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {value.measured}/{value.calls} measured
              </Typography>
            </Box>
          ))}
        </Box>
      </SectionCard>

      <SectionCard
        title="Cross-run trend"
        subtitle="Latest 20 completed runs"
        dense
      >
        <Table size="small" sx={tableSx}>
          <TableHead>
            <TableRow>
              <TableCell>Started</TableCell>
              <TableCell align="right">Tasks</TableCell>
              <TableCell align="right">Pass rate</TableCell>
              <TableCell align="right">p95 latency</TableCell>
              <TableCell align="right">Tokens</TableCell>
              <TableCell align="right">Cost</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.trends.map((row) => (
              <TableRow
                key={row.execution_id}
                selected={row.execution_id === executionId}
              >
                <TableCell>
                  {new Date(row.started_at).toLocaleString()}
                </TableCell>
                <TableCell align="right">{row.total}</TableCell>
                <TableCell align="right">{percent(row.pass_rate)}</TableCell>
                <TableCell align="right">
                  {row.latency?.p95 == null
                    ? "—"
                    : `${formatNumber(row.latency.p95)}ms`}
                </TableCell>
                <TableCell align="right">
                  {formatNumber(row.tokens?.total_value, 0)}
                </TableCell>
                <TableCell align="right">
                  {money(row.cost_cents?.total_value)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </SectionCard>
    </Stack>
  );
}

RunAnalytics.propTypes = { executionId: PropTypes.string.isRequired };
