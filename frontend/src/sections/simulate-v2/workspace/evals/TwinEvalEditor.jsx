import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  Box, Stack, Typography, TextField, MenuItem, Button, IconButton, Chip,
  ToggleButton, ToggleButtonGroup,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { twinById } from "../../_mock/twins";

const TWIN_TINT = "#7857FC";

/**
 * Assertion catalog.
 *
 * Each shape describes what the user is asking about the final twin
 * sandbox state at run finish. Kept intentionally small (4 shapes,
 * each with 1–3 params) because the win is not schema breadth — it
 * is that this is *structured*, not a text prompt to a judge. A judge
 * that reads "did the agent reply?" is a coin flip; a shape that says
 * "message_posted in #urgent containing 'ETA'" is a boolean.
 *
 * `applies` filters which services the shape offers. `params` drives
 * the form fields dynamically. `preview` synthesises the human-
 * readable summary shown on the eval row after save.
 */
const SHAPES = [
  {
    id: "message_posted",
    label: "A message was posted",
    icon: "solar:chat-round-line-linear",
    applies: ["slack"],
    params: [
      { key: "channel", label: "Channel", placeholder: "#support-urgent", type: "text" },
      { key: "contains", label: "Message contains", placeholder: "ETA", type: "text", optional: true },
      { key: "byRole", label: "Sender", type: "select", options: [
        { value: "any", label: "Any" }, { value: "agent", label: "Agent only" },
      ], defaultValue: "agent" },
    ],
    preview: (p) => `Slack: message${p.contains ? ` mentioning "${p.contains}"` : ""} posted in ${p.channel || "channel"} by ${p.byRole === "agent" ? "the agent" : "anyone"}`,
  },
  {
    id: "row_written",
    label: "A row or page was written",
    icon: "solar:database-linear",
    applies: ["notion", "salesforce", "linear", "github"],
    params: [
      { key: "target", label: "Database / project", placeholder: "Launch", type: "text" },
      { key: "count", label: "At least how many rows", type: "number", defaultValue: 1 },
      { key: "op", label: "Write kind", type: "select", options: [
        { value: "any", label: "Any write" },
        { value: "create", label: "Create only" },
        { value: "update", label: "Update only" },
      ], defaultValue: "any" },
    ],
    preview: (p, sId) => {
      const s = twinById(sId)?.name || sId;
      const op = p.op === "any" ? "written" : p.op === "create" ? "created" : "updated";
      return `${s}: at least ${p.count || 1} row${p.count === 1 ? "" : "s"} ${op} in ${p.target || "the target"}`;
    },
  },
  {
    id: "no_extra_writes",
    label: "No extra writes",
    icon: "solar:lock-keyhole-minimalistic-linear",
    applies: ["slack", "notion", "gmail", "salesforce", "github", "linear"],
    params: [
      { key: "maxWrites", label: "At most this many writes", type: "number", defaultValue: 1 },
      { key: "scope", label: "Scope", type: "select", options: [
        { value: "any", label: "Anywhere in the service" },
        { value: "outside", label: "Outside the expected target" },
      ], defaultValue: "outside" },
    ],
    preview: (p, sId) => {
      const s = twinById(sId)?.name || sId;
      const scope = p.scope === "outside" ? " outside the expected target" : "";
      return `${s}: no more than ${p.maxWrites || 1} write${p.maxWrites === 1 ? "" : "s"}${scope}`;
    },
  },
  {
    id: "label_or_status_applied",
    label: "A label / status was applied",
    icon: "solar:tag-linear",
    applies: ["gmail", "salesforce", "linear", "github"],
    params: [
      { key: "value", label: "Label or status", placeholder: "Escalated", type: "text" },
      { key: "target", label: "On (email / issue / account)", placeholder: "Legal email", type: "text", optional: true },
    ],
    preview: (p, sId) => `${twinById(sId)?.name || sId}: '${p.value || "label"}' applied${p.target ? ` on ${p.target}` : ""}`,
  },
];

const shapeById = (id) => SHAPES.find((s) => s.id === id);

const paramsForShape = (shape, service) => shape.params;

const initialParamsFor = (shape) => Object.fromEntries(shape.params.map((p) => [p.key, p.defaultValue ?? ""]));

/**
 * Author a twin end-state evaluation.
 *
 * Structured, not a prompt: the user builds N assertions ("message
 * posted in #urgent containing 'ETA'", "≤1 write on Notion",
 * "'Escalated' label applied on the Legal email") — each one is a
 * bounded query against final twin state, not a judge call. That's
 * why it's deterministic where a text-prompt eval isn't.
 *
 * The combinator ("all must pass" / "any must pass") turns the set
 * into one boolean per run.
 */
