/* eslint-disable react/prop-types */
import {
  Alert,
  Autocomplete,
  Box,
  Chip,
  InputAdornment,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import { canonicalEntries, canonicalKeys } from "src/utils/utils";

// Preview mode for the run-simulation creation flow. The simulation
// hasn't executed yet, so we can't fetch real call data — but we know
// agent/persona/scenario/prompt from the form, and we know the runtime
// key set the backend resolver supports. This panel renders that
// synthetic vocabulary so users can configure eval variable bindings
// upfront; placeholder values fill the runtime-only slots.
//
// Keys here must match the backend context_map in
// simulate/temporal/activities/xl.py::_build_simulation_context_map and
// the known_keys vocabulary in _run_single_evaluation.

const RUNTIME_PLACEHOLDER = "<populated after simulation run>";

// Runtime vocabulary — now nested under `call.*`. Keys match the
// TRANSCRIPT_DOT_ALIASES + CONTEXT_MAP_DOT_ALIASES sets in
// simulate/temporal/activities/xl.py.
const VOICE_RUNTIME_LEAVES = [
  "transcript",
  "voice_recording",
  "stereo_recording",
  "assistant_recording",
  "customer_recording",
  "agent_prompt",
];
const TEXT_RUNTIME_LEAVES = [
  "transcript",
  "user_chat_transcript",
  "assistant_chat_transcript",
  "agent_prompt",
];
const COMMON_RUNTIME_LEAVES = [
  "summary",
  "ended_reason",
  "duration_seconds",
  "status",
  "overall_score",
  "phone_number",
  "recording_url",
  "stereo_recording_url",
];

const PRIORITY_PREFIXES = [
  "call.transcript",
  "call.summary",
  "call.user_chat_transcript",
  "call.assistant_chat_transcript",
  "call.voice_recording",
  "call.stereo_recording",
  "call.assistant_recording",
  "call.customer_recording",
  "call.agent_prompt",
  "call.",
  "scenario.columns.",
  "scenario.info.",
  "scenario.",
  "simulation.",
  "agent.",
  "persona.",
  "prompt.",
];

// Walk a nested object to leaves, yielding [dotPath, value] pairs.
// Caps for the leaf walker:
//   ARRAY_PEEK — only inspect the first N array elements so a 1000-msg
//     conversation doesn't flood the table with thousands of rows
//   DICT_LIMIT — stop recursing once a dict gets too wide; the unflattened
//     object then renders as a JSON-stringified leaf via the fallback in
//     the row Typography
const FLATTEN_ARRAY_PEEK = 500;
const FLATTEN_DICT_LIMIT = 5000;

function flattenLeaves(obj, prefix) {
  const result = [];
  // Arrays — recurse with numeric indices so leaves get paths like
  // `messages.0.content`. Same notation that `walkPath` resolves via
  // `obj[k]` (works for both dict keys and numeric indices).
  if (Array.isArray(obj)) {
    obj.slice(0, FLATTEN_ARRAY_PEEK).forEach((item, idx) => {
      const path = prefix ? `${prefix}.${idx}` : String(idx);
      if (item && typeof item === "object") {
        result.push(...flattenLeaves(item, path));
      } else {
        result.push([path, item]);
      }
    });
    return result;
  }
  for (const [k, v] of canonicalEntries(obj || {})) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object") {
      if (Array.isArray(v)) {
        result.push(...flattenLeaves(v, path));
      } else if (canonicalKeys(v).length === 0) {
        // Empty group — skip entirely so it doesn't show up as a stray
        // `path: {}` leaf in the vocabulary table.
        continue;
      } else if (canonicalKeys(v).length < FLATTEN_DICT_LIMIT) {
        result.push(...flattenLeaves(v, path));
      } else {
        // Empty dict or wider than the limit — emit as a leaf and let
        // the Typography fallback JSON.stringify it.
        result.push([path, v]);
      }
    } else {
      result.push([path, v]);
    }
  }
  return result;
}
function sortEntries(entries) {
  const order = (k) => {
    for (let i = 0; i < PRIORITY_PREFIXES.length; i++) {
      const p = PRIORITY_PREFIXES[i];
      if (k === p || k.startsWith(p)) return i;
    }
    return PRIORITY_PREFIXES.length;
  };
  return [...entries].sort(([a], [b]) => order(a) - order(b));
}

