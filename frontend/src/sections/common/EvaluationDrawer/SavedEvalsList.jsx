/* eslint-disable react/prop-types */
import React, { useState } from "react";
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
    icon: "mdi:code-braces",
    color: "#f59e0b",
    bg: "rgba(245,158,11,0.1)",
  },
  agent: {
    label: "Agent",
    icon: "mdi:robot-outline",
    color: "#16a34a",
    bg: "rgba(22,163,74,0.1)",
  },
  llm: {
    label: "LLM",
    icon: "mdi:brain",
    color: "#7c4dff",
    bg: "rgba(124,77,255,0.1)",
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
}) => {
  const [open, setOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState(null); // "run" | "delete" | null
  const isComposite = (item.template_type || item.templateType) === "composite";
  const t = isComposite
    ? {
        label: "Composite",
        icon: "mdi:layers-outline",
        color: "#6366f1",
        bg: "rgba(99,102,241,0.1)",
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
      <Box
        onClick={() => setOpen((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.25,
          py: 0.85,
          cursor: "pointer",
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

        {/* Name */}
        <Typography
          variant="body2"
          fontWeight={600}
          noWrap
          sx={{ fontSize: "13px", flex: 1, minWidth: 0 }}
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
            fontSize: "10px",
            fontWeight: 700,
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
                    rawStatus === "notstarted" ? "#f59e0b" : "primary.main",
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
                icon="mdi:check-circle"
                width={14}
                sx={{ color: "#16a34a", flexShrink: 0 }}
              />
            </Box>
          </Tooltip>
        )}
        {isError && (
          <Tooltip title="Error" arrow>
            <Box sx={{ display: "flex" }}>
              <Iconify
                icon="mdi:alert-circle"
                width={14}
                sx={{ color: "#dc2626", flexShrink: 0 }}
              />
            </Box>
          </Tooltip>
        )}
        {isStopped && (
          <Tooltip title="User stopped evaluation" arrow>
            <Box sx={{ display: "flex" }}>
              <Iconify
                icon="mdi:stop-circle"
                width={14}
                sx={{ color: "#f59e0b", flexShrink: 0 }}
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
                    icon="mdi:stop-circle-outline"
                    width={17}
                    sx={{ color: "error.main" }}
                  />
                </IconButton>
              </Tooltip>
            ) : (
              <Tooltip title={isCompleted ? "Re-run" : "Run"} arrow>
                <IconButton
                  size="small"
                  onClick={() => setConfirmAction("run")}
                  sx={{ p: 0.4 }}
                >
                  <Iconify
                    icon={
                      isCompleted ? "mdi:refresh" : "mdi:play-circle-outline"
                    }
                    width={17}
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
                  icon="mdi:pencil-outline"
                  width={15}
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
                  icon="mdi:trash-can-outline"
                  width={15}
                  sx={{ color: "text.disabled" }}
                />
              </IconButton>
            </span>
          </Tooltip>
        </Box>

        <Iconify
          icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={16}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </Box>

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
              sx={{ fontSize: "11px", color: "text.secondary" }}
            >
              <Box
                component="span"
                sx={{
                  fontFamily: "monospace",
                  fontWeight: 600,
                  color: "text.primary",
                  fontSize: "11px",
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
            sx={{ fontSize: "10px", color: "text.disabled", ml: "auto" }}
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
                ? "rgba(220,38,38,0.04)"
                : "rgba(124,77,255,0.04)",
          }}
        >
          <Typography
            variant="caption"
            sx={{ fontSize: "11.5px", flex: 1, color: "text.secondary" }}
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
              textTransform: "none",
              fontSize: "11px",
              minWidth: 0,
              px: 1,
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
              textTransform: "none",
              fontSize: "11px",
              minWidth: 0,
              px: 1.5,
              borderRadius: "4px",
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
            bgcolor: (th) =>
              th.palette.mode === "dark"
                ? "rgba(255,255,255,0.02)"
                : "rgba(0,0,0,0.012)",
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
                        icon="mdi:arrow-right-thin"
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
                    icon="mdi:layers-outline"
                    label={`${item.aggregation_function?.replace(/_/g, " ") || "weighted avg"}`}
                  />
                  {item.children_count != null && (
                    <CChip
                      icon="mdi:format-list-numbered"
                      label={`${item.children_count} children`}
                    />
                  )}
                </Box>
              </Sec>
            ) : (
              <Sec title="Model & Runtime">
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                  {item.model && <CChip icon="mdi:chip" label={item.model} />}
                  {rc.agent_mode && (
                    <CChip
                      icon="mdi:robot-outline"
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
                          ? "mdi:check-decagram"
                          : item.output_type === "percentage"
                            ? "mdi:percent"
                            : "mdi:format-list-bulleted"
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
                    <CChip icon="mdi:web" label="Internet" hl />
                  )}
                  {rc.summary && rc.summary !== "concise" && (
                    <CChip
                      icon="mdi:text-short"
                      label={`Summary: ${rc.summary}`}
                    />
                  )}
                  {rc.pass_threshold != null && rc.pass_threshold !== 0.5 && (
                    <CChip
                      icon="mdi:target"
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
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.4 }}>
                  {item.eval_template_tags.slice(0, 8).map((tag) => (
                    <Chip
                      key={tag}
                      label={tag}
                      size="small"
                      variant="outlined"
                      sx={{ height: 18, fontSize: "10px", borderRadius: "4px" }}
                    />
                  ))}
                  {item.eval_template_tags.length > 8 && (
                    <Typography
                      variant="caption"
                      color="text.disabled"
                      sx={{ fontSize: "10px", lineHeight: "18px" }}
                    >
                      +{item.eval_template_tags.length - 8}
                    </Typography>
                  )}
                </Box>
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
        fontSize: "9px",
        fontWeight: 700,
        textTransform: "uppercase",
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
  <Box
    sx={{
      px: 0.6,
      py: 0.1,
      borderRadius: "3px",
      fontSize: "11px",
      fontWeight: 500,
      lineHeight: "16px",
      ...(mono
        ? {
            fontFamily: "monospace",
            bgcolor: "action.selected",
            color: "text.primary",
          }
        : {}),
      ...(outlined
        ? {
            border: "1px solid",
            borderColor: "divider",
            color: "text.secondary",
          }
        : {}),
    }}
  >
    {label}
  </Box>
);

const CChip = ({ icon, label, hl }) => (
  <Chip
    size="small"
    icon={<Iconify icon={icon} width={12} />}
    label={label}
    sx={{
      height: 20,
      fontSize: "10px",
      fontWeight: 500,
      borderRadius: "4px",
      bgcolor: hl ? "rgba(33,150,243,0.08)" : "background.neutral",
      color: hl ? "info.main" : "text.secondary",
      "& .MuiChip-icon": {
        fontSize: 12,
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
        fontSize: "11px",
        color: "text.disabled",
        width: 55,
        flexShrink: 0,
      }}
    >
      {label}
    </Typography>
    <Typography sx={{ fontSize: "11px", color: "text.primary" }} noWrap>
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
}) => {
  const { setVisibleSection, setCurrentTab } = useEvaluationContext();
  const handleAddClick = () => {
    if (onAddClick) {
      onAddClick();
      return;
    }
    setCurrentTab("evals");
    setVisibleSection("config");
  };
  const [sel, setSel] = useState(new Set());
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
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="mdi:plus" width={14} />}
            onClick={handleAddClick}
            sx={{
              textTransform: "none",
              fontSize: "12px",
              fontWeight: 500,
              borderRadius: "6px",
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
                startIcon={<Iconify icon="mdi:trash-can-outline" width={14} />}
                onClick={() => setConfirmBulkDelete(true)}
                sx={{
                  textTransform: "none",
                  fontSize: "12px",
                  borderRadius: "6px",
                }}
              >
                Delete
              </Button>
            </>
          )}
          <Button
            size="small"
            variant="contained"
            startIcon={<Iconify icon="mdi:play-circle-outline" width={16} />}
            onClick={async () => {
              const toRun = hasSel ? selected : evals;
              await Promise.all(toRun.map((e) => onRunEvalClick?.(e)));
              onClose?.();
            }}
            sx={{
              textTransform: "none",
              fontSize: "12px",
              borderRadius: "6px",
            }}
          >
            {hasSel
              ? `Run Selected (${sel.size})`
              : `Run All (${evals.length})`}
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
            sx={{ paddingX: "24px" }}
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
};

export default SavedEvalsList;
