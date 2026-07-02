import {
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  MenuItem,
  Select,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import { format } from "date-fns";
import {
  DEFAULT_EVAL_MODEL,
  getEvalBaseName,
} from "src/sections/common/EvaluationDrawer/common";
import { FUTUREAGI_LLM_MODELS } from "src/sections/common/EvaluationDrawer/validation";
import { useEvalPickerContext } from "./context/EvalPickerContext";
import { normalizeEvalPickerEval } from "./evalPickerValue";

/**
 * Auto-map eval variables to source columns by matching names.
 *
 * Normalization strips `_`, `-`, `.`, and whitespace so a variable
 * named `agent_name` or `agentname` auto-maps onto the dot-hierarchy
 * column `agent.name` introduced in the 2026-04-13 vocabulary switch.
 */
function autoMapVariables(variables, sourceColumns) {
  const mapping = {};
  if (!variables?.length || !sourceColumns?.length) return mapping;

  const normalize = (s) =>
    String(s || "")
      .toLowerCase()
      .replace(/[_\s.-]/g, "");

  for (const variable of variables) {
    const varLower = normalize(variable);

    // Exact match
    const exact = sourceColumns.find((col) => {
      const colName = (
        col.headerName ||
        col.field ||
        col.name ||
        col.label ||
        col
      ).toString();
      return colName === variable;
    });

    if (exact) {
      mapping[variable] = (
        exact.field ||
        exact.headerName ||
        exact.name ||
        exact.label ||
        exact
      ).toString();
      continue;
    }

    // Case-insensitive match (strips dots/underscores/dashes/whitespace)
    const ci = sourceColumns.find((col) => {
      const colName = (
        col.headerName ||
        col.field ||
        col.name ||
        col.label ||
        col
      ).toString();
      return normalize(colName) === varLower;
    });

    if (ci) {
      mapping[variable] = (
        ci.field ||
        ci.headerName ||
        ci.name ||
        ci.label ||
        ci
      ).toString();
      continue;
    }

    // Partial match — variable name is contained in column name or vice versa
    const partial = sourceColumns.find((col) => {
      const colName = normalize(
        col.headerName || col.field || col.name || col.label || col,
      );
      return colName.includes(varLower) || varLower.includes(colName);
    });

    if (partial) {
      mapping[variable] = (
        partial.field ||
        partial.headerName ||
        partial.name ||
        partial.label ||
        partial
      ).toString();
    }
  }

  return mapping;
}

const EvalPickerConfig = ({ evalData, onBack, onSave, isSaving }) => {
  const theme = useTheme();
  const { sourceColumns } = useEvalPickerContext();
  const normalizedEvalData = useMemo(
    () => normalizeEvalPickerEval(evalData),
    [evalData],
  );

  // Extract required variables from eval template
  const variables = useMemo(() => {
    const keys = normalizedEvalData?.requiredKeys || [];
    return [...new Set(keys)];
  }, [normalizedEvalData]);

  // Column options for mapping dropdowns
  const columnOptions = useMemo(() => {
    if (!sourceColumns?.length) return [];
    return sourceColumns.map((col) => {
      if (typeof col === "string") return { label: col, value: col };
      return {
        label:
          col.headerName || col.field || col.name || col.label || col.id || "",
        value:
          col.field || col.headerName || col.name || col.label || col.id || "",
      };
    });
  }, [sourceColumns]);

  // State
  const [evalName, setEvalName] = useState(() => {
    return `${getEvalBaseName(normalizedEvalData)}_${format(new Date(), "dd_MMM_yyyy")}`;
  });
  const [model, setModel] = useState(
    normalizedEvalData?.model || DEFAULT_EVAL_MODEL,
  );
  const [mapping, setMapping] = useState(() =>
    autoMapVariables(variables, sourceColumns),
  );

  // Re-run auto-mapping when source columns change
  useEffect(() => {
    if (sourceColumns?.length > 0 && variables.length > 0) {
      setMapping((prev) => {
        const auto = autoMapVariables(variables, sourceColumns);
        // Preserve any user-set mappings, only fill in unmapped variables
        const merged = { ...auto };
        for (const [key, val] of Object.entries(prev)) {
          if (val) merged[key] = val;
        }
        return merged;
      });
    }
  }, [sourceColumns, variables]);

  const handleMappingChange = useCallback((variable, value) => {
    setMapping((prev) => ({ ...prev, [variable]: value }));
  }, []);

  const unmappedCount = variables.filter((v) => !mapping[v]).length;

  const handleSave = useCallback(() => {
    const templateId =
      evalData?.templateId || evalData?.template_id || evalData?.id;
    const evalConfig = {
      templateId,
      evalTemplateId: templateId,
      name: evalName,
      model,
      mapping,
      evalTemplate: normalizedEvalData,
      evalType: normalizedEvalData?.evalType,
      templateType: normalizedEvalData?.templateType,
      outputType: normalizedEvalData?.outputType,
      config: normalizedEvalData?.config,
    };
    onSave(evalConfig);
  }, [evalData, normalizedEvalData, evalName, model, mapping, onSave]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          pb: 2,
        }}
      >
        <IconButton size="small" onClick={onBack} sx={{ p: 0.5 }}>
          <Iconify icon="solar:arrow-left-linear" width={18} />
        </IconButton>
        <Typography variant="subtitle1" fontWeight={600}>
          Configure Evaluation
        </Typography>
      </Box>

      <Divider />

      {/* Config Form */}
      <Box
        sx={{
          flex: 1,
          overflow: "auto",
          py: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {/* Eval Name */}
        <Box>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mb: 0.5, display: "block" }}
          >
            Evaluation Name
          </Typography>
          <TextField
            size="small"
            fullWidth
            value={evalName}
            onChange={(e) => setEvalName(e.target.value)}
            placeholder="Enter evaluation name"
            sx={{ "& .MuiInputBase-root": { fontSize: "13px" } }}
          />
        </Box>

        {/* Model Selector */}
        {normalizedEvalData?.evalType !== "code" && (
          <Box>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mb: 0.5, display: "block" }}
            >
              Model
            </Typography>
            <Select
              size="small"
              fullWidth
              value={model}
              onChange={(e) => setModel(e.target.value)}
              sx={{ fontSize: "13px" }}
            >
              {FUTUREAGI_LLM_MODELS.map((m) => (
                <MenuItem key={m.value} value={m.value}>
                  <Box>
                    <Typography variant="body2" sx={{ fontSize: "13px" }}>
                      {m.label}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ fontSize: "11px" }}
                    >
                      {m.description}
                    </Typography>
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </Box>
        )}

        {/* Variable Mapping */}
        {variables.length > 0 && (
          <Box>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                mb: 1,
              }}
            >
              <Typography variant="caption" color="text.secondary">
                Variable Mapping
              </Typography>
              {unmappedCount > 0 && (
                <Chip
                  label={`${unmappedCount} unmapped`}
                  size="small"
                  color="warning"
                  variant="outlined"
                  sx={{ fontSize: "11px", height: 20 }}
                />
              )}
            </Box>

            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 1.5,
                p: 1.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                bgcolor:
                  theme.palette.mode === "dark"
                    ? "rgba(255,255,255,0.02)"
                    : "rgba(0,0,0,0.01)",
              }}
            >
              {variables.map((variable) => (
                <Box
                  key={variable}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1.5,
                  }}
                >
                  <Box
                    sx={{
                      minWidth: 130,
                      display: "flex",
                      alignItems: "center",
                      gap: 0.5,
                    }}
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        fontSize: "12px",
                        fontFamily: "monospace",
                        color: "primary.main",
                      }}
                    >
                      {`{{${variable}}}`}
                    </Typography>
                  </Box>

                  <Iconify
                    icon="solar:arrow-right-linear"
                    width={14}
                    sx={{ color: "text.disabled", flexShrink: 0 }}
                  />

                  {columnOptions.length > 0 ? (
                    <Select
                      size="small"
                      fullWidth
                      value={mapping[variable] || ""}
                      onChange={(e) =>
                        handleMappingChange(variable, e.target.value)
                      }
                      displayEmpty
                      sx={{
                        fontSize: "12px",
                        "& .MuiSelect-select": { py: 0.75 },
                      }}
                    >
                      <MenuItem
                        value=""
                        sx={{ fontSize: "12px", color: "text.disabled" }}
                      >
                        Select column...
                      </MenuItem>
                      {columnOptions.map((col) => (
                        <MenuItem
                          key={col.value}
                          value={col.value}
                          sx={{ fontSize: "12px" }}
                        >
                          {col.label}
                        </MenuItem>
                      ))}
                    </Select>
                  ) : (
                    <TextField
                      size="small"
                      fullWidth
                      value={mapping[variable] || ""}
                      onChange={(e) =>
                        handleMappingChange(variable, e.target.value)
                      }
                      placeholder="Enter column name..."
                      sx={{
                        "& .MuiInputBase-root": { fontSize: "12px" },
                        "& .MuiInputBase-input": { py: 0.75 },
                      }}
                    />
                  )}
                </Box>
              ))}
            </Box>
          </Box>
        )}

        {/* Eval info summary */}
        <Box
          sx={{
            p: 1.5,
            borderRadius: 1,
            bgcolor: "action.hover",
          }}
        >
          <Typography
            variant="caption"
            color="text.secondary"
            fontWeight={600}
            sx={{ mb: 0.5, display: "block" }}
          >
            Evaluation Summary
          </Typography>
          <Typography variant="body2" sx={{ fontSize: "12px" }}>
            {evalData?.name} ({evalData?.eval_type || "LLM"} eval,{" "}
            {evalData?.output_type || "pass_fail"} output)
          </Typography>
          {evalData?.description && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mt: 0.5, display: "block" }}
            >
              {evalData.description}
            </Typography>
          )}
        </Box>
      </Box>

      {/* Actions */}
      <Divider />
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 1,
          pt: 2,
        }}
      >
        <Button
          variant="outlined"
          size="small"
          onClick={onBack}
          sx={{ textTransform: "none", minWidth: 80 }}
        >
          Back
        </Button>
        <Tooltip
          title={
            unmappedCount > 0
              ? `Map all ${unmappedCount} required variable(s) before adding`
              : ""
          }
        >
          <span>
            <LoadingButton
              variant="contained"
              size="small"
              onClick={handleSave}
              loading={isSaving}
              disabled={unmappedCount > 0}
              sx={{ textTransform: "none", minWidth: 140 }}
            >
              Add Evaluation
            </LoadingButton>
          </span>
        </Tooltip>
      </Box>
    </Box>
  );
};

EvalPickerConfig.propTypes = {
  evalData: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  isSaving: PropTypes.bool,
};

export default EvalPickerConfig;
