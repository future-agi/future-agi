/* eslint-disable react/prop-types */
import React, { useEffect, useId, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  IconButton,
  LinearProgress,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  ButtonBase,
} from "@mui/material";
import { useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  useEditDatasetEvalConfig,
  useSetExperimentDefaults,
} from "src/api/develop/analysis";
import { getUserEvalListKey } from "./getEvalsList";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useEvaluationContext } from "./context/EvaluationContext";
import { formatDistanceToNow } from "date-fns";
import { canonicalEntries, canonicalValues } from "src/utils/utils";
import { isSurfaceShown } from "src/launch/launchSwitch";
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";

// ── Constants ──

const TYPE_CFG = {
  code: {
    label: "Code",
    icon: "solar:code-linear",
    color: "status.warning.fg",
    bg: "status.warning.bg",
  },
  agent: {
    label: "Agent",
    icon: "solar:bot-linear",
    color: "accent.pass",
    bg: "rgba(22,163,74,0.1)",
  },
  llm: {
    label: "LLM",
    icon: "solar:brain-linear",
    color: "accent.brand",
    bg: "rgba(120,87,252,0.1)",
  },
};

const getType = (t) => TYPE_CFG[t] || TYPE_CFG.agent;
const ago = (d) => {
  try {
    return formatDistanceToNow(new Date(d), { addSuffix: true });
  } catch {
    return "";
  }
};

const UUID_RE = /^[0-9a-f-]{36}$/i;

// Resolve a mapped value (stored as column `field` or `id`, often a UUID) to
// its human-readable display name by looking it up in `allColumns`.
const resolveColumnName = (value, allColumns) => {
  if (!value) return value;
  if (Array.isArray(allColumns) && allColumns.length) {
    const match = allColumns.find(
      (c) =>
        c &&
        typeof c === "object" &&
        (c.field === value || c.id === value || c.name === value),
    );
    if (match) {
      return (
        match.headerName || match.name || match.label || match.field || value
      );
    }
  }
  return UUID_RE.test(value) ? null : value;
};

// ── Row ──

const isScoreEval = (item) =>
  ["percentage", "score", "numeric"].includes(
    String(item.output_type ?? "").toLowerCase(),
  );

const versionNo = (v) => Number(String(v ?? "").replace(/^v/i, "")) || 0;

/**
 * L7c g7 (expdrawer:open): the output chip of an eval's output type: a
 * score eval (`percentage`, `score`, `numeric`) reads Scoring, never
 * Choices; an unknown type shows no chip.
 */
export function outputChipOf(type) {
  const t = String(type ?? "").toLowerCase();
  if (t === "pass_fail" || t === "pass/fail")
    return { label: "Pass/fail", icon: "mdi:check-decagram" };
  if (["percentage", "score", "numeric"].includes(t))
    return { label: "Scoring", icon: "mdi:percent" };
  if (t === "choices" || t === "deterministic")
    return { label: "Choices", icon: "mdi:format-list-bulleted" };
  return null;
}
/** L7c g7: the newer of two version labels ("v3"), or the one given. */
export const newestOf = (a, b) =>
  versionNo(b) > versionNo(a) ? b : a ?? b ?? null;

/**
 * `↑ Higher is better · Pass ≥ 0.70 · v3` (threshold only for score evals),
 * with an amber `v2 → v3 available` when a newer version exists. HO D11: an
 * eval added with a pinned version (`pinned_version_id`) reads `v3 · pinned`.
 */
export function evalDefinitionLine(item) {
  if (item.higher_is_better == null && !item.version) return null;
  const lower = item.higher_is_better === false;
  const parts = [lower ? "↓ Lower is better" : "↑ Higher is better"];
  if (isScoreEval(item) && item.pass_threshold != null)
    parts.push(
      `Pass ${lower ? "≤" : "≥"} ${Number(item.pass_threshold).toFixed(2)}`,
    );
  if (item.version) parts.push(item.version);
  if (item.pinned_version_id) parts.push("pinned");
  const newer =
    item.latest_version &&
    item.version &&
    versionNo(item.latest_version) > versionNo(item.version)
      ? `${item.version} → ${item.latest_version} available`
      : null;
  return { text: parts.join(" · "), newer };
}

const thresholdText = (v) => Number(v).toFixed(2);

/**
 * The pass threshold of a dataset eval. Saves through edit_and_run_user_eval
 * with run:false; runs, the Summary, the leaderboard and Choose winner read
 * it. Direction is not built: a run passes a score at or above the
 * threshold, and nothing stores or reads another rule. So the direction
 * control shows that rule, disabled, with the shared line.
 */