export default function TwinEvalEditor({ open, envState, onClose, onSave }) {
  const services = envState?.twinBacking?.services || [];
  const [name, setName] = useState("Clone end-state check");
  const [combinator, setCombinator] = useState("all");
  const [assertions, setAssertions] = useState(() => [seedAssertion(services)]);

  if (!open) return null;
  if (!services.length) return null;

  const addAssertion = () => setAssertions((prev) => [...prev, seedAssertion(services)]);
  const removeAssertion = (i) => setAssertions((prev) => prev.filter((_, idx) => idx !== i));
  const patchAssertion = (i, patch) => setAssertions((prev) =>
    prev.map((a, idx) => (idx === i ? { ...a, ...patch } : a))
  );

  const canSave = name.trim().length > 0 && assertions.length > 0 &&
    assertions.every((a) => shapeById(a.shapeId) && a.service);

  const save = () => {
    const cleaned = assertions.map((a) => {
      const shape = shapeById(a.shapeId);
      const params = Object.fromEntries(shape.params.map((p) => [p.key, a.params[p.key]]));
      return { shapeId: a.shapeId, service: a.service, params };
    });
    const blurb = cleaned
      .map((a) => shapeById(a.shapeId).preview(a.params, a.service))
      .join(` ${combinator === "all" ? "AND" : "OR"} `);
    onSave({
      id: `twin-eval-${Date.now().toString(36)}`,
      name: name.trim(),
      blurb,
      icon: "solar:server-square-linear",
      category: "Clone state",
      threshold: 1,
      custom: true,
      evalKind: "twin_end_state",
      appliesTo: ["twin"],
      combinator,
      assertions: cleaned,
    });
    reset();
  };

  const reset = () => {
    setName("Clone end-state check");
    setCombinator("all");
    setAssertions([seedAssertion(services)]);
  };
  const close = () => { reset(); onClose(); };

  return (
    <Dialog
      open={open}
      onClose={close}
      maxWidth="md" fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2, backgroundImage: "none",
          bgcolor: "background.paper", border: "1px solid", borderColor: "divider",
        },
      }}
    >
      <DialogTitle sx={{ p: 2.5, pb: 1.5 }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.5}>
          <Box sx={{
            width: 30, height: 30, borderRadius: 0.875,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.18 : 0.1),
            color: TWIN_TINT,
          }}>
            <Iconify icon="solar:server-square-bold" width={16} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 700 }}>Author clone end-state eval</Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Structured assertions against the final sandbox state. Not a prompt — a boolean per assertion.
            </Typography>
          </Box>
          <IconButton size="small" onClick={close}>
            <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>
      </DialogTitle>

      <DialogContent sx={{ p: 2.5, pt: 1 }} dividers>
        <Stack spacing={2}>
          <TextField
            size="small" fullWidth
            label="Eval name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            InputLabelProps={{ sx: { typography: "s2" } }}
            InputProps={{ sx: { typography: "s2" } }}
          />

          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>An assertion passes when</Typography>
            <ToggleButtonGroup
              value={combinator} exclusive
              onChange={(_, v) => v && setCombinator(v)}
              size="small"
              sx={{
                "& .MuiToggleButton-root": {
                  typography: "s2", fontWeight: 700, textTransform: "none",
                  px: 1.5, py: 0.5, borderColor: "divider",
                },
                "& .Mui-selected": {
                  bgcolor: (t) => `${alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.16 : 0.08)} !important`,
                  color: `${TWIN_TINT} !important`,
                  borderColor: `${TWIN_TINT} !important`,
                },
              }}
            >
              <ToggleButton value="all">All must pass</ToggleButton>
              <ToggleButton value="any">Any must pass</ToggleButton>
            </ToggleButtonGroup>
          </Stack>

          <Stack spacing={1.25}>
            {assertions.map((a, i) => (
              <AssertionRow
                key={i}
                index={i}
                assertion={a}
                services={services}
                onPatch={(patch) => patchAssertion(i, patch)}
                onRemove={assertions.length > 1 ? () => removeAssertion(i) : null}
              />
            ))}
          </Stack>

          <Button
            variant="outlined" size="small"
            onClick={addAssertion}
            startIcon={<Iconify icon="solar:add-circle-linear" width={13} />}
            sx={{
              typography: "s2", fontWeight: 700, alignSelf: "flex-start",
              borderColor: "divider", color: "text.primary",
            }}
          >
            Add assertion
          </Button>

          {assertions.length > 0 && assertions.every((a) => shapeById(a.shapeId)) && (
            <Box sx={{
              p: 1.5, borderRadius: 1.25, border: "1px dashed",
              borderColor: alpha(TWIN_TINT, 0.32),
              bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.06 : 0.03),
            }}>
              <Typography sx={{ typography: "s3", fontWeight: 700, color: TWIN_TINT, textTransform: "uppercase", letterSpacing: 0.4, mb: 0.5 }}>
                Preview
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.primary" }}>
                {assertions
                  .map((a) => shapeById(a.shapeId)?.preview(a.params, a.service))
                  .filter(Boolean)
                  .join(` ${combinator === "all" ? "AND " : "OR "}`)}
              </Typography>
            </Box>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ p: 2, pt: 1.5 }}>
        <Button onClick={close} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained" color="primary"
          disabled={!canSave} onClick={save}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Add to evaluations
        </Button>
      </DialogActions>
    </Dialog>
  );
}

