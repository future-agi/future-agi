import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { AGENT_SHAPE } from "./agents.shapes";
import { mintNextVersion, deriveAgentName } from "./agentVersion.helpers";
import DynamicField from "./connect/DynamicField";
import McpConnect from "./connect/McpConnect";
import { useMcpConnect } from "./connect/useConnect";
import { mcpEndpointFor } from "./connect/mcpSnippets";
import {
  REACH_KINDS, REACH_FIELDS, REF_KINDS, SELECTION_ACCENT, ADD_VERSION_COPY,
} from "./connect/connect.constants";

// Drafts a new version of an existing source agent: pick how we reach it, fill
// the connection, and hand the composition (A4) the record it mints and patches.
// Nothing here uploads or connects for real — the MCP handshake is mocked behind
// useMcpConnect, and submit only builds a record. No store access.
//
// The form lives in a child gated on `open` so it mounts fresh every time the
// drawer opens, and (matching the designer) seeds from the current version's
// connection so a new version starts as an edit of the last rather than a blank
// form. The drawer relies on SideDrawer's own close (X) — no header close.
export default function AddAgentDrawer({ open, onClose, agent, type, onAdd }) {
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      {open && (
        <AddVersionForm agent={agent} type={type} onAdd={onAdd} onClose={onClose} />
      )}
    </SideDrawer>
  );
}

AddAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agent: AGENT_SHAPE,
  type: PropTypes.shape({ id: PropTypes.string, label: PropTypes.string }),
  onAdd: PropTypes.func,
};

function AddVersionForm({ agent, type, onAdd, onClose }) {
  // Seed from the current version's connection so a new version opens as an edit
  // of the last (designer parity), falling back to a blank endpoint form.
  const prev = agent?.values || {};
  const [reach, setReach] = useState(agent?.reach || "endpoint");
  const [values, setValues] = useState({ ...prev });
  const [refKind, setRefKind] = useState(prev.ref?.kind || "branch");
  const [refValue, setRefValue] = useState(prev.ref?.value || "");
  const [note, setNote] = useState("");
  const [mcpConnected, setMcpConnected] = useState(false);

  const mcp = useMcpConnect(() => setMcpConnected(true));
  const target = { name: deriveAgentName(agent || {}, type) };
  const previewLabel = mintNextVersion(agent || {}, {}).label;

  const pickReach = (id) => {
    setReach(id);
    setValues({});
    setRefValue("");
    setMcpConnected(false);
  };

  const fields = REACH_FIELDS[reach] || [];
  const fieldsReady = fields.every(
    (f) => !f.required || String(values[f.key] ?? "").trim(),
  );
  const canSave = {
    endpoint: fieldsReady,
    repo: fieldsReady && refValue.trim(),
    platform: fieldsReady,
    mcp: mcpConnected,
  }[reach];

  const buildRecord = () => {
    const v = { ...values };
    let via;
    if (reach === "repo") {
      v.ref = { kind: refKind, value: refValue };
      via = `Read from ${v.repoUrl}${refValue ? ` @ ${refKind}: ${refValue}` : ""}`;
    } else if (reach === "endpoint") {
      via = `Probed at ${v.endpoint}`;
    } else if (reach === "platform") {
      via = type?.label || "Hosted platform";
    } else {
      v.mcpUrl = mcpEndpointFor(target);
      via = "Connected over MCP";
    }
    return {
      reach,
      values: v,
      via,
      connectedAt: new Date().toISOString(),
      note: note.trim() || via,
    };
  };

  const save = () => {
    if (!canSave) return;
    onAdd?.(buildRecord());
    onClose?.();
  };

  return (
    <Stack sx={{ height: "100%" }}>
      <Box sx={{ px: 2.5, py: 2, pr: 6, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
          Add new version · {previewLabel}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {ADD_VERSION_COPY.subtitle}
        </Typography>
      </Box>

      <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <FieldGroup heading={ADD_VERSION_COPY.reachHeading}>
          <Stack spacing={1}>
            {REACH_KINDS.map((s) => (
              <ReachCard key={s.id} reach={s} active={s.id === reach} onPick={pickReach} />
            ))}
          </Stack>
        </FieldGroup>

        {reach === "mcp" ? (
          <McpConnect target={target} type={type} onConnect={mcp.connect} testing={mcp.testing} />
        ) : (
          <FieldGroup heading={ADD_VERSION_COPY.connectionHeading}>
            <Stack spacing={1.75}>
              {fields.map((f) => (
                <DynamicField
                  key={f.key}
                  field={f}
                  value={values[f.key]}
                  values={values}
                  onChange={(val) => setValues((s) => ({ ...s, [f.key]: val }))}
                />
              ))}
              {reach === "repo" && (
                <RefControl
                  refKind={refKind} setRefKind={setRefKind}
                  refValue={refValue} setRefValue={setRefValue}
                />
              )}
            </Stack>
          </FieldGroup>
        )}

        <FieldGroup heading={ADD_VERSION_COPY.noteHeading}>
          <TextField
            size="small" fullWidth
            value={note} onChange={(e) => setNote(e.target.value)}
            placeholder={ADD_VERSION_COPY.notePlaceholder}
            helperText={ADD_VERSION_COPY.noteHelp}
            InputProps={{ sx: { typography: "s2" } }}
          />
        </FieldGroup>
      </Stack>

      <Stack
        direction="row" justifyContent="flex-end" spacing={1}
        sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Button onClick={onClose} sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}>
          {ADD_VERSION_COPY.cancel}
        </Button>
        <Button
          variant="contained" color="primary" size="small"
          disabled={!canSave}
          onClick={save}
          startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
        >
          Create {previewLabel}
        </Button>
      </Stack>
    </Stack>
  );
}

AddVersionForm.propTypes = {
  agent: AGENT_SHAPE,
  type: PropTypes.shape({ id: PropTypes.string, label: PropTypes.string }),
  onAdd: PropTypes.func,
  onClose: PropTypes.func,
};

function FieldGroup({ heading, children }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3", fontWeight: "fontWeightBold", color: "text.primary",
          textTransform: "uppercase", letterSpacing: 0.6, mb: 0.75,
        }}
      >
        {heading}
      </Typography>
      {children}
    </Box>
  );
}
FieldGroup.propTypes = { heading: PropTypes.string, children: PropTypes.node };