const ScoringEditor = ({ item, disabled, onSave, busy }) => {
  const score = isScoreEval(item);
  const directionLineId = useId();
  const [threshold, setThreshold] = useState(
    item.pass_threshold == null ? "" : String(item.pass_threshold),
  );
  const t = threshold.trim() === "" ? null : Number(threshold);
  const invalid = score && (t == null || !Number.isFinite(t) || t < 0 || t > 1);
  const changed = score && t !== (item.pass_threshold ?? null);
  return (
    <Box
      sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}
    >
      <ToggleButtonGroup
        exclusive
        size="small"
        value="higher"
        disabled
        sx={{
          "& .MuiToggleButton-root": {
            py: 0.25,
            px: 1,
            fontSize: "11px",
            textTransform: "none",
          },
        }}
      >
        <ToggleButton value="higher" aria-describedby={directionLineId}>
          ↑ Higher is better
        </ToggleButton>
        <ToggleButton value="lower" aria-describedby={directionLineId}>
          ↓ Lower is better
        </ToggleButton>
      </ToggleButtonGroup>
      <Box
        component="span"
        id={directionLineId}
        sx={{ display: "inline-flex" }}
      >
        <NotInReleaseChip />
      </Box>
      {score && (
        <TextField
          size="small"
          label="Pass threshold"
          value={threshold}
          disabled={disabled}
          onChange={(e) => setThreshold(e.target.value)}
          error={invalid}
          helperText={invalid ? "Between 0 and 1" : undefined}
          inputProps={{ inputMode: "decimal", "aria-label": "Pass threshold" }}
          sx={{ width: 120, "& input": { py: 0.6, fontSize: "12px" } }}
        />
      )}
      <Button
        size="small"
        variant="contained"
        disabled={disabled || !changed || invalid || busy}
        onClick={() => onSave(item, { passThreshold: t })}
        sx={{ fontSize: "11px", py: 0.25, minWidth: 0 }}
      >
        Save
      </Button>
    </Box>
  );
};

/**
 * The Save request's body besides the new threshold. It is the evaluation
 * as stored: get_evals_list returns the saved config itself, so its run
 * settings, mapping, params, notes and reason column go back unchanged and
 * the server only sets the threshold sent in pass_threshold. The version
 * it runs goes back too (null when it follows the default), so the server
 * saves no new version. model and name are left out: the server keeps the
 * stored ones, and a name may be longer than the request allows.
 */
const scoringSavePayload = (item) => {
  const stored =
    item.config &&
    typeof item.config === "object" &&
    !Array.isArray(item.config)
      ? item.config
      : {};
  return {
    config: {
      ...stored,
      // The server turns a missing setting on; every reader treats a missing
      // one as off, so off is what was stored.
      reason_column: Object.hasOwn(stored, "reason_column")
        ? stored.reason_column
        : false,
    },
    pinned_version_id: item.pinned_version_id ?? null,
    ...(typeof item.error_localizer === "boolean"
      ? { error_localizer: item.error_localizer }
      : {}),
    save_as_template: false,
  };
};

// The evaluation as the server now reports it (null when the list no longer
// has it). Its pass_threshold is the one runs apply.
const readSavedEval = (datasetId, evalId) =>
  axios
    .get(endpoints.develop.eval.getEvalsList(datasetId), {
      params: { eval_type: "user" },
    })
    .then(
      (r) =>
        (r?.data?.result?.evals ?? []).find(
          (e) => String(e?.id) === String(evalId),
        ) ?? null,
    );

const sameThreshold = (a, b) =>
  a != null && b != null && Math.abs(Number(a) - Number(b)) < 1e-9;

