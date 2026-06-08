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
  FormControlLabel,
  Switch,
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

const DEFAULT_BUDGET_THRESHOLDS = [
  { percent: 50, enabled: true, severity: "info" },
  { percent: 80, enabled: true, severity: "warning" },
  { percent: 100, enabled: true, severity: "critical" },
];

const THRESHOLD_STAGE_COPY = {
  50: { label: "Early warning", color: "info" },
  80: { label: "Escalation", color: "warning" },
  100: { label: "Limit reached", color: "error" },
};

const createEmptyBudget = () => ({
  name: "",
  scope: "ai_credits",
  threshold_value: "",
  action: "notify",
  is_active: true,
  notify_emails: "",
  notify_slack_webhook: "",
  thresholds: DEFAULT_BUDGET_THRESHOLDS.map((stage) => ({ ...stage })),
});

function normalizeBudgetThresholds(thresholds) {
  const byPercent = new Map(
    DEFAULT_BUDGET_THRESHOLDS.map((stage) => [stage.percent, { ...stage }]),
  );

  (Array.isArray(thresholds) ? thresholds : []).forEach((stage) => {
    if (!byPercent.has(stage?.percent)) return;
    const fallback = byPercent.get(stage.percent);
    byPercent.set(stage.percent, {
      percent: stage.percent,
      enabled: stage.enabled !== false,
      severity: stage.severity || fallback.severity,
    });
  });

  return DEFAULT_BUDGET_THRESHOLDS.map((stage) => byPercent.get(stage.percent));
}

function parseEmailRecipients(value) {
  if (!value) return [];
  return value
    .split(",")
    .map((email) => email.trim())
    .filter(Boolean);
}

function formatEmailRecipients(recipients) {
  return Array.isArray(recipients) ? recipients.join(", ") : "";
}

function normalizeSlackWebhook(value) {
  const webhook = (value || "").trim();
  return webhook || null;
}

function budgetFormToPayload(budget) {
  return {
    name: budget.name.trim(),
    scope: budget.scope,
    threshold_value: budget.threshold_value,
    action: budget.action,
    is_active: budget.is_active !== false,
    notify_emails: parseEmailRecipients(budget.notify_emails),
    notify_slack_webhook: normalizeSlackWebhook(budget.notify_slack_webhook),
    thresholds: normalizeBudgetThresholds(budget.thresholds),
  };
}