function ReachCard({ reach, active, onPick }) {
  return (
    <Box
      role="button"
      tabIndex={0}
      aria-pressed={active}
      onClick={() => onPick(reach.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onPick(reach.id);
        }
      }}
      sx={{
        p: 1.25, borderRadius: 1.25, cursor: "pointer",
        border: "1px solid",
        borderColor: (t) => active
          ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.35) : SELECTION_ACCENT)
          : t.palette.divider,
        bgcolor: (t) => active
          ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.06) : alpha(SELECTION_ACCENT, 0.05))
          : "background.paper",
        transition: "border-color .16s ease, background-color .16s ease",
        "&:hover": {
          borderColor: (t) => active
            ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.35) : SELECTION_ACCENT)
            : t.palette.text.disabled,
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Iconify icon={reach.icon} width={17} sx={{ color: "text.secondary", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{reach.label}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{reach.blurb}</Typography>
        </Box>
      </Stack>
    </Box>
  );
}
ReachCard.propTypes = {
  reach: PropTypes.shape({
    id: PropTypes.string, label: PropTypes.string, blurb: PropTypes.string, icon: PropTypes.string,
  }),
  active: PropTypes.bool,
  onPick: PropTypes.func,
};

function RefControl({ refKind, setRefKind, refValue, setRefValue }) {
  const refDef = REF_KINDS.find((r) => r.id === refKind);
  return (
    <Stack direction="row" spacing={1.5}>
      <TextField
        select size="small" label={ADD_VERSION_COPY.refHeading}
        value={refKind} onChange={(e) => setRefKind(e.target.value)}
        sx={{ minWidth: 140, "& .MuiInputBase-root": { typography: "s2" } }}
      >
        {REF_KINDS.map((r) => (
          <MenuItem key={r.id} value={r.id} sx={{ typography: "s2" }}>{r.label}</MenuItem>
        ))}
      </TextField>
      <TextField
        size="small" fullWidth
        label={refDef?.label || "Ref"}
        value={refValue}
        onChange={(e) => setRefValue(e.target.value)}
        placeholder={refDef?.placeholder}
        InputProps={{ sx: { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
      />
    </Stack>
  );
}
RefControl.propTypes = {
  refKind: PropTypes.string, setRefKind: PropTypes.func,
  refValue: PropTypes.string, setRefValue: PropTypes.func,
};