const EvalRow = ({
  defaultSwitch,
  scoring,
  item,
  selected,
  onToggle,
  onRun,
  onStop,
  onEdit,
  onDelete,
  showRun,
  hideStatus,
  isProjectEvals,
  allColumns,
  disableDelete,
  disableDeleteReason,
  confirmRun = true,
}) => {
  const [open, setOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState(null); // "run" | "delete" | null
  const isComposite = (item.template_type || item.templateType) === "composite";
  // The version / pass-rule line and the newer-version notice are the
  // dataset.evalVersionNotice surface. Where the build hides it, the row
  // neither shows them nor reads the template's versions for them.
  const noticeShown = isSurfaceShown("dataset.evalVersionNotice");
  // L7c g7 (ver:saved-list-notice): the template's newest version, read
  // now (GET …/versions/), so a version saved in the editor shows here as
  // `v2 → v3 available` without the list being written again.
  const templateId = item.template_id ?? item.eval_template_id ?? null;
  const { data: newest } = useQuery({
    // Under the template's detail key: a saved version invalidates it.
    queryKey: ["evals", "detail", templateId, "versions"],
    queryFn: () =>
      axios
        .get(endpoints.develop.eval.getEvalVersions(templateId))
        .then((r) => {
          const list = r.data?.result?.versions ?? [];
          const n = Math.max(
            0,
            ...list.map((v) => Number(v.version_number) || 0),
          );
          return n ? `v${n}` : null;
        }),
    enabled: Boolean(noticeShown && templateId && item.version && !isComposite),
    staleTime: 10_000,
  });
  const { data: templateDetail } = useQuery({
    queryKey: ["evals", "detail", templateId],
    queryFn: () =>
      axios
        .get(endpoints.develop.eval.getEvalDetail(templateId))
        .then((r) => r.data?.result ?? null),
    enabled: Boolean(open && templateId),
    staleTime: 30_000,
  });
  const tags = Array.isArray(templateDetail?.tags)
    ? templateDetail.tags
    : item.eval_template_tags ?? [];
  const definition = noticeShown
    ? evalDefinitionLine({
        ...item,
        latest_version: newestOf(item.latest_version, newest),
      })
    : null;
  const t = isComposite
    ? {
        label: "Composite",
        icon: "solar:layers-linear",
        color: "accent.violet",
        bg: "rgba(139,92,246,0.1)",
      }
    : getType(item.evalType || item.eval_type || "agent");
  const rawStatus = (item.status || "").toLowerCase();
  // NotStarted means "queued — worker will pick it up" so treat as running
  const isRunning = rawStatus === "running" || rawStatus === "notstarted";
  const isCompleted = rawStatus === "completed";
  const isError = rawStatus === "error";
  // CANCELLED is set by StopUserEvalView — render it as a stopped state so
  // the row shows "Stopped" instead of no indicator.
  const isStopped = rawStatus === "cancelled";
  const mapping = item.mapping || {};
  const keys = Array.isArray(item.eval_required_keys)
    ? item.eval_required_keys
    : [];
  // canonicalValues / canonicalEntries strip the camelCase aliases the
  // axios interceptor adds, so each mapping pair renders once.
  const hasMappingNames = canonicalValues(mapping).some(
    (v) => v && resolveColumnName(v, allColumns),
  );
  const rc = item.run_config || {};
  const isSystem =
    (item.owner || item.eval_owner) === "system" ||
    ((item.owner ?? item.eval_owner) == null &&
      !isComposite &&
      item.template_name &&
      item.eval_template_tags); // heuristic fallback

  // Build mapping display pairs
  const mappingPairs = hasMappingNames
    ? canonicalEntries(mapping).slice(0, 3)
    : keys.map((k) => [k, null]);

  return (
    <Box
      data-testid="saved-eval-row"
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: open ? "primary.main" : "divider",
        transition: "all 0.15s",
        "&:hover": { borderColor: open ? "primary.main" : "text.disabled" },
      }}
    >
      {/* ── Main clickable row ── */}
      <ButtonBase
        component="div"
        onClick={() => setOpen((p) => !p)}
        sx={{
          display: "flex",
          justifyContent: "flex-start",
          textAlign: "left",
          alignItems: "center",
          gap: 0.75,
          px: 1.25,
          py: 0.85,
        }}
      >
        {showRun && !isProjectEvals && (
          <Checkbox
            size="small"
            checked={selected}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onToggle(item)}
            sx={{ p: 0 }}
          />
        )}

        {/* Type icon (replaces status dot) */}
        <Iconify
          icon={t.icon}
          width={16}
          sx={{ color: t.color, flexShrink: 0 }}
        />

        {/* Name + definition line */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            variant="body2"
            fontWeight={600}
            noWrap
            sx={{ fontSize: "13px" }}
          >
            {item.name}
          </Typography>
          {definition && (
            <Typography
              data-testid="saved-eval-definition"
              noWrap
              sx={{
                fontSize: "11px",
                color: "text.secondary",
                lineHeight: 1.4,
              }}
            >
              {definition.text}
            </Typography>
          )}
          {/* L7c g7 (ver:saved-list-notice): the newer version on its own
              line, so a narrow drawer never cuts it off. */}
          {definition?.newer && (
            <Typography
              data-testid="saved-eval-newer-version"
              sx={{
                fontSize: "11px",
                lineHeight: 1.4,
                color: "warning.dark",
                fontWeight: 600,
              }}
            >
              {definition.newer}
            </Typography>
          )}
        </Box>

        {/* Type badge */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.3,
            px: 0.6,
            py: 0.15,
            borderRadius: "4px",
            fontSize: "12px",
            fontWeight: 600,
            color: t.color,
            bgcolor: t.bg,
            flexShrink: 0,
          }}
        >
          {t.label}
        </Box>

        {/* Status indicator */}
        {!hideStatus && isRunning && (
          <Tooltip
            title={rawStatus === "notstarted" ? "Queued" : "Running"}
            arrow
          >
            <Box sx={{ display: "flex" }}>
              <Iconify
                icon="svg-spinners:ring-resize"
                width={14}
                sx={{
                  color:
                    rawStatus === "notstarted"
                      ? "status.warning.fg"
                      : "primary.main",
                  flexShrink: 0,
                }}
              />
            </Box>
          </Tooltip>
        )}
        {isCompleted && (
          <Tooltip title="Completed" arrow>
            <Box sx={{ display: "flex" }}>
              <Iconify
                icon="solar:check-circle-bold"
                width={14}
                sx={{ color: "accent.pass", flexShrink: 0 }}
              />
            </Box>
          </Tooltip>
        )}
        {isError && (
          <Tooltip title="Error" arrow>
            <Box sx={{ display: "flex" }}>
              <Iconify
                icon="solar:danger-circle-linear"
                width={14}
                sx={{ color: "accent.fail", flexShrink: 0 }}
              />
            </Box>
          </Tooltip>
        )}
        {isStopped && (
          <Tooltip title="User stopped evaluation" arrow>
            <Box sx={{ display: "flex" }}>
              <Iconify
                icon="solar:stop-circle-linear"
                width={14}
                sx={{ color: "status.warning.fg", flexShrink: 0 }}
              />
            </Box>
          </Tooltip>
        )}

        {/* Actions */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.25,
            ml: 0.25,
            flexShrink: 0,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {defaultSwitch && (
            <Tooltip
              arrow
              title={
                defaultSwitch.disabled
                  ? "You need edit access to this dataset"
                  : defaultSwitch.checked
                    ? "Preselected when you create an experiment on this dataset"
                    : "Not preselected in new experiments"
              }
            >
              <Box
                component="label"
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.25,
                  mr: 0.5,
                  cursor: defaultSwitch.disabled ? "default" : "pointer",
                }}
              >
                <Switch
                  size="small"
                  checked={defaultSwitch.checked}
                  disabled={defaultSwitch.disabled || defaultSwitch.busy}
                  onChange={(e) => defaultSwitch.onChange(e.target.checked)}
                  inputProps={{
                    "aria-label": `Use ${item.name} in new experiments`,
                  }}
                />
                <Typography
                  sx={{
                    fontSize: "11px",
                    color: "text.secondary",
                    whiteSpace: "nowrap",
                  }}
                >
                  Use in new experiments
                </Typography>
              </Box>
            </Tooltip>
          )}
          {showRun &&
            !isProjectEvals &&
            (isRunning ? (
              // SVP-4.4: the red icon says what it does.
              <Tooltip
                title={`${item.name} is still scoring rows. Stop it; rows already scored keep their scores.`}
                arrow
              >
                <Button
                  size="small"
                  color="error"
                  variant="text"
                  onClick={() => onStop?.(item)}
                  startIcon={
                    <Iconify icon="solar:stop-circle-linear" width={15} />
                  }
                  aria-label={`Stop scoring with ${item.name}`}
                  sx={{
                    minWidth: 0,
                    px: 0.75,
                    py: 0.25,
                    fontSize: "11px",
                    textTransform: "none",
                    "& .MuiButton-startIcon": { mr: 0.25 },
                  }}
                >
                  Stop
                </Button>
              </Tooltip>
            ) : (
              <Tooltip title={isCompleted ? "Re-run" : "Run"} arrow>
                <IconButton
                  size="small"
                  // W1-2: an experiment's Run opens the re-score confirm
                  // (cost and new runs) instead of the inline one.
                  onClick={() =>
                    confirmRun ? setConfirmAction("run") : onRun?.(item)
                  }
                  sx={{ p: 0.4 }}
                >
                  <Iconify
                    icon={
                      isCompleted
                        ? "solar:refresh-linear"
                        : "solar:play-circle-linear"
                    }
                    width={16}
                    sx={{ color: "primary.main" }}
                  />
                </IconButton>
              </Tooltip>
            ))}
          {!isProjectEvals && (
            <Tooltip title="Edit mapping & config" arrow>
              <IconButton
                size="small"
                onClick={() => onEdit?.(item)}
                sx={{ p: 0.4 }}
              >
                <Iconify
                  icon="solar:pen-linear"
                  width={16}
                  sx={{ color: "text.secondary" }}
                />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip
            title={
              disableDelete
                ? disableDeleteReason || "Delete disabled"
                : "Delete"
            }
            arrow
          >
            <span>
              <IconButton
                size="small"
                onClick={() => setConfirmAction("delete")}
                disabled={disableDelete}
                sx={{ p: 0.4, "&:hover": { color: "error.main" } }}
              >
                <Iconify
                  icon="solar:trash-bin-trash-linear"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </IconButton>
            </span>
          </Tooltip>
        </Box>

        <Iconify
          icon={
            open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={16}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </ButtonBase>

      {/* ── Subtitle: mapping + time ── */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.25,
          pb: 0.75,
          mt: -0.25,
          flexWrap: "wrap",
        }}
      >
        {mappingPairs.map(([variable, column]) => {
          // Show "variable → column" when mapped to a different name, just "variable" when same or unmapped
          const col = column ? resolveColumnName(column, allColumns) : null;
          const differs = col && col.toLowerCase() !== variable.toLowerCase();
          return (
            <Typography
              key={variable}
              variant="caption"
              sx={{ fontSize: "12px", color: "text.secondary" }}
            >
              <Box
                component="span"
                sx={{
                  fontFamily: "monospace",
                  fontWeight: 600,
                  color: "text.primary",
                  fontSize: "12px",
                }}
              >
                {variable}
              </Box>
              {differs && (
                <>
                  <Box
                    component="span"
                    sx={{ mx: 0.4, color: "text.disabled" }}
                  >
                    →
                  </Box>
                  <Box component="span" sx={{ fontWeight: 500 }}>
                    {col}
                  </Box>
                </>
              )}
            </Typography>
          );
        })}
        {item.updated_at && (
          <Typography
            variant="caption"
            sx={{ fontSize: "12px", color: "text.disabled", ml: "auto" }}
          >
            {ago(item.updated_at)}
          </Typography>
        )}
      </Box>

      {/* ── Inline confirmation ── */}
      {confirmAction && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 1.25,
            py: 0.6,
            borderTop: "1px solid",
            borderColor:
              confirmAction === "delete" ? "error.main" : "primary.main",
            bgcolor:
              confirmAction === "delete"
                ? "status.danger.bg"
                : "status.discovery.bg",
          }}
        >
          <Typography
            variant="caption"
            sx={{ fontSize: "12px", flex: 1, color: "text.secondary" }}
          >
            {confirmAction === "delete"
              ? "Delete this evaluation and its results?"
              : isCompleted
                ? "Re-run? This will overwrite previous results."
                : "Run this evaluation on all rows?"}
          </Typography>
          <Button
            size="small"
            variant="text"
            onClick={() => setConfirmAction(null)}
            sx={{
              minWidth: 0,
              color: "text.secondary",
            }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color={confirmAction === "delete" ? "error" : "primary"}
            onClick={() => {
              if (confirmAction === "delete")
                onDelete?.(isProjectEvals ? undefined : item);
              else onRun?.(item);
              setConfirmAction(null);
            }}
            sx={{
              minWidth: 0,
            }}
          >
            {confirmAction === "delete"
              ? "Delete"
              : isCompleted
                ? "Re-run"
                : "Run"}
          </Button>
        </Box>
      )}

      {/* Running bar */}
      {!hideStatus && isRunning && (
        <LinearProgress variant="indeterminate" sx={{ height: 2 }} />
      )}

      {/* ── Expanded ── */}
      <Collapse in={open}>
        <Box
          sx={{
            px: 1.25,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          <Stack spacing={1.25}>
            {scoring && (
              <Sec title="Scoring">
                <ScoringEditor
                  item={item}
                  disabled={scoring.disabled}
                  busy={scoring.busy}
                  onSave={scoring.onSave}
                />
              </Sec>
            )}
            {/* ── Mapping (full) ── */}
            {canonicalEntries(mapping).length > 0 && (
              <Sec title="Variable Mapping">
                {canonicalEntries(mapping).map(([v, c]) => {
                  const resolved = resolveColumnName(c, allColumns);
                  return (
                    <Box
                      key={v}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.5,
                        py: 0.2,
                      }}
                    >
                      <Tag label={v} mono />
                      <Iconify
                        icon="solar:arrow-right-linear"
                        width={14}
                        sx={{ color: "text.disabled" }}
                      />
                      <Tag label={resolved || "—"} outlined />
                    </Box>
                  );
                })}
              </Sec>
            )}

            {/* ── Model & Mode (hidden for composites) ── */}
            {isComposite ? (
              <Sec title="Composite">
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                  <CChip
                    icon="solar:layers-linear"
                    label={`${item.aggregation_function?.replace(/_/g, " ") || "weighted avg"}`}
                  />
                  {item.children_count != null && (
                    <CChip
                      icon="solar:list-ordered-linear"
                      label={`${item.children_count} children`}
                    />
                  )}
                </Box>
              </Sec>
            ) : (
              <Sec title="Model & Runtime">
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                  {item.model && (
                    <CChip icon="solar:cpu-linear" label={item.model} />
                  )}
                  {rc.agent_mode && (
                    <CChip
                      icon="solar:bot-linear"
                      label={
                        rc.agent_mode === "agent"
                          ? "Agent"
                          : rc.agent_mode === "quick"
                            ? "Quick"
                            : "Auto"
                      }
                    />
                  )}
                  {outputChipOf(item.output_type) && (
                    <CChip
                      icon={outputChipOf(item.output_type).icon}
                      label={outputChipOf(item.output_type).label}
                    />
                  )}
                </Box>
              </Sec>
            )}

            {/* ── Advanced config (+ button stuff) ── */}
            {(rc.check_internet ||
              (rc.summary && rc.summary !== "concise") ||
              (rc.pass_threshold != null && rc.pass_threshold !== 0.5)) && (
              <Sec title="Advanced">
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                  {rc.check_internet && (
                    <CChip icon="solar:global-linear" label="Internet" hl />
                  )}
                  {rc.summary && rc.summary !== "concise" && (
                    <CChip
                      icon="solar:notes-linear"
                      label={`Summary: ${rc.summary}`}
                    />
                  )}
                  {rc.pass_threshold != null && rc.pass_threshold !== 0.5 && (
                    <CChip
                      icon="solar:target-linear"
                      label={`Threshold: ${rc.pass_threshold}`}
                    />
                  )}
                </Box>
              </Sec>
            )}

            {/* ── Meta ── */}
            <Sec title="Details">
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.3 }}>
                <Row
                  label="Template"
                  value={
                    item.template_name || item.eval_template_name || item.name
                  }
                />
                <Row label="Owner" value={isSystem ? "System" : "User"} />
                {(item.evalGroup ?? item.eval_group) && (
                  <Row
                    label="Group"
                    value={item.evalGroup ?? item.eval_group}
                  />
                )}
                {item.description && (
                  <Row label="About" value={item.description} />
                )}
              </Box>
            </Sec>

            {/* ── Tags ── (L7c g7: the template's own, read when open) */}
            {tags?.length > 0 && (
              <Sec title="Tags">
                <Typography sx={{ fontSize: "12px", color: "text.primary" }}>
                  {tags.slice(0, 8).join(" · ")}
                  {tags.length > 8 && (
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary", ml: 0.75 }}
                    >
                      +{tags.length - 8}
                    </Box>
                  )}
                </Typography>
              </Sec>
            )}
          </Stack>
        </Box>
      </Collapse>
    </Box>
  );
};

