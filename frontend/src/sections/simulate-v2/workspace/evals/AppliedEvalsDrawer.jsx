import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, Checkbox,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { useAppliedEvals } from "./appliedEvals";
import AddEvalsDrawer from "./AddEvalsDrawer";
import EditEvalDrawer, { canEditEval } from "./EditEvalDrawer";

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
 *  · Bottom: Run All (N)
 */

/* Same type config prod uses in SavedEvalsList — code / agent / llm.
   Kept inline so this file has no dependency on prod. */
const TYPE_CFG = {
  code:  { label: "Code",  icon: "mdi:code-braces",   color: "#f59e0b", bg: "rgba(245,158,11,0.10)" },
  agent: { label: "Agent", icon: "mdi:robot-outline", color: "#16A34A", bg: "rgba(22,163,74,0.10)" },
  llm:   { label: "LLM",   icon: "mdi:brain",         color: "#7857FC", bg: "rgba(120,87,252,0.10)" },
};
const evalType = (e) => (
  e.evalType || e.eval_type || (String(e.category || "").toLowerCase().includes("code") ? "code" : "agent")
);
const typeCfg = (e) => TYPE_CFG[evalType(e)] || TYPE_CFG.agent;

export default function AppliedEvalsDrawer({ open, onClose, env, envState, patch }) {
  const { appliedEvals, appliedIds, add, remove, update } = useAppliedEvals(envState, patch);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const editing = appliedEvals.find((e) => e.id === editingId) || null;
  const [selected, setSelected] = useState(() => new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);

  const selectedEvals = useMemo(
    () => appliedEvals.filter((e) => selected.has(e.id)),
    [appliedEvals, selected],
  );
  const allChecked = appliedEvals.length > 0 && selected.size === appliedEvals.length;
  const someChecked = selected.size > 0 && !allChecked;

  const toggleOne = (id) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleAll = () => setSelected(allChecked ? new Set() : new Set(appliedEvals.map((e) => e.id)));
  const clearSelection = () => setSelected(new Set());

  const doBulkDelete = () => {
    selectedEvals.forEach((e) => remove(e.id));
    clearSelection();
    setConfirmBulk(false);
  };

  return (
    <>
      <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", md: 560 }}>
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          {/* Header */}
          <Stack
            direction="row" alignItems="center"
            sx={{ px: 2.5, pt: 2, pb: 1, flexShrink: 0 }}
          >
            <Typography sx={{ fontSize: 16, fontWeight: 700, flex: 1 }}>
              All Evaluations
            </Typography>
            <IconButton size="small" onClick={onClose} sx={{ p: 0.25, color: "text.primary" }}>
              <Iconify icon="mingcute:close-line" width={20} />
            </IconButton>
          </Stack>

          {/* Sub-header: select-all + count / bulk state + Add */}
          <Stack
            direction="row" alignItems="center" spacing={0.75}
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
                    textTransform: "none", fontSize: 12, fontWeight: 500,
                    color: "text.secondary", minWidth: 0, px: 1,
                    "&:hover": { color: "text.primary", bgcolor: "transparent" },
                  }}
                >
                  Clear
                </Button>
                <Button
                  size="small"
                  onClick={() => setConfirmBulk(true)}
                  startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
                  sx={{
                    textTransform: "none", fontSize: 12, fontWeight: 500,
                    color: "#DC2626",
                    "&:hover": { bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.12 : 0.06) },
                  }}
                >
                  Delete
                </Button>
              </>
            ) : (
              <Button
                variant="outlined"
                size="small"
                startIcon={<Iconify icon="mdi:plus" width={14} />}
                onClick={() => setPickerOpen(true)}
                sx={{
                  textTransform: "none", fontSize: 12, fontWeight: 500,
                  borderRadius: "6px", flexShrink: 0,
                }}
              >
                Add Evaluations
              </Button>
            )}
          </Stack>

          {/* Rows */}
          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2.5, pb: 2 }}>
            {appliedEvals.length === 0 ? (
              <Stack alignItems="center" justifyContent="center" sx={{ height: "100%", textAlign: "center" }}>
                <Typography sx={{ fontSize: 16, fontWeight: 700 }}>
                  No evaluations added
                </Typography>
                <Typography sx={{ fontSize: 12, color: "text.disabled", mb: 2 }}>
                  Select and configure the evals to run in your environment
                </Typography>
                <Button
                  variant="contained" color="primary" size="small"
                  onClick={() => setPickerOpen(true)}
                  startIcon={<Iconify icon="mdi:plus" width={14} />}
                  sx={{ px: 3, textTransform: "none", fontWeight: 500 }}
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
                  />
                ))}
              </Stack>
            )}
          </Box>

          {/* Footer — Run All */}
          {appliedEvals.length > 0 && (
            <Stack
              direction="row" justifyContent="flex-end"
              sx={{
                px: 2.5, py: 1.5, flexShrink: 0,
                borderTop: "1px solid", borderColor: "divider",
              }}
            >
              <Button
                variant="contained" color="primary" size="small"
                startIcon={<Iconify icon="mdi:play" width={14} />}
                sx={{ textTransform: "none", fontWeight: 600 }}
                onClick={() => { /* prototype: no live re-run wiring */ }}
              >
                Run All ({appliedEvals.length})
              </Button>
            </Stack>
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
        onAdd={(items) => { add(items); setPickerOpen(false); }}
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
        maxWidth="xs" fullWidth
      >
        <DialogTitle sx={{ typography: "m2", fontWeight: 700 }}>
          Remove {selected.size} eval{selected.size === 1 ? "" : "s"}?
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ typography: "s2" }}>
            The selected evaluations will be removed from this environment. Runs already scored against them will keep their scores; new runs won&apos;t be graded on them.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setConfirmBulk(false)}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={doBulkDelete}
            sx={{
              typography: "s2", fontWeight: 700,
              bgcolor: "#DC2626", color: "#fff",
              "&:hover": { bgcolor: "#B91C1C", color: "#fff" },
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

function EvalRow({ item, selected, onToggle, onEdit, onDelete }) {
  const [open, setOpen] = useState(false);
  const t = typeCfg(item);
  const mappingEntries = Object.entries(item.mapping || {}).slice(0, 3);
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
        onClick={() => setOpen((p) => !p)}
        sx={{
          display: "flex", alignItems: "center", gap: 0.75,
          px: 1.25, py: 0.85, cursor: "pointer",
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
        <Iconify icon={t.icon} width={16} sx={{ color: t.color, flexShrink: 0 }} />

        {/* Name */}
        <Typography noWrap sx={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0 }}>
          {item.name}
        </Typography>

        {/* Type badge */}
        <Box
          sx={{
            display: "flex", alignItems: "center", gap: 0.3,
            px: 0.6, py: 0.15, borderRadius: "4px",
            fontSize: 10, fontWeight: 700,
            color: t.color, bgcolor: t.bg, flexShrink: 0,
          }}
        >
          {t.label}
        </Box>

        {/* Action icons */}
        <Box
          sx={{ display: "flex", gap: 0.25, ml: 0.25, flexShrink: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          <Tooltip title="Run" arrow>
            <IconButton size="small" sx={{ p: 0.4 }}>
              <Iconify icon="mdi:play-circle-outline" width={17} sx={{ color: "primary.main" }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Edit mapping & config" arrow>
            <IconButton size="small" sx={{ p: 0.4 }} onClick={onEdit} disabled={!canEditEval(item)}>
              <Iconify icon="mdi:pencil-outline" width={17} sx={{ color: "text.secondary" }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Delete" arrow>
            <IconButton size="small" sx={{ p: 0.4 }} onClick={onDelete}>
              <Iconify icon="mdi:trash-can-outline" width={17} sx={{ color: "text.secondary" }} />
            </IconButton>
          </Tooltip>
        </Box>

        {/* Expand indicator */}
        <Iconify
          icon="mdi:chevron-down"
          width={16}
          sx={{
            color: "text.disabled", flexShrink: 0,
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 160ms ease",
          }}
        />
      </Box>

      {/* Expanded body — mapping + description */}
      {open && (
        <Box
          sx={{
            px: 1.5, py: 1.25,
            borderTop: "1px solid", borderColor: "divider",
            bgcolor: (t2) => alpha(t2.palette.text.primary, t2.palette.mode === "dark" ? 0.03 : 0.02),
          }}
        >
          {item.blurb && (
            <Typography sx={{ fontSize: 12, color: "text.secondary", mb: mappingEntries.length ? 1 : 0 }}>
              {item.blurb}
            </Typography>
          )}
          {mappingEntries.length > 0 && (
            <Stack spacing={0.5}>
              {mappingEntries.map(([variable, column]) => (
                <Stack key={variable} direction="row" alignItems="center" spacing={0.75}>
                  <Typography sx={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11, color: "text.secondary" }}>
                    {variable}
                  </Typography>
                  <Iconify icon="solar:arrow-right-linear" width={11} sx={{ color: "text.disabled" }} />
                  <Typography sx={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11, color: "text.primary" }}>
                    {column}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          )}
          {item.threshold != null && (
            <Typography sx={{ fontSize: 11, color: "text.subtitle", mt: mappingEntries.length ? 1 : 0 }}>
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
};
