// W7 A1: one composite child's jury and level controls in the composite
// editor (CompositeDetailPanel mounts it per child row): its Judge (the eval
// model picker), its Level (Whole row | Each step, with the step kind and the
// reducer) and Add judge (a jury seat: the same template again, judged by
// the model the author picks next).
import React from "react";
import PropTypes from "prop-types";
import { Button, MenuItem, Select, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ModelSelector from "src/sections/evals/components/ModelSelector";
import {
  LEVEL_OPTIONS,
  REDUCE_OPTIONS,
  STEP_KIND_OPTIONS,
  addSeat,
  levelText,
  patchRow,
  rowKeyOf,
} from "src/sections/common/EvalTypes/compositeModel";
import { isSurfaceShown } from "src/launch/launchSwitch";

const selectSx = { fontSize: "12px", minWidth: 120, height: 30 };

export default function CompositeChildControls({
  child,
  rows,
  onChildrenChange,
  disabled = false,
}) {
  const key = rowKeyOf(child);
  const set = (patch) => onChildrenChange?.(patchRow(rows, key, patch));
  // L6 launch switch: jury seats, step levels and checks post
  // child_judge_models / child_levels / child_kinds, which dev's strict
  // composite routes refuse; a launch build shows none of these controls.
  if (!isSurfaceShown("evals.jury")) return null;
  if (child.kind === "check")
    return (
      <Typography typography="s3" color="text.secondary" sx={{ pl: 4 }}>
        Task-suite check · read from the try, no judge call
      </Typography>
    );
  if (!onChildrenChange)
    return (
      <Typography typography="s3" color="text.secondary" sx={{ pl: 4 }}>
        {`${child.judge_model ? `Judge ${child.judge_model} · ` : ""}${levelText(child)}`}
      </Typography>
    );
  const step = child.level === "step";
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      flexWrap="wrap"
      sx={{ pl: 4 }}
      data-testid="composite-child-controls"
    >
      <Typography typography="s3" color="text.secondary">
        Judge
      </Typography>
      <ModelSelector
        model={child.judge_model ?? ""}
        onModelChange={(m) => set({ judge_model: m || null })}
        disabled={disabled}
        showMode={false}
        showPlus={false}
        hideDatasetContextToggle
      />
      <Typography typography="s3" color="text.secondary">
        Level
      </Typography>
      <Select
        size="small"
        value={step ? "step" : "row"}
        onChange={(e) =>
          set(
            e.target.value === "step"
              ? {
                  level: "step",
                  step_kind: child.step_kind ?? "llm",
                  reduce: child.reduce ?? "all_pass",
                }
              : { level: "row", step_kind: undefined, reduce: undefined },
          )
        }
        disabled={disabled}
        sx={selectSx}
        inputProps={{ "aria-label": "Level" }}
      >
        {LEVEL_OPTIONS.map((o) => (
          <MenuItem key={o.value} value={o.value} sx={{ fontSize: "12px" }}>
            {o.label}
          </MenuItem>
        ))}
      </Select>
      {step ? (
        <>
          <Select
            size="small"
            value={child.step_kind ?? "llm"}
            onChange={(e) => set({ step_kind: e.target.value })}
            disabled={disabled}
            sx={selectSx}
            inputProps={{ "aria-label": "Step kind" }}
          >
            {STEP_KIND_OPTIONS.map((o) => (
              <MenuItem key={o.value} value={o.value} sx={{ fontSize: "12px" }}>
                {o.label}
              </MenuItem>
            ))}
          </Select>
          <Select
            size="small"
            value={child.reduce ?? "all_pass"}
            onChange={(e) => set({ reduce: e.target.value })}
            disabled={disabled}
            sx={selectSx}
            inputProps={{ "aria-label": "Steps reduce to" }}
          >
            {REDUCE_OPTIONS.map((o) => (
              <MenuItem key={o.value} value={o.value} sx={{ fontSize: "12px" }}>
                {o.label}
              </MenuItem>
            ))}
          </Select>
        </>
      ) : null}
      <Button
        size="small"
        variant="text"
        startIcon={
          <Iconify icon="mdi:account-multiple-plus-outline" width={14} />
        }
        onClick={() => onChildrenChange(addSeat(rows, child))}
        disabled={disabled}
        sx={{ textTransform: "none", fontSize: "12px" }}
      >
        Add judge
      </Button>
    </Stack>
  );
}

CompositeChildControls.propTypes = {
  child: PropTypes.object.isRequired,
  rows: PropTypes.array.isRequired,
  onChildrenChange: PropTypes.func,
  disabled: PropTypes.bool,
};