export default function BudgetManager() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [newBudget, setNewBudget] = useState(createEmptyBudget);

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
      setNewBudget(createEmptyBudget());
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
      setNewBudget(createEmptyBudget());
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

  const activeToggleMutation = useMutation({
    mutationFn: ({ id, isActive }) =>
      axios.put(endpoints.settings.v2.budgetDetail(id), {
        is_active: isActive,
      }),
    onSuccess: (_response, variables) => {
      queryClient.invalidateQueries({ queryKey: ["v2-budgets"] });
      enqueueSnackbar(
        variables.isActive ? "Budget enabled" : "Budget disabled",
        {
          variant: "success",
        },
      );
    },
    onError: () =>
      enqueueSnackbar("Failed to update budget status", { variant: "error" }),
  });

  const handleOpenEdit = useCallback((budget) => {
    setEditingId(budget.id);
    setNewBudget({
      name: budget.name,
      scope: budget.scope,
      threshold_value: String(budget.threshold_value),
      action: budget.action,
      is_active: budget.is_active !== false,
      notify_emails: formatEmailRecipients(budget.notify_emails),
      notify_slack_webhook: budget.notify_slack_webhook || "",
      thresholds: normalizeBudgetThresholds(budget.thresholds),
    });
    setDialogOpen(true);
  }, []);

  const handleSave = useCallback(() => {
    const payload = budgetFormToPayload(newBudget);
    if (editingId) {
      updateMutation.mutate({ id: editingId, data: payload });
    } else {
      createMutation.mutate(payload);
    }
  }, [editingId, newBudget, updateMutation, createMutation]);

  const handleCloseDialog = useCallback(() => {
    setDialogOpen(false);
    setEditingId(null);
    setNewBudget(createEmptyBudget());
  }, []);

  const handleThresholdToggle = useCallback((percent, enabled) => {
    setNewBudget((current) => ({
      ...current,
      thresholds: normalizeBudgetThresholds(current.thresholds).map((stage) =>
        stage.percent === percent ? { ...stage, enabled } : stage,
      ),
    }));
  }, []);

  const handleBudgetActiveToggle = useCallback(
    (budget, isActive) => {
      activeToggleMutation.mutate({ id: budget.id, isActive });
    },
    [activeToggleMutation],
  );

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
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "stretch", sm: "center" }}
        mb={2}
        spacing={1}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          Usage Budgets
        </Typography>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          alignItems={{ xs: "stretch", sm: "center" }}
        >
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="mdi:plus" />}
            onClick={() => setDialogOpen(true)}
          >
            Add Budget
          </Button>
          <Button
            variant="text"
            size="small"
            startIcon={<Iconify icon="mdi:bell-cog-outline" />}
            href="/dashboard/settings/notifications"
          >
            Notification settings
          </Button>
        </Stack>
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
            const thresholdStages = normalizeBudgetThresholds(
              budget.thresholds,
            );
            const isActive = budget.is_active !== false;

            return (
              <Paper
                key={budget.id}
                variant="outlined"
                sx={{
                  p: 2,
                  borderRadius: 2,
                  opacity: isActive ? 1 : 0.72,
                }}
              >
                <Stack
                  direction={{ xs: "column", md: "row" }}
                  justifyContent="space-between"
                  alignItems={{ xs: "stretch", md: "center" }}
                  spacing={2}
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
                        {Number(budget.threshold_value).toLocaleString()} -{" "}
                        {actionConfig.label}
                      </Typography>
                      <Stack direction="row" spacing={0.75} flexWrap="wrap">
                        {thresholdStages.map((stage) => {
                          const stageCopy = THRESHOLD_STAGE_COPY[
                            stage.percent
                          ] || {
                            label: "Threshold",
                            color: "default",
                          };
                          const disabled = stage.enabled === false;
                          return (
                            <Chip
                              key={stage.percent}
                              size="small"
                              label={`${stage.percent}% ${
                                disabled ? "Off" : stageCopy.label
                              }`}
                              color={disabled ? "default" : stageCopy.color}
                              variant="outlined"
                              sx={{ mt: 0.75 }}
                            />
                          );
                        })}
                      </Stack>
                    </Box>
                  </Stack>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    justifyContent={{ xs: "space-between", md: "flex-end" }}
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <FormControlLabel
                      sx={{ mr: 0 }}
                      control={
                        <Switch
                          size="small"
                          checked={isActive}
                          onChange={(event) =>
                            handleBudgetActiveToggle(
                              budget,
                              event.target.checked,
                            )
                          }
                          disabled={activeToggleMutation.isPending}
                          inputProps={{
                            "aria-label": `${budget.name} active`,
                          }}
                        />
                      }
                      label={
                        <Typography variant="caption" color="text.secondary">
                          {isActive ? "Active" : "Disabled"}
                        </Typography>
                      }
                    />
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
                    {budget.notify_slack_webhook && (
                      <Chip
                        label="Slack webhook"
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
            <Box>
              <Typography variant="subtitle2" mb={0.5}>
                Notification stages
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Send budget alerts as usage approaches the limit.
              </Typography>
              <Stack spacing={1} mt={1}>
                {normalizeBudgetThresholds(newBudget.thresholds).map(
                  (stage) => {
                    const stageCopy = THRESHOLD_STAGE_COPY[stage.percent];
                    return (
                      <FormControlLabel
                        key={stage.percent}
                        control={
                          <Switch
                            checked={stage.enabled !== false}
                            onChange={(event) =>
                              handleThresholdToggle(
                                stage.percent,
                                event.target.checked,
                              )
                            }
                            inputProps={{
                              "aria-label": `Alert at ${stage.percent}%`,
                            }}
                          />
                        }
                        label={
                          <Stack
                            direction="row"
                            spacing={1}
                            alignItems="center"
                            flexWrap="wrap"
                          >
                            <Chip
                              size="small"
                              label={`${stage.percent}%`}
                              color={stageCopy.color}
                              variant="outlined"
                            />
                            <Typography variant="body2">
                              {stageCopy.label}
                            </Typography>
                          </Stack>
                        }
                      />
                    );
                  },
                )}
              </Stack>
            </Box>
            <TextField
              label="Notification emails"
              fullWidth
              size="small"
              value={newBudget.notify_emails}
              onChange={(e) =>
                setNewBudget({
                  ...newBudget,
                  notify_emails: e.target.value,
                })
              }
              placeholder="ops@example.com, finance@example.com"
              helperText="Leave empty to notify organization admins."
            />
            <TextField
              label="Slack webhook"
              fullWidth
              size="small"
              value={newBudget.notify_slack_webhook}
              onChange={(e) =>
                setNewBudget({
                  ...newBudget,
                  notify_slack_webhook: e.target.value,
                })
              }
              placeholder="https://hooks.slack.com/services/..."
              helperText="Optional. Shared Slack channels can also be managed from notification settings."
            />
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
