/* eslint-disable react/prop-types */
import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  IconButton,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
  ButtonBase,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useEvaluationContext } from "./context/EvaluationContext";
import { formatDistanceToNow } from "date-fns";
import { canonicalEntries, canonicalValues } from "src/utils/utils";

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

const EvalRow = ({
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
  disabledReason,
  confirmText,
  confirmRun = true,
}) => {
  const [open, setOpen] = useState(false);
  // Optional per-row gate: a non-empty reason turns that control off and is
  // its tooltip. Lists that don't pass one keep every control on.
  const reasonFor = (action) => disabledReason?.(item, action) || "";
  const selectReason = reasonFor("select");
  const runReason = reasonFor("run");
  const editReason = reasonFor("edit");
  const deleteReason = disableDelete
    ? disableDeleteReason || "Delete disabled"
    : reasonFor("delete");
  const [confirmAction, setConfirmAction] = useState(null); // "run" | "delete" | null
  const deleteLabel = confirmText?.deleteLabel || "Delete";
  const isComposite = (item.template_type || item.templateType) === "composite";
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
    (!isComposite && item.template_name && item.eval_template_tags); // heuristic fallback

  // Build mapping display pairs
  const mappingPairs = hasMappingNames
    ? canonicalEntries(mapping).slice(0, 3)
    : keys.map((k) => [k, null]);

  return (
    <Box
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
          <Tooltip title={selectReason} arrow>
            <span style={{ display: "flex" }}>
              <Checkbox
                size="small"
                checked={selected}
                disabled={Boolean(selectReason)}
                onClick={(e) => e.stopPropagation()}
                onChange={() => onToggle(item)}
                inputProps={{ "aria-label": `Select ${item.name}` }}
                sx={{ p: 0 }}
              />
            </span>
          </Tooltip>
        )}

        {/* Type icon (replaces status dot) */}
        <Iconify
          icon={t.icon}
          width={16}
          sx={{ color: t.color, flexShrink: 0 }}
        />

        {/* Name */}
        <Typography
          variant="body2"
          fontWeight={600}
          noWrap
          sx={{ fontSize: "12px", flex: 1, minWidth: 0 }}
        >
          {item.name}
        </Typography>

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
        {!hideStatus && isCompleted && (
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
        {!hideStatus && isError && (
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
        {!hideStatus && isStopped && (
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
          sx={{ display: "flex", gap: 0.25, ml: 0.25, flexShrink: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          {showRun &&
            !isProjectEvals &&
            (isRunning ? (
              <Tooltip title="Stop" arrow>
                <IconButton
                  size="small"
                  onClick={() => onStop?.(item)}
                  sx={{ p: 0.4 }}
                >
                  <Iconify
                    icon="solar:stop-circle-linear"
                    width={16}
                    sx={{ color: "error.main" }}
                  />
                </IconButton>
              </Tooltip>
            ) : (
              <Tooltip
                title={runReason || (isCompleted ? "Re-run" : "Run")}
                arrow
              >
                <span>
                  <IconButton
                    size="small"
                    aria-label={`Run ${item.name}`}
                    disabled={Boolean(runReason)}
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
                      sx={{
                        color: runReason ? "text.disabled" : "primary.main",
                      }}
                    />
                  </IconButton>
                </span>
              </Tooltip>
            ))}
          {!isProjectEvals && (
            <Tooltip title={editReason || "Edit mapping & config"} arrow>
              <span>
                <IconButton
                  size="small"
                  aria-label={`Edit ${item.name}`}
                  disabled={Boolean(editReason)}
                  onClick={() => onEdit?.(item)}
                  sx={{ p: 0.4 }}
                >
                  <Iconify
                    icon="solar:pen-linear"
                    width={16}
                    sx={{
                      color: editReason ? "text.disabled" : "text.secondary",
                    }}
                  />
                </IconButton>
              </span>
            </Tooltip>
          )}
          <Tooltip title={deleteReason || deleteLabel} arrow>
            <span>
              <IconButton
                size="small"
                aria-label={`${deleteLabel} ${item.name}`}
                onClick={() => setConfirmAction("delete")}
                disabled={Boolean(deleteReason)}
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
              ? (typeof confirmText?.delete === "function"
                  ? confirmText.delete(item)
                  : confirmText?.delete) ||
                "Delete this evaluation and its results?"
              : confirmText?.run ||
                (isCompleted
                  ? "Re-run? This will overwrite previous results."
                  : "Run this evaluation on all rows?")}
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
              ? deleteLabel
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
                  {item.output_type && (
                    <CChip
                      icon={
                        item.output_type === "pass_fail"
                          ? "solar:check-circle-bold"
                          : item.output_type === "percentage"
                            ? "mdi:percent"
                            : "solar:list-linear"
                      }
                      label={
                        item.output_type === "pass_fail"
                          ? "Pass/fail"
                          : item.output_type === "percentage"
                            ? "Scoring"
                            : "Choices"
                      }
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
                {item.evalGroup && <Row label="Group" value={item.evalGroup} />}
                {item.description && (
                  <Row label="About" value={item.description} />
                )}
              </Box>
            </Sec>

            {/* ── Tags ── */}
            {item.eval_template_tags?.length > 0 && (
              <Sec title="Tags">
                <Typography sx={{ fontSize: "12px", color: "text.primary" }}>
                  {item.eval_template_tags.slice(0, 8).join(" · ")}
                  {item.eval_template_tags.length > 8 && (
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary", ml: 0.75 }}
                    >
                      +{item.eval_template_tags.length - 8}
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
  autoSelectRequests,
  onAutoSelectApplied,
  disabledReason,
  onRunSelected,
  confirmText,
  confirmRun = true,
}) => {
  const { setVisibleSection, setCurrentTab } = useEvaluationContext();
  // "select" | "run" | "edit" | "delete" for a row; "add", "runAll" and
  // "bulkDelete" (item null) for the header and footer buttons. Empty means allowed.
  const reasonFor = (item, action) => disabledReason?.(item, action) || "";
  const pickable = evals.filter((e) => !reasonFor(e, "select"));
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
      pickable.length > 0 && pickable.every((e) => sel.has(e.id))
        ? new Set()
        : new Set(pickable.map((e) => e.id)),
    );
  // A row whose select is off can't stay ticked (it may have changed since).
  const selected = pickable.filter((e) => sel.has(e.id));
  const hasSel = selected.length > 0;
  const toRun = (hasSel ? selected : evals).filter((e) => !reasonFor(e, "run"));
  const runAllReason =
    reasonFor(null, "runAll") ||
    (disabledReason && toRun.length === 0 ? "Nothing here can run" : "");
  const bulkDeleteReason = selected
    .map((e) => reasonFor(e, "delete"))
    .find(Boolean);
  const addReason = reasonFor(null, "add");
  // A non-empty reason for "bulkDelete" hides the footer Delete altogether.
  const bulkDeleteHidden = Boolean(reasonFor(null, "bulkDelete"));

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
              checked={
                pickable.length > 0 && selected.length === pickable.length
              }
              indeterminate={hasSel && selected.length < pickable.length}
              disabled={pickable.length === 0}
              onChange={toggleAll}
              inputProps={{ "aria-label": `Evals (${evals.length})` }}
              sx={{ p: 0 }}
            />
          )}
          <Typography
            variant="body2"
            fontWeight={600}
            sx={{ fontSize: "12px", flex: 1 }}
          >
            {hasSel
              ? `${selected.length} of ${evals.length} selected`
              : `Evals (${evals.length})`}
          </Typography>
          <Tooltip title={addReason} arrow>
            <span>
              <Button
                variant="outlined"
                size="small"
                startIcon={<Iconify icon="solar:add-linear" width={14} />}
                onClick={handleAddClick}
                disabled={Boolean(addReason)}
                sx={{
                  flexShrink: 0,
                }}
              >
                Add
              </Button>
            </span>
          </Tooltip>
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
            selected={sel.has(e.id)}
            onToggle={toggle}
            onRun={onRunEvalClick}
            onStop={onStopEvalClick}
            onEdit={onEditEvalClick}
            onDelete={onDeleteEvalClick}
            showRun={showRun}
            hideStatus={hideStatus}
            isProjectEvals={isProjectEvals}
            allColumns={allColumns}
            disableDelete={disableDelete}
            disableDeleteReason={disableDeleteReason}
            disabledReason={disabledReason}
            confirmText={confirmText}
            confirmRun={confirmRun}
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
          {hasSel && !bulkDeleteHidden && (
            <>
              <Typography
                variant="caption"
                sx={{ fontSize: "12px", color: "text.secondary", flex: 1 }}
              >
                {selected.length} selected
              </Typography>
              <Tooltip title={bulkDeleteReason || ""} arrow>
                <span>
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    startIcon={
                      <Iconify icon="solar:trash-bin-trash-linear" width={14} />
                    }
                    disabled={Boolean(bulkDeleteReason)}
                    onClick={() => setConfirmBulkDelete(true)}
                  >
                    Delete
                  </Button>
                </span>
              </Tooltip>
            </>
          )}
          <Tooltip title={runAllReason} arrow>
            <span>
              <Button
                size="small"
                variant="contained"
                startIcon={
                  <Iconify icon="solar:play-circle-linear" width={16} />
                }
                disabled={Boolean(runAllReason)}
                onClick={async () => {
                  // A list that grades in one request (a run's re-grade)
                  // takes them together; otherwise each eval runs alone.
                  if (onRunSelected) {
                    onRunSelected(toRun);
                    return;
                  }
                  await Promise.all(toRun.map((e) => onRunEvalClick?.(e)));
                  onClose?.();
                }}
              >
                {hasSel
                  ? `Run Selected (${toRun.length})`
                  : `Run All (${toRun.length})`}
              </Button>
            </span>
          </Tooltip>
        </Box>
      )}

      <ConfirmDialog
        open={confirmBulkDelete}
        onClose={() => setConfirmBulkDelete(false)}
        title="Delete evaluations"
        content={`Delete ${selected.length} selected evaluation${
          selected.length === 1 ? "" : "s"
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
  autoSelectRequests: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      token: PropTypes.number.isRequired,
    }),
  ),
  onAutoSelectApplied: PropTypes.func,
  // (item, "select" | "run" | "edit" | "delete") or
  // (null, "add" | "runAll" | "bulkDelete"; a reason for "bulkDelete" hides
  // the footer Delete)
  // → a reason that turns that control off, shown as its tooltip; "" or
  // nothing keeps it on.
  disabledReason: PropTypes.func,
  // Runs the footer's evals in one call instead of one call per eval.
  onRunSelected: PropTypes.func,
  // false when the caller confirms a run itself, so it isn't asked twice.
  confirmRun: PropTypes.bool,
  confirmText: PropTypes.shape({
    run: PropTypes.string,
    // A sentence, or (item) => sentence to name the eval.
    delete: PropTypes.oneOfType([PropTypes.string, PropTypes.func]),
    // The delete action's name on the row button, tooltip and confirm.
    deleteLabel: PropTypes.string,
  }),
};

export default SavedEvalsList;
