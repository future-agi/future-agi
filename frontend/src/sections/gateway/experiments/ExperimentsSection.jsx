/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Box,
  Typography,
  Stack,
  Chip,
  Card,
  CardContent,
  CardActionArea,
  Skeleton,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  InputAdornment,
  Button,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import PageErrorState from "../components/PageErrorState";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useGatewayContext } from "../context/useGatewayContext";

import {
  useExperiments,
  useCreateExperiment,
  usePauseExperiment,
  useResumeExperiment,
  useDeleteExperiment,
} from "./hooks/useExperiments";
import CreateExperimentDialog from "./CreateExperimentDialog";
import ExperimentDetailSection from "./ExperimentDetailSection";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "completed", label: "Completed" },
];

const STATUS_COLORS = {
  active: "success",
  paused: "warning",
  completed: "info",
};

const ExperimentsSection = () => {
  const { experimentId } = useParams();
  const navigate = useNavigate();
  const { gatewayId } = useGatewayContext();

  // If there's an experimentId in the URL, show the detail page
  if (experimentId) {
    return (
      <ExperimentDetailSection
        experimentId={experimentId}
        onBack={() => navigate("/dashboard/gateway/experiments")}
      />
    );
  }

  return <ExperimentsListView gatewayId={gatewayId} />;
};