// ── Micro components ──

const Sec = ({ title, children }) => (
  <Box>
    <Typography
      sx={{
        fontSize: "12px",
        fontWeight: 600,
        textTransform: "none",
        letterSpacing: "0.6px",
        color: "text.disabled",
        mb: 0.3,
      }}
    >
      {title}
    </Typography>
    {children}
  </Box>
);

const Tag = ({ label, mono, outlined }) => (
  <Chip
    size="small"
    variant={outlined ? "outlined" : "filled"}
    label={label}
    sx={mono ? { fontFamily: "monospace" } : undefined}
  />
);

const CChip = ({ icon, label, hl }) => (
  <Chip
    size="small"
    icon={<Iconify icon={icon} width={12} />}
    label={label}
    sx={{
      bgcolor: hl ? "status.info.bg" : "background.neutral",
      color: hl ? "status.info.fg" : "text.secondary",
      "& .MuiChip-icon": {
        ml: 0.25,
        color: hl ? "info.main" : "text.secondary",
      },
      "& .MuiChip-label": { px: 0.5 },
    }}
  />
);

const Row = ({ label, value }) => (
  <Box sx={{ display: "flex", gap: 0.75 }}>
    <Typography
      sx={{
        fontSize: "12px",
        color: "text.disabled",
        width: 55,
        flexShrink: 0,
      }}
    >
      {label}
    </Typography>
    <Typography sx={{ fontSize: "12px", color: "text.primary" }} noWrap>
      {value}
    </Typography>
  </Box>
);

