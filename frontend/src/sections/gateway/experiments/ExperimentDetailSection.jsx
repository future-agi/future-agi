/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Box,
  Typography,
  Stack,
  Chip,
  Button,
  Tabs,
  Tab,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Skeleton,
  Collapse,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import PageErrorState from "../components/PageErrorState";
import { formatDateTime } from "../utils/formatters";

import {
  useExperimentDetail,
  useExperimentStats,
  useShadowResults,
  usePauseExperiment,
  useResumeExperiment,
  useCompleteExperiment,
  useDeleteExperiment,
} from "./hooks/useExperiments";
import SideBySideViewer from "./SideBySideViewer";

const STATUS_COLORS = {
  active: "success",
  paused: "warning",
  completed: "info",
};

const TAB_LABELS = ["Overview", "Results", "Configuration"];

const ExperimentDetailSection = ({ experimentId, onBack }) => {
  const [activeTab, setActiveTab] = useState(0);
  const {
    data: experiment,
    isLoading,
    error,
    refetch,
  } = useExperimentDetail(experimentId);

  const pauseMutation = usePauseExperiment();
  const resumeMutation = useResumeExperiment();
  const completeMutation = useCompleteExperiment();
  const deleteMutation = useDeleteExperiment();

  const handlePause = () => {
    pauseMutation.mutate(experimentId, {
      onSuccess: () =>
        enqueueSnackbar("Experiment paused", { variant: "success" }),
      onError: () => enqueueSnackbar("Failed to pause", { variant: "error" }),
    });
  };

  const handleResume = () => {
    resumeMutation.mutate(experimentId, {
      onSuccess: () =>
        enqueueSnackbar("Experiment resumed", { variant: "success" }),
      onError: () => enqueueSnackbar("Failed to resume", { variant: "error" }),
    });
  };

  const handleComplete = () => {
    if (
      !window.confirm(
        "Complete this experiment? No more shadow results will be collected.",
      )
    )
      return;
    completeMutation.mutate(experimentId, {
      onSuccess: () =>
        enqueueSnackbar("Experiment completed", { variant: "success" }),
      onError: () =>
        enqueueSnackbar("Failed to complete", { variant: "error" }),
    });
  };

  const handleDelete = () => {
    if (!window.confirm("Delete this experiment and all its results?")) return;
    deleteMutation.mutate(experimentId, {
      onSuccess: () => {
        enqueueSnackbar("Experiment deleted", { variant: "success" });
        onBack();
      },
      onError: () => enqueueSnackbar("Failed to delete", { variant: "error" }),
    });
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Skeleton width={120} height={32} sx={{ mb: 2 }} />
        <Skeleton width={300} height={40} sx={{ mb: 1 }} />
        <Skeleton width={200} height={24} sx={{ mb: 3 }} />
        <Stack direction="row" spacing={2} mb={3}>
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} variant="rounded" width={180} height={100} />
          ))}
        </Stack>
      </Box>
    );
  }

  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load experiment: ${error.message}`}
          onRetry={refetch}
        />
      </Box>
    );
  }

  if (!experiment) return null;

  const status = experiment.status;
  const sourceModel = experiment.source_model;
  const shadowModel = experiment.shadow_model;

  return (
    <Box p={3}>
      {/* Back button */}
      <Button
        startIcon={<Iconify icon="eva:arrow-back-outline" />}
        onClick={onBack}
        sx={{ mb: 2, textTransform: "none" }}
        size="small"
      >
        Back to Experiments
      </Button>

      {/* Header */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        mb={1}
      >
        <Box>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <Typography variant="h5" fontWeight={600}>
              {experiment.name}
            </Typography>
            <Chip
              label={status}
              color={STATUS_COLORS[status] || "default"}
              size="small"
            />
          </Stack>
          {experiment.description && (
            <Typography variant="body2" color="text.secondary" mt={0.5}>
              {experiment.description}
            </Typography>
          )}
          <Typography variant="body2" color="text.secondary" mt={0.5}>
            {sourceModel} &rarr; {shadowModel}
            {" \u2022 "}
            Sample rate: {Math.round(Number(experiment.sample_rate || 0) * 100)}
            %
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          {status === "active" && (
            <>
              <Button size="small" variant="outlined" onClick={handlePause}>
                Pause
              </Button>
              <Button size="small" variant="outlined" onClick={handleComplete}>
                Complete
              </Button>
            </>
          )}
          {status === "paused" && (
            <>
              <Button size="small" variant="outlined" onClick={handleResume}>
                Resume
              </Button>
              <Button size="small" variant="outlined" onClick={handleComplete}>
                Complete
              </Button>
            </>
          )}
          <Button
            size="small"
            variant="outlined"
            color="error"
            onClick={handleDelete}
          >
            Delete
          </Button>
        </Stack>
      </Stack>

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onChange={(_, val) => setActiveTab(val)}
        sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
      >
        {TAB_LABELS.map((label) => (
          <Tab key={label} label={label} sx={{ textTransform: "none" }} />
        ))}
      </Tabs>

      {activeTab === 0 && (
        <OverviewTab experimentId={experimentId} experiment={experiment} />
      )}
      {activeTab === 1 && (
        <ResultsTab experimentId={experimentId} experiment={experiment} />
      )}
      {activeTab === 2 && <ConfigurationTab experiment={experiment} />}
    </Box>
  );
};

// ── Overview Tab ─────────────────────────────────────────────

const OverviewTab = ({ experimentId, experiment }) => {
  const { data: stats, isLoading } = useExperimentStats(experimentId);
  const sourceModel = experiment.source_model;
  const shadowModel = experiment.shadow_model;

  if (isLoading) {
    return (
      <Stack direction="row" spacing={2} mb={3}>
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} variant="rounded" width={180} height={100} />
        ))}
      </Stack>
    );
  }

  if (!stats) return null;

  const summaryCards = [
    {
      label: "Total Comparisons",
      value: Number(stats.total_comparisons || 0).toLocaleString(),
    },
    {
      label: "Latency Delta",
      value: `${Number(stats.latency_delta_pct || 0).toFixed(1)}%`,
      delta: Number(stats.latency_delta_pct || 0),
    },
    {
      label: "Token Delta",
      value: `${Number(stats.token_delta_pct || 0).toFixed(1)}%`,
      delta: Number(stats.token_delta_pct || 0),
    },
    {
      label: "Shadow Error Rate",
      value: `${Number(stats.shadow_error_rate || 0).toFixed(2)}%`,
      delta: Number(stats.shadow_error_rate || 0),
      invertColor: true,
    },
  ];

  const metricsRows = [
    {
      metric: "Avg Latency (ms)",
      prod: Number(stats.avg_source_latency_ms || 0).toFixed(1),
      shadow: Number(stats.avg_shadow_latency_ms || 0).toFixed(1),
      delta: stats.latency_delta_pct,
    },
    {
      metric: "Avg Tokens",
      prod: Number(stats.avg_source_tokens || 0).toFixed(1),
      shadow: Number(stats.avg_shadow_tokens || 0).toFixed(1),
      delta: stats.token_delta_pct,
    },
    {
      metric: "Total Tokens",
      prod: Number(stats.total_source_tokens || 0).toLocaleString(),
      shadow: Number(stats.total_shadow_tokens || 0).toLocaleString(),
      delta: null,
    },
    {
      metric: "Error Count",
      prod: stats.source_error_count || 0,
      shadow: stats.shadow_error_count || 0,
      delta: null,
    },
    {
      metric: "Error Rate",
      prod: `${Number(stats.source_error_rate || 0).toFixed(2)}%`,
      shadow: `${Number(stats.shadow_error_rate || 0).toFixed(2)}%`,
      delta: null,
    },
  ];

  return (
    <Box>
      {/* Summary cards */}
      <Stack direction="row" spacing={2} mb={4} flexWrap="wrap">
        {summaryCards.map((card) => (
          <Card
            key={card.label}
            variant="outlined"
            sx={{ minWidth: 170, flex: 1, borderRadius: 2 }}
          >
            <CardContent sx={{ py: 2 }}>
              <Typography variant="caption" color="text.secondary">
                {card.label}
              </Typography>
              <Typography
                variant="h5"
                fontWeight={600}
                sx={{
                  color:
                    card.delta != null
                      ? card.invertColor
                        ? card.delta > 0
                          ? "#EF4444"
                          : "#10B981"
                        : card.delta < 0
                          ? "#10B981"
                          : card.delta > 0
                            ? "#EF4444"
                            : undefined
                      : undefined,
                }}
              >
                {card.delta != null &&
                  card.delta !== 0 &&
                  (card.delta < 0 ? "\u25BC " : "\u25B2 ")}
                {card.value}
              </Typography>
            </CardContent>
          </Card>
        ))}
      </Stack>

      {/* Metrics comparison table */}
      <Typography variant="subtitle1" fontWeight={600} mb={2}>
        Metrics Comparison
      </Typography>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Metric</TableCell>
              <TableCell>Production ({sourceModel})</TableCell>
              <TableCell>Shadow ({shadowModel})</TableCell>
              <TableCell>Delta</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {metricsRows.map((row) => (
              <TableRow key={row.metric}>
                <TableCell>{row.metric}</TableCell>
                <TableCell>{row.prod}</TableCell>
                <TableCell>{row.shadow}</TableCell>
                <TableCell>
                  {row.delta != null ? (
                    <DeltaText value={Number(row.delta)} />
                  ) : (
                    "\u2014"
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      <Typography
        variant="caption"
        color="text.secondary"
        mt={2}
        display="block"
      >
        {"\u25BC"} = shadow is better (green) &nbsp; {"\u25B2"} = shadow is
        worse (red)
      </Typography>
    </Box>
  );
};

// ── Results Tab ──────────────────────────────────────────────

const ResultsTab = ({ experimentId, experiment }) => {
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState(null);
  const pageSize = 20;

  const { data, isLoading } = useShadowResults({
    experiment: experimentId,
    page,
    page_size: pageSize,
  });

  const results = data?.results || data || [];
  const totalCount = data?.count || results.length;

  if (isLoading) {
    return (
      <Box>
        {[...Array(6)].map((_, i) => (
          <Skeleton key={i} width="100%" height={40} sx={{ mb: 0.5 }} />
        ))}
      </Box>
    );
  }

  if (results.length === 0) {
    return (
      <Box display="flex" flexDirection="column" alignItems="center" py={6}>
        <Iconify
          icon="mdi:table-off"
          width={40}
          sx={{ color: "text.disabled", mb: 1 }}
        />
        <Typography variant="body1" color="text.secondary">
          No shadow results yet
        </Typography>
        <Typography variant="body2" color="text.secondary" mt={0.5}>
          Results will appear here once the gateway starts mirroring traffic.
        </Typography>
      </Box>
    );
  }

  const totalPages = Math.ceil(totalCount / pageSize);

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" mb={2}>
        <Typography variant="subtitle1" fontWeight={600}>
          Shadow Results
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {Number(totalCount).toLocaleString()} total
        </Typography>
      </Stack>

      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell width={30} />
              <TableCell>Time</TableCell>
              <TableCell>Prod Latency</TableCell>
              <TableCell>Shadow Latency</TableCell>
              <TableCell>Prod Tokens</TableCell>
              <TableCell>Shadow Tokens</TableCell>
              <TableCell>Status</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {results.map((r) => {
              const id = r.id;
              const isExpanded = expandedId === id;
              const sourceOk = Number(r.source_status_code || 200) === 200;
              const shadowOk =
                Number(r.shadow_status_code || 0) === 200 && !r.shadow_error;
              return (
                <React.Fragment key={id}>
                  <TableRow
                    hover
                    sx={{ cursor: "pointer" }}
                    onClick={() => setExpandedId(isExpanded ? null : id)}
                  >
                    <TableCell>
                      <Iconify
                        icon={
                          isExpanded ? "mdi:chevron-down" : "mdi:chevron-right"
                        }
                        width={18}
                      />
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        sx={{ fontFamily: "monospace", fontSize: "0.8rem" }}
                      >
                        {formatDateTime(r.created_at)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      {Number(r.source_latency_ms || 0).toLocaleString()}
                      ms
                    </TableCell>
                    <TableCell>
                      {Number(r.shadow_latency_ms || 0).toLocaleString()}
                      ms
                    </TableCell>
                    <TableCell>
                      {Number(r.source_tokens || 0).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      {Number(r.shadow_tokens || 0).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" spacing={0.5}>
                        <StatusDot ok={sourceOk} />
                        <StatusDot ok={shadowOk} />
                      </Stack>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell
                      colSpan={7}
                      sx={{ p: 0, border: isExpanded ? undefined : "none" }}
                    >
                      <Collapse in={isExpanded} unmountOnExit>
                        <Box sx={{ p: 2, bgcolor: "background.neutral" }}>
                          <SideBySideViewer
                            result={r}
                            sourceModel={experiment.source_model}
                            shadowModel={experiment.shadow_model}
                          />
                        </Box>
                      </Collapse>
                    </TableCell>
                  </TableRow>
                </React.Fragment>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Pagination */}
      {totalPages > 1 && (
        <Stack
          direction="row"
          justifyContent="center"
          alignItems="center"
          spacing={2}
          mt={2}
        >
          <Button
            size="small"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </Button>
          <Typography variant="body2">
            Page {page} of {totalPages}
          </Typography>
          <Button
            size="small"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </Stack>
      )}
    </Box>
  );
};

// ── Configuration Tab ────────────────────────────────────────

const ConfigurationTab = ({ experiment }) => {
  const fields = [
    { label: "Name", value: experiment.name },
    { label: "Description", value: experiment.description || "\u2014" },
    { label: "Status", value: experiment.status },
    {
      label: "Created",
      value: formatDateTime(experiment.created_at),
    },
    {
      label: "Production Model",
      value: experiment.source_model,
    },
    {
      label: "Shadow Model",
      value: experiment.shadow_model,
    },
    {
      label: "Shadow Provider",
      value: experiment.shadow_provider,
    },
    {
      label: "Sample Rate",
      value: `${Math.round(Number(experiment.sample_rate || 0) * 100)}%`,
    },
    {
      label: "Total Comparisons",
      value: Number(experiment.total_comparisons || 0).toLocaleString(),
    },
  ];

  return (
    <Card variant="outlined" sx={{ borderRadius: 2 }}>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={600} mb={2}>
          Experiment Configuration
        </Typography>
        <Stack spacing={1.5}>
          {fields.map((f) => (
            <Stack key={f.label} direction="row" spacing={2}>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ minWidth: 160 }}
              >
                {f.label}:
              </Typography>
              <Typography variant="body2">{f.value}</Typography>
            </Stack>
          ))}
        </Stack>
      </CardContent>
    </Card>
  );
};

// ── Helpers ──────────────────────────────────────────────────

const DeltaText = ({ value }) => {
  if (value === 0)
    return (
      <Box component="span" sx={{ color: "accent.neutral" }}>
        {"\u2014"}
      </Box>
    );
  const isImprovement = value < 0;
  const color = isImprovement ? "#10B981" : "#EF4444";
  const arrow = isImprovement ? "\u25BC" : "\u25B2";
  return (
    <span style={{ color, fontWeight: 500 }}>
      {arrow} {Math.abs(value).toFixed(1)}%
    </span>
  );
};

const StatusDot = ({ ok }) => (
  <Box
    sx={{
      width: 8,
      height: 8,
      borderRadius: "50%",
      bgcolor: ok ? "#22C55E" : "#EF4444",
      display: "inline-block",
      mt: 0.5,
    }}
  />
);

export default ExperimentDetailSection;