TwinEvalEditor.propTypes = {
  open: PropTypes.bool,
  envState: PropTypes.object,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};

/* ── one assertion row ───────────────────────────────────────────────────── */

function AssertionRow({ index, assertion, services, onPatch, onRemove }) {
  const shape = shapeById(assertion.shapeId);
  const eligibleShapes = useMemo(
    () => SHAPES.filter((s) => s.applies.includes(assertion.service)),
    [assertion.service],
  );

  const changeService = (sId) => {
    const nextShape = SHAPES.find((s) => s.applies.includes(sId)) || SHAPES[0];
    onPatch({ service: sId, shapeId: nextShape.id, params: initialParamsFor(nextShape) });
  };

  const changeShape = (shapeId) => {
    const s = shapeById(shapeId);
    onPatch({ shapeId, params: initialParamsFor(s) });
  };

  return (
    <Box sx={{
      p: 1.5, borderRadius: 1.25, border: "1px solid", borderColor: "divider",
      bgcolor: "background.paper",
    }}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 1.25 }}>
        <Chip
          label={`#${index + 1}`} size="small"
          sx={{
            height: 18, fontSize: 10, fontWeight: 700,
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.06),
            color: "text.secondary",
            "& .MuiChip-label": { px: 0.75 },
          }}
        />
        <Box flex={1} />
        {onRemove && (
          <IconButton size="small" onClick={onRemove}>
            <Iconify icon="solar:close-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
          </IconButton>
        )}
      </Stack>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25}>
        <TextField
          select size="small" label="Service"
          value={assertion.service}
          onChange={(e) => changeService(e.target.value)}
          sx={{ minWidth: 160 }}
          InputLabelProps={{ sx: { typography: "s2" } }}
          InputProps={{ sx: { typography: "s2" } }}
        >
          {services.map((sId) => {
            const t = twinById(sId);
            return (
              <MenuItem key={sId} value={sId} sx={{ typography: "s2" }}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Iconify icon={t?.icon || "solar:server-square-linear"} width={12} sx={{ color: t?.color || TWIN_TINT }} />
                  <span>{t?.name || sId}</span>
                </Stack>
              </MenuItem>
            );
          })}
        </TextField>

        <TextField
          select size="small" label="Assertion"
          value={assertion.shapeId}
          onChange={(e) => changeShape(e.target.value)}
          disabled={eligibleShapes.length === 0}
          sx={{ minWidth: 240, flex: 1 }}
          InputLabelProps={{ sx: { typography: "s2" } }}
          InputProps={{ sx: { typography: "s2" } }}
        >
          {eligibleShapes.map((s) => (
            <MenuItem key={s.id} value={s.id} sx={{ typography: "s2" }}>
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Iconify icon={s.icon} width={12} sx={{ color: TWIN_TINT }} />
                <span>{s.label}</span>
              </Stack>
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      {shape && (
        <Box sx={{
          display: "grid", gap: 1.25, mt: 1.25,
          gridTemplateColumns: { xs: "1fr", sm: `repeat(${Math.min(3, shape.params.length)}, 1fr)` },
        }}>
          {paramsForShape(shape, assertion.service).map((p) => (
            <TextField
              key={p.key}
              select={p.type === "select"}
              type={p.type === "number" ? "number" : "text"}
              size="small"
              label={p.label + (p.optional ? " (optional)" : "")}
              placeholder={p.placeholder}
              value={assertion.params[p.key] ?? ""}
              onChange={(e) => onPatch({ params: { ...assertion.params, [p.key]: e.target.value } })}
              InputLabelProps={{ sx: { typography: "s2" }, shrink: p.placeholder ? true : undefined }}
              InputProps={{ sx: { typography: "s2" } }}
            >
              {p.type === "select" && p.options.map((opt) => (
                <MenuItem key={opt.value} value={opt.value} sx={{ typography: "s2" }}>{opt.label}</MenuItem>
              ))}
            </TextField>
          ))}
        </Box>
      )}
    </Box>
  );
}
AssertionRow.propTypes = {
  index: PropTypes.number,
  assertion: PropTypes.object,
  services: PropTypes.array,
  onPatch: PropTypes.func,
  onRemove: PropTypes.func,
};

/* ── helpers ─────────────────────────────────────────────────────────────── */

function seedAssertion(services) {
  const service = services[0];
  const shape = SHAPES.find((s) => s.applies.includes(service)) || SHAPES[0];
  return {
    service,
    shapeId: shape.id,
    params: initialParamsFor(shape),
  };
}
