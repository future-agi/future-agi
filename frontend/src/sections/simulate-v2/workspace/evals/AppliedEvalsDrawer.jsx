import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tooltip,
  Checkbox,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { useAppliedEvals } from "./useAppliedEvals";
import AddEvalsDrawer from "./AddEvalsDrawer";
import EditEvalDrawer from "./EditEvalDrawer";
import { canEditEval } from "./canEditEval";
import { MAPPING_NOTE, mappingWithout } from "./evalMapping";
import { clickable, focusRing } from "../../components/clickable";

/**
 * All Evaluations — mirrors the prod dataset drawer
 * (`sections/common/EvaluationDrawer` + `SavedEvalsList`) visually but
 * powered by the v2 SimStore. The prod version is coupled to backend
 * endpoints tied to a real dataset/experiment id; v2 envs have no such
 * id, so this is the visual match with mock-safe wiring.
 *
 * Layout:
 *  · Header: title + close X
 *  · Sub-header: select-all + "Evals (N)" + Add
 *  · Row: checkbox + type icon + name + type badge + status + action icons
 *  · Bottom: when the evaluations apply
 */

/* Same type config prod uses in SavedEvalsList — code / agent / llm.
   Kept inline so this file has no dependency on prod. */
const TYPE_CFG = {
  code: {
    label: "Code",
    icon: "solar:code-linear",
    color: "status.warning.fg",
  },
  agent: {
    label: "Agent",
    icon: "solar:bot-linear",
    color: "status.success.fg",
  },
  llm: { label: "LLM", icon: "solar:brain-linear", color: "primary.main" },
};
const evalType = (e) =>
  e.evalType ||
  e.eval_type ||
  (String(e.category || "")
    .toLowerCase()
    .includes("code")
    ? "code"
    : "agent");
const typeCfg = (e) => TYPE_CFG[evalType(e)] || TYPE_CFG.agent;