// ── Main ──

const SavedEvalsList = ({
  evals,
  onDeleteEvalClick,
  onRunEvalClick,
  onStopEvalClick,
  onEditEvalClick,
  onAddClick,
  isProjectEvals = false,
  showRun = true,
  hideStatus = false,
  hideHeader = false,
  allColumns,
  onClose,
  disableDelete = false,
  disableDeleteReason,
  onRunEvalsClick,
  headerExtra = null,
  autoSelectRequests,
  onAutoSelectApplied,
}) => {
  const { setVisibleSection, setCurrentTab } = useEvaluationContext();
  // `Use in new experiments` (§3.15): dataset evals only (the dataset route).
  const { dataset: datasetId } = useParams();
  const { role } = useAuthContext();
  const canEdit = Boolean(
    RolePermission.DATASETS?.[PERMISSIONS.UPDATE]?.[role] ?? true,
  );
  const queryClient = useQueryClient();
  const [defaultOverrides, setDefaultOverrides] = useState({});
  const setDefaults = useSetExperimentDefaults(datasetId);
  const isDefault = (e) => defaultOverrides[e.id] ?? e.is_experiment_default;
  const toggleDefault = (item, checked) => {
    const ids = evals
      .filter((e) => (e.id === item.id ? checked : isDefault(e)))
      .map((e) => e.id);
    setDefaultOverrides((p) => ({ ...p, [item.id]: checked }));
    setDefaults.mutate(ids, {
      onSuccess: () => {
        enqueueSnackbar(
          checked
            ? `${item.name} will be preselected in new experiments`
            : `${item.name} won't be preselected in new experiments`,
          { variant: "success" },
        );
        queryClient.invalidateQueries({
          queryKey: getUserEvalListKey("dataset", datasetId),
        });
      },
      onError: () => {
        setDefaultOverrides((p) => ({ ...p, [item.id]: !checked }));
        enqueueSnackbar("Couldn't update the experiment defaults", {
          variant: "error",
        });
      },
    });
  };
  const editScoring = useEditDatasetEvalConfig(datasetId);
  const [confirmingScoring, setConfirmingScoring] = useState(false);
  // Saves the threshold, then reads the evaluation back: the success line
  // shows only when the server reports the threshold that was typed and the
  // version the row showed.
  const saveScoring = async (item, { passThreshold }) => {
    try {
      await editScoring.mutateAsync({
        evalId: item.id,
        // No direction is sent: nothing stores or applies one.
        higherIsBetter: undefined,
        passThreshold,
        extra: scoringSavePayload(item),
      });
    } catch (err) {
      enqueueSnackbar(
        err?.result || err?.message || "Couldn't save the pass threshold",
        { variant: "error" },
      );
      return;
    }
    setConfirmingScoring(true);
    try {
      const saved = await readSavedEval(datasetId, item.id);
      if (!saved) {
        enqueueSnackbar(
          `${item.name}: couldn't confirm the pass threshold was saved. Reopen the evaluations to check.`,
          { variant: "warning" },
        );
      } else if (!sameThreshold(saved.pass_threshold, passThreshold)) {
        enqueueSnackbar(
          saved.pass_threshold == null
            ? `${item.name}: the pass threshold wasn't saved.`
            : `${item.name}: the pass threshold wasn't saved. Runs still use Pass ≥ ${thresholdText(saved.pass_threshold)}.`,
          { variant: "error" },
        );
      } else if (
        (saved.pinned_version_id ?? null) !== (item.pinned_version_id ?? null)
      ) {
        enqueueSnackbar(
          `${item.name}: the pass threshold is saved, but the version it runs changed. Check the evaluation's version.`,
          { variant: "warning" },
        );
      } else {
        enqueueSnackbar(
          `${item.name}: pass threshold saved (Pass ≥ ${thresholdText(passThreshold)})`,
          { variant: "success" },
        );
      }
    } catch {
      enqueueSnackbar(
        `${item.name}: couldn't confirm the pass threshold was saved. Reopen the evaluations to check.`,
        { variant: "warning" },
      );
    } finally {
      setConfirmingScoring(false);
      queryClient.invalidateQueries({
        queryKey: getUserEvalListKey("dataset", datasetId),
      });
      queryClient.invalidateQueries({
        queryKey: ["evals-summary", datasetId],
      });
    }
  };
  // The Scoring editor saves the pass threshold and keeps the rest of the
  // stored evaluation (scoringSavePayload); its direction control is
  // disabled. "Use in new experiments" shows wherever the backend reports
  // is_experiment_default (it stores it).
  const scoringFor = (e) =>
    isSurfaceShown("dataset.evalScoring") &&
    datasetId &&
    !isProjectEvals &&
    typeof e.is_experiment_default === "boolean"
      ? {
          disabled: !canEdit,
          busy: editScoring.isPending || confirmingScoring,
          onSave: saveScoring,
        }
      : null;
  // L5 merge note 1c: the switch only preselects evals in New experiment,
  // so it shows only where L6's dataset.experimentDefaults surface does.
  const defaultSwitchFor = (e) =>
    isSurfaceShown("dataset.experimentDefaults") &&
    datasetId &&
    !isProjectEvals &&
    typeof e.is_experiment_default === "boolean"
      ? {
          checked: Boolean(isDefault(e)),
          disabled: !canEdit,
          busy: setDefaults.isPending,
          onChange: (checked) => toggleDefault(e, checked),
        }
      : null;
  const handleAddClick = () => {
    if (onAddClick) {
      onAddClick();
      return;
    }
    setCurrentTab("evals");
    setVisibleSection("config");
  };
  const [sel, setSel] = useState(new Set());
  useEffect(() => {
    if (!autoSelectRequests?.length) return;
    // Rows can arrive out of order. Acknowledge only the saves whose rows are
    // present, leaving every other save pending until a later refresh.
    const rowsByName = new Map(
      evals.map((evalItem) => [evalItem.name, evalItem]),
    );
    const applied = autoSelectRequests.filter(({ name }) =>
      rowsByName.has(name),
    );
    if (!applied.length) return;
    setSel((prev) => {
      const next = new Set(prev);
      applied.forEach(({ name }) => next.add(rowsByName.get(name).id));
      return next;
    });
    onAutoSelectApplied(applied.map(({ token }) => token));
  }, [evals, autoSelectRequests, onAutoSelectApplied]);
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const toggle = (item) =>
    setSel((p) => {
      const n = new Set(p);
      n.has(item.id) ? n.delete(item.id) : n.add(item.id);
      return n;
    });
  const toggleAll = () =>
    setSel(
      sel.size === evals.length ? new Set() : new Set(evals.map((e) => e.id)),
    );
  const selected = evals.filter((e) => sel.has(e.id));
  const hasSel = sel.size > 0;

  const handleBulkDelete = async () => {
    try {
      await Promise.all(selected.map((e) => onDeleteEvalClick?.(e)));
    } finally {
      setConfirmBulkDelete(false);
      setSel(new Set());
      onClose?.();
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        width: "100%",
      }}
    >
      {/* Header — checkbox aligns with row checkboxes via same px */}
      {!hideHeader && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            mb: 1,
            flexShrink: 0,
            gap: 0.75,
          }}
        >
          {showRun && !isProjectEvals && evals.length > 0 && (
            <Checkbox
              size="small"
              checked={sel.size === evals.length && evals.length > 0}
              indeterminate={hasSel && sel.size < evals.length}
              onChange={toggleAll}
              sx={{ p: 0 }}
            />
          )}
          <Typography
            variant="body2"
            fontWeight={600}
            sx={{ fontSize: "13px", flex: 1 }}
          >
            {hasSel
              ? `${sel.size} of ${evals.length} selected`
              : `Evals (${evals.length})`}
          </Typography>
          {headerExtra}
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="solar:add-linear" width={14} />}
            onClick={handleAddClick}
            sx={{
              flexShrink: 0,
            }}
          >
            Add
          </Button>
        </Box>
      )}

      {/* List */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 0.75,
        }}
      >
        {evals.map((e) => (
          <EvalRow
            key={e.id}
            item={e}
            defaultSwitch={defaultSwitchFor(e)}
            scoring={scoringFor(e)}
            selected={sel.has(e.id)}
            onToggle={toggle}
            onRun={
              onRunEvalsClick
                ? (item) => onRunEvalsClick([item])
                : onRunEvalClick
            }
            confirmRun={!onRunEvalsClick}
            onStop={onStopEvalClick}
            onEdit={onEditEvalClick}
            onDelete={onDeleteEvalClick}
            showRun={showRun}
            hideStatus={hideStatus}
            isProjectEvals={isProjectEvals}
            allColumns={allColumns}
            disableDelete={disableDelete}
            disableDeleteReason={disableDeleteReason}
          />
        ))}
      </Box>

      {/* ── Fixed footer ── */}
      {showRun && !isProjectEvals && evals.length > 0 && (
        <Box
          sx={{
            flexShrink: 0,
            pt: 1.5,
            pb: 0.5,
            borderTop: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            gap: 1,
            px: 1.25,
          }}
        >
          {hasSel && (
            <>
              <Typography
                variant="caption"
                sx={{ fontSize: "12px", color: "text.secondary", flex: 1 }}
              >
                {sel.size} selected
              </Typography>
              <Button
                size="small"
                variant="outlined"
                color="error"
                startIcon={
                  <Iconify icon="solar:trash-bin-trash-linear" width={14} />
                }
                onClick={() => setConfirmBulkDelete(true)}
              >
                Delete
              </Button>
            </>
          )}
          <Button
            size="small"
            variant="contained"
            startIcon={<Iconify icon="solar:play-circle-linear" width={16} />}
            onClick={async () => {
              const toRun = hasSel ? selected : evals;
              // W1-2: a door that runs several evals in one request (an
              // experiment's re-score) confirms first; Cancel keeps the list.
              if (onRunEvalsClick) {
                const out = await onRunEvalsClick(toRun);
                if (out?.cancelled) return;
              } else {
                await Promise.all(toRun.map((e) => onRunEvalClick?.(e)));
              }
              onClose?.();
            }}
          >
            {hasSel
              ? `Run selected (${sel.size})`
              : `Run all (${evals.length})`}
          </Button>
        </Box>
      )}

      <ConfirmDialog
        open={confirmBulkDelete}
        onClose={() => setConfirmBulkDelete(false)}
        title="Delete evaluations"
        content={`Delete ${sel.size} selected evaluation${
          sel.size === 1 ? "" : "s"
        } and their results? This action cannot be undone.`}
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleBulkDelete}
          >
            Delete
          </Button>
        }
      />
    </Box>
  );
};

SavedEvalsList.propTypes = {
  evals: PropTypes.array,
  onDeleteEvalClick: PropTypes.func,
  onRunEvalClick: PropTypes.func,
  onStopEvalClick: PropTypes.func,
  onEditEvalClick: PropTypes.func,
  onAddClick: PropTypes.func,
  isProjectEvals: PropTypes.bool,
  showRun: PropTypes.bool,
  hideStatus: PropTypes.bool,
  hideHeader: PropTypes.bool,
  allColumns: PropTypes.array,
  onClose: PropTypes.func,
  disableDelete: PropTypes.bool,
  disableDeleteReason: PropTypes.string,
  onRunEvalsClick: PropTypes.func,
  headerExtra: PropTypes.node,
  autoSelectRequests: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      token: PropTypes.number.isRequired,
    }),
  ),
  onAutoSelectApplied: PropTypes.func,
};

export default SavedEvalsList;