function deepMatch(val, q) {
  if (val === null || val === undefined) return false;
  if (typeof val === "string") return val.toLowerCase().includes(q);
  if (typeof val === "number" || typeof val === "boolean")
    return String(val).toLowerCase().includes(q);
  if (Array.isArray(val)) return val.some((v) => deepMatch(v, q));
  if (typeof val === "object")
    return Object.entries(val).some(
      ([k, v]) => k.toLowerCase().includes(q) || deepMatch(v, q),
    );
  return false;
}

const CreateSimulationPreviewMode = React.forwardRef(
  (
    {
      variables = [],
      onColumnsLoaded,
      onReadyChange,
      onTestResult,
      previewData,
      initialMapping = null,
    },
    ref,
  ) => {
    const [mapping, setMapping] = useState(
      initialMapping && typeof initialMapping === "object"
        ? { ...initialMapping }
        : {},
    );
    const [tableSearch, setTableSearch] = useState("");
    const [expandedCols, setExpandedCols] = useState({});

    // Track displayKey -> UUID for scenario columns. The backend
    // resolver only accepts column UUIDs, so the saved eval mapping
    // must persist UUIDs even though the dropdown shows the friendly
    // `scenario_<name>` label.
    const scenarioKeyMap = useRef({});

    // Build the nested vocabulary dict from the preview data. Real
    // values fill in what the user already chose in the form (agent,
    // persona, scenario, prompt); placeholder strings seed the runtime
    // keys so they render in the panel and are pickable in the dropdown.
    const callDetail = useMemo(() => {
      const ctx = previewData || {};
      const flat = {
        simulation: {},
        agent: {},
        persona: {},
        prompt: {},
        scenario: { info: {}, columns: {} },
        call: {},
      };
      scenarioKeyMap.current = {};

      const isText = ctx.sim_call_type === "text" || ctx.simCallType === "text";

      // ── Simulation metadata ──
      if (ctx.simulation_name) flat.simulation.name = ctx.simulation_name;
      if (ctx.simulation_type) flat.simulation.type = ctx.simulation_type;
      flat.simulation.call_type = isText ? "text" : "voice";

      // ── Agent definition ──
      const ad = ctx.agent_definition;
      if (ad) {
        if (ad.agent_name) flat.agent.name = ad.agent_name;
        if (ad.agent_type) flat.agent.type = ad.agent_type;
        if (ad.provider) flat.agent.provider = ad.provider;
        if (ad.contact_number) flat.agent.contact_number = ad.contact_number;
        if (ad.model) flat.agent.model = ad.model;
        if (ad.language) flat.agent.language = ad.language;
        if (ad.description) flat.agent.description = ad.description;
      }

      // Agent version snapshot — overrides live agent def fields since
      // this is what the call will actually run against.
      const snap = ctx.agent_version?.configuration_snapshot;
      if (snap) {
        if (snap.model) flat.agent.model = snap.model;
        if (snap.description) flat.agent.description = snap.description;
      }

      // ── Persona (simulator agent) ──
      const persona = ctx.simulator_agent;
      if (persona) {
        if (persona.name) flat.persona.name = persona.name;
        if (persona.prompt) flat.persona.prompt = persona.prompt;
        if (persona.description) flat.persona.description = persona.description;
        if (persona.voice_name) flat.persona.voice_name = persona.voice_name;
        if (persona.model) flat.persona.model = persona.model;
        if (persona.initial_message)
          flat.persona.initial_message = persona.initial_message;
      }

      // ── Prompt template (prompt-type sims) ──
      const promptTemplate = ctx.prompt_template;
      if (promptTemplate) {
        if (promptTemplate.name) flat.prompt.name = promptTemplate.name;
        if (promptTemplate.description)
          flat.prompt.description = promptTemplate.description;
      }

      // ── Scenario row metadata ──
      const scenarioRow = ctx.scenario_info;
      if (scenarioRow) {
        if (scenarioRow.name) flat.scenario.info.name = scenarioRow.name;
        if (scenarioRow.description)
          flat.scenario.info.description = scenarioRow.description;
        if (scenarioRow.scenario_type)
          flat.scenario.info.type = scenarioRow.scenario_type;
        if (scenarioRow.source) flat.scenario.info.source = scenarioRow.source;
      }

      // ── Scenario columns (per-row dataset cells) ──
      // Shape: { <uuid>: { name, type } }. Display path is
      // `scenario.columns.<name>`; persisted mapping value is the UUID.
      const scenarioColumns = ctx.scenario_columns || {};
      for (const [uuid, col] of Object.entries(scenarioColumns)) {
        const colName = col?.name;
        if (!colName) continue;
        flat.scenario.columns[colName] = RUNTIME_PLACEHOLDER;
        scenarioKeyMap.current[`scenario.columns.${colName}`] = uuid;
      }

      // ── Runtime vocabulary nested under `call.*` — always rendered
      //    with a placeholder so users see the full binding surface.
      //    Actual resolution happens server-side during eval execution.
      const runtimeLeaves = [
        ...COMMON_RUNTIME_LEAVES,
        ...(isText ? TEXT_RUNTIME_LEAVES : VOICE_RUNTIME_LEAVES),
      ];
      for (const leaf of runtimeLeaves) {
        if (!(leaf in flat.call)) flat.call[leaf] = RUNTIME_PLACEHOLDER;
      }

      return flat;
    }, [previewData]);

    // Leaves only — intermediate group keys (`agent`, `call`) are not
    // pickable. Matches the walker in SimulationTestMode.
    const fieldNames = useMemo(() => {
      if (!callDetail) return [];
      const leaves = flattenLeaves(callDetail, "");
      return leaves.map(([k]) => k);
    }, [callDetail]);

    // Expose available columns to the parent for any downstream
    // autocomplete UIs (keeps API parity with SimulationTestMode).
    useEffect(() => {
      if (!fieldNames.length || !onColumnsLoaded) return;
      const cols = fieldNames.map((k) => ({
        id: k,
        name: k,
        dataType: "text",
      }));
      onColumnsLoaded(cols, {});
    }, [fieldNames.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

    // Auto-map variables by name match.
    useEffect(() => {
      if (!fieldNames.length || !variables.length) return;
      setMapping((prev) => {
        const next = { ...prev };
        let changed = false;
        variables.forEach((v) => {
          if (next[v]) return;
          const exact = fieldNames.find((f) => f === v);
          const ci =
            !exact &&
            fieldNames.find((f) => f.toLowerCase() === v.toLowerCase());
          const match = exact || ci;
          if (match) {
            next[v] = match;
            changed = true;
          }
        });
        return changed ? next : prev;
      });
    }, [variables, fieldNames]);

    // Translate display keys → persistence keys (scenario UUIDs) before
    // emitting to the parent. Same contract as SimulationTestMode.
    const persistedMapping = useMemo(() => {
      const out = {};
      for (const [variable, field] of Object.entries(mapping)) {
        out[variable] = scenarioKeyMap.current[field] || field;
      }
      return out;
    }, [mapping, callDetail]);

    const isReady = useMemo(
      () => variables.length > 0 && variables.every((v) => !!mapping[v]),
      [variables, mapping],
    );

    useEffect(() => {
      onReadyChange?.(isReady, persistedMapping);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isReady, persistedMapping]);

    // No-op test runner — there's no call to evaluate against yet.
    // Exposed via useImperativeHandle to match SimulationTestMode's API
    // so the parent's "Run Test" button stays wired without special-
    // casing the source mode.
    const handleRunTest = useCallback(() => {
      onTestResult?.(
        false,
        "Preview only — save the eval and run the simulation to test against real call data.",
      );
    }, [onTestResult]);

    useImperativeHandle(
      ref,
      () => ({
        runTest: () => handleRunTest(),
      }),
      [handleRunTest],
    );

    const scenarioSummaries = previewData?.scenario_summaries || [];

    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Alert
          severity="info"
          icon={<Iconify icon="mdi:information-outline" width={18} />}
          sx={{ py: 0.5, "& .MuiAlert-message": { fontSize: "12px" } }}
        >
          Preview — the simulation hasn&apos;t run yet. Runtime fields
          (transcript, recordings, summary, etc.) are shown with a placeholder;
          real values are resolved server-side when the eval runs against each
          call.
        </Alert>

        {/* Per-scenario breakdown — each selected scenario will run its
            own test. The vocabulary below is shared, but each scenario
            contributes its own persona + dataset columns at run time. */}
        {scenarioSummaries.length > 0 && (
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              p: 1,
            }}
          >
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ display: "block", mb: 0.75 }}
            >
              Selected Scenarios ({scenarioSummaries.length})
            </Typography>
            <Stack spacing={0.5}>
              {scenarioSummaries.map((s) => (
                <Box
                  key={s.id}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    flexWrap: "wrap",
                    px: 1,
                    py: 0.5,
                    borderRadius: "4px",
                    bgcolor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.03)"
                        : "grey.50",
                  }}
                >
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    sx={{ fontSize: "12px" }}
                  >
                    {s.name || s.id}
                  </Typography>
                  {s.scenario_type && (
                    <Chip
                      label={s.scenario_type}
                      size="small"
                      sx={{ height: 18, fontSize: "10px" }}
                    />
                  )}
                  {s.persona?.name && (
                    <Chip
                      icon={<Iconify icon="mdi:account-voice" width={12} />}
                      label={`Persona: ${s.persona.name}`}
                      size="small"
                      variant="outlined"
                      sx={{ height: 18, fontSize: "10px" }}
                    />
                  )}
                  {s.prompt_template?.name && (
                    <Chip
                      icon={<Iconify icon="mdi:file-document" width={12} />}
                      label={`Prompt: ${s.prompt_template.name}`}
                      size="small"
                      variant="outlined"
                      sx={{ height: 18, fontSize: "10px" }}
                    />
                  )}
                </Box>
              ))}
            </Stack>
          </Box>
        )}

        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            overflow: "hidden",
          }}
        >
          <Box
            sx={{
              px: 1,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <TextField
              size="small"
              fullWidth
              placeholder="Search columns or values..."
              value={tableSearch}
              onChange={(e) => setTableSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="mdi:magnify"
                      width={14}
                      sx={{ color: "text.disabled" }}
                    />
                  </InputAdornment>
                ),
                sx: { fontSize: "12px", height: 28 },
              }}
            />
          </Box>

          <Box
            sx={{
              display: "flex",
              px: 1.5,
              py: 0.5,
              backgroundColor: (theme) =>
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.03)"
                  : "#fafafa",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ width: 220, flexShrink: 0 }}
            >
              Columns
            </Typography>
            <Typography variant="caption" fontWeight={600} sx={{ flex: 1 }}>
              Value
            </Typography>
          </Box>

          <Box sx={{ maxHeight: 400, overflowY: "auto" }}>
            {sortEntries(flattenLeaves(callDetail, ""))
              .filter(([key, val]) => {
                if (!tableSearch.trim()) return true;
                const q = tableSearch.toLowerCase();
                return key.toLowerCase().includes(q) || deepMatch(val, q);
              })
              .map(([key, val]) => {
                const isPlaceholder = val === RUNTIME_PLACEHOLDER;
                return (
                  <Box
                    key={key}
                    sx={{
                      display: "flex",
                      alignItems: "flex-start",
                      px: 1.5,
                      py: 0.6,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      "&:last-child": { borderBottom: "none" },
                      "&:hover": { backgroundColor: "action.hover" },
                    }}
                  >
                    <Typography
                      variant="caption"
                      fontWeight={500}
                      noWrap
                      sx={{ width: 220, flexShrink: 0, pt: 0.25 }}
                    >
                      {key}
                    </Typography>
                    <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                      <Typography
                        variant="caption"
                        sx={{
                          fontSize: "12px",
                          color: isPlaceholder
                            ? "text.disabled"
                            : "primary.main",
                          fontStyle: isPlaceholder ? "italic" : "normal",
                          wordBreak: "break-all",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          display: "-webkit-box",
                          WebkitLineClamp: expandedCols[key] ? 999 : 2,
                          WebkitBoxOrient: "vertical",
                          cursor: "pointer",
                        }}
                        onClick={() =>
                          setExpandedCols((prev) => ({
                            ...prev,
                            [key]: !prev[key],
                          }))
                        }
                      >
                        {/* `flattenLeaves` stops recursing when an
                            object has ≥50 keys or when it's an array
                            (so deeply-nested chat-message arrays etc.
                            arrive here as raw values). Without this
                            check React would render `[object Object]`
                            via String() coercion. JSON.stringify gives
                            users the actual content; the click handler
                            still toggles between truncated and expanded
                            so they can read the full structure. */}
                        {typeof val === "string"
                          ? isPlaceholder
                            ? val
                            : `"${val}"`
                          : val !== null && typeof val === "object"
                            ? JSON.stringify(val)
                            : String(val)}
                      </Typography>
                    </Box>
                  </Box>
                );
              })}
          </Box>
        </Box>

        {/* Variable mapping */}
        {variables.length > 0 && (
          <Box>
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ mb: 0.5, display: "block" }}
            >
              Variable Mapping
            </Typography>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
              {variables.map((variable) => (
                <Box
                  key={variable}
                  sx={{ display: "flex", alignItems: "center", gap: 1 }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.5,
                      px: 1,
                      py: 0.25,
                      borderRadius: "4px",
                      border: "1px solid",
                      borderColor: "divider",
                      minWidth: 120,
                    }}
                  >
                    <Iconify
                      icon="mdi:code-braces"
                      width={14}
                      sx={{ color: "text.secondary" }}
                    />
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{ fontSize: "12px" }}
                    >
                      {variable}
                    </Typography>
                  </Box>
                  <Iconify
                    icon="mdi:arrow-right"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                  <Autocomplete
                    size="small"
                    options={
                      mapping[variable] &&
                      !fieldNames.includes(mapping[variable])
                        ? [mapping[variable], ...fieldNames]
                        : fieldNames
                    }
                    value={mapping[variable] || null}
                    onChange={(_, val) =>
                      setMapping((prev) => ({
                        ...prev,
                        [variable]: val || "",
                      }))
                    }
                    openOnFocus
                    autoHighlight
                    selectOnFocus
                    handleHomeEndKeys
                    isOptionEqualToValue={(opt, val) => opt === val}
                    sx={{ flex: 1 }}
                    ListboxProps={{ style: { maxHeight: 260 } }}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        placeholder="Search field..."
                        InputProps={{
                          ...params.InputProps,
                          sx: {
                            ...params.InputProps.sx,
                            fontSize: "12px",
                            fontFamily: "monospace",
                            height: 28,
                            py: 0,
                          },
                        }}
                      />
                    )}
                    renderOption={(props, col) => {
                      const { key, ...rest } = props;
                      return (
                        <Box
                          component="li"
                          key={key}
                          {...rest}
                          title={col}
                          sx={{
                            ...rest.sx,
                            fontSize: "12px",
                            fontFamily: "monospace",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {col}
                        </Box>
                      );
                    }}
                  />
                </Box>
              ))}
            </Box>
          </Box>
        )}
      </Box>
    );
  },
);

CreateSimulationPreviewMode.displayName = "CreateSimulationPreviewMode";

export default CreateSimulationPreviewMode;