export default function AppliedEvalsDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
}) {
  const { appliedEvals, appliedIds, add, remove, update, slotsLeft } =
    useAppliedEvals(envState, patch);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const editing = appliedEvals.find((e) => e.id === editingId) || null;
  const [selected, setSelected] = useState(() => new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);

  const selectedEvals = useMemo(
    () => appliedEvals.filter((e) => selected.has(e.id)),
    [appliedEvals, selected],
  );
  const allChecked =
    appliedEvals.length > 0 && selected.size === appliedEvals.length;
  const someChecked = selected.size > 0 && !allChecked;

  const toggleOne = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelected(
      allChecked ? new Set() : new Set(appliedEvals.map((e) => e.id)),
    );
  const clearSelection = () => setSelected(new Set());

  const doBulkDelete = () => {
    selectedEvals.forEach((e) => remove(e.id));
    clearSelection();
    setConfirmBulk(false);
  };

  return (
    <>
      <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", md: 640 }}>
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          {/* Header */}
          <Stack
            direction="row"
            alignItems="center"
            sx={{ px: 2.5, pt: 2, pb: 1, flexShrink: 0 }}
          >
            <Typography sx={{ typography: "m2", fontWeight: 600, flex: 1 }}>
              All Evaluations
            </Typography>
            <IconButton
              size="small"
              onClick={onClose}
              sx={{ color: "text.primary" }}
            >
              <Iconify icon="solar:close-linear" width={20} />
            </IconButton>
          </Stack>

          {/* Sub-header: select-all + count / bulk state + Add */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ px: 2.5, pb: 1.25, flexShrink: 0 }}
          >
            {appliedEvals.length > 0 && (
              <Checkbox
                size="small"
                checked={allChecked}
                indeterminate={someChecked}
                onChange={toggleAll}
                sx={{ p: 0 }}
              />
            )}
            <Typography sx={{ fontSize: 13, fontWeight: 600, flex: 1 }}>
              {selected.size > 0
                ? `${selected.size} of ${appliedEvals.length} selected`
                : `Evals (${appliedEvals.length})`}
            </Typography>
            {selected.size > 0 ? (
              <>
                <Button
                  size="small"
                  onClick={clearSelection}
                  sx={{
                    color: "text.secondary",
                    minWidth: 0,
                    "&:hover": {
                      color: "text.primary",
                      bgcolor: "transparent",
                    },
                  }}
                >
                  Clear
                </Button>
                <Button
                  size="small"
                  onClick={() => setConfirmBulk(true)}
                  startIcon={
                    <Iconify icon="solar:trash-bin-trash-linear" width={14} />
                  }
                  sx={{
                    color: "status.danger.fg",
                    "&:hover": { bgcolor: "status.danger.bg" },
                  }}
                >
                  Delete
                </Button>
              </>
            ) : (
              <Button
                variant="outlined"
                size="small"
                startIcon={<Iconify icon="solar:add-linear" width={14} />}
                onClick={() => setPickerOpen(true)}
                sx={{
                  flexShrink: 0,
                }}
              >
                Add Evaluations
              </Button>
            )}
          </Stack>

          {/* Rows */}
          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2.5, pb: 2 }}>
            {appliedEvals.length === 0 ? (
              <Stack
                alignItems="center"
                justifyContent="center"
                sx={{ height: "100%", textAlign: "center" }}
              >
                <Typography sx={{ fontSize: 16, fontWeight: 600 }}>
                  No evaluations added
                </Typography>
                <Typography
                  sx={{ fontSize: 12, color: "text.disabled", mb: 2 }}
                >
                  Select and configure the evals to run in your environment
                </Typography>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={() => setPickerOpen(true)}
                  startIcon={<Iconify icon="solar:add-linear" width={14} />}
                >
                  Add Evaluations
                </Button>
              </Stack>
            ) : (
              <Stack spacing={0.75}>
                {appliedEvals.map((e) => (
                  <EvalRow
                    key={e.id}
                    item={e}
                    selected={selected.has(e.id)}
                    onToggle={() => toggleOne(e.id)}
                    onEdit={() => setEditingId(e.id)}
                    onDelete={() => remove(e.id)}
                    onRemoveMapping={(variable) =>
                      update(e.id, {
                        mapping: mappingWithout(e.mapping, variable),
                      })
                    }
                  />
                ))}
              </Stack>
            )}
          </Box>

          {/* Footer — when they apply. There's no re-scoring of past runs. */}
          {appliedEvals.length > 0 && (
            <Box
              sx={{
                px: 2.5,
                py: 1.5,
                flexShrink: 0,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {`${appliedEvals.length} evaluation${appliedEvals.length === 1 ? "" : "s"} score every run from the next one on.`}
              </Typography>
            </Box>
          )}
        </Stack>
      </SideDrawer>

      {/* Nested picker — v2 mock-store aware. */}
      <AddEvalsDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        env={env}
        envState={envState}
        existingIds={appliedIds}
        onAdd={(items) => {
          add(items);
          setPickerOpen(false);
        }}
        slotsLeft={slotsLeft}
      />

      {editing && (
        <EditEvalDrawer
          item={editing}
          env={env}
          envState={envState}
          onClose={() => setEditingId(null)}
          onSave={(changes) => update(editing.id, changes)}
        />
      )}

      {/* Bulk-delete confirm */}
      <Dialog
        open={confirmBulk}
        onClose={() => setConfirmBulk(false)}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 560 } }}
        fullWidth
      >
        <DialogTitle sx={{ typography: "m2", fontWeight: 600 }}>
          Remove {selected.size} eval{selected.size === 1 ? "" : "s"}?
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ typography: "s2" }}>
            The selected evaluations will be removed from this environment. Runs
            already scored against them will keep their scores; new runs
            won&apos;t be graded on them.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            size="small"
            onClick={() => setConfirmBulk(false)}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            onClick={doBulkDelete}
            sx={{
              bgcolor: "status.danger.solid",
              color: "common.white",
              "&:hover": {
                bgcolor: "status.danger.solid",
                color: "common.white",
              },
            }}
          >
            Remove {selected.size}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

AppliedEvalsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
};

/* ── Row — visual clone of SavedEvalsList's EvalRow (prod). ───────────── */

