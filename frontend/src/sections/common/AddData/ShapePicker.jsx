import React from "react";
import PropTypes from "prop-types";
import {
  Button,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import Term from "src/sections/common/ux/Term";
import { shapesFor } from "./shapes";

/** `disabledReason(id)` may return a string or `{text, actionLabel, onAction}`. */
const reasonOf = (r) =>
  !r ? null : typeof r === "string" ? { text: r } : { ...r, text: r.text };

const helperFor = (shape, sourceKind) => {
  if (sourceKind === "traces") return shape.explainer;
  switch (shape.id) {
    case "task":
      return (
        <>
          As tasks: each session becomes a task the agent solves from its{" "}
          <Term k="startState">start state</Term>.
        </>
      );
    case "conversation":
      return "As conversations: each session becomes a chat to replay";
    case "per_turn":
      return "One row per turn: every agent turn is checked";
    case "failing_turn":
      return "Only the failing turn: replay up to the turn that failed";
    default:
      return shape.explainer;
  }
};

/**
 * What each source item becomes (R2-SPEC §7.3.2 / §7.3.3): segmented; each
 * segment's one-line explainer and mini example are its tooltip (the sheet's
 * 70-word budget; the preview shows the result). Shapes a target cannot take
 * are disabled with the reason.
 */
const ShapePicker = ({ sourceKind, value, onChange, disabledReason }) => {
  const shapes = shapesFor(sourceKind);
  const current = shapes.find((s) => s.id === value) ?? shapes[0];
  if (shapes.length < 2)
    return (
      <Typography typography="s2" color="text.secondary">
        {current.explainer}
      </Typography>
    );
  return (
    <Stack spacing={0.75}>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={current.id}
        onChange={(_, v) => v && onChange?.(v)}
        aria-label="Item shape"
        sx={{
          display: "grid",
          gap: 0.75,
          gridTemplateColumns: shapes.length === 3 ? "1fr" : "1fr 1fr",
          "& .MuiToggleButtonGroup-grouped, & .MuiToggleButtonGroup-grouped:not(:first-of-type), & .MuiToggleButtonGroup-grouped:not(:last-of-type)":
            {
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              marginLeft: 0,
            },
          "& .MuiToggleButton-root": {
            typography: "s2",
            py: 0.25,
            px: 1.25,
            textTransform: "none",
            height: 30,
            justifyContent: "flex-start",
          },
          // IT-R2 G5 P3: selected = filled with ✓; disabled = grey dashed.
          "& .MuiToggleButton-root.Mui-selected, & .MuiToggleButtonGroup-grouped.Mui-selected:not(:first-of-type)":
            {
              borderColor: "primary.main",
              bgcolor: "primary.main",
              color: "primary.contrastText",
              fontWeight: 600,
              "&:hover": { bgcolor: "primary.dark" },
            },
          "& .MuiToggleButton-root.Mui-disabled, & .MuiToggleButtonGroup-grouped.Mui-disabled:not(:first-of-type)":
            {
              borderStyle: "dashed",
              borderColor: "divider",
              color: "text.disabled",
              bgcolor: "transparent",
            },
        }}
      >
        {shapes.map((s) => {
          const reason = reasonOf(disabledReason?.(s.id));
          const selected = s.id === current.id;
          return (
            <ToggleButton
              key={s.id}
              value={s.id}
              disabled={Boolean(reason)}
              aria-label={reason ? `${s.label} (unavailable)` : s.label}
              sx={reason ? { pointerEvents: "auto !important" } : undefined}
            >
              <CustomTooltip
                show
                title={
                  reason?.text ??
                  `${s.explainer}${s.example ? ` Example: ${s.example}` : ""}`
                }
                size="small"
                arrow
                enterDelay={250}
              >
                <Stack
                  component="span"
                  direction="row"
                  alignItems="center"
                  gap={0.5}
                >
                  {selected ? <Iconify icon="mdi:check" width={14} /> : null}
                  {reason ? (
                    <Iconify icon="mdi:lock-outline" width={13} />
                  ) : null}
                  <span>{s.label}</span>
                </Stack>
              </CustomTooltip>
            </ToggleButton>
          );
        })}
      </ToggleButtonGroup>
      <Typography typography="s3" color="text.secondary">
        {helperFor(current, sourceKind)}
      </Typography>
      {shapes
        .map((s) => ({ s, r: reasonOf(disabledReason?.(s.id)) }))
        .filter(({ r }) => r)
        .slice(0, 1)
        .map(({ s, r }) => (
          <Stack
            key={s.id}
            direction="row"
            alignItems="flex-start"
            gap={0.75}
            data-testid="shape-disabled-reason"
          >
            <Iconify
              icon="mdi:information-outline"
              width={15}
              sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
            />
            <Stack sx={{ flex: 1, minWidth: 0 }} alignItems="flex-start">
              <Typography typography="s3" color="text.secondary">
                {r.text}
              </Typography>
              {r.actionLabel && r.onAction ? (
                <Button
                  size="small"
                  variant="text"
                  onClick={r.onAction}
                  sx={{ px: 0, minWidth: 0, typography: "s3", fontWeight: 600 }}
                >
                  {r.actionLabel}
                </Button>
              ) : null}
            </Stack>
          </Stack>
        ))}
    </Stack>
  );
};

ShapePicker.propTypes = {
  sourceKind: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func,
  disabledReason: PropTypes.func,
};

export default ShapePicker;
