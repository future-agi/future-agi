import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Drawer,
  Stack,
  Typography,
  TextField,
  Button,
  IconButton,
  MenuItem,
  InputAdornment,
  Autocomplete,
  Checkbox,
  Chip,
} from "@mui/material";
import { GatewayIcon } from "../shell/gateway-icons";
import { useAvailableModels } from "../hooks/useAvailableModels";
import { CUSTOM_TAG_SEPARATOR } from "../constants/requestTags";
import useMetadataValues from "./hooks/useMetadataValues";
import AgentBillingFilters from "./AgentBillingFilters";
import {
  EMPTY_AGENT_BILLING_FILTERS,
  dateFilterParam,
  isoToLocalInput,
  localInputToIso,
  readAgentBillingFilters,
  timeZoneLabel,
  writeAgentBillingFilters,
} from "./explorerFilters";
import { MANAGED_PROVIDER_LABEL } from "../utils/providerLabel";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PANEL_WIDTH = 380;

const EMPTY_STATE = {
  startedAfter: "",
  startedBefore: "",
  model: "",
  provider: [],
  application: [],
  service: [],
  tags: [],
  statusCodeMin: "",
  statusCodeMax: "",
  minLatency: "",
  maxLatency: "",
  minCost: "",
  maxCost: "",
  userId: "",
  sessionId: "",
  isError: "",
  cacheHit: "",
  guardrailTriggered: "",
  ...EMPTY_AGENT_BILLING_FILTERS,
};

const splitList = (value) => (value ? value.split(",").filter(Boolean) : []);
const booleanValue = (value) =>
  ["true", "false"].includes(value) ? value : "";

// The one provider the panel can list without a lookup: the label managed
// calls carry. Any other provider is typed in.
const MANAGED_PROVIDER_OPTIONS = [MANAGED_PROVIDER_LABEL];
const NO_PROVIDER_OPTIONS = [];

// The text field this replaced took "openai, anthropic": a typed list still
// becomes one pick per name.
const providerPicks = (values) => [
  ...new Set(
    values
      .flatMap((value) => String(value).split(","))
      .map((value) => value.trim())
      .filter(Boolean),
  ),
];

const isCustomTag = (tag) => tag.indexOf(CUSTOM_TAG_SEPARATOR) > 0;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const MultiValuePicker = ({
  label,
  options,
  value,
  onChange,
  placeholder,
  helperText,
}) => (
  <Box>
    <Typography variant="subtitle2" gutterBottom>
      {label}
    </Typography>
    <Autocomplete
      multiple
      freeSolo
      autoSelect
      disableCloseOnSelect
      size="small"
      options={options}
      value={value}
      onChange={(event, val, reason) => {
        // autoSelect re-picks the highlighted option on blur, which would untick it
        if (reason === "removeOption" && event?.type === "blur") return;
        onChange(val);
      }}
      renderOption={(props, option, { selected }) => (
        <li {...props} key={option}>
          <Checkbox size="small" checked={selected} sx={{ mr: 1 }} />
          {option}
        </li>
      )}
      renderTags={(tags, getTagProps) =>
        tags.map((option, index) => (
          <Chip
            variant="outlined"
            label={option}
            size="small"
            {...getTagProps({ index })}
            key={option}
          />
        ))
      }
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder={value.length ? "" : placeholder}
          helperText={helperText}
        />
      )}
    />
  </Box>
);

MultiValuePicker.propTypes = {
  label: PropTypes.string.isRequired,
  options: PropTypes.arrayOf(PropTypes.string).isRequired,
  value: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  helperText: PropTypes.string,
};