function EvalRow({
  item,
  selected,
  onToggle,
  onEdit,
  onDelete,
  onRemoveMapping,
}) {
  const [open, setOpen] = useState(false);
  const t = typeCfg(item);
  /* Every pair, not the first three — each carries its own Remove now. */
  const mappingEntries = Object.entries(item.mapping || {});
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
      {/* Main clickable row */}
      <Box
        {...clickable(() => setOpen((p) => !p), { expanded: open })}
        sx={{
          ...focusRing,
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.25,
          py: 0.85,
          cursor: "pointer",
        }}
      >
        <Checkbox
          size="small"
          checked={selected}
          onClick={(e) => e.stopPropagation()}
          onChange={onToggle}
          sx={{ p: 0 }}
        />

        {/* Type icon */}
        <Iconify
          icon={t.icon}
          width={16}
          sx={{ color: t.color, flexShrink: 0 }}
        />

        {/* Name */}
        <Typography
          noWrap
          sx={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0 }}
        >
          {item.name}
        </Typography>

        {/* Type — a plain qualifier, not a tag */}
        <Box
          component="span"
          sx={{
            typography: "s3",
            fontWeight: 500,
            color: "text.tertiary",
            flexShrink: 0,
          }}
        >
          {t.label}
        </Box>

        {/* Action icons */}
        <Box
          sx={{ display: "flex", gap: 0.25, ml: 0.25, flexShrink: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* View, not edit — the mapping is read-only in v0 (PRD v6). */}
          <Tooltip title="View mapping" arrow>
            <span>
              <IconButton
                size="small"
                onClick={onEdit}
                disabled={!canEditEval(item)}
              >
                <Iconify
                  icon="solar:eye-linear"
                  width={16}
                  sx={{ color: "text.secondary" }}
                />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Delete" arrow>
            <IconButton size="small" onClick={onDelete}>
              <Iconify
                icon="solar:trash-bin-trash-linear"
                width={16}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          </Tooltip>
        </Box>

        {/* Expand indicator */}
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={16}
          sx={{
            color: "text.disabled",
            flexShrink: 0,
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 160ms ease",
          }}
        />
      </Box>

      {/* Expanded body — mapping + description */}
      {open && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          {item.blurb && (
            <Typography
              sx={{
                fontSize: 12,
                color: "text.secondary",
                mb: mappingEntries.length ? 1 : 0,
              }}
            >
              {item.blurb}
            </Typography>
          )}
          {mappingEntries.length > 0 && (
            <Stack spacing={0.25}>
              {mappingEntries.map(([variable, column]) => (
                <Stack
                  key={variable}
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                >
                  <Typography
                    sx={{
                      fontFamily: "ui-monospace, Menlo, monospace",
                      fontSize: 12,
                      color: "text.secondary",
                    }}
                  >
                    {variable}
                  </Typography>
                  <Iconify
                    icon="solar:arrow-right-linear"
                    width={12}
                    sx={{ color: "text.disabled" }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      fontFamily: "ui-monospace, Menlo, monospace",
                      fontSize: 12,
                      color: "text.primary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {column}
                  </Typography>
                  {onRemoveMapping && (
                    <Tooltip title="Remove mapping" arrow>
                      <IconButton
                        size="small"
                        onClick={() => onRemoveMapping(variable)}
                        aria-label={`Remove ${variable} mapping`}
                      >
                        <Iconify
                          icon="solar:close-linear"
                          width={14}
                          sx={{ color: "text.tertiary" }}
                        />
                      </IconButton>
                    </Tooltip>
                  )}
                </Stack>
              ))}
              <Typography
                sx={{ fontSize: 12, color: "text.tertiary", pt: 0.5 }}
              >
                {MAPPING_NOTE}
              </Typography>
            </Stack>
          )}
          {item.threshold != null && (
            <Typography
              sx={{
                fontSize: 12,
                color: "text.tertiary",
                mt: mappingEntries.length ? 1 : 0,
              }}
            >
              pass ≥ {(item.threshold * 100).toFixed(0)}%
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
}

EvalRow.propTypes = {
  item: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onToggle: PropTypes.func,
  onEdit: PropTypes.func,
  onDelete: PropTypes.func,
  onRemoveMapping: PropTypes.func,
};
