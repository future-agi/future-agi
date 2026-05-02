import PropTypes from "prop-types";
import {
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  LinearProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useQueueAnalytics } from "src/api/annotation-queues/annotation-queues";
import { fDateTime } from "src/utils/format-time";

function StatCard({ title, value, color = "text.primary" }) {
  return (
    <Card sx={{ flex: 1, minWidth: 140 }}>
      <CardContent sx={{ py: 2, "&:last-child": { pb: 2 } }}>
        <Typography variant="caption" color="text.secondary">
          {title}
        </Typography>
        <Typography variant="h4" color={color}>
          {value}
        </Typography>
      </CardContent>
    </Card>
  );
}

StatCard.propTypes = {
  title: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
  color: PropTypes.string,
};

function StatusBar({ statusBreakdown, total }) {
  if (!total) return null;
  const segments = [
    { key: "completed", color: "success.main", label: "Completed" },
    { key: "in_progress", color: "warning.main", label: "In Progress" },
    { key: "pending", color: "info.main", label: "Pending" },
    { key: "skipped", color: "text.disabled", label: "Skipped" },
  ];

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        Status Breakdown
      </Typography>
      <Box
        sx={{
          display: "flex",
          height: 24,
          borderRadius: 0.5,
          overflow: "hidden",
        }}
      >
        {segments.map((seg) => {
          const count = statusBreakdown[seg.key] || 0;
          const pct = (count / total) * 100;
          if (!pct) return null;
          return (
            <Box
              key={seg.key}
              sx={{
                width: `${pct}%`,
                bgcolor: seg.color,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {pct > 10 && (
                <Typography
                  variant="caption"
                  sx={{ color: "common.white", fontSize: 10 }}
                >
                  {count}
                </Typography>
              )}
            </Box>
          );
        })}
      </Box>
      <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
        {segments.map((seg) => (
          <Stack
            key={seg.key}
            direction="row"
            alignItems="center"
            spacing={0.5}
          >
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                bgcolor: seg.color,
              }}
            />
            <Typography variant="caption">
              {seg.label}: {statusBreakdown[seg.key] || 0}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

StatusBar.propTypes = {
  statusBreakdown: PropTypes.object.isRequired,
  total: PropTypes.number,
};

function formatLabelValue(val) {
  try {
    const parsed =
      typeof val === "string" && val.startsWith("{")
        ? JSON.parse(val.replace(/'/g, '"'))
        : val;
    if (typeof parsed === "object" && parsed !== null) {
      if (parsed.selected)
        return Array.isArray(parsed.selected)
          ? parsed.selected.join(", ")
          : String(parsed.selected);
      if (parsed.text) return parsed.text;
      if (parsed.rating != null) return `${parsed.rating} stars`;
      if (parsed.value != null)
        return parsed.value === "up"
          ? "👍 Up"
          : parsed.value === "down"
            ? "👎 Down"
            : String(parsed.value);
    }
  } catch {
    /* fall through */
  }
  return String(val);
}

function LabelDistribution({ labelDistribution }) {
  const labels = Object.entries(labelDistribution || {});
  if (!labels.length) return null;

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        Label Distribution
      </Typography>
      <Stack spacing={2}>
        {labels.map(([id, label]) => {
          const entries = Object.entries(label.values || {});
          const total = entries.reduce((s, [, c]) => s + c, 0);
          return (
            <Card key={id} variant="outlined">
              <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  {label.name}
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.secondary"
                    sx={{ ml: 1 }}
                  >
                    ({label.type})
                  </Typography>
                </Typography>
                <Stack spacing={0.5}>
                  {entries.map(([val, count]) => (
                    <Stack
                      key={val}
                      direction="row"
                      alignItems="center"
                      spacing={1}
                    >
                      <Typography
                        variant="body2"
                        sx={{ minWidth: 120, fontSize: 12 }}
                      >
                        {formatLabelValue(val)}
                      </Typography>
                      <LinearProgress
                        variant="determinate"
                        value={total ? (count / total) * 100 : 0}
                        sx={{ flex: 1, height: 6, borderRadius: 3 }}
                      />
                      <Typography
                        variant="caption"
                        sx={{ minWidth: 30, textAlign: "right" }}
                      >
                        {count}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </CardContent>
            </Card>
          );
        })}
      </Stack>
    </Box>
  );
}

LabelDistribution.propTypes = {
  labelDistribution: PropTypes.object,
};

export default function QueueAnalyticsTab({ queueId }) {
  const { data: analytics, isLoading } = useQueueAnalytics(queueId);

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!analytics) return null;

  const {
    throughput,
    annotator_performance,
    label_distribution,
    status_breakdown,
    total,
  } = analytics;
  // camelCase alternatives
  const annotatorPerf =
    annotator_performance || analytics.annotatorPerformance || [];
  const labelDist = label_distribution || analytics.labelDistribution || {};
  const statusBreak = status_breakdown || analytics.statusBreakdown || {};
  const tp = throughput || {};

  const completionPct = total
    ? Math.round(((statusBreak.completed || 0) / total) * 100)
    : 0;

  const handleExport = async (format) => {
    try {
      const { default: axiosInstance } = await import("src/utils/axios");
      const response = await axiosInstance.get(
        `/model-hub/annotation-queues/${queueId}/export/`,
        { params: { export_format: format }, responseType: "blob" },
      );
      const blob = new Blob([response.data]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `queue-export.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      const { enqueueSnackbar } = await import("notistack");
      enqueueSnackbar("Export failed", { variant: "error" });
    }
  };

  return (
    <Box sx={{ p: 3, py: 0 }}>
      {/* Export */}
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ mb: 3 }}
      >
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="eva:download-fill" width={16} />}
          onClick={() => handleExport("json")}
        >
          Export JSON
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="eva:download-fill" width={16} />}
          onClick={() => handleExport("csv")}
        >
          Export CSV
        </Button>
      </Stack>

      {/* Overview Cards */}
      <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
        <StatCard title="Total Items" value={total || 0} />
        <StatCard
          title="Completed"
          value={statusBreak.completed || 0}
          color="success.main"
        />
        <StatCard title="Completion Rate" value={`${completionPct}%`} />
        <StatCard
          title="Avg / Day"
          value={tp.avg_per_day || tp.avgPerDay || 0}
        />
      </Stack>

      {/* Status Bar */}
      <Box sx={{ mb: 3 }}>
        <StatusBar statusBreakdown={statusBreak} total={total} />
      </Box>

      {/* Daily Throughput */}
      {(tp.daily || []).length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Daily Throughput (Last 30 Days)
          </Typography>
          <Stack
            direction="row"
            spacing={0.5}
            alignItems="flex-end"
            sx={{ height: 100, overflow: "hidden" }}
          >
            {(() => {
              const dailyData = tp.daily || [];
              const maxCount = dailyData.reduce(
                (max, x) => Math.max(max, x.count),
                1,
              );
              return dailyData.map((d, i) => {
                const height = (d.count / maxCount) * 100;
                return (
                  <Box
                    key={i}
                    sx={{
                      flex: 1,
                      maxWidth: 20,
                      height: `${height}%`,
                      bgcolor: "primary.main",
                      borderRadius: "2px 2px 0 0",
                      minHeight: 2,
                    }}
                    title={`${d.date}: ${d.count}`}
                  />
                );
              });
            })()}
          </Stack>
        </Box>
      )}

      {/* Label Distribution */}
      <Box sx={{ mb: 3 }}>
        <LabelDistribution labelDistribution={labelDist} />
      </Box>

      {/* Annotator Performance */}
      {annotatorPerf.length > 0 && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Annotator Performance
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell align="right">Completed</TableCell>
                  <TableCell>Last Active</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {annotatorPerf.map((a) => (
                  <TableRow key={a.user_id}>
                    <TableCell>{a.name}</TableCell>
                    <TableCell align="right">{a.completed}</TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {a.last_active ? fDateTime(a.last_active) : "—"}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}
    </Box>
  );
}

QueueAnalyticsTab.propTypes = {
  queueId: PropTypes.string.isRequired,
};
