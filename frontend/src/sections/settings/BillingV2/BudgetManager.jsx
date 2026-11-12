/**
 * Budget Manager — CRUD UI for per-dimension usage budgets.
 *
 * Users can set spending limits per dimension with actions:
 * - notify: email/slack alert only
 * - warn: alert + in-app banner
 * - pause: block further usage
 *
 * Where the installed billing package does not act on a total-spend budget,
 * or sends no budget e-mails to a budget that lists no recipients of its own,
 * the budget says so. Where that cannot be checked, the section says so once
 * and no budget claims either way. A budget that is turned off says so and
 * makes no claim about what reaching it does. In the budget dialog a
 * total-spend budget always says what it counts, and that line describes the
 * Threshold input.
 */

import EmptyState from "src/components/empty-state";
import PropTypes from "prop-types";
import React, { useState, useCallback, useId } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Stack,
  Button,
  Chip,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  IconButton,
  InputAdornment,
  DialogTitle,
  DialogContent,
  DialogActions,
  Skeleton,
} from "@mui/material";
import Section from "src/components/section";
import { useGatewayAdmin } from "src/sections/gateway/hooks/useGatewayAdmin";
import { formatExactMoney } from "src/sections/common/format/money";
import Iconify from "src/components/iconify";
import LoadFailedState from "src/components/load-failed-state";
import { ClosableDialog, ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

const ACTION_LABELS = {
  notify: { label: "Notify", color: "info", icon: "solar:bell-linear" },
  warn: {
    label: "Warn",
    color: "warning",
    icon: "solar:danger-triangle-linear",
  },
  pause: {
    label: "Pause Usage",
    color: "error",
    icon: "solar:pause-circle-linear",
  },
};

// A dimension's threshold is set in its display unit (the backend's
// display_unit: credits, GB, requests, hits, tokens, minutes, events). A
// total-spend threshold is in US dollars: budget enforcement compares it with
// this billing period's usage charges so far, the Usage charges figure on the
// Usage summary page, which includes AI Credits usage and leaves out the
// platform fee and AI Gateway model spend paid with Future AGI credits.
const SCOPE_OPTIONS = [
  { value: "ai_credits", label: "AI Credits", unit: "credits" },
  { value: "storage", label: "Storage", unit: "GB" },
  { value: "gateway_requests", label: "Gateway Requests", unit: "requests" },
  { value: "gateway_cache_hits", label: "Cache Hits", unit: "hits" },
  { value: "text_sim_tokens", label: "Text Sim Tokens", unit: "tokens" },
  { value: "voice_sim_minutes", label: "Voice Sim Minutes", unit: "minutes" },
  { value: "tracing_events", label: "Tracing Events", unit: "events" },
  { value: "total_spend", label: "Total Spend", unit: "USD" },
];

const scopeOption = (scope) =>
  SCOPE_OPTIONS.find((s) => s.value === scope) || { label: scope, unit: "" };

// "500 credits" for a dimension, singular at exactly one ("1 credit"); total
// spend in dollars written as Current Period writes its total, grouped and
// with every digit kept ("$2,500.00").
function thresholdText(scope, value) {
  if (scope === "total_spend") {
    return formatExactMoney(value, { grouping: true });
  }
  const amount = Number(value);
  const { unit } = scopeOption(scope);
  const unitText = amount === 1 ? unit.replace(/s$/, "") : unit;
  return `${amount.toLocaleString()}${unitText ? ` ${unitText}` : ""}`;
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

// The backend stores the period as "YYYY-MM"; show "Oct 2026".
function periodText(period) {
  const match = /^(\d{4})-(\d{2})$/.exec(period || "");
  const month = match && MONTHS[Number(match[2]) - 1];
  return month ? `${month} ${match[1]}` : period;
}

const EMPTY_BUDGET = {
  name: "",
  scope: "ai_credits",
  threshold_value: "",
  action: "notify",
};

// Every member sees the budget controls; changing a budget needs an
// organization Owner or Admin, so below that they show disabled with this.
const BUDGET_EDIT_REASON =
  "Only an organization Owner or Admin can change budgets.";

// What a budget says when the installed billing package does not do what it
// asks: a total-spend budget is not acted on when reached (the sentence
// follows its action), or a budget's e-mails reach no one.
const TOTAL_SPEND_NOT_PAUSED =
  "Not enforced: usage is not paused when this budget is reached.";
const TOTAL_SPEND_NOT_RAISED =
  "Not enforced: no notice or warning is raised when this budget is reached.";
const NOTICES_NOT_SENT = "Email notices are not sent for this budget.";
// Said once for the whole section when the check gives no answer.
const ENFORCEMENT_NOT_CHECKED =
  "Couldn't check whether budgets are enforced. Reload the page.";
// What a budget that is turned off says under its threshold line.
const BUDGET_OFF = "This budget is off. Reaching it does nothing.";
// What a total-spend budget counts, said under Threshold in the budget dialog
// whatever the probe answers, and read out as the Threshold input's
// description. AI Gateway → Budgets is the gateway's own Budgets page, as the
// AI Gateway rail names it.
const TOTAL_SPEND_COUNTS =
  "Total spend is this billing period's usage charges so far, AI Credits usage included: the same figure as Usage charges (MTD) on the Usage summary page. Your platform fee and AI Gateway model spend paid with Future AGI credits are not part of it; limit AI Gateway model spend in AI Gateway → Budgets.";

const totalSpendNotEnforcedText = (action) =>
  action === "pause" ? TOTAL_SPEND_NOT_PAUSED : TOTAL_SPEND_NOT_RAISED;

// A budget that lists recipients of its own is e-mailed to them; only one
// that lists none relies on the package's own recipients.
const listsOwnRecipients = (budget) => budget.notify_emails?.length > 0;

// A budget sentence with a warning tone: a warning icon announced as
// "Warning", then the text.
function BudgetWarning({ children }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Box
        component="span"
        role="img"
        aria-label="Warning"
        sx={{ display: "inline-flex", flexShrink: 0, color: "warning.main" }}
      >
        <Iconify icon="solar:danger-triangle-linear" width={16} />
      </Box>
      <Typography variant="s1" color="text.primary">
        {children}
      </Typography>
    </Stack>
  );
}

BudgetWarning.propTypes = {
  children: PropTypes.node.isRequired,
};

export default function BudgetManager() {
  const { canEdit } = useGatewayAdmin();
  const reasonId = useId();
  const describedBy = canEdit ? undefined : reasonId;
  const thresholdErrorId = useId();
  const totalSpendCountsId = useId();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  // The Delete dialog keeps naming its budget while it fades out; the budget
  // is let go only once the dialog has gone (see its onExited).
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [newBudget, setNewBudget] = useState(EMPTY_BUDGET);

  const queryClient = useQueryClient();

  const {
    data: budgets,
    isLoading,
    isError,
    refetch,
    isRefetching,
  } = useQuery({
    queryKey: ["v2-budgets"],
    queryFn: () => axios.get(endpoints.settings.v2.budgets),
    select: (res) => res.data?.result?.budgets || [],
  });

  // The probe useDeploymentMode reads also says whether the installed billing
  // package acts on total-spend budgets and sends budget e-mails. A budget
  // sentence shows only on an explicit false. While it loads nothing is said.
  // If it fails, or its answer lacks either of the two, every budget reads as
  // before and the section says once that this couldn't be checked.
  const {
    data: billingPackage,
    isSuccess: probeSucceeded,
    isError: probeFailed,
  } = useQuery({
    queryKey: ["deployment-info"],
    queryFn: () => axios.get(endpoints.settings.v2.deploymentInfo),
    select: (res) => res?.data?.result,
    staleTime: Infinity,
  });
  const packageAnswered =
    typeof billingPackage?.total_spend_budgets_enforced === "boolean" &&
    typeof billingPackage?.budget_notices_delivered === "boolean";
  const enforcementNotChecked =
    (probeSucceeded || probeFailed) && !packageAnswered;
  const totalSpendNotEnforced =
    packageAnswered && billingPackage.total_spend_budgets_enforced === false;
  const noticesNotSent =
    packageAnswered && billingPackage.budget_notices_delivered === false;

  const createMutation = useMutation({
    mutationFn: (data) => {
      if (!canEdit) throw new Error("Organization Owner or Admin required");
      return axios.post(endpoints.settings.v2.budgets, data);
    },
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
    mutationFn: ({ id, data }) => {
      if (!canEdit) throw new Error("Organization Owner or Admin required");
      return axios.put(endpoints.settings.v2.budgetDetail(id), data);
    },
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
    mutationFn: (id) => {
      if (!canEdit) throw new Error("Organization Owner or Admin required");
      return axios.delete(endpoints.settings.v2.budgetDetail(id));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2-budgets"] });
      setDeleteOpen(false);
      enqueueSnackbar("Budget deleted", { variant: "success" });
    },
  });

  const handleOpenEdit = useCallback(
    (budget) => {
      if (!canEdit) return;
      setEditingId(budget.id);
      setNewBudget({
        name: budget.name,
        scope: budget.scope,
        threshold_value: String(budget.threshold_value),
        action: budget.action,
      });
      setDialogOpen(true);
    },
    [canEdit],
  );

  const handleSave = useCallback(() => {
    if (!canEdit) return;
    if (editingId) {
      updateMutation.mutate({ id: editingId, data: newBudget });
    } else {
      createMutation.mutate(newBudget);
    }
  }, [canEdit, editingId, newBudget, updateMutation, createMutation]);

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
  // The Threshold input is described by its error while it shows and, for
  // a total-spend budget, by the line that says what total spend counts.
  // Any other valid or empty Threshold has no description.
  const isTotalSpend = newBudget.scope === "total_spend";
  const thresholdDescribedBy =
    [thresholdIsInvalid && thresholdErrorId, isTotalSpend && totalSpendCountsId]
      .filter(Boolean)
      .join(" ") || undefined;

  if (isLoading) return <Skeleton variant="rounded" height={150} />;

  if (isError && !budgets) {
    return (
      <LoadFailedState
        title="Usage budgets couldn't be loaded"
        onRetry={refetch}
        retrying={isRefetching}
      />
    );
  }

  return (
    <Box>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={2}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          Usage budgets
        </Typography>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          {!canEdit && (
            <Typography id={reasonId} variant="caption" color="text.secondary">
              {BUDGET_EDIT_REASON}
            </Typography>
          )}
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="solar:add-linear" />}
            onClick={() => setDialogOpen(true)}
            disabled={!canEdit}
            aria-describedby={describedBy}
            sx={{ flexShrink: 0 }}
          >
            Add Budget
          </Button>
        </Stack>
      </Stack>

      {enforcementNotChecked && (
        <Box mb={1.5}>
          <BudgetWarning>{ENFORCEMENT_NOT_CHECKED}</BudgetWarning>
        </Box>
      )}

      {!budgets || budgets.length === 0 ? (
        <Section variant="panel" sx={{ p: 0 }}>
          <EmptyState
            icon="solar:shield-check-linear"
            title="No budgets yet"
            description="No budgets set. Add a budget to get notified or pause usage when thresholds are reached."
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => setDialogOpen(true)}
                disabled={!canEdit}
                aria-describedby={describedBy}
              >
                Add budget
              </Button>
            }
            sx={{ pt: 6, pb: 6 }}
          />
        </Section>
      ) : (
        <Stack spacing={1.5}>
          {budgets.map((budget) => {
            const actionConfig =
              ACTION_LABELS[budget.action] || ACTION_LABELS.notify;
            const scopeLabel = scopeOption(budget.scope).label;
            // A budget that is turned off does nothing when reached, so it
            // says so and none of the sentences below (they describe what
            // reaching it does). Its Triggered chip stays: that happened.
            const isOff = budget.is_active === false;
            const spendSentence =
              !isOff && budget.scope === "total_spend" && totalSpendNotEnforced
                ? totalSpendNotEnforcedText(budget.action)
                : null;
            // A budget that lists no recipients of its own says its e-mails
            // are not sent: notify, warn and pause budgets all e-mail when
            // reached. A notify budget only e-mails, so it says so instead of
            // claiming it was Triggered; a warn budget still raises its flag
            // and banner, and a pause budget still pauses usage, so both keep
            // the chip. A pause budget says it under its threshold line (and
            // under the line that total spend is not enforced, when shown).
            const notSent =
              !isOff && noticesNotSent && !listsOwnRecipients(budget);
            const notSentUnderLine = notSent && budget.action === "pause";
            const triggeredShown =
              Boolean(budget.last_triggered_period) &&
              !(notSent && budget.action === "notify");

            return (
              <Section
                variant="panel"
                key={budget.id}
                data-testid="usage-budget-row"
                sx={{ p: 2 }}
              >
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                  spacing={2}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    sx={{ minWidth: 0 }}
                  >
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
                        {`${scopeLabel}: ${thresholdText(
                          budget.scope,
                          budget.threshold_value,
                        )} → ${actionConfig.label}`}
                      </Typography>
                      {isOff && (
                        <Box mt={0.5}>
                          <BudgetWarning>{BUDGET_OFF}</BudgetWarning>
                        </Box>
                      )}
                      {spendSentence && (
                        <Box mt={0.5}>
                          <BudgetWarning>{spendSentence}</BudgetWarning>
                        </Box>
                      )}
                      {notSentUnderLine && (
                        <Box mt={0.5}>
                          <BudgetWarning>{NOTICES_NOT_SENT}</BudgetWarning>
                        </Box>
                      )}
                    </Box>
                  </Stack>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    justifyContent="flex-end"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <Chip
                      label={actionConfig.label}
                      size="small"
                      color={actionConfig.color}
                      variant="outlined"
                    />
                    {isOff && (
                      <Chip label="Inactive" size="small" variant="outlined" />
                    )}
                    {notSent && !notSentUnderLine && (
                      <BudgetWarning>{NOTICES_NOT_SENT}</BudgetWarning>
                    )}
                    {triggeredShown && (
                      <Chip
                        label={`Triggered ${periodText(
                          budget.last_triggered_period,
                        )}`}
                        size="small"
                        variant="outlined"
                      />
                    )}
                    <IconButton
                      size="small"
                      onClick={() => handleOpenEdit(budget)}
                      title="Edit budget"
                      disabled={!canEdit}
                      aria-describedby={describedBy}
                    >
                      <Iconify icon="solar:pen-linear" width={20} />
                    </IconButton>
                    <IconButton
                      size="small"
                      onClick={() => {
                        setDeleteTarget(budget);
                        setDeleteOpen(true);
                      }}
                      title="Delete budget"
                      disabled={!canEdit}
                      aria-describedby={describedBy}
                    >
                      <Iconify icon="solar:trash-bin-trash-linear" width={20} />
                    </IconButton>
                  </Stack>
                </Stack>
              </Section>
            );
          })}
        </Stack>
      )}

      {/* Create / Edit Budget Dialog */}
      <ClosableDialog
        open={canEdit && dialogOpen}
        onClose={handleCloseDialog}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 720 } }}
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
            <Box>
              <TextField
                label="Threshold"
                type="text"
                inputMode="decimal"
                fullWidth
                size="small"
                value={newBudget.threshold_value}
                onChange={(e) =>
                  setNewBudget({
                    ...newBudget,
                    threshold_value: e.target.value,
                  })
                }
                placeholder="e.g., 5000"
                InputProps={
                  scopeOption(newBudget.scope).unit
                    ? {
                        endAdornment: (
                          <InputAdornment position="end">
                            {scopeOption(newBudget.scope).unit}
                          </InputAdornment>
                        ),
                      }
                    : undefined
                }
                error={thresholdIsInvalid}
                // No blank line is reserved under a valid value, so Action
                // sits at the dialog's usual field spacing.
                helperText={
                  thresholdIsInvalid ? "Enter a positive number" : undefined
                }
                FormHelperTextProps={{ id: thresholdErrorId }}
                inputProps={{ "aria-describedby": thresholdDescribedBy }}
              />
              {/* A total-spend budget says what it counts, whatever the
                  probe answers, and below that, when the probe says so, that
                  it is not enforced. */}
              {isTotalSpend && (
                <Stack spacing={1} mt={1}>
                  <Typography
                    id={totalSpendCountsId}
                    variant="s1"
                    component="p"
                    color="text.secondary"
                  >
                    {TOTAL_SPEND_COUNTS}
                  </Typography>
                  {totalSpendNotEnforced && (
                    <BudgetWarning>
                      {totalSpendNotEnforcedText(newBudget.action)}
                    </BudgetWarning>
                  )}
                </Stack>
              )}
            </Box>
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
      </ClosableDialog>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={canEdit && deleteOpen}
        onClose={() => {
          if (deleteMutation.isPending) return;
          setDeleteOpen(false);
        }}
        // Once it has gone, however it closed (a role change included), the
        // dialog forgets its budget and stays shut.
        TransitionProps={{
          onExited: () => {
            setDeleteTarget(null);
            setDeleteOpen(false);
          },
        }}
        closeDisabled={deleteMutation.isPending}
        title="Delete Budget?"
        content={`Delete "${deleteTarget?.name ?? ""}"? This will remove the budget rule and clear any pause flags.`}
        action={
          <LoadingButton
            size="small"
            variant="contained"
            color="error"
            loading={deleteMutation.isPending}
            onClick={() => {
              if (canEdit && deleteOpen && deleteTarget)
                deleteMutation.mutate(deleteTarget.id);
            }}
          >
            Delete
          </LoadingButton>
        }
      />
    </Box>
  );
}