const ExperimentsListView = ({ gatewayId }) => {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const params = {};
  if (gatewayId) params.gateway = gatewayId;
  if (statusFilter) params.status = statusFilter;

  const {
    data: experiments,
    isLoading,
    error,
    refetch,
  } = useExperiments(params);
  const createMutation = useCreateExperiment();
  const pauseMutation = usePauseExperiment();
  const resumeMutation = useResumeExperiment();
  const deleteMutation = useDeleteExperiment();
  // Every shadow-experiment write is Owner or Admin only (useGatewayAdmin).
  const { canEdit, disabledReason } = useGatewayAdmin();

  const filtered = useMemo(() => {
    if (!experiments) return [];
    if (!searchQuery.trim()) return experiments;
    const q = searchQuery.toLowerCase();
    return experiments.filter(
      (e) =>
        e.name?.toLowerCase().includes(q) ||
        e.source_model?.toLowerCase().includes(q) ||
        e.shadow_model?.toLowerCase().includes(q),
    );
  }, [experiments, searchQuery]);

  const handleCreate = (payload) => {
    if (gatewayId) payload.gateway = gatewayId;
    createMutation.mutate(payload, {
      onSuccess: (result) => {
        enqueueSnackbar("Experiment created", { variant: "success" });
        setCreateOpen(false);
        if (result?.id) {
          navigate(`/dashboard/gateway/experiments/${result.id}`);
        }
      },
      onError: (err) => {
        enqueueSnackbar(
          `Failed to create: ${err?.response?.data?.result || err.message}`,
          { variant: "error" },
        );
      },
    });
  };

  const handlePause = (e, id) => {
    e.stopPropagation();
    pauseMutation.mutate(id, {
      onSuccess: () =>
        enqueueSnackbar("Experiment paused", { variant: "success" }),
      onError: () => enqueueSnackbar("Failed to pause", { variant: "error" }),
    });
  };

  const handleResume = (e, id) => {
    e.stopPropagation();
    resumeMutation.mutate(id, {
      onSuccess: () =>
        enqueueSnackbar("Experiment resumed", { variant: "success" }),
      onError: () => enqueueSnackbar("Failed to resume", { variant: "error" }),
    });
  };

  const handleDelete = (e, id) => {
    e.stopPropagation();
    if (!window.confirm("Delete this experiment and all its results?")) return;
    deleteMutation.mutate(id, {
      onSuccess: () =>
        enqueueSnackbar("Experiment deleted", { variant: "success" }),
      onError: () => enqueueSnackbar("Failed to delete", { variant: "error" }),
    });
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={200} height={40} />
          <Skeleton width={140} height={36} />
        </Stack>
        {[...Array(3)].map((_, i) => (
          <Skeleton
            key={i}
            variant="rounded"
            height={120}
            sx={{ mb: 2, borderRadius: 2 }}
          />
        ))}
      </Box>
    );
  }

  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load experiments: ${error.message}`}
          onRetry={refetch}
        />
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.experiments}
        title="Shadow Experiments"
        subtitle="Run shadow tests against alternate models without affecting production traffic"
        actions={[
          {
            label: "New Experiment",
            variant: "contained",
            size: "small",
            icon: "eva:plus-outline",
            onClick: () => setCreateOpen(true),
            disabled: !canEdit,
            tooltip: disabledReason || undefined,
          },
        ]}
      />

      <Stack direction="row" spacing={2} alignItems="center" mb={3}>
        <ToggleButtonGroup
          value={statusFilter}
          exclusive
          onChange={(_, val) => val !== null && setStatusFilter(val)}
          size="small"
        >
          {STATUS_FILTERS.map((f) => (
            <ToggleButton
              key={f.value}
              value={f.value}
              sx={{
                px: 1.5,
                py: 0.25,
                textTransform: "none",
                fontSize: "0.8125rem",
              }}
            >
              {f.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <TextField
          placeholder="Search experiments..."
          size="small"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          sx={{ width: 300 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="eva:search-outline" width={18} />
              </InputAdornment>
            ),
          }}
        />
      </Stack>

      {filtered.length === 0 ? (
        <Box display="flex" flexDirection="column" alignItems="center" py={8}>
          <Iconify
            icon="mdi:flask-outline"
            width={48}
            sx={{ color: "text.disabled", mb: 2 }}
          />
          <Typography variant="h6" color="text.secondary">
            {experiments?.length === 0
              ? "No experiments yet"
              : "No experiments match your filters"}
          </Typography>
          <Typography variant="body2" color="text.secondary" mt={1} mb={3}>
            Shadow experiments let you compare models by routing a sample of
            production traffic to a secondary model in the background.
          </Typography>
          {experiments?.length === 0 && (
            <GatewayAdminOnly allowed={canEdit}>
              <Button
                variant="outlined"
                startIcon={<Iconify icon="eva:plus-outline" />}
                onClick={() => setCreateOpen(true)}
                disabled={!canEdit}
              >
                Create Your First Experiment
              </Button>
            </GatewayAdminOnly>
          )}
        </Box>
      ) : (
        <Stack spacing={2}>
          {filtered.map((exp) => (
            <ExperimentCard
              key={exp.id}
              experiment={exp}
              onClick={() =>
                navigate(`/dashboard/gateway/experiments/${exp.id}`)
              }
              onPause={handlePause}
              onResume={handleResume}
              onDelete={handleDelete}
              canEdit={canEdit}
            />
          ))}
          <Typography variant="body2" color="text.secondary" mt={1}>
            Showing {filtered.length} experiment
            {filtered.length !== 1 ? "s" : ""}
          </Typography>
        </Stack>
      )}

      <CreateExperimentDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={handleCreate}
        isLoading={createMutation.isPending}
      />
    </Box>
  );
};

// ── Experiment Card ──────────────────────────────────────────

const ExperimentCard = ({
  experiment,
  onClick,
  onPause,
  onResume,
  onDelete,
  canEdit,
}) => {
  const exp = experiment;
  const latencyDelta = exp.latencyDeltaPct ?? exp.latency_delta_pct;
  const tokenDelta = exp.tokenDeltaPct ?? exp.token_delta_pct;

  return (
    <Card variant="outlined" sx={{ borderRadius: 2 }}>
      <CardActionArea onClick={onClick} sx={{ p: 0 }}>
        <CardContent sx={{ py: 2 }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="flex-start"
          >
            <Box>
              <Stack direction="row" spacing={1} alignItems="center" mb={0.5}>
                <Typography variant="subtitle1" fontWeight={600}>
                  {exp.name}
                </Typography>
                <Chip
                  label={exp.status}
                  color={STATUS_COLORS[exp.status] || "default"}
                  size="small"
                />
              </Stack>
              <Typography variant="body2" color="text.secondary">
                Production: {exp.source_model}
                {" \u2192 "}
                Shadow: {exp.shadow_model}
              </Typography>
              <Typography variant="body2" color="text.secondary" mt={0.5}>
                Sample rate: {Math.round(Number(exp.sample_rate || 0) * 100)}%
                {" \u2022 "}
                {Number(exp.total_comparisons || 0).toLocaleString()}{" "}
                comparisons
              </Typography>
            </Box>

            <Stack direction="row" spacing={1} alignItems="center">
              {latencyDelta != null && Number(latencyDelta) !== 0 && (
                <DeltaChip label="Latency" value={Number(latencyDelta)} />
              )}
              {tokenDelta != null && Number(tokenDelta) !== 0 && (
                <DeltaChip label="Tokens" value={Number(tokenDelta)} />
              )}
            </Stack>
          </Stack>

          <Stack
            direction="row"
            spacing={1}
            justifyContent="flex-end"
            mt={1}
            onClick={(e) => e.stopPropagation()}
          >
            {exp.status === "active" && (
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={(e) => onPause(e, exp.id)}
                  disabled={!canEdit}
                >
                  Pause
                </Button>
              </GatewayAdminOnly>
            )}
            {exp.status === "paused" && (
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={(e) => onResume(e, exp.id)}
                  disabled={!canEdit}
                >
                  Resume
                </Button>
              </GatewayAdminOnly>
            )}
            {exp.status === "completed" && (
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  size="small"
                  variant="outlined"
                  color="error"
                  onClick={(e) => onDelete(e, exp.id)}
                  disabled={!canEdit}
                >
                  Delete
                </Button>
              </GatewayAdminOnly>
            )}
            <Button size="small" variant="text">
              View Details
            </Button>
          </Stack>
        </CardContent>
      </CardActionArea>
    </Card>
  );
};

// ── Delta Chip ───────────────────────────────────────────────

const DeltaChip = ({ label, value }) => {
  const isImprovement = value < 0;
  const color = isImprovement ? "#10B981" : "#EF4444";
  const arrow = isImprovement ? "\u25BC" : "\u25B2";
  return (
    <Chip
      label={`${label}: ${arrow} ${Math.abs(value).toFixed(1)}%`}
      size="small"
      sx={{
        color,
        borderColor: color,
        fontWeight: 500,
        fontSize: "0.75rem",
      }}
      variant="outlined"
    />
  );
};

export default ExperimentsSection;
