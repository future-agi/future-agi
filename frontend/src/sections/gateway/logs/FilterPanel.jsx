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
  Switch,
  FormControlLabel,
  InputAdornment,
  Autocomplete,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAvailableModels } from "../hooks/useAvailableModels";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PANEL_WIDTH = 380;

const EMPTY_STATE = {
  startedAfter: "",
  startedBefore: "",
  model: "",
  provider: "",
  statusCodeMin: "",
  statusCodeMax: "",
  minLatency: "",
  maxLatency: "",
  minCost: "",
  maxCost: "",
  userId: "",
  sessionId: "",
  isError: false,
  cacheHit: false,
  guardrailTriggered: false,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const FilterPanel = ({ open, onClose, filters, onApply }) => {
  const availableModels = useAvailableModels();
  const [local, setLocal] = useState({ ...EMPTY_STATE });

  // Sync props -> local state when the panel opens
  useEffect(() => {
    if (open) {
      setLocal({
        startedAfter: filters.startedAfter || "",
        startedBefore: filters.startedBefore || "",
        model: filters.model || "",
        provider: filters.provider || "",
        statusCodeMin: filters.statusCodeMin || "",
        statusCodeMax: filters.statusCodeMax || "",
        minLatency: filters.minLatency || "",
        maxLatency: filters.maxLatency || "",
        minCost: filters.minCost || "",
        maxCost: filters.maxCost || "",
        userId: filters.userId || "",
        sessionId: filters.sessionId || "",
        isError: filters.isError === "true",
        cacheHit: filters.cacheHit === "true",
        guardrailTriggered: filters.guardrailTriggered === "true",
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

  const handleSwitch = useCallback(
    (key) => (event) => {
      setLocal((prev) => ({ ...prev, [key]: event.target.checked }));
    },
    [],
  );

  // --- Apply ----------------------------------------------------------------
  const handleApply = useCallback(() => {
    // Build a clean filter object -- only include non-empty values
    const out = {};

    // Preserve non-filter URL params
    if (filters.view) out.view = filters.view;
    if (filters.search) out.search = filters.search;

    if (local.startedAfter) out.startedAfter = local.startedAfter;
    if (local.startedBefore) out.startedBefore = local.startedBefore;
    if (local.model) out.model = local.model;
    if (local.provider) out.provider = local.provider;
    if (local.statusCodeMin) out.statusCodeMin = local.statusCodeMin;
    if (local.statusCodeMax) out.statusCodeMax = local.statusCodeMax;
    if (local.minLatency) out.minLatency = local.minLatency;
    if (local.maxLatency) out.maxLatency = local.maxLatency;
    if (local.minCost) out.minCost = local.minCost;
    if (local.maxCost) out.maxCost = local.maxCost;
    if (local.userId) out.userId = local.userId;
    if (local.sessionId) out.sessionId = local.sessionId;
    if (local.isError) out.isError = "true";
    if (local.cacheHit) out.cacheHit = "true";
    if (local.guardrailTriggered) out.guardrailTriggered = "true";

    onApply(out);
  }, [local, filters.view, filters.search, onApply]);

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
        <IconButton onClick={onClose} edge="end">
          <Iconify icon="mdi:close" width={20} />
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
                InputLabelProps={{ shrink: true }}
              />
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

          {/* Provider */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Provider
            </Typography>
            <TextField
              size="small"
              fullWidth
              placeholder="e.g., openai, anthropic"
              helperText="Comma-separated provider names"
              value={local.provider}
              onChange={handleText("provider")}
            />
          </Box>

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

          {/* Boolean toggles */}
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Flags
            </Typography>
            <Stack spacing={0.5}>
              <FormControlLabel
                control={
                  <Switch
                    checked={local.isError}
                    onChange={handleSwitch("isError")}
                  />
                }
                label="Errors only"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={local.cacheHit}
                    onChange={handleSwitch("cacheHit")}
                  />
                }
                label="Cache hits only"
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={local.guardrailTriggered}
                    onChange={handleSwitch("guardrailTriggered")}
                  />
                }
                label="Guardrail triggered"
              />
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
        <Button variant="contained" onClick={handleApply} sx={{ flex: 1 }}>
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
};

export default FilterPanel;
