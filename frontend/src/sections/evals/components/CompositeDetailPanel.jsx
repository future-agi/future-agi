import {
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  FormControlLabel,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import EvalPickerDrawer from "src/sections/common/EvalPicker/EvalPickerDrawer";
import CompositeAxisTabs, { axisToLockedFilters } from "./CompositeAxisTabs";

const AGGREGATION_OPTIONS = [
  { value: "weighted_avg", label: "Weighted Average" },
  { value: "avg", label: "Average" },
  { value: "min", label: "Minimum (safety gate)" },
  { value: "max", label: "Maximum" },
  { value: "pass_rate", label: "Pass Rate" },
];

const AGGREGATION_DESCRIPTIONS = {
  weighted_avg:
    "Sum of (score × weight) divided by sum of weights. Set weights on each child below.",
  avg: "Simple average of all child scores.",
  min: "Composite equals the lowest child score — useful as a safety gate.",
  max: "Composite equals the highest child score.",
  pass_rate: "Fraction of children that met their own pass threshold.",
};

/**
 * Editable panel for composite eval configuration.
 *
 * Controlled component — parent owns state. Read-only if `editable={false}`.
 * When editable, calls the provided onChange* handlers on any change; parent
 * is responsible for marking dirty and persisting via the update API.
 */
const CompositeDetailPanel = ({
  name,
  description,
  aggregationEnabled,
  aggregationFunction,
  compositeChildAxis = "pass_fail",
  children: childrenList = [],
  childWeights = {},
  editable = false,
  disabled = false,
  // When true AND `editable` is false, weight inputs stay interactive so
  // callers (e.g. the EvalPicker) can collect per-binding weight
  // overrides without exposing the full composite edit surface
  // (add/remove children, switch axis, rename, etc.).
  weightEditable = false,
  // Forward dataset context to the inner child picker so the EvalPicker
  // can show its mapping screen for each child. When omitted (e.g. when
  // editing a composite from /evals/create with no dataset bound), the
  // child picker falls back to the original direct-add behaviour.
  pickerSourceId = "",
  pickerSourceColumns = [],
  onNameChange,
  onDescriptionChange,
  onAggregationEnabledChange,
  onAggregationFunctionChange,
  onCompositeChildAxisChange,
  onChildrenChange,
  onChildWeightsChange,
}) => {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pendingAxis, setPendingAxis] = useState(null);

  const showWeightInput =
    aggregationEnabled && aggregationFunction === "weighted_avg";
  const weightsInteractive = !disabled && (editable || weightEditable);

  const handleAxisRequested = (nextAxis) => {
    if (nextAxis === compositeChildAxis) return;
    if (childrenList.length > 0) {
      setPendingAxis(nextAxis);
      return;
    }
    onCompositeChildAxisChange?.(nextAxis);
  };

  const confirmAxisSwitch = () => {
    if (!pendingAxis) return;
    onChildrenChange?.([]);
    onChildWeightsChange?.({});
    onCompositeChildAxisChange?.(pendingAxis);
    setPendingAxis(null);
  };

  // Accepts both shapes:
  //   - skipConfig=true raw eval metadata: { id, name, evalType, ... }
  //   - skipConfig=false EvalPickerConfigFull payload:
  //     { templateId, evalTemplateId, name, evalType, mapping,
  //       versionId, ... }
  // The latter is sent when the user goes through the config screen
  // for a child eval (playground + version + settings + mapping).
  const handleEvalAdded = (evalMeta) => {
    const childId =
      evalMeta?.id || evalMeta?.templateId || evalMeta?.evalTemplateId;
    if (!childId) return;
    if (childrenList.some((c) => c.child_id === childId)) {
      setPickerOpen(false);
      return;
    }
    const next = [
      ...childrenList,
      {
        child_id: childId,
        child_name: evalMeta.name || childId,
        order: childrenList.length,
        eval_type: evalMeta.evalType || evalMeta.eval_type || "llm",
        weight: 1.0,
        // Persist the per-child version the user pinned in the picker
        // so the composite always invokes this exact version. Stored as
        // `pinned_version_id` for the backend; the human-readable
        // `pinned_version_number` is resolved server-side on fetch.
        ...(evalMeta.versionId
          ? { pinned_version_id: evalMeta.versionId }
          : {}),
        // Persist the per-child variable mapping the user just configured.
        // Backend `composite_runner` uses it when resolving each child's
        // variables against the dataset row at evaluation time.
        ...(evalMeta.mapping && Object.keys(evalMeta.mapping).length
          ? { mapping: evalMeta.mapping }
          : {}),
      },
    ];
    onChildrenChange?.(next);
    setPickerOpen(false);
  };

  const handleRemoveChild = (childId) => {
    const next = childrenList
      .filter((c) => c.child_id !== childId)
      .map((c, i) => ({ ...c, order: i }));
    onChildrenChange?.(next);
    if (childWeights[childId] != null) {
      const nextWeights = { ...childWeights };
      delete nextWeights[childId];
      onChildWeightsChange?.(nextWeights);
    }
  };

  const handleWeightChange = (childId, value) => {
    onChildWeightsChange?.({
      ...childWeights,
      [childId]: parseFloat(value) || 0,
    });
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box>
        <Typography
          typography="s1"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
          sx={{ mb: 0.5 }}
        >
          Composite Configuration
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          A composite eval runs multiple child evals against the same input and
          optionally aggregates their scores into a single value.
        </Typography>
      </Box>

      {/* Name */}
      {editable ? (
        <Box>
          <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
            Name
            <Box component="span" sx={{ color: "error.main", ml: 0.25 }}>
              *
            </Box>
          </Typography>
          <TextField
            fullWidth
            size="small"
            value={name || ""}
            onChange={(e) =>
              onNameChange?.(
                e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""),
              )
            }
            disabled={disabled}
            helperText="Lowercase letters, numbers, hyphens, underscores only"
          />
        </Box>
      ) : (
        <Typography
          typography="s1"
          fontWeight={"fontWeightRegular"}
          color="text.secondary"
        >
          {name}
          {description ? ` — ${description}` : ""}
        </Typography>
      )}

      {/* Description — editable mode only */}
      {editable && (
        <Box>
          <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
            Description
          </Typography>
          <TextField
            fullWidth
            size="small"
            multiline
            minRows={2}
            placeholder="Describe what this composite evaluates"
            value={description || ""}
            onChange={(e) => onDescriptionChange?.(e.target.value)}
            disabled={disabled}
          />
        </Box>
      )}

      {/* Child axis tab selector — locked once children are added */}
      {editable && (
        <CompositeAxisTabs
          value={compositeChildAxis}
          onChange={handleAxisRequested}
          disabled={disabled || childrenList.length > 0}
        />
      )}

      {/* Aggregation settings */}
      <Box
        sx={{
          p: 1.5,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            mb: aggregationEnabled ? 2 : 0,
            gap: 1,
          }}
        >
          <Box>
            <Typography variant="body2" fontWeight={600}>
              Aggregate child eval scores
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.25 }}
            >
              When on, child scores are combined into a single composite score.
              When off, each child runs independently.
            </Typography>
          </Box>
          {editable ? (
            <FormControlLabel
              control={
                <Checkbox
                  checked={!!aggregationEnabled}
                  onChange={(e) =>
                    onAggregationEnabledChange?.(e.target.checked)
                  }
                  disabled={disabled}
                />
              }
              label=""
              sx={{ m: 0 }}
            />
          ) : (
            <Chip
              size="small"
              label={aggregationEnabled ? "ON" : "OFF"}
              color={aggregationEnabled ? "success" : "default"}
              sx={{ fontWeight: 600 }}
            />
          )}
        </Box>

        {aggregationEnabled && (
          <Box>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              Aggregation function
            </Typography>
            {editable ? (
              <Select
                fullWidth
                size="small"
                value={aggregationFunction || "weighted_avg"}
                onChange={(e) => onAggregationFunctionChange?.(e.target.value)}
                disabled={disabled}
              >
                {AGGREGATION_OPTIONS.map((opt) => (
                  <MenuItem
                    key={opt.value}
                    value={opt.value}
                    sx={{ fontSize: "13px" }}
                  >
                    {opt.label}
                  </MenuItem>
                ))}
              </Select>
            ) : (
              <Typography variant="body2">
                {AGGREGATION_OPTIONS.find(
                  (o) => o.value === aggregationFunction,
                )?.label || aggregationFunction}
              </Typography>
            )}
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.75 }}
            >
              {AGGREGATION_DESCRIPTIONS[aggregationFunction] || ""}
            </Typography>
          </Box>
        )}
      </Box>

      <Divider />

      {/* Child list */}
      <Box>
        <Typography variant="body2" fontWeight={600} sx={{ mb: 1 }}>
          Children ({childrenList.length})
        </Typography>
        <Stack spacing={1}>
          {childrenList.map((child) => (
            <Box
              key={child.child_id}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                p: 1.25,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                variant="body2"
                sx={{
                  color: "primary.main",
                  fontWeight: 600,
                  minWidth: 24,
                }}
              >
                #{child.order + 1}
              </Typography>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  variant="body2"
                  sx={{
                    fontWeight: 600,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {child.child_name}
                </Typography>
                {child.eval_type && (
                  <Typography variant="caption" color="text.secondary">
                    {child.eval_type}
                    {child.pinned_version_number != null
                      ? ` · v${child.pinned_version_number}`
                      : ""}
                  </Typography>
                )}
              </Box>
              {showWeightInput && (
                <TextField
                  size="small"
                  type="number"
                  label="Weight"
                  inputProps={{ min: 0, step: 0.1 }}
                  value={
                    childWeights[child.child_id] ??
                    (child.weight != null ? child.weight : 1.0)
                  }
                  onChange={(e) =>
                    handleWeightChange(child.child_id, e.target.value)
                  }
                  disabled={!weightsInteractive}
                  sx={{ width: 90 }}
                />
              )}
              {!showWeightInput &&
                child.weight != null &&
                child.weight !== 1 && (
                  <Chip
                    size="small"
                    label={`weight: ${child.weight}`}
                    variant="outlined"
                  />
                )}
              {editable && (
                <IconButton
                  size="small"
                  onClick={() => handleRemoveChild(child.child_id)}
                  disabled={disabled}
                >
                  <Iconify icon="solar:trash-bin-trash-bold" width={16} />
                </IconButton>
              )}
            </Box>
          ))}

          {editable && (
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="mingcute:add-line" width={16} />}
              onClick={() => setPickerOpen(true)}
              disabled={disabled}
              sx={{
                mt: 0.5,
                alignSelf: "flex-start",
                textTransform: "none",
                fontSize: "12px",
              }}
            >
              Add evaluation
            </Button>
          )}
        </Stack>
      </Box>

      {editable && (
        <EvalPickerDrawer
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          onEvalAdded={handleEvalAdded}
          existingEvals={childrenList.map((c) => ({ id: c.child_id }))}
          // Always step into the EvalPickerConfigFull screen — users
          // need the version selector, scoring settings and (when a
          // dataset is bound) the variable-mapping editor before the
          // child is committed to the composite. The previous
          // skipConfig=true bypassed all of that and added the child
          // straight to the list with template defaults.
          skipConfig={false}
          source={pickerSourceId ? "dataset" : "composite"}
          sourceId={pickerSourceId || ""}
          sourceColumns={pickerSourceColumns || []}
          lockedFilters={axisToLockedFilters(compositeChildAxis)}
        />
      )}

      <Dialog
        open={Boolean(pendingAxis)}
        onClose={() => setPendingAxis(null)}
        maxWidth="xs"
      >
        <DialogTitle sx={{ fontSize: "16px", fontWeight: 600 }}>
          Change child evaluation type?
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ fontSize: "13px" }}>
            Switching the child evaluation type will clear the current{" "}
            {childrenList.length} child
            {childrenList.length === 1 ? "" : "ren"} because they don&apos;t
            match the new type. Continue?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            size="small"
            onClick={() => setPendingAxis(null)}
            sx={{ textTransform: "none" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="warning"
            onClick={confirmAxisSwitch}
            sx={{ textTransform: "none" }}
          >
            Clear &amp; switch
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

CompositeDetailPanel.propTypes = {
  name: PropTypes.string,
  description: PropTypes.string,
  aggregationEnabled: PropTypes.bool,
  aggregationFunction: PropTypes.string,
  compositeChildAxis: PropTypes.string,
  children: PropTypes.array,
  childWeights: PropTypes.object,
  editable: PropTypes.bool,
  disabled: PropTypes.bool,
  weightEditable: PropTypes.bool,
  pickerSourceId: PropTypes.string,
  pickerSourceColumns: PropTypes.array,
  onNameChange: PropTypes.func,
  onDescriptionChange: PropTypes.func,
  onAggregationEnabledChange: PropTypes.func,
  onAggregationFunctionChange: PropTypes.func,
  onCompositeChildAxisChange: PropTypes.func,
  onChildrenChange: PropTypes.func,
  onChildWeightsChange: PropTypes.func,
};

export default CompositeDetailPanel;