// `managedOffered` false: the deployment offers no managed models, so the
// Provider field's help names provider names only. Its Future AGI managed
// option is offered where managed models are offered, or where
// `managedLogged` says the loaded logs hold a managed call (one from before
// managed models were turned off); otherwise no request shown has that
// provider, and the field offers none.
const FilterPanel = ({
  open,
  onClose,
  filters,
  onApply,
  managedOffered = true,
  managedLogged = false,
  hideCacheHits = false,
  hideGuardrails = false,
}) => {
  const availableModels = useAvailableModels();
  const { data: metadataValues } = useMetadataValues({ enabled: open });
  const [local, setLocal] = useState({ ...EMPTY_STATE });

  // Sync props -> local state when the panel opens
  useEffect(() => {
    if (open) {
      setLocal({
        // The inputs hold local time; the applied filter holds ISO 8601 with
        // an offset.
        startedAfter: isoToLocalInput(filters.startedAfter),
        startedBefore: isoToLocalInput(filters.startedBefore),
        model: filters.model || "",
        provider: providerPicks(splitList(filters.provider)),
        application: splitList(filters.application),
        service: splitList(filters.service),
        tags: splitList(filters.tags),
        statusCodeMin: filters.statusCodeMin || "",
        statusCodeMax: filters.statusCodeMax || "",
        minLatency: filters.minLatency || "",
        maxLatency: filters.maxLatency || "",
        minCost: filters.minCost || "",
        maxCost: filters.maxCost || "",
        userId: filters.userId || "",
        sessionId: filters.sessionId || "",
        isError: booleanValue(filters.isError),
        cacheHit: booleanValue(filters.cacheHit),
        guardrailTriggered: booleanValue(filters.guardrailTriggered),
        ...readAgentBillingFilters(filters),
      });
    }
  }, [open, filters]);

  // --- Field updaters -------------------------------------------------------
  const handleText = useCallback(
    (key) => (event) => {
      setLocal((prev) => ({ ...prev, [key]: event.target.value }));
    },
    [],
  );

  const handleList = useCallback(
    (key) => (values) => {
      setLocal((prev) => ({ ...prev, [key]: values }));
    },
    [],
  );

  const handlePatch = useCallback((patch) => {
    setLocal((prev) => ({ ...prev, ...patch }));
  }, []);

  // Keep seconds and the original DST offset when the minute-resolution input
  // is unchanged. An edited input still uses the existing local-zone converter.
  const dateValue = (key) =>
    local[key] && local[key] === isoToLocalInput(filters[key])
      ? dateFilterParam(filters[key]) || ""
      : localInputToIso(local[key]);
  const startedAfter = dateValue("startedAfter");
  const startedBefore = dateValue("startedBefore");
  const reversedRange = Boolean(
    startedAfter &&
      startedBefore &&
      new Date(startedBefore).getTime() < new Date(startedAfter).getTime(),
  );

  // --- Apply ----------------------------------------------------------------
  const handleApply = useCallback(() => {
    if (reversedRange) return;
    // Keep what this panel does not edit (search, sort, quick filters such as
    // fallbacks or streaming); a page number no longer applies.
    const out = {};
    Object.entries(filters).forEach(([key, value]) => {
      if (key in EMPTY_STATE || key === "page") return;
      out[key] = value;
    });

    // An instant with its zone, so the range is the one the viewer picked.
    if (startedAfter) out.startedAfter = startedAfter;
    if (startedBefore) out.startedBefore = startedBefore;
    if (local.model) out.model = local.model;
    if (local.provider.length) out.provider = local.provider.join(",");
    if (local.application.length) out.application = local.application.join(",");
    if (local.service.length) out.service = local.service.join(",");
    if (local.tags.length) out.tags = local.tags.join(",");
    if (local.statusCodeMin) out.statusCodeMin = local.statusCodeMin;
    if (local.statusCodeMax) out.statusCodeMax = local.statusCodeMax;
    if (local.minLatency) out.minLatency = local.minLatency;
    if (local.maxLatency) out.maxLatency = local.maxLatency;
    if (local.minCost) out.minCost = local.minCost;
    if (local.maxCost) out.maxCost = local.maxCost;
    if (local.userId) out.userId = local.userId;
    if (local.sessionId) out.sessionId = local.sessionId;
    if (local.isError) out.isError = local.isError;
    if (local.cacheHit) out.cacheHit = local.cacheHit;
    if (local.guardrailTriggered)
      out.guardrailTriggered = local.guardrailTriggered;
    Object.assign(out, writeAgentBillingFilters(local));

    onApply(out);
  }, [local, filters, onApply, reversedRange, startedAfter, startedBefore]);

  // --- Clear ----------------------------------------------------------------
  const handleClear = useCallback(() => {
    setLocal({ ...EMPTY_STATE });
  }, []);

  // =========================================================================
  // Render
  // =========================================================================

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={{
        "& .MuiDrawer-paper": { width: PANEL_WIDTH },
      }}
    >
      {/* ---- Header ---- */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        p={2}
        borderBottom={1}
        borderColor="divider"
      >
        <Typography variant="h6">Filters</Typography>
        <IconButton onClick={onClose} edge="end" aria-label="Close filters">
          <GatewayIcon name="close" size={16} />
        </IconButton>
      </Stack>

      {/* ---- Body ---- */}
      <Box p={2} sx={{ overflowY: "auto", flex: 1 }}>
        <Stack spacing={3}>
          {/* Date range */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Date Range
            </Typography>
            <Stack spacing={1.5}>
              <TextField
                label="Start Date"
                type="datetime-local"
                size="small"
                fullWidth
                value={local.startedAfter}
                onChange={handleText("startedAfter")}
                InputLabelProps={{ shrink: true }}
              />
              <TextField
                label="End Date"
                type="datetime-local"
                size="small"
                fullWidth
                value={local.startedBefore}
                onChange={handleText("startedBefore")}
                error={reversedRange}
                helperText={
                  reversedRange
                    ? "End time must be on or after start time."
                    : undefined
                }
                InputLabelProps={{ shrink: true }}
              />
              <Typography variant="caption" color="text.secondary">
                Times are in {timeZoneLabel()}, as the table shows them.
              </Typography>
            </Stack>
          </Box>

          {/* Model */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Model
            </Typography>
            <Autocomplete
              freeSolo
              options={availableModels}
              inputValue={local.model}
              onInputChange={(_, val) =>
                setLocal((prev) => ({ ...prev, model: val }))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  fullWidth
                  placeholder="e.g., gpt-4o, claude-3-opus"
                  helperText="Comma-separated model names"
                />
              )}
            />
          </Box>

          {/* Provider: the Provider column names a managed call's provider
              "Future AGI managed", and
              the API's provider filter takes that label for the managed
              rows; provider names are typed in as before. */}
          <MultiValuePicker
            label="Provider"
            options={
              managedOffered || managedLogged
                ? MANAGED_PROVIDER_OPTIONS
                : NO_PROVIDER_OPTIONS
            }
            value={local.provider}
            onChange={(values) => handleList("provider")(providerPicks(values))}
            placeholder="e.g., openai, anthropic"
            helperText={
              managedOffered
                ? `${MANAGED_PROVIDER_LABEL} for managed calls, or type provider names`
                : "Comma-separated provider names"
            }
          />

          <MultiValuePicker
            label="Application"
            options={metadataValues?.application ?? []}
            value={local.application}
            onChange={handleList("application")}
            placeholder="Select applications..."
          />

          <MultiValuePicker
            label="Service"
            options={metadataValues?.service ?? []}
            value={local.service}
            onChange={handleList("service")}
            placeholder="Select services..."
          />

          <MultiValuePicker
            label="Custom Tags"
            options={metadataValues?.tags ?? []}
            value={local.tags}
            onChange={(values) =>
              handleList("tags")(values.filter(isCustomTag))
            }
            placeholder="Select or type key:value..."
            helperText="Any value of a key matches; every key must match"
          />

          {/* Status code range */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Status Code
            </Typography>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Min"
                type="number"
                size="small"
                fullWidth
                value={local.statusCodeMin}
                onChange={handleText("statusCodeMin")}
                inputProps={{ min: 100, max: 599 }}
              />
              <TextField
                label="Max"
                type="number"
                size="small"
                fullWidth
                value={local.statusCodeMax}
                onChange={handleText("statusCodeMax")}
                inputProps={{ min: 100, max: 599 }}
              />
            </Stack>
          </Box>

          {/* Latency range */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Latency
            </Typography>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Min (ms)"
                type="number"
                size="small"
                fullWidth
                value={local.minLatency}
                onChange={handleText("minLatency")}
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">ms</InputAdornment>
                  ),
                }}
              />
              <TextField
                label="Max (ms)"
                type="number"
                size="small"
                fullWidth
                value={local.maxLatency}
                onChange={handleText("maxLatency")}
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">ms</InputAdornment>
                  ),
                }}
              />
            </Stack>
          </Box>

          {/* Cost range */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Cost
            </Typography>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Min ($)"
                type="number"
                size="small"
                fullWidth
                value={local.minCost}
                onChange={handleText("minCost")}
                inputProps={{ step: "0.001" }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">$</InputAdornment>
                  ),
                }}
              />
              <TextField
                label="Max ($)"
                type="number"
                size="small"
                fullWidth
                value={local.maxCost}
                onChange={handleText("maxCost")}
                inputProps={{ step: "0.001" }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">$</InputAdornment>
                  ),
                }}
              />
            </Stack>
          </Box>

          {/* User ID */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              User ID
            </Typography>
            <TextField
              size="small"
              fullWidth
              placeholder="e.g., user_123"
              value={local.userId}
              onChange={handleText("userId")}
            />
          </Box>

          {/* Session ID */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Session ID
            </Typography>
            <TextField
              size="small"
              fullWidth
              placeholder="e.g., sess_abc"
              value={local.sessionId}
              onChange={handleText("sessionId")}
            />
          </Box>

          <AgentBillingFilters
            value={local}
            onChange={handlePatch}
            managedBilling={managedOffered}
          />

          {/* Any, Yes and No are distinct API conditions. */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Flags
            </Typography>
            <Stack spacing={1.5}>
              {[
                ["isError", "Error", false],
                ["cacheHit", "Cache hit", hideCacheHits],
                ["guardrailTriggered", "Guardrail triggered", hideGuardrails],
              ]
                .filter(([key, , hidden]) => !hidden || local[key])
                .map(([key, label]) => (
                  <TextField
                    key={key}
                    select
                    size="small"
                    fullWidth
                    label={label}
                    value={local[key]}
                    onChange={handleText(key)}
                    SelectProps={{ displayEmpty: true }}
                    InputLabelProps={{ shrink: true }}
                  >
                    <MenuItem value="">Any</MenuItem>
                    <MenuItem value="true">Yes</MenuItem>
                    <MenuItem value="false">No</MenuItem>
                  </TextField>
                ))}
            </Stack>
          </Box>
        </Stack>
      </Box>

      {/* ---- Footer ---- */}
      <Stack
        direction="row"
        spacing={2}
        p={2}
        borderTop={1}
        borderColor="divider"
        sx={{ position: "sticky", bottom: 0, bgcolor: "background.paper" }}
      >
        <Button variant="text" onClick={handleClear} sx={{ flex: 1 }}>
          Clear All
        </Button>
        <Button
          variant="contained"
          color="primary"
          onClick={handleApply}
          disabled={reversedRange}
          sx={{ flex: 1 }}
        >
          Apply
        </Button>
      </Stack>
    </Drawer>
  );
};

FilterPanel.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  filters: PropTypes.object.isRequired,
  onApply: PropTypes.func.isRequired,
  managedOffered: PropTypes.bool,
  managedLogged: PropTypes.bool,
  // The gateway runs no response cache / no guardrail check: the flag is not
  // offered (one already applied stays, so it can be turned off).
  hideCacheHits: PropTypes.bool,
  hideGuardrails: PropTypes.bool,
};

export default FilterPanel;
