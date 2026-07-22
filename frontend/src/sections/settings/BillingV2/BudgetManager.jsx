/**
 * Budget Manager — CRUD UI for per-dimension usage budgets.
 *
 * Users can set spending limits per dimension with actions:
 * - notify: email/slack alert only
 * - warn: alert + in-app banner
 * - pause: block further usage
 */

import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Stack,
  Paper,
  Button,
  Chip,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Skeleton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

const ACTION_LABELS = {
  notify: { label: "Notify", color: "info", icon: "mdi:bell-outline" },
  warn: { label: "Warn", color: "warning", icon: "mdi:alert-outline" },
  pause: {
    label: "Pause Usage",
    color: "error",
    icon: "mdi:pause-circle-outline",
  },
};

const SCOPE_OPTIONS = [
  { value: "ai_credits", label: "AI Credits" },
  { value: "storage", label: "Storage (GB)" },
  { value: "gateway_requests", label: "Gateway Requests" },
  { value: "gateway_cache_hits", label: "Cache Hits" },
  { value: "text_sim_tokens", label: "Text Sim Tokens" },
  { value: "voice_sim_minutes", label: "Voice Sim Minutes" },
  { value: "tracing_events", label: "Tracing Events" },
  { value: "total_spend", label: "Total Spend ($)" },
];

const EMPTY_BUDGET = {
  name: "",
  scope: "ai_credits",
  threshold_value: "",
  action: "notify",
};

