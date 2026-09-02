import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, TextField, MenuItem, Select, FormControl, InputLabel,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { AGENT_TYPES, agentTypesForSurface, getAgentType } from "../../_mock/agentTypes";
import { SOURCE_KINDS, REF_KINDS } from "../../_mock/builder";
import DynamicField from "../connect/DynamicField";

/**
 * Add another agent — matches the primary connect flow's five source kinds.
 *
 * Agents can live in any of these places:
 *   · a source repository we read
 *   · a running agent we probe
 *   · a hosted platform (Vapi/Retell/…) we read via API
 *   · an MCP server whose manifest we read
 *   · an uploaded code / SDK bundle
 *
 * The subsequent form is shaped by the pick — a repo needs a location and
 * a git ref, a running agent needs an endpoint, a hosted platform needs
 * provider + credentials, MCP needs the server URL, and uploads need
 * the file itself. All five paths end at the same output: an agent
 * record that gets attached to `envState.additionalAgents`.
 */
export default function AddAgentDrawer({ open, onClose, env, onAdd, editing }) {
  /*
    The drawer serves two flows: adding a fresh implementation, and
    editing the existing source's connection. The `editing` prop
    flips it into edit mode — copy changes ("Edit connection" /
    "Save changes"), the primary CTA calls `onAdd` with the same
    record shape, and fields prefill from the passed agent.
    Callers use their local state to decide whether to interpret the
    onAdd payload as an append or a replace.
  */
  const isEditing = !!editing;
  const [sourceKind, setSourceKind] = useState(() => deriveSourceKind(editing) || "repo");
  const [location, setLocation] = useState(() => editing?.location || editing?.values?.repoUrl || editing?.values?.endpoint || "");
  const [refKind, setRefKind] = useState(() => editing?.ref?.kind || "branch");
  const [refValue, setRefValue] = useState(() => editing?.ref?.value || "");
  const [mcpUrl, setMcpUrl] = useState(() => editing?.values?.mcpUrl || "");
  const [file, setFile] = useState(null);
  const [note, setNote] = useState(() => (editing?.note && editing.note !== "Environment source" ? editing.note : ""));

  /* Hosted-platform state — the schema-driven type + values live here. */
  const surfaceTypes = useMemo(() => {
    const { recommended } = agentTypesForSurface(env?.surface);
    return recommended || [];
  }, [env?.surface]);
  const [typeId, setTypeId] = useState(() => editing?.typeId || surfaceTypes[0]?.id || AGENT_TYPES[0]?.id);
  const [values, setValues] = useState(() => (editing?.values && Object.keys(editing.values).length ? { ...editing.values } : {}));
  const [showAll, setShowAll] = useState(false);

  const type = getAgentType(typeId);
  const pickable = showAll ? AGENT_TYPES : surfaceTypes;

  const isVisible = (f) => {
    if (!f.dependsOn) return true;
    const dep = values?.[f.dependsOn.key];
    if (f.dependsOn.not != null && dep === f.dependsOn.not) return false;
    if (f.dependsOn.eq != null && dep !== f.dependsOn.eq) return false;
    return true;
  };
  const platformReady = type && (type.fields || []).every((f) => !f.required || !isVisible(f) || values[f.key]);

  /* Per-source-kind readiness check for the primary CTA. */
  const canSave = ({
    repo: location.trim() && refValue.trim(),
    endpoint: location.trim(),
    platform: platformReady,
    mcp: mcpUrl.trim(),
    upload: !!file,
  })[sourceKind];

  const reset = () => {
    setSourceKind("repo");
    setLocation(""); setRefKind("branch"); setRefValue("");
    setMcpUrl(""); setFile(null); setNote("");
    setTypeId(surfaceTypes[0]?.id || AGENT_TYPES[0]?.id);
    setValues({});
    setShowAll(false);
  };

  const save = () => {
    if (!canSave) return;
    /* Every path produces the same record shape. `typeId` is the
       "connect via" for how we reach it at run time — for repo/endpoint
       /mcp/upload we default to the current env's surface type; the
       platform path already has its own. */
    const chosenTypeId = sourceKind === "platform" ? typeId : (surfaceTypes[0]?.id || AGENT_TYPES[0]?.id);
    const chosenType = getAgentType(chosenTypeId);
    const via = ({
      repo: `Read from ${location}${refValue ? ` @ ${refKind}: ${refValue}` : ""}`,
      endpoint: `Probed at ${location}`,
      platform: chosenType?.label || "Hosted platform",
      mcp: `MCP manifest at ${mcpUrl}`,
      upload: `Uploaded — ${file?.name || "bundle"}`,
    })[sourceKind];
    onAdd({
      sourceKind,
      typeId: chosenTypeId,
      values: sourceKind === "platform" ? values : {},
      via,
      location: location || mcpUrl || file?.name || "",
      ref: sourceKind === "repo" ? { kind: refKind, value: refValue } : null,
      connectedAt: new Date().toISOString(),
      note: note.trim() || via,
    });
    reset();
  };

  return (
    <SideDrawer open={open} onClose={() => { reset(); onClose(); }} width={640}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              {isEditing ? "Edit connection" : "Add another agent"}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {isEditing
                ? "Update where this environment reads its source agent from. Contract is not re-derived — use Promote for that."
                : "Attach a second implementation. Every source is read, never typed — but they don't all carry the same things."}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>

        <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
          {/* ── source kind picker ── */}
          <Box>
            <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6, mb: 0.75 }}>
              Where does this agent live?
            </Typography>
            <Stack spacing={1}>
              {SOURCE_KINDS.map((s) => {
                const active = s.id === sourceKind;
                return (
                  <Box
                    key={s.id}
                    onClick={() => setSourceKind(s.id)}
                    sx={{
                      p: 1.25, borderRadius: 1.25, cursor: "pointer",
                      border: "1px solid",
                      /*
                        Purple selection in light theme; neutral grey
                        in dark. Dark mode's high-contrast surfaces make
                        even a subtle purple tint read as loud — the
                        neutral text-primary tint matches the template
                        row and stays out of the way.
                      */
                      borderColor: (t) => active
                        ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.35) : "#7857FC")
                        : t.palette.divider,
                      bgcolor: (t) => active
                        ? (t.palette.mode === "dark"
                          ? alpha(t.palette.text.primary, 0.06)
                          : alpha("#7857FC", 0.05))
                        : "background.paper",
                      transition: "border-color .16s ease, background-color .16s ease",
                      "&:hover": {
                        borderColor: (t) => active
                          ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.35) : "#7857FC")
                          : t.palette.text.disabled,
                      },
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1.25}>
                      <Iconify icon={s.icon} width={17} sx={{ color: "text.secondary", flexShrink: 0 }} />
                      <Box flex={1} minWidth={0}>
                        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{s.label}</Typography>
                        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{s.blurb}</Typography>
                      </Box>
                    </Stack>
                  </Box>
                );
              })}
            </Stack>
          </Box>

          {/* ── source-specific fields ── */}
          {sourceKind === "repo" && (
            <RepoFields
              location={location} setLocation={setLocation}
              refKind={refKind} setRefKind={setRefKind}
              refValue={refValue} setRefValue={setRefValue}
            />
          )}

          {sourceKind === "endpoint" && (
            <TextField
              size="small" fullWidth
              label="Running agent URL"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="https://api.yourapp.com/agent"
              helperText="We'll probe the deployed agent and infer its shape from how it answers."
              InputProps={{ sx: { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
          )}

          {sourceKind === "platform" && (
            <PlatformFields
              pickable={pickable}
              typeId={typeId} setTypeId={setTypeId}
              type={type}
              values={values} setValues={setValues}
              showAll={showAll} setShowAll={setShowAll}
              surfaceTypes={surfaceTypes}
            />
          )}

          {sourceKind === "mcp" && (
            <TextField
              size="small" fullWidth
              label="MCP server URL"
              value={mcpUrl}
              onChange={(e) => setMcpUrl(e.target.value)}
              placeholder="https://mcp.yourapp.com/v1/mcp"
              helperText="We'll read the manifest for the exact tool schemas. Nothing else is inferred."
              InputProps={{ sx: { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
          )}

          {sourceKind === "upload" && (
            <UploadField file={file} setFile={setFile} />
          )}

          {/* ── note ── */}
          <Box>
            <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6, mb: 0.75 }}>
              Note
            </Typography>
            <TextField
              size="small" fullWidth
              value={note} onChange={(e) => setNote(e.target.value)}
              placeholder={`e.g. "GPT-4o rewrite, comparison against source"`}
              helperText="Optional. Shows next to this agent in the list."
              InputProps={{ sx: { typography: "s2" } }}
            />
          </Box>
        </Stack>

        <Stack
          direction="row" justifyContent="flex-end" spacing={1}
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Button onClick={() => { reset(); onClose(); }} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
            Cancel
          </Button>
          <Button
            variant="contained" color="primary" size="small"
            disabled={!canSave}
            onClick={save}
            startIcon={<Iconify icon={isEditing ? "solar:diskette-linear" : "solar:link-circle-linear"} width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            {isEditing ? "Save changes" : "Add agent"}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

AddAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  onAdd: PropTypes.func,
  editing: PropTypes.object,
};

/**
 * Infer which "where does this live" bucket a stored agent falls
 * into so the drawer can prefill the correct picker + fields when
 * opened in edit mode. Falls back to repo (the neutral default).
 */
function deriveSourceKind(agent) {
  if (!agent) return null;
  const v = agent.values || {};
  if (v.provider) return "platform";
  if (v.mcpUrl) return "mcp";
  if (v.endpoint) return "endpoint";
  if (v.repoUrl || agent.ref) return "repo";
  if (agent.location?.startsWith("mcp:")) return "mcp";
  if (agent.location?.startsWith("http")) return "endpoint";
  return null;
}

/* ── source-specific field groups ─────────────────────────────────────────── */

function RepoFields({ location, setLocation, refKind, setRefKind, refValue, setRefValue }) {
  const refDef = REF_KINDS.find((r) => r.id === refKind);
  return (
    <Stack spacing={2}>
      <TextField
        size="small" fullWidth
        label="Repository URL"
        value={location}
        onChange={(e) => setLocation(e.target.value)}
        placeholder="https://github.com/your-org/your-agent"
        InputProps={{ sx: { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
      />
      <Stack direction="row" spacing={1.5}>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>Pin to</InputLabel>
          <Select
            label="Pin to" value={refKind}
            onChange={(e) => setRefKind(e.target.value)}
            sx={{ typography: "s2" }}
          >
            {REF_KINDS.map((r) => (
              <MenuItem key={r.id} value={r.id} sx={{ typography: "s2" }}>{r.label}</MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField
          size="small" fullWidth
          label={refDef?.label || "Ref"}
          value={refValue}
          onChange={(e) => setRefValue(e.target.value)}
          placeholder={refDef?.placeholder}
          helperText={refDef?.note}
          InputProps={{ sx: { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
        />
      </Stack>
    </Stack>
  );
}
RepoFields.propTypes = {
  location: PropTypes.string, setLocation: PropTypes.func,
  refKind: PropTypes.string, setRefKind: PropTypes.func,
  refValue: PropTypes.string, setRefValue: PropTypes.func,
};

function PlatformFields({ pickable, typeId, setTypeId, type, values, setValues, showAll, setShowAll, surfaceTypes }) {
  return (
    <Stack spacing={2}>
      <Box>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6, mb: 0.75 }}>
          Agent type
        </Typography>
        <Box
          sx={{
            display: "grid", gap: 1,
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          }}
        >
          {pickable.map((t) => {
            const active = t.id === typeId;
            return (
              <Box
                key={t.id}
                onClick={() => { setTypeId(t.id); setValues({}); }}
                sx={{
                  p: 1.25, borderRadius: 1.25, cursor: "pointer",
                  border: "1px solid",
                  /*
                    Same theme-aware selection style as the
                    "where does this agent live?" picker above:
                    neutral text-primary tint in dark theme,
                    purple in light. Uniformity across the drawer's
                    two card grids was the request.
                  */
                  borderColor: (t2) => active
                    ? (t2.palette.mode === "dark" ? alpha(t2.palette.text.primary, 0.35) : "#7857FC")
                    : t2.palette.divider,
                  bgcolor: (t2) => active
                    ? (t2.palette.mode === "dark"
                      ? alpha(t2.palette.text.primary, 0.06)
                      : alpha("#7857FC", 0.05))
                    : "background.paper",
                  transition: "border-color .15s ease, background-color .15s ease",
                  "&:hover": {
                    borderColor: (t2) => active
                      ? (t2.palette.mode === "dark" ? alpha(t2.palette.text.primary, 0.35) : "#7857FC")
                      : t2.palette.text.disabled,
                  },
                }}
              >
                <Stack direction="row" alignItems="center" spacing={1}>
                  {/*
                    Icon stays neutral so the card grid doesn't
                    read as "purple everywhere". The type's brand
                    color still lives on the agent's list-row icon
                    tile — this picker only needs to distinguish
                    selected vs unselected, not brand vs brand.
                  */}
                  <Iconify icon={t.icon} width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{t.label}</Typography>
                </Stack>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", mt: 0.375 }}>
                  {t.blurb}
                </Typography>
              </Box>
            );
          })}
        </Box>
        {!showAll && surfaceTypes.length < AGENT_TYPES.length && (
          <Button
            size="small" onClick={() => setShowAll(true)}
            startIcon={<Iconify icon="solar:widget-linear" width={14} />}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", mt: 1 }}
          >
            Show all {AGENT_TYPES.length} agent types
          </Button>
        )}
      </Box>

      {type && (
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6, mb: 0.75 }}>
            Connection
          </Typography>
          <Stack spacing={1.75}>
            {(type.fields || []).map((f) => (
              <DynamicField
                key={f.key}
                field={f}
                value={values[f.key]}
                values={values}
                onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
              />
            ))}
          </Stack>
        </Box>
      )}
    </Stack>
  );
}
PlatformFields.propTypes = {
  pickable: PropTypes.array, typeId: PropTypes.string, setTypeId: PropTypes.func,
  type: PropTypes.object, values: PropTypes.object, setValues: PropTypes.func,
  showAll: PropTypes.bool, setShowAll: PropTypes.func, surfaceTypes: PropTypes.array,
};

function UploadField({ file, setFile }) {
  return (
    <Box>
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6, mb: 0.75 }}>
        Bundle
      </Typography>
      <Box
        component="label"
        sx={{
          display: "flex", alignItems: "center", gap: 1.5,
          p: 1.75, borderRadius: 1.25, cursor: "pointer",
          border: "1px dashed", borderColor: "divider",
          "&:hover": { borderColor: "text.subtitle", bgcolor: "action.hover" },
        }}
      >
        <Iconify icon="solar:upload-square-linear" width={20} sx={{ color: "text.subtitle" }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {file ? file.name : "Drop a .zip or SDK bundle"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {file ? `${(file.size / 1024).toFixed(0)} kB — click to change` : "Same depth as pointing at a repo — it's the same code."}
          </Typography>
        </Box>
        <input
          hidden type="file"
          accept=".zip,.tar,.tgz,.whl,.pyz"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
      </Box>
    </Box>
  );
}
UploadField.propTypes = { file: PropTypes.object, setFile: PropTypes.func };