export default function BudgetManager() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [newBudget, setNewBudget] = useState(EMPTY_BUDGET);

  const queryClient = useQueryClient();

  const { data: budgets, isLoading } = useQuery({
    queryKey: ["v2-budgets"],
    queryFn: () => axios.get(endpoints.settings.v2.budgets),
    select: (res) => res.data?.result?.budgets || [],
  });

  const createMutation = useMutation({
    mutationFn: (data) => axios.post(endpoints.settings.v2.budgets, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2-budgets"] });
      setDialogOpen(false);
      setEditingId(null);
      setNewBudget(EMPTY_BUDGET);
      enqueueSnackbar("Budget created", { variant: "success" });
    },
    onError: () =>
      enqueueSnackbar("Failed to create budget", { variant: "error" }),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }) =>
      axios.put(endpoints.settings.v2.budgetDetail(id), data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2-budgets"] });
      setDialogOpen(false);
      setEditingId(null);
      setNewBudget(EMPTY_BUDGET);
      enqueueSnackbar("Budget updated", { variant: "success" });
    },
    onError: () =>
      enqueueSnackbar("Failed to update budget", { variant: "error" }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id) => axios.delete(endpoints.settings.v2.budgetDetail(id)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2-budgets"] });
      setDeleteTarget(null);
      enqueueSnackbar("Budget deleted", { variant: "success" });
    },
  });

  const handleOpenEdit = useCallback((budget) => {
    setEditingId(budget.id);
    setNewBudget({
      name: budget.name,
      scope: budget.scope,
      threshold_value: String(budget.threshold_value),
      action: budget.action,
    });
    setDialogOpen(true);
  }, []);

  const handleSave = useCallback(() => {
    if (editingId) {
      updateMutation.mutate({ id: editingId, data: newBudget });
    } else {
      createMutation.mutate(newBudget);
    }
  }, [editingId, newBudget, updateMutation, createMutation]);

  const handleCloseDialog = useCallback(() => {
    setDialogOpen(false);
    setEditingId(null);
    setNewBudget(EMPTY_BUDGET);
  }, []);

  // Threshold must be a positive decimal. HTML `type="number"` accepts
  // `e` / `E` as scientific-notation exponents (`1e5` parses as 100000),
  // which is why users could see letters in the field without any
  // inline warning — the native control treated the input as valid.
  // Switching to `type="text"` + `inputMode="decimal"` keeps the soft
  // keyboard numeric on mobile while letting us enforce our own regex.
  const thresholdRaw = newBudget.threshold_value;
  const thresholdIsInvalid =
    thresholdRaw !== "" &&
    !(/^\d+\.?\d*$/.test(thresholdRaw) && parseFloat(thresholdRaw) > 0);

  if (isLoading) return <Skeleton variant="rounded" height={150} />;

  return (
    <Box>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={2}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          Usage Budgets
        </Typography>
        <Button
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="mdi:plus" />}
          onClick={() => setDialogOpen(true)}
        >
          Add Budget
        </Button>
      </Stack>

      {!budgets || budgets.length === 0 ? (
        <Paper
          variant="outlined"
          sx={{
            p: 3,
            textAlign: "center",
            borderStyle: "dashed",
            borderRadius: 2,
          }}
        >
          <Iconify
            icon="mdi:shield-check-outline"
            width={36}
            sx={{ color: "text.disabled", mb: 1 }}
          />
          <Typography variant="body2" color="text.secondary">
            No budgets set. Add a budget to get notified or pause usage when
            thresholds are reached.
          </Typography>
        </Paper>
      ) : (
        <Stack spacing={1.5}>
          {budgets.map((budget) => {
            const actionConfig =
              ACTION_LABELS[budget.action] || ACTION_LABELS.notify;
            const scopeLabel =
              SCOPE_OPTIONS.find((s) => s.value === budget.scope)?.label ||
              budget.scope;

            return (
              <Paper
                key={budget.id}
                variant="outlined"
                sx={{ p: 2, borderRadius: 2 }}
              >
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Iconify
                      icon={actionConfig.icon}
                      width={20}
                      sx={{ color: `${actionConfig.color}.main` }}
                    />
                    <Box>
                      <Typography variant="body2" fontWeight={600}>
                        {budget.name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {scopeLabel}:{" "}
                        {Number(budget.threshold_value).toLocaleString()} →{" "}
                        {actionConfig.label}
                      </Typography>
                    </Box>
                  </Stack>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip
                      label={actionConfig.label}
                      size="small"
                      color={actionConfig.color}
                      variant="outlined"
                    />
                    {budget.last_triggered_period && (
                      <Chip
                        label={`Triggered ${budget.last_triggered_period}`}
                        size="small"
                        variant="outlined"
                      />
                    )}
                    <IconButton
                      size="small"
                      onClick={() => handleOpenEdit(budget)}
                      title="Edit budget"
                    >
                      <Iconify icon="mdi:pencil-outline" width={18} />
                    </IconButton>
                    <IconButton
                      size="small"
                      onClick={() => setDeleteTarget(budget)}
                      title="Delete budget"
                    >
                      <Iconify icon="mdi:delete-outline" width={18} />
                    </IconButton>
                  </Stack>
                </Stack>
              </Paper>
            );
          })}
        </Stack>
      )}

      {/* Create / Edit Budget Dialog */}
      <Dialog
        open={dialogOpen}
        onClose={handleCloseDialog}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {editingId ? "Edit Budget" : "Add Usage Budget"}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} mt={1}>
            <TextField
              label="Budget Name"
              fullWidth
              size="small"
              value={newBudget.name}
              onChange={(e) =>
                setNewBudget({ ...newBudget, name: e.target.value })
              }
              placeholder="e.g., AI Credits monthly cap"
            />
            <FormControl fullWidth size="small">
              <InputLabel>Scope</InputLabel>
              <Select
                value={newBudget.scope}
                label="Scope"
                onChange={(e) =>
                  setNewBudget({ ...newBudget, scope: e.target.value })
                }
                disabled={!!editingId}
              >
                {SCOPE_OPTIONS.map((opt) => (
                  <MenuItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              label="Threshold"
              type="text"
              inputMode="decimal"
              fullWidth
              size="small"
              value={newBudget.threshold_value}
              onChange={(e) =>
                setNewBudget({ ...newBudget, threshold_value: e.target.value })
              }
              placeholder="e.g., 5000"
              error={thresholdIsInvalid}
              helperText={thresholdIsInvalid ? "Enter a positive number" : " "}
            />
            <FormControl fullWidth size="small">
              <InputLabel>Action</InputLabel>
              <Select
                value={newBudget.action}
                label="Action"
                onChange={(e) =>
                  setNewBudget({ ...newBudget, action: e.target.value })
                }
              >
                <MenuItem value="notify">
                  Notify — email/Slack alert only
                </MenuItem>
                <MenuItem value="warn">Warn — alert + in-app banner</MenuItem>
                <MenuItem value="pause">Pause — block further usage</MenuItem>
              </Select>
            </FormControl>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseDialog}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={
              !newBudget.name ||
              !newBudget.threshold_value ||
              thresholdIsInvalid ||
              createMutation.isPending ||
              updateMutation.isPending
            }
          >
            {createMutation.isPending || updateMutation.isPending
              ? "Saving..."
              : editingId
                ? "Save Changes"
                : "Create Budget"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation */}
      <CustomDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete Budget?"
        actionButton="Delete"
        color="error"
        onClickAction={() => deleteMutation.mutate(deleteTarget?.id)}
        loading={deleteMutation.isPending}
        preTitleIcon="mdi:alert-circle"
      >
        <Box sx={{ px: 2, py: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Delete &quot;{deleteTarget?.name}&quot;? This will remove the budget
            rule and clear any pause flags.
          </Typography>
        </Box>
      </CustomDialog>
    </Box>
  );
}
